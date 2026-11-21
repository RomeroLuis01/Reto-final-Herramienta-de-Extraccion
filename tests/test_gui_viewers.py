"""Lectura y presentación de reportes y logs sin depender de pantalla."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.gui.app import main
from src.gui.viewers import label, read_log, read_report


def test_read_report_preserves_sections_and_sanitizes(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"url": "https://example.com/?token=secret", "protection": {"captcha_detected": True}, "quality": {"valid_records": 3}}), encoding="utf-8")
    result = read_report(path)
    assert result["protection"]["captcha_detected"] is True
    assert result["quality"]["valid_records"] == 3
    assert "secret" not in str(result)
    assert label("protection") == "Protecciones y restricciones"
    assert label("quality") == "Calidad de datos"


@pytest.mark.parametrize("text", ["", "[]", "{}", "not-json"])
def test_invalid_or_empty_report_is_rejected(tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        read_report(path)


def test_logs_parse_levels_and_multiline_tracebacks(tmp_path):
    path = tmp_path / "session.log"
    path.write_text("2026-11-21 13:04:00 \x1b[31mERROR   \x1b[0m [src.module] Falló\nTraceback (most recent call last):\n  línea del error\n2026-11-21 13:04:01 INFO     [src.service] Listo\n", encoding="utf-8")
    entries, truncated = read_log(path)
    assert not truncated
    assert entries[0]["level"] == "ERROR"
    assert entries[0]["component"] == "src.module"
    assert "línea del error" in entries[0]["message"]
    assert entries[1]["message"] == "Listo"


def test_empty_log(tmp_path):
    path = tmp_path / "empty.log"
    path.write_text("", encoding="utf-8")
    assert read_log(path) == ([], False)


def test_gui_initializes_logging(tmp_path):
    config = SimpleNamespace(paths=SimpleNamespace(logs_dir=tmp_path), log_level="INFO")
    root = MagicMock()
    with patch("src.gui.app.get_config", return_value=config), patch("src.gui.app.setup_logging") as setup, patch("src.gui.app.tk.Tk", return_value=root), patch("src.gui.app.ExtractionApp"):
        main()
    assert setup.call_args.kwargs["level"] == "INFO"
    assert setup.call_args.kwargs["log_file"].parent == tmp_path
    root.mainloop.assert_called_once()


def test_text_report_and_invalid_text(tmp_path):
    from src.gui.viewers import read_report_text
    report = tmp_path / "reporte.txt"
    report.write_text("REPORTE\nEstado: correcto", encoding="utf-8")
    assert "Estado: correcto" in read_report_text(report)
    report.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="vacío"):
        read_report_text(report)


def test_dataset_json_csv_excel_and_sqlite(tmp_path):
    import sqlite3
    from openpyxl import Workbook
    from src.gui.viewers import read_dataset, sqlite_tables

    json_path = tmp_path / "raw.json"
    json_path.write_text(json.dumps([{"nombre": "Libro", "precio": 10}]), encoding="utf-8")
    assert read_dataset(json_path) == (["nombre", "precio"], [["Libro", 10]], False)
    csv_path = tmp_path / "processed.csv"
    csv_path.write_text("nombre,precio\nLibro,10\n", encoding="utf-8-sig")
    assert read_dataset(csv_path) == (["nombre", "precio"], [["Libro", "10"]], False)
    xlsx_path = tmp_path / "processed.xlsx"
    workbook = Workbook()
    workbook.active.append(["nombre", "precio"])
    workbook.active.append(["Libro", 10])
    workbook.save(xlsx_path)
    workbook.close()
    assert read_dataset(xlsx_path) == (["nombre", "precio"], [["Libro", 10]], False)
    db_path = tmp_path / "datasets.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute('CREATE TABLE "datos originales" (nombre TEXT, precio INTEGER)')
        db.execute('INSERT INTO "datos originales" VALUES (?, ?)', ("Libro", 10))
        db.execute('CREATE TABLE procesados (nombre TEXT)')
    assert sqlite_tables(db_path) == ["datos originales", "procesados"]
    assert read_dataset(db_path, "datos originales") == (["nombre", "precio"], [["Libro", 10]], False)
    with pytest.raises(ValueError, match="tabla válida"):
        read_dataset(db_path, "inexistente")


def test_invalid_dataset_and_bounded_rows(tmp_path):
    from src.gui.viewers import read_dataset
    invalid = tmp_path / "bad.json"
    invalid.write_text('{"field": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="lista de registros"):
        read_dataset(invalid)
    csv_path = tmp_path / "large.csv"
    csv_path.write_text("n\n" + "x\n" * 3001, encoding="utf-8")
    columns, rows, truncated = read_dataset(csv_path)
    assert columns == ["n"]
    assert len(rows) == 3000
    assert truncated
