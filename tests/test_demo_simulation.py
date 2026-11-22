"""La simulación no se presenta como extracción ni consulta la web."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.config.settings import PathConfig, ProjectConfig
from src.extractor.demo_simulation import DEMO_URL, generate_demo, is_demo_url
from src.gui.app import ExtractionApp


@pytest.mark.parametrize("url", [
    "http://www.google.com/recaptcha/api2/demo", "https://google.com/recaptcha/api2/demo",
    "https://user@www.google.com/recaptcha/api2/demo", DEMO_URL + "?token=123",
    DEMO_URL + "#x", DEMO_URL + "/", "https://www.google.com:443/recaptcha/api2/demo",
])
def test_other_urls_denied(url, tmp_path):
    assert not is_demo_url(url)
    config = ProjectConfig(paths=PathConfig(base_dir=tmp_path))
    with pytest.raises(ValueError):
        generate_demo(url, config, ["json"])


def test_simulation_writes_labeled_records_and_reports_without_network(tmp_path):
    config = ProjectConfig(paths=PathConfig(base_dir=tmp_path))
    with patch("requests.sessions.Session.request", side_effect=AssertionError("red no autorizada")):
        result = generate_demo(DEMO_URL, config, ["json", "csv"])
    assert result["estado"] == "simulado"
    assert result["tipo"] == "SIMULACION_LOCAL"
    assert "no se verificó el CAPTCHA" in result["aviso"]
    for stage, path in result["outputs"]["json"].items():
        records = json.loads(Path(path).read_text(encoding="utf-8"))
        assert records and all(row["origen"] == "SIMULADO" and row["metodo"] == "simulacion_local" for row in records)
        assert "simulado" in Path(path).name
    report = Path(result["report_files"]["txt"]).read_text(encoding="utf-8")
    assert "SIMULACIÓN LOCAL" in report and "NO ES EXTRACCIÓN REAL" in report
    assert not list(Path(config.paths.profiles_dir).glob("**/*.json"))


def test_simulation_keeps_partial_outputs_on_storage_failure(tmp_path):
    config = ProjectConfig(paths=PathConfig(base_dir=tmp_path))
    from src.storage.adapters import StorageFactory
    create = StorageFactory.create
    def factory(fmt, path):
        if fmt == "postgresql":
            raise ValueError("secreto-que-no-debe-aparecer")
        return create(fmt, path)
    with patch("src.extractor.demo_simulation.StorageFactory.create", side_effect=factory):
        result = generate_demo(DEMO_URL, config, ["json", "postgresql", "csv"])
    assert result["estado"] == "simulado_parcial"
    assert set(result["outputs"]) == {"json", "csv"}
    assert "secreto-que-no-debe-aparecer" not in str(result)


def test_gui_requires_explicit_simulation_confirmation():
    app = ExtractionApp.__new__(ExtractionApp)
    app.root = MagicMock()
    app.url = SimpleNamespace(get=lambda: DEMO_URL)
    app.formats = {"json": SimpleNamespace(get=lambda: True)}
    with patch("src.gui.app.messagebox.askyesno", return_value=False), patch("src.gui.app.threading.Thread") as thread:
        app._start_simulation()
    thread.assert_not_called()
