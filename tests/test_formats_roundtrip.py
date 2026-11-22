"""Lectura real de formatos generados desde una extracción controlada."""
from __future__ import annotations

import csv
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from openpyxl import load_workbook
import pytest

from src.config.settings import AnalysisTarget, PathConfig, ProjectConfig
from src.extractor.extraction_service import ExtractionService


@pytest.fixture
def local_json():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'[{"nombre":"  Ana  ","edad":"23"},{"nombre":"Luis","edad":"24"}]'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/data.json"
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def _xlsx_rows(path):
    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        values = workbook.active.values
        header = next(values)
        return [dict(zip(header, row)) for row in values]
    finally:
        workbook.close()


def _sqlite_rows(path, stage):
    with sqlite3.connect(path) as connection:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        matched = [name for name in tables if f"_{stage}_" in name]
        assert len(matched) == 1
        connection.row_factory = sqlite3.Row
        safe_name = matched[0].replace('"', '""')
        return [dict(row) for row in connection.execute(f'SELECT * FROM "{safe_name}"')]


def test_all_local_formats_preserve_raw_processed_and_nested_lineage(local_json, tmp_path):
    config = ProjectConfig(paths=PathConfig(base_dir=tmp_path))
    diagnosis = {"access_status": "accesible", "protection": {}, "viability": {},
                 "acquisition_mechanisms": {"content_format": "json"}}
    schema = {"name": "persons", "base_selector": "", "fields": [
        {"name": "nombre", "selector": "nombre", "selector_type": "json_path", "transform": "upper"},
        {"name": "edad", "selector": "edad", "selector_type": "json_path"},
    ]}
    summary = ExtractionService(config).run(
        AnalysisTarget(url=local_json, allow_private_network=True), diagnosis=diagnosis,
        schema_config=schema, strategy="requests", max_pages=1, delay_s=0,
        formats=["json", "csv", "excel", "sqlite"], output_dir=tmp_path / "out")
    assert summary["status"] == "ok", summary["errors"]
    assert summary["raw_records"] == summary["processed_records"] == 2
    outputs = summary["outputs"]
    raw_json = json.loads(Path(outputs["json"]["raw"]).read_text(encoding="utf-8"))
    processed_json = json.loads(Path(outputs["json"]["processed"]).read_text(encoding="utf-8"))
    assert raw_json[0]["nombre"] == "  Ana  "
    assert processed_json[0]["nombre"] == "ANA"
    assert processed_json[0]["_field_lineage"]["nombre"]["selector"] == "nombre"
    assert "normalize:upper" in processed_json[0]["_field_lineage"]["nombre"]["applied_transformations"]
    for stage, expected in (("raw", raw_json), ("processed", processed_json)):
        with open(outputs["csv"][stage], encoding="utf-8-sig", newline="") as stream:
            csv_rows = list(csv.DictReader(stream))
        excel_rows = _xlsx_rows(outputs["excel"][stage])
        sqlite_rows = _sqlite_rows(outputs["sqlite"][stage], stage)
        for rows in (csv_rows, excel_rows, sqlite_rows):
            assert len(rows) == 2
            assert rows[0]["nombre"] == expected[0]["nombre"]
            nested_fields = ("_source", "_field_meta") if stage == "raw" else ("_lineage", "_field_lineage")
            for nested in nested_fields:
                assert json.loads(rows[0][nested]) == expected[0][nested]
