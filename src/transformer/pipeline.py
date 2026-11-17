"""
Módulo 5 — Transformación y calidad de datos (arquitectura preparada).

Define las interfaces base para limpieza, normalización, validación,
deduplicación y estandarización de datos extraídos.
Se implementará funcionalmente en el Avance 2/3.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTransformer(ABC):
    """Interfaz base para transformadores de datos."""

    @abstractmethod
    def transform(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Aplica la transformación a una lista de registros."""
        raise NotImplementedError


class DataCleaner(BaseTransformer):
    """
    Limpia datos extraídos: elimina espacios, caracteres inválidos,
    valores nulos y formatos incorrectos.
    Implementación básica preparada — se expandirá en Avance 2.
    """

    def transform(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Limpieza básica de cadenas de texto."""
        cleaned = []
        for record in data:
            clean_record = {}
            for key, value in record.items():
                if isinstance(value, str):
                    clean_record[key] = value.strip()
                else:
                    clean_record[key] = value
            cleaned.append(clean_record)
        return cleaned


class Deduplicator(BaseTransformer):
    """
    Elimina registros duplicados basándose en campos clave.
    Implementación preparada — se completará en Avance 2.
    """

    def __init__(self, key_fields: list[str] | None = None) -> None:
        self.key_fields = key_fields or []

    def transform(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Elimina duplicados basándose en los campos clave configurados."""
        if not self.key_fields:
            return data

        seen: set[tuple] = set()
        unique = []
        for record in data:
            key = tuple(record.get(f, "") for f in self.key_fields)
            if key not in seen:
                seen.add(key)
                unique.append(record)
        return unique


class TransformationPipeline:
    """
    Pipeline de transformaciones encadenadas.
    Permite combinar múltiples transformadores en secuencia.
    """

    def __init__(self) -> None:
        self._transformers: list[BaseTransformer] = []

    def add(self, transformer: BaseTransformer) -> "TransformationPipeline":
        """Agrega un transformador al pipeline."""
        self._transformers.append(transformer)
        return self

    def run(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Ejecuta todos los transformadores en orden."""
        result = data
        for transformer in self._transformers:
            result = transformer.transform(result)
        return result

