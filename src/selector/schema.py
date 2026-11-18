"""
Módulo 4 — Selección de información (funcional desde el Avance 2).

Define los esquemas declarativos que guían al motor de extracción: qué
campos obtener, con qué selector (CSS, XPath, regex, JSONPath o atributo)
y cómo combinar los resultados en registros.

Los esquemas se construyen automáticamente desde el perfil del Avance 1
o se cargan desde un archivo JSON definido por el usuario.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from src.monitor.logger import get_logger
from src.selector.engines import extract_field_values, select_containers

logger = get_logger("selector.schema")

SelectorType = Literal["css", "xpath", "regex", "json_path", "attribute"]
_VALID_SELECTOR_TYPES = frozenset(
    {"css", "xpath", "regex", "json_path", "attribute"}
)


@dataclass
class FieldSelector:
    """Define cómo extraer un campo específico de una página."""

    name: str
    selector: str
    selector_type: SelectorType = "css"
    attribute: str = "text"  # "text", "href", "src", etc.
    multiple: bool = False
    required: bool = False
    transform: str = ""  # Transformación declarada para el Módulo 5

    def to_dict(self) -> dict[str, Any]:
        """Serializa el selector a diccionario (incluye transform)."""
        return {
            "name": self.name,
            "selector": self.selector,
            "selector_type": self.selector_type,
            "attribute": self.attribute,
            "multiple": self.multiple,
            "required": self.required,
            "transform": self.transform,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FieldSelector":
        """Construye un selector desde un diccionario JSON."""
        return cls(
            name=str(data.get("name", "")).strip(),
            selector=str(data.get("selector", "")).strip(),
            selector_type=str(data.get("selector_type", "css")).lower(),  # type: ignore[arg-type]
            attribute=str(data.get("attribute", "text") or "text"),
            multiple=bool(data.get("multiple", False)),
            required=bool(data.get("required", False)),
            transform=str(data.get("transform", "") or ""),
        )


@dataclass
class ExtractionSchema:
    """
    Esquema de extracción para un sitio o sección.

    Se construye tras el diagnóstico del Avance 1 y guía la extracción:
    define el contenedor de registros, los campos y la paginación.
    """

    name: str
    description: str = ""
    base_selector: str = ""  # Selector del contenedor principal
    fields: list[FieldSelector] = field(default_factory=list)
    pagination_selector: str = ""
    next_page_selector: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_field(self, field_selector: FieldSelector) -> None:
        """Agrega un selector de campo al esquema."""
        self.fields.append(field_selector)

    def to_dict(self) -> dict[str, Any]:
        """Serializa el esquema a diccionario (listo para guardar en JSON)."""
        return {
            "name": self.name,
            "description": self.description,
            "base_selector": self.base_selector,
            "fields": [f.to_dict() for f in self.fields],
            "pagination_selector": self.pagination_selector,
            "next_page_selector": self.next_page_selector,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExtractionSchema":
        """Construye un esquema desde un diccionario (típicamente JSON)."""
        fields_data = data.get("fields") or []
        return cls(
            name=str(data.get("name", "esquema_usuario")),
            description=str(data.get("description", "") or ""),
            base_selector=str(data.get("base_selector", "") or ""),
            fields=[
                FieldSelector.from_dict(f) for f in fields_data if isinstance(f, dict)
            ],
            pagination_selector=str(data.get("pagination_selector", "") or ""),
            next_page_selector=str(data.get("next_page_selector", "") or ""),
            metadata=dict(data.get("metadata") or {}),
        )

    def validate(self) -> list[str]:
        """Devuelve una lista de problemas de configuración del esquema."""
        errors: list[str] = []
        if not self.name.strip():
            errors.append("El esquema debe tener un nombre.")
        if not self.fields:
            errors.append("El esquema debe definir al menos un campo.")
        seen: set[str] = set()
        for f in self.fields:
            if not f.name.strip():
                errors.append("Todos los campos deben tener nombre.")
            elif f.name in seen:
                errors.append(f"Campo duplicado: {f.name}")
            else:
                seen.add(f.name)
            if not f.selector.strip():
                errors.append(f"El campo '{f.name}' no tiene selector.")
            if f.selector_type not in _VALID_SELECTOR_TYPES:
                errors.append(
                    f"Tipo de selector no válido en '{f.name}': {f.selector_type}"
                )
        return errors

    # ------------------------------------------------------------------
    # Aplicación del esquema
    # ------------------------------------------------------------------

    def apply(
        self,
        source: Any,
        *,
        content_format: str = "html",
        base_url: str = "",
        page_url: str = "",
        method: str = "requests",
        acquired_at: str = "",
        page_number: int = 1,
    ) -> list[dict[str, Any]]:
        """
        Aplica el esquema a una página y devuelve registros crudos.

        Cada registro incluye metadatos internos (`_source` y `_field_meta`)
        que el Módulo 5 utiliza para construir el linaje auditable.
        """
        errors = self.validate()
        if errors:
            raise ValueError("; ".join(errors))

        containers = select_containers(
            source, self.base_selector, content_format=content_format
        )
        records: list[dict[str, Any]] = []

        for container in containers:
            record: dict[str, Any] = {}
            field_meta: dict[str, Any] = {}

            for f in self.fields:
                values = extract_field_values(
                    container,
                    f,
                    base_url=base_url,
                    content_format=content_format,
                )
                value: Any
                if not values:
                    value = None
                elif f.multiple:
                    value = list(dict.fromkeys(values))
                else:
                    value = values[0]
                record[f.name] = value
                field_meta[f.name] = {
                    "selector": f.selector,
                    "selector_type": f.selector_type,
                    "attribute": f.attribute,
                    "transform": f.transform,
                    "required": f.required,
                }

            # Se conserva el registro aunque tenga campos vacíos: la validación
            # y el reporte de calidad deciden si se descarta o se marca.
            if not any(v not in (None, "", []) for v in record.values()):
                continue

            record["_source"] = {
                "url": page_url or base_url,
                "page_number": page_number,
                "method": method,
                "acquired_at": acquired_at,
            }
            record["_field_meta"] = field_meta
            records.append(record)

        logger.debug(
            "Esquema '%s' produjo %d registros (%d contenedores)",
            self.name,
            len(records),
            len(containers),
        )
        return records
