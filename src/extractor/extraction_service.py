"""
Módulo 3 — Servicio orquestador de extracción del Avance 2.

Conecta los módulos implementados en este avance:

    diagnóstico (Avance 1)
      → M3 extracción multitecnología
      → M4 selección/esquema
      → M5 transformación, calidad y linaje
      → M6 almacenamiento raw/processed
      → M7 reporte de ejecución

Nunca intenta evadir barreras: si el diagnóstico detectó CAPTCHA,
autenticación, HTTP 401/403/429/503 o robots.txt desfavorable, se detiene.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from src.config.settings import AnalysisTarget, ProjectConfig, get_config
from src.extractor.base import ExtractionBatch, ExtractorFactory
from src.monitor.extraction_reporter import ExtractionReporter
from src.monitor.logger import get_logger
from src.selector.builder import SchemaBuilder
from src.selector.schema import ExtractionSchema
from src.storage.adapters import StorageFactory
from src.transformer.pipeline import (
    DataCleaner,
    Deduplicator,
    LineageEnricher,
    Normalizer,
    TransformationPipeline,
    Validator,
)
from src.utils.profiles import ProfileManager
from src.utils.security import sanitize_url

logger = get_logger("extractor.service")


class ExtractionService:
    """Ejecuta el flujo completo de extracción para un objetivo registrado."""

    def __init__(self, config: ProjectConfig | None = None) -> None:
        self.config = config or get_config()
        self.profiles = ProfileManager(self.config.paths.profiles_dir)  # type: ignore[arg-type]
        self.reporter = ExtractionReporter(self.config.paths.reports_dir)  # type: ignore[arg-type]

    def run(
        self,
        target: AnalysisTarget,
        *,
        diagnosis: dict[str, Any] | None = None,
        schema_config: dict[str, Any] | None = None,
        schema_origin: str | None = None,
        strategy: str | None = None,
        max_pages: int | None = None,
        max_records: int | None = None,
        delay_s: float | None = None,
        formats: list[str] | None = None,
        output_dir: Path | str | None = None,
        profile: dict[str, Any] | None = None,
        reuse_diagnosis: bool = False,
        use_selenium: bool = True,
        manual_wait: Callable[[], bool] | None = None,
        manual_retry: Callable[[str, float], bool] | None = None,
        manual_next: Callable[[int, float], bool] | None = None,
    ) -> dict[str, Any]:
        """Ejecuta la extracción y devuelve un resumen sanitizado."""
        if target.auth_mode != "none":
            if strategy == "selenium":
                raise ValueError("La autenticación HTTP solo está disponible con requests o API.")
            if strategy in (None, "auto"):
                strategy = "requests"
        started_at = datetime.now().isoformat(timespec="seconds")
        execution_id = str(uuid.uuid4())
        profile = profile if profile is not None else self.profiles.load(target.url)

        effective_diagnosis = diagnosis
        if target.auth_mode != "none" and effective_diagnosis is None:
            reuse_diagnosis = False
        if profile and (profile.get("current_diagnosis") or {}).get("http_auth_mode", "none") != "none":
            reuse_diagnosis = False
        if effective_diagnosis is None:
            if reuse_diagnosis and profile and profile.get("current_diagnosis"):
                effective_diagnosis = profile["current_diagnosis"]
                logger.info("Reutilizando diagnóstico almacenado en el perfil")
            else:
                from src.inspector.web_inspector import WebInspector

                effective_diagnosis = WebInspector(self.config).inspect(
                    target,
                    use_selenium=use_selenium,
                    previous_profile=profile,
                )

        summary: dict[str, Any] = self._base_summary(
            target,
            execution_id=execution_id,
            started_at=started_at,
            diagnosis=effective_diagnosis,
        )

        block_reason = self._block_reason(target, effective_diagnosis)
        manual_batch = None
        if block_reason and manual_wait is not None:
            from src.extractor.manual_session import acquire_manual, manual_eligible
            if manual_eligible(target, effective_diagnosis):
                if manual_next is None:
                    manual_batch = acquire_manual(target, self.config, manual_wait, manual_retry)
                else:
                    manual_batch = acquire_manual(
                        target, self.config, manual_wait, manual_retry, manual_next,
                        max(1, int(max_pages or self.config.extraction.max_pages)),
                    )
                if not manual_batch.pages:
                    summary["errors"].extend(manual_batch.errors)
                    manual_batch = None
        if block_reason and manual_batch is None:
            summary["status"] = "blocked"
            summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
            summary["errors"].append(block_reason)
            summary["outputs"] = {}
            logger.warning("Extracción cancelada: %s", block_reason)
            self._write_report(summary)
            return summary
        if manual_batch is not None:
            summary["access_status"] = "verificado_manual"
            summary["manual_verification"] = True

        rules = dict(target.extraction_rules or {})
        max_pages = max(1, int(max_pages or self.config.extraction.max_pages))
        max_records = max(1, int(max_records or self.config.extraction.max_records))
        delay = (
            float(delay_s)
            if delay_s is not None
            else float(self.config.extraction.delay_s)
        )
        formats = [f.lower() for f in (formats or [self.config.extraction.default_format])]
        pagination = effective_diagnosis.get("pagination") or {}
        schema_source = schema_config or rules.get("schema") or {}
        extraction_config: dict[str, Any] = {
            "auth_mode": target.auth_mode,
            "diagnosis": effective_diagnosis,
            "max_pages": max_pages,
            "max_records": max_records,
            "delay_s": delay,
            "next_page_selector": schema_source.get("next_page_selector")
            or pagination.get("pagination_selector")
            or "",
            "load_more_selector": rules.get("load_more_selector", ""),
            "infinite_scroll": rules.get(
                "infinite_scroll", pagination.get("infinite_scroll", False)
            ),
            "endpoints": rules.get("api_endpoints", []),
            "api_params": rules.get("api_params", {}),
            "api_page_param": rules.get("api_page_param", self.config.extraction.page_param),
        }
        summary["extraction_config"].update(
            {
                "max_pages": max_pages,
                "max_records": max_records,
                "delay_s": delay,
                "formats": formats,
                "schema_origin": (schema_origin or "archivo") if schema_config else "automatico",
            }
        )

        # ---- M3: adquisición ----
        batch = manual_batch or self._acquire(target, effective_diagnosis, extraction_config, strategy)
        strategy_used = batch.method
        summary["extraction_config"]["strategy"] = strategy_used
        summary["pages"] = batch.to_dict()["pages"]
        summary["coverage"] = {
            "requested_max_pages": max_pages,
            "captured_pages": len(batch.pages),
            "record_limit_reached": False,
            "warnings": [],
        }
        if strategy_used == "selenium":
            coverage = summary["coverage"]
            coverage["mechanism"] = batch.metadata.get("mechanism", "desconocido")
            coverage["stopped_reason"] = batch.metadata.get("stopped_reason", "desconocido")
            if coverage["stopped_reason"] == "límite de capturas alcanzado":
                coverage["warnings"].append(
                    "Se alcanzó el límite de capturas; puede existir más contenido."
                )
            if coverage["mechanism"] in ("scroll_infinito", "cargar_mas"):
                coverage["warnings"].append(
                    "Las capturas dinámicas pueden contener registros repetidos; "
                    "el límite de registros se aplica antes de eliminar duplicados."
                )
            if pagination.get("load_more_button") and not extraction_config["load_more_selector"]:
                coverage["warnings"].append(
                    "Se detectó un botón de carga, pero no se configuró un selector para usarlo."
                )
            coverage["warnings"].append(
                "Las capturas obtenidas no demuestran que el contenido esté completo."
            )
        if strategy_used == "selenium_manual":
            summary["coverage"]["stopped_reason"] = batch.metadata.get("stopped_reason", "sin captura adicional")
            summary["coverage"]["warnings"].append(
                ("El modo manual captura únicamente la página verificada; " if manual_next is None else
                 "El modo manual captura solo páginas comprobadas individualmente; ")
                + "esto no demuestra que se haya extraído toda la información."
            )
        summary["errors"].extend(batch.errors)

        if not batch.pages:
            summary["status"] = "error"
            summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
            logger.error("No se obtuvo ninguna página: %s", batch.errors)
            self._write_report(summary)
            return summary

        # ---- M4: esquema y selección ----
        builder = SchemaBuilder(
            effective_diagnosis,
            profile=profile,
            sample_page=batch.pages[0],
        )
        try:
            schema = builder.build(schema_source or None)
        except Exception as exc:
            summary["status"] = "error"
            summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
            if strategy_used == "selenium_manual":
                summary["errors"].append(
                    "Se verificó la página, pero no se encontraron registros extraíbles "
                    "o no fue posible inferir su estructura."
                )
            else:
                summary["errors"].append(f"No se pudo construir el esquema: {exc}")
            logger.exception("Error al construir esquema de extracción")
            self._write_report(summary)
            return summary
        summary["schema"] = schema.to_dict()
        summary["extraction_config"]["schema_origin"] = (
            (schema_origin or "archivo") if schema_config else schema.metadata.get("origin", "automatico")
        )

        raw_records = self._select_records(schema, batch, max_records)
        summary["raw_records"] = len(raw_records)
        if len(raw_records) >= max_records:
            summary["coverage"]["record_limit_reached"] = True
            summary["coverage"]["warnings"].append(
                "Se alcanzó el límite de registros; es posible que existan más datos."
            )
        if not raw_records:
            summary["status"] = "error"
            summary["errors"].append(
                "Se verificó la página, pero no se encontraron registros extraíbles."
                if strategy_used == "selenium_manual" else
                "No se seleccionaron registros; revise los selectores y el contenido obtenido."
            )
            summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
            self._write_report(summary)
            return summary

        # ---- M5: transformación, validación, deduplicación y linaje ----
        pipeline = self._build_pipeline(schema, rules, execution_id, target, strategy_used)
        try:
            processed = pipeline.run(raw_records)
        except Exception as exc:
            summary["status"] = "error"
            summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
            summary["errors"].append(f"Error en transformación: {exc}")
            logger.exception("Error en pipeline de transformación")
            self._write_report(summary)
            return summary
        quality = pipeline.last_report
        summary["processed_records"] = len(processed)
        summary["quality"] = quality
        summary["lineage_coverage"] = self._lineage_coverage(processed)

        # ---- M6: guardado separado raw / processed ----
        try:
            summary["outputs"], storage_errors = self._persist(
                target,
                raw_records,
                processed,
                formats,
                output_dir,
                execution_id,
            )
        except Exception as exc:
            summary["status"] = "error"
            summary["errors"].append(f"Error al almacenar datasets: {exc}")
            summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
            logger.exception("Error al almacenar datasets")
            self._write_report(summary)
            return summary

        summary["errors"].extend(storage_errors)
        if storage_errors and not summary["outputs"]:
            summary["status"] = "error"
            summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
            self._write_report(summary)
            return summary

        try:
            summary["changes"] = self.profiles.save_extraction(
                target.url, summary["schema"], raw_records,
                [page.data for page in batch.pages
                 if page.content_format in ("json", "api") and page.data is not None],
                execution_id,
                configured_endpoints=(
                    [rules["api_endpoints"]] if isinstance(rules.get("api_endpoints"), str)
                    else list(rules.get("api_endpoints") or [])
                ),
            )
        except (OSError, ValueError, TypeError) as exc:
            summary["errors"].append(f"No se pudo guardar el perfil de cambios: {exc}")
            logger.warning("No se pudo guardar el perfil de cambios: %s", exc)

        summary["status"] = "partial" if storage_errors or (strategy_used == "selenium_manual" and batch.errors) else "ok"
        summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
        self._write_report(summary)
        logger.info(
            "Extracción completada (%s): %d crudos → %d procesados",
            strategy_used,
            len(raw_records),
            len(processed),
        )
        return summary

    # ------------------------------------------------------------------
    # Etapas internas
    # ------------------------------------------------------------------

    def _acquire(
        self,
        target: AnalysisTarget,
        diagnosis: dict[str, Any],
        extraction_config: dict[str, Any],
        strategy: str | None,
    ) -> ExtractionBatch:
        """Ejecuta el motor recomendado con un respaldo prudente ante fallos."""
        extractor = ExtractorFactory.create_for_diagnosis(
            diagnosis,
            self.config,
            strategy=strategy,
            allow_private_network=target.allow_private_network,
        )
        automatic = not strategy or strategy.strip().lower() == "auto"
        fallback_reason = ""
        if not extractor.is_applicable(diagnosis) and automatic:
            fallback_reason = f"Motor '{extractor.method}' no aplicable; se usó requests."
            logger.warning(
                "Motor '%s' no aplicable; se usa requests como respaldo",
                extractor.method,
            )
            extractor = ExtractorFactory.create(
                "requests",
                self.config,
                allow_private_network=target.allow_private_network,
            )
        batch = extractor.extract(target.url, extraction_config)
        if fallback_reason:
            batch.errors.insert(0, fallback_reason)

        if (
            not batch.pages
            and extractor.method == "selenium"
            and automatic
            and not self._barrier_active(diagnosis)
            and not any(
                word in error.lower()
                for error in batch.errors
                for word in ("ssrf", "captcha", "bloquead", "403", "429", "401", "503")
            )
        ):
            logger.warning("Selenium falló; se intenta respaldo con requests")
            previous_errors = list(batch.errors)
            batch = ExtractorFactory.create(
                "requests",
                self.config,
                allow_private_network=target.allow_private_network,
            ).extract(target.url, extraction_config)
            batch.errors.insert(
                0,
                "Selenium no produjo páginas; se intentó con requests. "
                + ("; ".join(previous_errors) if previous_errors else "Sin detalle adicional."),
            )
        return batch

    @staticmethod
    def _select_records(
        schema: ExtractionSchema,
        batch: ExtractionBatch,
        max_records: int,
    ) -> list[dict[str, Any]]:
        """Aplica el esquema a todas las páginas respetando el límite global."""
        records: list[dict[str, Any]] = []
        for page in batch.pages:
            remaining = max_records - len(records)
            if remaining <= 0:
                break
            source = page.data if page.content_format in ("json", "api", "csv", "xml") else page.html
            page_records = schema.apply(
                source,
                content_format=page.content_format,
                base_url=page.final_url or page.url,
                page_url=page.url,
                method=page.method,
                acquired_at=page.acquired_at,
                page_number=page.page_number,
            )
            records.extend(page_records[:remaining])
        return records

    @staticmethod
    def _build_pipeline(
        schema: ExtractionSchema,
        rules: dict[str, Any],
        execution_id: str,
        target: AnalysisTarget,
        method: str,
    ) -> TransformationPipeline:
        """Configura el pipeline según el esquema y las reglas del objetivo."""
        field_transforms = {f.name: f.transform for f in schema.fields if f.transform}
        field_types = dict(rules.get("field_types") or {})
        required = list(rules.get("required_fields") or []) or [
            f.name for f in schema.fields if f.required
        ]
        key_fields = list(rules.get("deduplicate_by") or [])
        return (
            TransformationPipeline()
            .add(DataCleaner())
            .add(Normalizer(field_types, field_transforms))
            .add(Validator(required))
            .add(Deduplicator(key_fields))
            .add(
                LineageEnricher(
                    execution_id=execution_id,
                    default_source_url=target.safe_url,
                    default_method=method,
                )
            )
        )

    def _persist(
        self,
        target: AnalysisTarget,
        raw_records: list[dict[str, Any]],
        processed: list[dict[str, Any]],
        formats: list[str],
        output_dir: Path | str | None,
        execution_id: str,
    ) -> tuple[dict[str, Any], list[str]]:
        """Guarda cada formato de forma independiente y conserva salidas parciales."""
        data_dir = Path(output_dir) if output_dir else Path(self.config.paths.data_dir)  # type: ignore[arg-type]
        data_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_id = execution_id[:8]
        domain = re.sub(r"[^a-zA-Z0-9_-]+", "_", target.domain).strip("_") or "sitio"
        outputs: dict[str, Any] = {}
        errors: list[str] = []

        for fmt in formats:
            try:
                storage = StorageFactory.create(fmt, data_dir)  # type: ignore[arg-type]
                for stage, records in (("raw", raw_records), ("processed", processed)):
                    name = f"{domain}_{stage}_{stamp}_{safe_id}"
                    path = storage.save(
                        records, name, table=name, database=f"{domain}_datasets"
                    )
                    outputs.setdefault(fmt, {})[stage] = str(path)
            except Exception as exc:
                # No registrar la excepción: puede contener contraseñas o URI.
                hint = {
                    "postgresql": " Revise EXTRACTION_POSTGRES_URI y la conexión.",
                    "mongodb": " Revise EXTRACTION_MONGODB_URI y la conexión.",
                }.get(fmt, " Revise el formato y los permisos de escritura.")
                errors.append(f"No se pudo guardar {fmt} ({type(exc).__name__}).{hint}")
                logger.warning("No se pudo guardar el formato %s (%s)", fmt, type(exc).__name__)
        return outputs, errors

    def _write_report(self, summary: dict[str, Any]) -> None:
        """Persiste el reporte y añade las rutas al resumen devuelto."""
        paths = self.reporter.generate(summary)
        summary["report_files"] = {key: str(value) for key, value in paths.items()}

    # ------------------------------------------------------------------
    # Seguridad y resumen
    # ------------------------------------------------------------------

    @staticmethod
    def _barrier_active(diagnosis: dict[str, Any]) -> bool:
        protection = diagnosis.get("protection") or {}
        viability = diagnosis.get("viability") or {}
        return bool(
            protection.get("blocking_barrier")
            or viability.get("blocked_by_barrier")
            or str(diagnosis.get("access_status", "")).startswith("restringido")
            or diagnosis.get("access_status") in ("inaccesible", "bloqueado_ssrf")
        )

    def _block_reason(
        self,
        target: AnalysisTarget,
        diagnosis: dict[str, Any],
    ) -> str | None:
        """Devuelve el motivo ético/robots por el que no debe extraerse."""
        protection = diagnosis.get("protection") or {}
        if protection.get("auth_required"):
            return "Extracción detenida: el sitio requiere autenticación autorizada."
        if self._barrier_active(diagnosis):
            restrictions = protection.get("restrictions") or []
            detail = "; ".join(str(r) for r in restrictions) if restrictions else ""
            return (
                "Extracción detenida: el diagnóstico detectó una barrera "
                "(CAPTCHA, autenticación, HTTP 401/403/429/503 o SSRF). "
                f"{detail}".strip()
            )
        if (
            target.respect_robots_txt
            and protection.get("robots_crawl_allowed") is False
        ):
            return (
                "Extracción detenida: robots.txt no autoriza la ruta solicitada "
                "y el objetivo está configurado para respetarlo."
            )
        return None

    @staticmethod
    def _base_summary(
        target: AnalysisTarget,
        *,
        execution_id: str,
        started_at: str,
        diagnosis: dict[str, Any],
    ) -> dict[str, Any]:
        """Inicializa el resumen con información sanitizada."""
        return {
            "execution_id": execution_id,
            "url": sanitize_url(target.url),
            "domain": target.domain,
            "label": target.label,
            "started_at": started_at,
            "finished_at": "",
            "status": "running",
            "diagnosis_timestamp": diagnosis.get("diagnosis_timestamp"),
            "access_status": diagnosis.get("access_status"),
            "extraction_config": {
                "strategy": "auto",
                "max_pages": None,
                "max_records": None,
                "delay_s": None,
                "formats": [],
                "schema_origin": "",
            },
            "schema": {},
            "pages": [],
            "raw_records": 0,
            "processed_records": 0,
            "quality": {},
            "lineage_coverage": {},
            "outputs": {},
            "errors": list(diagnosis.get("errors") or []),
        }

    @staticmethod
    def _lineage_coverage(records: list[dict[str, Any]]) -> dict[str, int]:
        required = (
            "source_url",
            "acquisition_date",
            "extraction_method",
            "source_field",
            "validation_status",
            "applied_transformations",
            "execution_id",
        )
        with_lineage = sum(
            1
            for record in records
            if isinstance(record.get("_lineage"), dict)
            and all(key in record["_lineage"] for key in required)
        )
        return {
            "records_total": len(records),
            "records_with_lineage": with_lineage,
            "records_missing_lineage": len(records) - with_lineage,
        }
