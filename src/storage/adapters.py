"""
Módulo 6 — Almacenamiento y exportación (funcional desde el Avance 2).

Persiste dos conjuntos separados:
- *raw*: registros tal como los produjo el Módulo 4.
- *processed*: registros transformados, con metadatos de linaje por fila.

Formatos operativos: JSON, CSV, Excel, SQLite, PostgreSQL y MongoDB.
Los servicios externos requieren dependencias y configuración opcionales.
"""

from __future__ import annotations

import csv
import json
import hashlib
import os
import re
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

from src.monitor.logger import get_logger

logger = get_logger("storage.adapters")

StorageFormat = Literal["csv", "excel", "json", "sqlite", "postgresql", "mongodb"]


def _scalarize(value: Any) -> Any:
    """Convierte valores anidados a texto compatible con tablas."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _flatten_record(record: dict[str, Any]) -> dict[str, Any]:
    """Aplana los valores de un registro para CSV/Excel/SQLite."""
    return {key: _scalarize(value) for key, value in record.items()}


def _all_fieldnames(data: list[dict[str, Any]]) -> list[str]:
    """Devuelve la unión ordenada de todas las columnas presentes."""
    fields: list[str] = []
    for record in data:
        for key in record:
            if key not in fields:
                fields.append(key)
    return fields or ["_empty"]


class BaseStorage(ABC):
    """Interfaz base para todos los adaptadores de almacenamiento."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

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
    """Almacenamiento en JSON (diagnósticos, perfiles y datasets)."""

    def save(
        self,
        data: list[dict[str, Any]],
        name: str,
        **kwargs: Any,
    ) -> Path:
        path = self.output_dir / f"{name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        logger.info("JSON guardado: %s (%d registros)", path, len(data))
        return path


class CsvStorage(BaseStorage):
    """Almacenamiento en CSV delimitado por comas con UTF-8 BOM para Excel."""

    def save(
        self,
        data: list[dict[str, Any]],
        name: str,
        **kwargs: Any,
    ) -> Path:
        path = self.output_dir / f"{name}.csv"
        fieldnames: list[str] = list(kwargs.get("fieldnames") or _all_fieldnames(data))
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for record in data:
                writer.writerow(_flatten_record(record))
        logger.info("CSV guardado: %s (%d registros)", path, len(data))
        return path


class ExcelStorage(BaseStorage):
    """Almacenamiento en libro de Excel (.xlsx) mediante pandas/openpyxl."""

    def save(
        self,
        data: list[dict[str, Any]],
        name: str,
        **kwargs: Any,
    ) -> Path:
        import pandas as pd

        path = self.output_dir / f"{name}.xlsx"
        rows = [_flatten_record(record) for record in data]
        fieldnames = list(kwargs.get("fieldnames") or _all_fieldnames(data))
        dataframe = pd.DataFrame(rows, columns=fieldnames)
        dataframe.to_excel(path, index=False, engine="openpyxl")
        logger.info("Excel guardado: %s (%d registros)", path, len(data))
        return path


class SqliteStorage(BaseStorage):
    """Almacenamiento en base SQLite local, una tabla por dataset."""

    def save(
        self,
        data: list[dict[str, Any]],
        name: str,
        **kwargs: Any,
    ) -> Path:
        path = self.output_dir / f"{kwargs.get('database', 'datasets')}.sqlite"
        table = str(kwargs.get("table") or name).replace("-", "_").replace(" ", "_")
        table = "".join(ch for ch in table if ch.isalnum() or ch == "_")
        if not table or table[0].isdigit():
            table = f"t_{table}"

        fieldnames = list(kwargs.get("fieldnames") or _all_fieldnames(data))
        quoted = ", ".join(f'"{col}" TEXT' for col in fieldnames)
        columns = ", ".join(f'"{col}"' for col in fieldnames)
        placeholders = ", ".join("?" for _ in fieldnames)

        with sqlite3.connect(path) as connection:
            connection.execute(
                f"CREATE TABLE IF NOT EXISTS {table} ({quoted})"  # noqa: S608
            )
            rows = [
                tuple(_scalarize(record.get(col)) for col in fieldnames)
                for record in data
            ]
            if rows:
                connection.executemany(
                    f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",  # noqa: S608
                    rows,
                )
            connection.commit()
        logger.info("SQLite guardado: %s tabla=%s (%d registros)", path, table, len(data))
        return path


