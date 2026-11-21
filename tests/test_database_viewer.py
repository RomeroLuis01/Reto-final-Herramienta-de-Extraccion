"""Consulta de datasets de bases sin depender de servidores."""
import queue
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.gui.database_viewer import _tabular, list_datasets, read_database_dataset
from src.gui.viewers import DatasetViewer


def test_tabular_limits_and_combines_columns():
    columns, rows, truncated = _tabular([{"a": 1}, {"b": 2}] + [{"a": 3}] * 3000)
    assert columns == ["a", "b"]
    assert rows[:2] == [[1, None], [None, 2]]
    assert len(rows) == 3000 and truncated


def test_postgres_lists_project_tables_and_reads_records(monkeypatch):
    monkeypatch.setenv("EXTRACTION_POSTGRES_URI", "postgresql://user:secret@localhost/db")
    import psycopg
    connection = MagicMock()
    cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [("site_raw_20261121_111942_1d66d91a",), ("otro",)]
    monkeypatch.setattr(psycopg, "connect", MagicMock(return_value=connection))
    assert list_datasets("PostgreSQL") == ["site_raw_20261121_111942_1d66d91a"]
    cursor.fetchall.return_value = [({"titulo": "Uno"},)]
    assert read_database_dataset("PostgreSQL", "site_raw_20261121_111942_1d66d91a") == (["titulo"], [["Uno"]], False)
    assert psycopg.connect.call_args.kwargs["connect_timeout"] == 5


def test_mongo_lists_and_reads_records(monkeypatch):
    monkeypatch.setenv("EXTRACTION_MONGODB_URI", "mongodb://user:secret@localhost")
    client = MagicMock()
    db = client.__enter__.return_value.__getitem__.return_value
    db.list_collection_names.return_value = ["site_processed_20261121_111942_1d66d91a", "otro"]
    db.__getitem__.return_value.find.return_value.limit.return_value = [{"record": {"nombre": "NASA"}}]
    monkeypatch.setitem(sys.modules, "pymongo", SimpleNamespace(MongoClient=MagicMock(return_value=client)))
    assert list_datasets("MongoDB") == ["site_processed_20261121_111942_1d66d91a"]
    assert read_database_dataset("MongoDB", "site_processed_20261121_111942_1d66d91a") == (["nombre"], [["NASA"]], False)


def test_errors_do_not_expose_connection_secrets(monkeypatch):
    monkeypatch.setenv("EXTRACTION_MONGODB_URI", "mongodb://user:secret@localhost")
    monkeypatch.setitem(sys.modules, "pymongo", SimpleNamespace(MongoClient=MagicMock(side_effect=RuntimeError("secret"))))
    with pytest.raises(ValueError) as error:
        list_datasets("MongoDB")
    assert "secret" not in str(error.value)
    with pytest.raises(ValueError, match="válido"):
        read_database_dataset("MongoDB", "not-a-dataset")


@pytest.mark.parametrize("source,limit", [("PostgreSQL", 63), ("MongoDB", 120)])
def test_long_exported_names_are_candidates(source, limit):
    from src.gui.database_viewer import _is_dataset
    from src.storage.adapters import _external_name

    original = "long_domain_" * 20 + "raw_20261121_111942_1d66d91a"
    name = _external_name(original, limit=limit)
    assert len(name) == limit
    assert _is_dataset(name, source)
    assert not _is_dataset("a" * (limit - 13) + "_not_a_hash__", source)


def test_stale_background_results_do_not_change_view():
    viewer = SimpleNamespace(_database_results=queue.Queue(), _request_id=2,
                             notice=MagicMock(), _display_data=MagicMock(), after=MagicMock())
    viewer._poll_database = lambda: None
    viewer._database_results.put((1, "read", (["secret"], [[1]], False), None))
    DatasetViewer._poll_database(viewer)
    viewer._display_data.assert_not_called()
    viewer.notice.set.assert_not_called()
