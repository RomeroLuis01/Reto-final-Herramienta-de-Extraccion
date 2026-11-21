"""Nombres de presentación independientes de los archivos y bases."""
import json

import pytest

from src.gui.column_labels import ColumnLabels


def test_labels_persist_by_source_dataset_and_sqlite_table(tmp_path):
    labels = ColumnLabels(tmp_path / "profiles")
    columns = ["columna_1", "columna_2"]
    dataset = tmp_path / "data.csv"
    dataset.write_text("columna_1,columna_2\na,b\n", encoding="utf-8")
    original = dataset.read_bytes()
    local = labels.key("Archivos locales", str(dataset))
    postgres = labels.key("PostgreSQL", "ejemplo_processed_20261121_120000_12345678")
    mongo = labels.key("MongoDB", "ejemplo_processed_20261121_120000_12345678")
    sqlite_a = labels.key("Archivos locales", "datasets.sqlite", "raw")
    sqlite_b = labels.key("Archivos locales", "datasets.sqlite", "processed")
    labels.save(local, columns, ["Nombre", "Descripción"])
    labels.save(postgres, columns, ["Título", "URL"])
    labels.save(sqlite_a, columns, ["Uno", "Dos"])
    reopened = ColumnLabels(tmp_path / "profiles")
    assert reopened.get(local, columns) == ["Nombre", "Descripción"]
    assert reopened.get(postgres, columns) == ["Título", "URL"]
    assert reopened.get(mongo, columns) == columns
    assert reopened.get(sqlite_b, columns) == columns
    assert reopened.get(local, ["columna_1", "nuevo"]) == ["columna_1", "nuevo"]
    assert dataset.read_bytes() == original
    reopened.save(local, columns, columns)
    assert reopened.get(local, columns) == columns
    assert local not in json.loads(reopened.path.read_text(encoding="utf-8"))


def test_corrupt_labels_are_not_overwritten(tmp_path):
    labels = ColumnLabels(tmp_path)
    labels.path.parent.mkdir(parents=True)
    labels.path.write_text("{corrupto", encoding="utf-8")
    with pytest.raises(ValueError, match="no es válido"):
        labels.get("dataset", ["columna_1"])
    with pytest.raises(ValueError, match="no es válido"):
        labels.save("dataset", ["columna_1"], ["Nombre"])
    assert labels.path.read_text(encoding="utf-8") == "{corrupto"


def test_invalid_labels_do_not_create_a_file(tmp_path):
    labels = ColumnLabels(tmp_path)
    with pytest.raises(ValueError, match="cada columna"):
        labels.save("dataset", ["columna_1"], ["  "])
    assert not labels.path.exists()


def test_database_scope_separates_servers_without_credentials(monkeypatch):
    monkeypatch.setenv("EXTRACTION_POSTGRES_URI", "postgresql://user:secret@db1:5432/test?sslmode=require")
    first = ColumnLabels.database_scope("PostgreSQL")
    monkeypatch.setenv("EXTRACTION_POSTGRES_URI", "postgresql://user:secret@db2:5432/test")
    second = ColumnLabels.database_scope("PostgreSQL")
    assert first != second
    assert "user" not in first and "secret" not in first
    assert ColumnLabels.key("PostgreSQL", "dataset", scope=first) != ColumnLabels.key("PostgreSQL", "dataset", scope=second)