def _external_name(name: str, limit: int = 63) -> str:
    """Crea identificadores seguros y conserva unicidad al truncar."""
    value = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if not value or value[0].isdigit():
        value = "t_" + value
    if len(value) > limit:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        value = value[:limit - 13] + "_" + digest
    return value.lower()


class PostgresStorage(BaseStorage):
    """Almacena registros como JSONB en una tabla por dataset."""

    def save(self, data: list[dict[str, Any]], name: str, **kwargs: Any) -> str:
        uri = os.getenv("EXTRACTION_POSTGRES_URI")
        if not uri:
            raise ValueError("Defina EXTRACTION_POSTGRES_URI para usar PostgreSQL.")
        try:
            import psycopg
        except ImportError:
            raise RuntimeError("Instale la dependencia opcional: pip install psycopg[binary].") from None
        table = _external_name(str(kwargs.get("table") or name))
        try:
            with psycopg.connect(uri) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f'CREATE TABLE IF NOT EXISTS "{table}" (record JSONB NOT NULL)')
                    if data:
                        cursor.executemany(
                            f'INSERT INTO "{table}" (record) VALUES (%s::jsonb)',
                            [(json.dumps(record, ensure_ascii=False, default=str),) for record in data],
                        )
        except Exception:
            raise RuntimeError("No se pudo guardar el dataset en PostgreSQL; revise la configuración y conexión.") from None
        logger.info("PostgreSQL: dataset guardado (%d registros)", len(data))
        return f"postgresql:tabla:{table}"


class MongoStorage(BaseStorage):
    """Almacena un documento por registro en una colección por dataset."""

    def save(self, data: list[dict[str, Any]], name: str, **kwargs: Any) -> str:
        uri = os.getenv("EXTRACTION_MONGODB_URI")
        database = os.getenv("EXTRACTION_MONGODB_DATABASE", "extraction")
        if not uri:
            raise ValueError("Defina EXTRACTION_MONGODB_URI para usar MongoDB.")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,62}", database):
            raise ValueError("EXTRACTION_MONGODB_DATABASE debe contener solo letras, números, guiones o guiones bajos.")
        try:
            from pymongo import MongoClient
        except ImportError:
            raise RuntimeError("Instale la dependencia opcional: pip install pymongo.") from None
        collection = _external_name(str(kwargs.get("table") or name), limit=120)
        try:
            client = MongoClient(uri, serverSelectionTimeoutMS=5000)
            try:
                db = client[database]
                # Crear la colección incluso cuando no hay registros.
                if data:
                    records = json.loads(json.dumps(data, ensure_ascii=False, default=str))
                    db[collection].insert_many([{"record": record} for record in records])
                elif collection not in db.list_collection_names():
                    db.create_collection(collection)
            finally:
                client.close()
        except Exception:
            raise RuntimeError("No se pudo guardar el dataset en MongoDB; revise la configuración y conexión.") from None
        logger.info("MongoDB: dataset guardado (%d registros)", len(data))
        return f"mongodb:coleccion:{database}.{collection}"


class StorageFactory:
    """Fábrica de adaptadores de almacenamiento (patrón Factory)."""

    _registry: dict[str, type[BaseStorage]] = {
        "json": JsonStorage,
        "csv": CsvStorage,
        "excel": ExcelStorage,
        "sqlite": SqliteStorage,
        "postgresql": PostgresStorage,
        "mongodb": MongoStorage,
    }

    @classmethod
    def available(cls) -> list[str]:
        """Lista los formatos registrados."""
        return sorted(cls._registry)

    @classmethod
    def create(
        cls,
        format: StorageFormat,  # noqa: A002
        output_dir: Path,
    ) -> BaseStorage:
        """Crea el adaptador de almacenamiento para el formato dado."""
        key = str(format).lower()
        storage_class = cls._registry.get(key)
        if storage_class is None:
            raise NotImplementedError(
                f"Formato '{format}' no implementado. "
                f"Disponibles: {', '.join(cls.available())}."
            )
        return storage_class(output_dir)
