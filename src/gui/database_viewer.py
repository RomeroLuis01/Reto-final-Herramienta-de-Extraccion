"""Lectura limitada de datasets exportados a bases de datos."""
from __future__ import annotations

import os
import re
from typing import Any

MAX_ROWS = 3000
_DATASET = re.compile(r"_(?:raw|processed)_\d{8}_\d{6}_[a-z0-9_]+$")


def _is_dataset(name: str, source: str) -> bool:
    """Admite nombres normales y candidatos truncados por el adaptador.

    El sufijo hash de un identificador truncado no prueba su procedencia: se
    trata como candidato y solo se ofrecen operaciones de lectura.
    """
    limit = 63 if source == "PostgreSQL" else 120
    return bool(_DATASET.search(name) or (
        len(name) == limit and re.fullmatch(r"[a-z0-9_]+_[0-9a-f]{12}", name)
    ))


def _uri(source: str) -> str:
    variable = "EXTRACTION_POSTGRES_URI" if source == "PostgreSQL" else "EXTRACTION_MONGODB_URI"
    value = os.getenv(variable)
    if not value:
        raise ValueError(f"Configure {variable} en .env y reinicie la aplicación.")
    return value


def _tabular(records: list[Any]) -> tuple[list[str], list[list[Any]], bool]:
    truncated = len(records) > MAX_ROWS
    sample = [record for record in records[:MAX_ROWS] if isinstance(record, dict)]
    columns = list(dict.fromkeys(key for row in sample for key in row))
    return columns, [[row.get(key) for key in columns] for row in sample], truncated


def list_datasets(source: str) -> list[str]:
    uri = _uri(source)
    if source == "PostgreSQL":
        try:
            import psycopg
        except ImportError:
            raise ValueError("Instale psycopg con requirements.txt.") from None
        try:
            with psycopg.connect(uri, connect_timeout=5) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT table_name FROM information_schema.columns WHERE table_schema = current_schema() AND column_name = 'record' AND data_type = 'jsonb' ORDER BY table_name")
                    return [row[0] for row in cursor.fetchall() if _is_dataset(row[0], source)]
        except Exception:
            raise ValueError("No se pudo conectar a PostgreSQL. Revise el servidor y su configuración.") from None
    try:
        from pymongo import MongoClient
    except ImportError:
        raise ValueError("Instale pymongo con requirements.txt.") from None
    try:
        with MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000, socketTimeoutMS=5000) as client:
            database = client[os.getenv("EXTRACTION_MONGODB_DATABASE", "extraction")]
            return sorted(name for name in database.list_collection_names() if _is_dataset(name, source))
    except Exception:
        raise ValueError("No se pudo conectar a MongoDB. Revise el servidor y su configuración.") from None


def read_database_dataset(source: str, name: str) -> tuple[list[str], list[list[Any]], bool]:
    if not _is_dataset(name, source):
        raise ValueError("Seleccione un dataset válido.")
    uri = _uri(source)
    if source == "PostgreSQL":
        try:
            import psycopg
            from psycopg import sql
        except ImportError:
            raise ValueError("Instale psycopg con requirements.txt.") from None
        try:
            with psycopg.connect(uri, connect_timeout=5) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql.SQL("SELECT record FROM {} LIMIT %s").format(sql.Identifier(name)), (MAX_ROWS + 1,))
                    return _tabular([row[0] for row in cursor.fetchall()])
        except Exception:
            raise ValueError("No se pudo leer el dataset de PostgreSQL.") from None
    try:
        from pymongo import MongoClient
    except ImportError:
        raise ValueError("Instale pymongo con requirements.txt.") from None
    try:
        with MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000, socketTimeoutMS=5000) as client:
            collection = client[os.getenv("EXTRACTION_MONGODB_DATABASE", "extraction")][name]
            return _tabular([row.get("record") for row in collection.find({}, {"record": 1, "_id": 0}).limit(MAX_ROWS + 1)])
    except Exception:
        raise ValueError("No se pudo leer el dataset de MongoDB.") from None
