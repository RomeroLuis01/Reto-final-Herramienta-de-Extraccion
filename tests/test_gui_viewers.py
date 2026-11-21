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
