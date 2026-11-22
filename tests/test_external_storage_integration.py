"""Integración optativa con bases reales; usa exclusivamente datos sintéticos aislados.

Active RUN_EXTERNAL_STORAGE_TESTS=1 y configure las URI en el entorno. No se
leen archivos .env automáticamente ni se imprimen credenciales.
"""
from __future__ import annotations

import os
import uuid

import pytest

from src.storage.adapters import MongoStorage, PostgresStorage

pytestmark = pytest.mark.integration


@pytest.fixture
def external_config():
    if os.getenv("RUN_EXTERNAL_STORAGE_TESTS") != "1":
        pytest.skip("Defina RUN_EXTERNAL_STORAGE_TESTS=1 para probar bases reales")
    if not os.getenv("EXTRACTION_POSTGRES_URI") or not os.getenv("EXTRACTION_MONGODB_URI"):
        pytest.skip("Configure las URI de PostgreSQL y MongoDB para la integración")
    return uuid.uuid4().hex


def _records(marker):
    raw = {"test_marker": marker, "tipo": "sintético", "valor": "  ejemplo  ",
           "_source": {"url": "https://example.invalid/test"}}
    processed = {"test_marker": marker, "tipo": "sintético", "valor": "ejemplo",
                 "_lineage": {"execution_id": marker, "source_url": "https://example.invalid/test"},
                 "_field_lineage": {"valor": {"selector": ".valor", "applied_transformations": ["clean"]}}}
    return raw, processed


def test_postgres_raw_processed_roundtrip(external_config, tmp_path):
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql

    marker = external_config
    names = [f"codex_test_{marker}_raw", f"codex_test_{marker}_processed"]
    raw, processed = _records(marker)
    uri = os.environ["EXTRACTION_POSTGRES_URI"]
    created = []
    try:
        # No se reclama ninguna tabla preexistente, aunque la colisión UUID sea improbable.
        with psycopg.connect(uri, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                for name in names:
                    cursor.execute("SELECT to_regclass(%s)", (name,))
                    assert cursor.fetchone()[0] is None, "Identificador de prueba ya existente"
        storage = PostgresStorage(tmp_path)
        for name, record in zip(names, (raw, processed)):
            storage.save([record], name)
            created.append(name)
        with psycopg.connect(uri, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                for name, expected in zip(names, (raw, processed)):
                    cursor.execute(sql.SQL("SELECT record FROM {}").format(sql.Identifier(name)))
                    assert cursor.fetchall() == [(expected,)]
    except (psycopg.Error, RuntimeError):
        pytest.fail("No se pudo completar la integración PostgreSQL", pytrace=False)
    finally:
        # Solo borrar tablas identificadas por nombre y contenido exacto de esta prueba.
        try:
            with psycopg.connect(uri, connect_timeout=5) as connection:
                with connection.cursor() as cursor:
                    expected_records = dict(zip(names, (raw, processed)))
                    for name in created:
                        cursor.execute(sql.SQL("SELECT record FROM {}").format(sql.Identifier(name)))
                        rows = cursor.fetchall()
                        if rows != [(expected_records[name],)]:
                            pytest.fail("La tabla de prueba cambió; se conservó sin borrar", pytrace=False)
                        cursor.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(name)))
        except psycopg.Error:
            pytest.fail("No se pudo comprobar o limpiar la tabla aislada de prueba", pytrace=False)


def test_mongodb_raw_processed_roundtrip(external_config, tmp_path):
    pymongo = pytest.importorskip("pymongo")
    marker = external_config
    names = [f"codex_test_{marker}_raw", f"codex_test_{marker}_processed"]
    raw, processed = _records(marker)
    uri = os.environ["EXTRACTION_MONGODB_URI"]
    database = os.getenv("EXTRACTION_MONGODB_DATABASE", "extraction")
    created = []
    client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        db = client[database]
        existing = set(db.list_collection_names())
        assert not existing.intersection(names), "Identificador de prueba ya existente"
        storage = MongoStorage(tmp_path)
        for name, record in zip(names, (raw, processed)):
            storage.save([record], name)
            created.append(name)
        for name, expected in zip(names, (raw, processed)):
            docs = list(db[name].find({}, {"_id": 0}))
            assert docs == [{"record": expected}]
    except (pymongo.errors.PyMongoError, RuntimeError):
        pytest.fail("No se pudo completar la integración MongoDB", pytrace=False)
    finally:
        try:
            expected_records = dict(zip(names, (raw, processed)))
            for name in created:
                docs = list(db[name].find({}, {"_id": 0}))
                if docs != [{"record": expected_records[name]}]:
                    pytest.fail("La colección de prueba cambió; se conservó sin borrar", pytrace=False)
                db[name].drop()
        except pymongo.errors.PyMongoError:
            pytest.fail("No se pudo comprobar o limpiar la colección aislada de prueba", pytrace=False)
        finally:
            client.close()
