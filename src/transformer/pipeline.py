"""
Módulo 5 — Transformación, calidad y linaje de datos (Avance 2).

El pipeline encadena limpieza, normalización, validación, deduplicación y
enriquecimiento de linaje. Cada registro procesado conserva siete atributos
auditorables en `_lineage`: URL origen, fecha de adquisición, método de
extracción, campo de procedencia, estado de validación, transformaciones
aplicadas e identificador de ejecución.
"""

from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any

from dateutil import parser as date_parser

from src.monitor.logger import get_logger

logger = get_logger("transformer.pipeline")


class BaseTransformer(ABC):
    """Interfaz base para transformadores de datos."""

    @abstractmethod
    def transform(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Aplica la transformación a una lista de registros."""
        raise NotImplementedError


def _clean_value(value: Any) -> Any:
    """Limpia cadenas sin alterar otros tipos."""
    if not isinstance(value, str):
        return value
    cleaned = value.replace("\xa0", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = "".join(ch for ch in cleaned if ord(ch) >= 32 or ch in "\t\n")
    return cleaned or None


class DataCleaner(BaseTransformer):
    """Elimina espacios, caracteres inválidos y cadenas vacías."""

    def transform(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Limpieza de cadenas en todas las claves del registro."""
        cleaned_records: list[dict[str, Any]] = []
        for record in data:
            cleaned: dict[str, Any] = {}
            for key, value in record.items():
                if key.startswith("_"):
                    cleaned[key] = value
                elif isinstance(value, list):
                    cleaned[key] = [v for v in (_clean_value(i) for i in value) if v is not None]
                else:
                    cleaned[key] = _clean_value(value)
            cleaned["_transformations"] = list(record.get("_transformations", [])) + ["clean"]
            cleaned_records.append(cleaned)
        return cleaned_records


class Normalizer(BaseTransformer):
    """Normaliza tipos y formatos según la configuración del esquema."""

    def __init__(
        self,
        field_types: dict[str, str] | None = None,
        field_transforms: dict[str, str] | None = None,
    ) -> None:
        self.field_types = field_types or {}
        self.field_transforms = field_transforms or {}

    def transform(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Aplica conversiones declaradas campo por campo."""
        normalized: list[dict[str, Any]] = []
        for record in data:
            result = dict(record)
            applied = list(record.get("_transformations", []))
            for field_name, transform in self.field_transforms.items():
                if field_name not in result or result[field_name] is None:
                    continue
                new_value, ok = self._apply(result[field_name], transform)
                if ok:
                    result[field_name] = new_value
                    applied.append(f"normalize:{field_name}:{transform}")
                else:
                    result["_validation_status"] = "error_tipo"
                    result.setdefault("_validation_errors", []).append(
                        f"No se pudo aplicar '{transform}' a '{field_name}'"
                    )
            for field_name, target_type in self.field_types.items():
                if field_name not in result or result[field_name] is None:
                    continue
                if field_name in self.field_transforms:
                    continue
                new_value, ok = self._apply(result[field_name], target_type)
                if ok:
                    result[field_name] = new_value
                    applied.append(f"normalize:{field_name}:{target_type}")
                else:
                    result["_validation_status"] = "error_tipo"
                    result.setdefault("_validation_errors", []).append(
                        f"No se pudo convertir '{field_name}' a {target_type}"
                    )
            result["_transformations"] = applied
            normalized.append(result)
        return normalized

    @staticmethod
    def _apply(value: Any, transform: str) -> tuple[Any, bool]:
        name = transform.strip().lower()
        try:
            if name in ("strip", "text", "string", "str"):
                return str(value).strip(), True
            if name == "upper":
                return str(value).upper(), True
            if name == "lower":
                return str(value).lower(), True
            if name == "title":
                return str(value).title(), True
            if name in ("number", "float", "decimal"):
                return Normalizer._to_number(value), True
            if name in ("integer", "int"):
                return int(Normalizer._to_number(value)), True
            if name == "date":
                return Normalizer._to_date(value), True
            if name in ("boolean", "bool"):
                return Normalizer._to_bool(value), True
            if name == "url":
                text = str(value).strip()
                if text and not text.startswith(("http://", "https://")):
                    return f"https://{text.lstrip('/')}", True
                return text, True
        except (ValueError, TypeError, OverflowError):
            return value, False
        return value, True  # transform desconocido: se conserva el valor

    @staticmethod
    def _to_number(value: Any) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        text = re.sub(r"[^\d,.\-]", "", str(value))
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif "," in text:
            groups = text.split(",")
            if len(groups) == 2 and len(groups[1]) in (1, 2):
                text = text.replace(",", ".")
            else:
                text = text.replace(",", "")
        return float(text)

    @staticmethod
    def _to_date(value: Any) -> str:
        if isinstance(value, (datetime, date)):
            return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
        try:
            return date_parser.parse(str(value)).date().isoformat()
        except (ValueError, TypeError, OverflowError):
            return datetime.fromisoformat(str(value)[:10]).date().isoformat()

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "1", "sí", "si", "yes", "y"):
            return True
        if text in ("false", "0", "no", "n"):
            return False
        raise ValueError(f"Valor booleano no reconocido: {value}")


class Validator(BaseTransformer):
    """Marca registros incompletos según los campos requeridos."""

    def __init__(
        self,
        required_fields: list[str] | None = None,
        *,
        drop_invalid: bool = False,
    ) -> None:
        self.required_fields = required_fields or []
        self.drop_invalid = drop_invalid

    def transform(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Valida campos requeridos y conserva el motivo del rechazo."""
        validated: list[dict[str, Any]] = []
        for record in data:
            result = dict(record)
            missing = [
                name
                for name in self.required_fields
                if result.get(name) in (None, "", [])
            ]
            errors = list(result.get("_validation_errors", []))
            if missing:
                errors.append(f"Campos requeridos ausentes: {', '.join(missing)}")
            if result.get("_validation_status") == "error_tipo":
                status = "error_tipo"
            elif missing:
                status = "incompleto"
            else:
                status = "valido"
            result["_validation_status"] = status
            result["_validation_errors"] = errors
            result["_transformations"] = list(result.get("_transformations", [])) + ["validate"]
            if not (self.drop_invalid and status != "valido"):
                validated.append(result)
        return validated


class Deduplicator(BaseTransformer):
    """Elimina registros duplicados basándose en campos clave."""

    def __init__(self, key_fields: list[str] | None = None) -> None:
        self.key_fields = key_fields or []

    def transform(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Elimina duplicados conservando el primero de cada clave."""
        seen: set[tuple[Any, ...]] = set()
        unique: list[dict[str, Any]] = []
        for record in data:
            values = (
                tuple(str(record.get(f, "")) for f in self.key_fields)
                if self.key_fields
                else tuple(
                    str(v) for k, v in record.items() if not k.startswith("_")
                )
            )
            if values in seen:
                continue
            seen.add(values)
            result = dict(record)
            result["_transformations"] = list(record.get("_transformations", [])) + [
                "dedupe"
            ]
            unique.append(result)
        return unique


class LineageEnricher(BaseTransformer):
    """Adjunta los 7 atributos de linaje exigidos por el punto 10.C del PDF."""

    _LINEAGE_KEYS = (
        "source_url",
        "acquisition_date",
        "extraction_method",
        "source_field",
        "validation_status",
        "applied_transformations",
        "execution_id",
    )

    def __init__(
        self,
        *,
        execution_id: str | None = None,
        default_source_url: str = "",
        default_method: str = "requests",
    ) -> None:
        self.execution_id = execution_id or str(uuid.uuid4())
        self.default_source_url = default_source_url
        self.default_method = default_method

    def transform(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Construye `_lineage` por registro y limpia las claves internas."""
        enriched: list[dict[str, Any]] = []
        for record in data:
            source = record.get("_source") or {}
            field_meta = record.get("_field_meta") or {}
            result = {
                k: v for k, v in record.items() if not k.startswith("_")
            }
            result["_lineage"] = {
                "source_url": source.get("url") or self.default_source_url,
                "acquisition_date": source.get("acquired_at")
                or datetime.now().isoformat(timespec="seconds"),
                "extraction_method": source.get("method") or self.default_method,
                "source_field": ", ".join(field_meta.keys()) or "*",
                "validation_status": record.get("_validation_status", "valido"),
                "applied_transformations": list(
                    dict.fromkeys(record.get("_transformations", []))
                ),
                "execution_id": self.execution_id,
            }
            enriched.append(result)
        return enriched


class TransformationPipeline:
    """Pipeline de transformaciones encadenadas con reporte de calidad."""

    def __init__(self) -> None:
        self._transformers: list[BaseTransformer] = []
        self.last_report: dict[str, Any] = {}

    def add(self, transformer: BaseTransformer) -> "TransformationPipeline":
        """Agrega un transformador al pipeline."""
        self._transformers.append(transformer)
        return self

    def run(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Ejecuta todos los transformadores en orden y registra métricas."""
        result = data
        counts = [len(result)]
        for transformer in self._transformers:
            result = transformer.transform(result)
            counts.append(len(result))

        status_counts: dict[str, int] = {}
        transformations: set[str] = set()
        for record in result:
            lineage = record.get("_lineage") or {}
            status = str(lineage.get("validation_status", "desconocido"))
            status_counts[status] = status_counts.get(status, 0) + 1
            transformations.update(lineage.get("applied_transformations", []))

        business_keys = {
            key
            for record in result
            for key in record
            if not key.startswith("_")
        }
        completeness: dict[str, float] = {}
        if result and business_keys:
            for key in sorted(business_keys):
                filled = sum(1 for r in result if r.get(key) not in (None, "", []))
                completeness[key] = round(filled / len(result) * 100, 1)

        self.last_report = {
            "records_input": counts[0],
            "records_output": counts[-1],
            "counts_by_stage": {
                type(t).__name__: counts[i + 1] for i, t in enumerate(self._transformers)
            },
            "duplicates_removed": max(0, counts[-3] - counts[-1])
            if len(counts) >= 3
            else 0,
            "validation_status": status_counts,
            "valid_records": status_counts.get("valido", 0),
            "invalid_records": sum(
                count for status, count in status_counts.items() if status != "valido"
            ),
            "applied_transformations": sorted(transformations),
            "field_completeness_pct": completeness,
            "lineage_keys": list(LineageEnricher._LINEAGE_KEYS),
        }
        logger.info(
            "Pipeline completado: %d registros entrada → %d salida (%d inválidos)",
            counts[0],
            counts[-1],
            self.last_report["invalid_records"],
        )
        return result
