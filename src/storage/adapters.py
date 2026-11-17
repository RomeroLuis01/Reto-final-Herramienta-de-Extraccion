"""
Módulo 6 — Almacenamiento y exportación (arquitectura preparada).

Define las interfaces base y el adaptador de almacenamiento.
Soportará: CSV, Excel, JSON, SQLite, PostgreSQL, MongoDB.
Se implementará funcionalmente en el Avance 2/3.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

StorageFormat = Literal["csv", "excel", "json", "sqlite", "postgresql", "mongodb"]


class BaseStorage(ABC):
    """Interfaz base para todos los adaptadores de almacenamiento."""

    @abstractmethod
    def save(
        self,
        data: list[dict[str, Any]],
        name: str,
        **kwargs: Any,
    ) -> Path | str:
        """
        Persiste los datos en el formato correspondiente.

        Args:
            data: Lista de registros a guardar.
            name: Nombre base del recurso de destino.
            **kwargs: Parámetros específicos del adaptador.

        Returns:
            Ruta o identificador del recurso guardado.
        """
        raise NotImplementedError


class JsonStorage(BaseStorage):
    """
    Almacenamiento en JSON.
    Implementación básica funcional para diagnósticos y perfiles.
    """

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        data: list[dict[str, Any]],
        name: str,
        **kwargs: Any,
    ) -> Path:
        path = self.output_dir / f"{name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        return path


class StorageFactory:
    """
    Fábrica de adaptadores de almacenamiento (patrón Factory).
    Se expandirá con todos los adaptadores en el Avance 2.
    """

    _registry: dict[str, type[BaseStorage]] = {
        "json": JsonStorage,
    }

    @classmethod
    def create(
        cls,
        format: StorageFormat,  # noqa: A002
        output_dir: Path,
    ) -> BaseStorage:
        """
        Crea el adaptador de almacenamiento para el formato dado.

        Args:
            format: Formato de almacenamiento.
            output_dir: Directorio de salida.

        Returns:
            Instancia del adaptador.

        Raises:
            NotImplementedError: Si el formato no está implementado aún.
        """
        if format not in cls._registry:
            raise NotImplementedError(
                f"Formato '{format}' no implementado. "
                f"Disponibles: {list(cls._registry.keys())}. "
                "El soporte completo se implementará en el Avance 2."
            )
        return cls._registry[format](output_dir)

