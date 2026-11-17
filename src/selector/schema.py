"""
Módulo 4 — Selección de información (arquitectura preparada).

Define las estructuras de configuración de selección de campos,
selectores CSS/XPath, atributos y reglas personalizadas.
Se implementará funcionalmente en el Avance 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


SelectorType = Literal["css", "xpath", "regex", "json_path", "attribute"]


@dataclass
class FieldSelector:
    """Define cómo extraer un campo específico de una página."""

    name: str
    selector: str
    selector_type: SelectorType = "css"
    attribute: str = "text"  # "text", "href", "src", etc.
    multiple: bool = False
    required: bool = False
    transform: str = ""  # Nombre de transformación a aplicar


@dataclass
class ExtractionSchema:
    """
    Esquema de extracción para un sitio o sección.

    Define qué campos extraer y cómo encontrarlos.
    Se construye tras el diagnóstico del Avance 1 y se utiliza
    en el Avance 2 para guiar el motor de extracción.
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
        """Serializa el esquema a diccionario."""
        return {
            "name": self.name,
            "description": self.description,
            "base_selector": self.base_selector,
            "fields": [
                {
                    "name": f.name,
                    "selector": f.selector,
                    "selector_type": f.selector_type,
                    "attribute": f.attribute,
                    "multiple": f.multiple,
                    "required": f.required,
                }
                for f in self.fields
            ],
            "pagination_selector": self.pagination_selector,
            "next_page_selector": self.next_page_selector,
        }

