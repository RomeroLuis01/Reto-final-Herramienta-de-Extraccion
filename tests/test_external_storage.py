"""Pruebas de adaptadores externos mediante clientes simulados."""
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from main import _parse_formats
from src.storage.adapters import MongoStorage, PostgresStorage


def test_postgresql_uses_safe_identifiers_and_parameters(monkeypatch, tmp_path):
    monkeypatch.setenv("EXTRACTION_POSTGRES_URI", "postgresql://user:secret@localhost/db")
    driver = MagicMock()
    monkeypatch.setitem(sys.modules, "psycopg", driver)
    cursor = driver.connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    result = PostgresStorage(tmp_path).save([{"title": "'payload'", "_lineage": {"id": 1}}], 'raw"; DROP TABLE x')
    assert result == "postgresql:tabla:raw___drop_table_x"
    sql = cursor.execute.call_args.args[0]
    assert 'DROP TABLE' not in sql and '"raw___drop_table_x"' in sql
    sql, rows = cursor.executemany.call_args.args
    assert "'payload'" not in sql
    assert json.loads(rows[0][0])["_lineage"] == {"id": 1}


def test_mongodb_separate_collections_and_nested_records(monkeypatch, tmp_path):
    monkeypatch.setenv("EXTRACTION_MONGODB_URI", "mongodb://user:secret@localhost")
    monkeypatch.setenv("EXTRACTION_MONGODB_DATABASE", "extraction")
    client = MagicMock()
    monkeypatch.setitem(sys.modules, "pymongo", SimpleNamespace(MongoClient=MagicMock(return_value=client)))
    storage = MongoStorage(tmp_path)
    raw = storage.save([{"_id": "site-id", "nested": {"a": 1}}], "raw")
    processed = storage.save([{"_lineage": {"source": "url"}}], "processed")
    assert raw != processed
    db = client.__getitem__.return_value
    assert db.__getitem__.call_args_list[0].args == ("raw",)
    assert db.__getitem__.call_args_list[1].args == ("processed",)
    inserted = db.__getitem__.return_value.insert_many.call_args_list[0].args[0]
    assert inserted == [{"record": {"_id": "site-id", "nested": {"a": 1}}}]
    assert client.close.call_count == 2


@pytest.mark.parametrize("kind,module,variable", [
    (PostgresStorage, "psycopg", "EXTRACTION_POSTGRES_URI"),
    (MongoStorage, "pymongo", "EXTRACTION_MONGODB_URI"),
])
def test_external_errors_never_disclose_uri(monkeypatch, tmp_path, kind, module, variable):
    monkeypatch.setenv(variable, "protocol://user:secret@example.com")
    driver = MagicMock()
    if module == "psycopg":
        driver.connect.side_effect = RuntimeError("secret")
    else:
        driver.MongoClient.side_effect = RuntimeError("secret")
    monkeypatch.setitem(sys.modules, module, driver)
    with pytest.raises(RuntimeError) as exc:
        kind(tmp_path).save([{"a": 1}], "dataset")
    assert "secret" not in str(exc.value)
    assert exc.value.__suppress_context__


def test_cli_accepts_external_formats():
    assert _parse_formats("postgresql,mongodb") == ["postgresql", "mongodb"]
