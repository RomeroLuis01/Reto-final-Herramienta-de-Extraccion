"""
Módulo 7 — Reportes de extracción (Avance 2).

Serializa el resumen de cada ejecución en JSON sanitizado y en un informe
de texto legible: estrategia seleccionada, esquema usado, registros crudos y
procesados, calidad, linaje, archivos generados y cualquier incidencia ética.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.monitor.logger import get_logger
from src.utils.security import sanitize_for_persistence

logger = get_logger("monitor.extraction_reporter")


class ExtractionReporter:
    """Genera reportes de ejecución de extracción en JSON y texto."""

    def __init__(self, reports_dir: Path) -> None:
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, summary: dict[str, Any]) -> dict[str, Path]:
        """
        Persiste el reporte de extracción.

        Returns:
            Diccionario con las rutas ``json`` y ``txt`` generadas.
        """
        timestamp = str(
            summary.get("finished_at") or datetime.now().isoformat(timespec="seconds")
        )
        stamp = timestamp.replace(":", "-").replace(".", "-")
        domain = str(summary.get("domain") or "extraccion")
        domain = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in domain)

        payload = sanitize_for_persistence(summary)
        json_path = self.reports_dir / f"{domain}_extraccion_{stamp}.json"
        txt_path = self.reports_dir / f"{domain}_extraccion_{stamp}.txt"

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        txt_path.write_text(self.to_text(payload), encoding="utf-8")

        logger.info("Reporte de extracción generado: %s", json_path)
        return {"json": json_path, "txt": txt_path}

    # ------------------------------------------------------------------
    # Render de texto
    # ------------------------------------------------------------------

    @staticmethod
    def to_text(summary: dict[str, Any]) -> str:
        """Convierte el resumen a un informe legible en texto plano."""
        lines: list[str] = []
        lines.append("=" * 70)
        lines.append("REPORTE DE EXTRACCIÓN — HERRAMIENTA INTELIGENTE")
        lines.append("=" * 70)
        lines.append(f"URL objetivo      : {summary.get('url', '-')}")
        lines.append(f"Dominio           : {summary.get('domain', '-')}")
        lines.append(f"Etiqueta          : {summary.get('label', '-') or '-'}")
        lines.append(f"Estado            : {summary.get('status', '-')}")
        lines.append(f"ID de ejecución   : {summary.get('execution_id', '-')}")
        lines.append(f"Inicio            : {summary.get('started_at', '-')}")
        lines.append(f"Fin               : {summary.get('finished_at', '-')}")
        lines.append("")

        lines.append("--- Configuración de extracción ---")
        config = summary.get("extraction_config") or {}
        for key in (
            "strategy",
            "max_pages",
            "max_records",
            "delay_s",
            "formats",
            "schema_origin",
        ):
            lines.append(f"{key:<18}: {config.get(key, '-')}")
        schema_name = (summary.get("schema") or {}).get("name", "-")
        lines.append(f"{'schema':<18}: {schema_name}")
        lines.append("")

        lines.append("--- Páginas adquiridas ---")
        pages = summary.get("pages") or []
        for page in pages:
            lines.append(
                f"  [{page.get('page_number')}] {page.get('method')} "
                f"{page.get('content_format')} {page.get('url')} "
                f"(HTTP {page.get('status_code', '-')})"
            )
        if not pages:
            lines.append("  (ninguna)")
        lines.append("")

        coverage = summary.get("coverage") or {}
        if coverage:
            lines.append("--- Alcance de la extracción ---")
            lines.append(f"Páginas máximas solicitadas: {coverage.get('requested_max_pages', '-')}")
            lines.append(f"Páginas capturadas         : {coverage.get('captured_pages', 0)}")
            if coverage.get("mechanism"):
                lines.append(f"Mecanismo                 : {coverage['mechanism']}")
            if coverage.get("stopped_reason"):
                lines.append(f"Motivo de parada          : {coverage['stopped_reason']}")
            for warning in coverage.get("warnings") or []:
                lines.append(f"  Aviso: {warning}")
            lines.append("")

        lines.append("--- Resultados ---")
        lines.append(f"Registros crudos     : {summary.get('raw_records', 0)}")
        lines.append(f"Registros procesados : {summary.get('processed_records', 0)}")
        quality = summary.get("quality") or {}
        lines.append(f"Válidos              : {quality.get('valid_records', 0)}")
        lines.append(f"Inválidos/incompletos: {quality.get('invalid_records', 0)}")
        lines.append(f"Duplicados eliminados: {quality.get('duplicates_removed', 0)}")
        transformations = quality.get("applied_transformations") or []
        lines.append(
            "Transformaciones     : "
            + (", ".join(transformations) if transformations else "ninguna")
        )
        lines.append("")

        lines.append("--- Linaje por registro ---")
        for key in (
            "source_url",
            "acquisition_date",
            "extraction_method",
            "source_field",
            "validation_status",
            "applied_transformations",
            "execution_id",
        ):
            lines.append(f"  - {key}")
        cobertura = summary.get("lineage_coverage") or {}
        lines.append(
            f"Cobertura: {cobertura.get('records_with_lineage', 0)}/"
            f"{cobertura.get('records_total', 0)} registros"
        )
        lines.append("")

        lines.append("--- Cambios frente a la extracción anterior ---")
        changes = summary.get("changes") or {}
        if not changes.get("baseline_available"):
            lines.append("  Sin extracción anterior para comparar.")
        else:
            for key, value in changes.items():
                if key != "baseline_available":
                    lines.append(f"  {key}: {json.dumps(value, ensure_ascii=False)}")
        lines.append("")

        lines.append("--- Archivos generados ---")
        outputs = summary.get("outputs") or {}
        for format_name, paths in outputs.items():
            lines.append(f"  {format_name}:")
            if isinstance(paths, dict):
                for stage, path in paths.items():
                    lines.append(f"    {stage}: {path}")
            else:
                lines.append(f"    {paths}")
        reportes = summary.get("report_files") or {}
        for name, path in reportes.items():
            lines.append(f"  reporte.{name}: {path}")
        lines.append("")

        lines.append("--- Errores / incidencias ---")
        errors = summary.get("errors") or []
        if errors:
            for error in errors:
                lines.append(f"  - {error}")
        else:
            lines.append("  (ninguna)")
        lines.append("=" * 70)
        return "\n".join(lines) + "\n"
