"""Pruebas del flujo gráfico sin depender de una pantalla."""
import queue
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.gui.app import ExtractionApp


def test_worker_diagnoses_and_extracts_using_same_diagnosis():
    app = ExtractionApp.__new__(ExtractionApp)
    app.config = SimpleNamespace(paths=SimpleNamespace(profiles_dir="profiles", reports_dir="reports"))
    app.messages = queue.Queue()
    target = SimpleNamespace(url="https://example.com")
    diagnosis = {"url": target.url, "protection": {"blocking_barrier": False}}
    options = {"target": target, "use_selenium": False, "strategy": "requests",
               "max_pages": 2, "max_records": 10, "formats": ["csv"],
               "schema_path": "", "extract": True}
    with patch("src.gui.app.ProfileManager") as profiles, \
         patch("src.gui.app.WebInspector") as inspector, \
         patch("src.gui.app.DiagnosticReporter") as reporter, \
         patch("src.gui.app.ExtractionService") as service:
        profiles.return_value.load.return_value = {"old": True}
        profiles.return_value.save.return_value = "perfil.json"
        inspector.return_value.inspect.return_value = diagnosis
        reporter.return_value.generate.return_value = {"txt": "reporte.txt"}
        service.return_value.run.return_value = {"status": "ok", "outputs": {}}
        app._run(options)
        assert service.return_value.run.call_args.kwargs["diagnosis"] is diagnosis
        assert service.return_value.run.call_args.kwargs["formats"] == ["csv"]
        messages = list(app.messages.queue)
        assert [kind for kind, _ in messages] == ["text", "text", "done"]
        assert "reporte.txt" in messages[0][1]


def test_worker_reports_error_and_completes():
    app = ExtractionApp.__new__(ExtractionApp)
    app.config = MagicMock()
    app.messages = queue.Queue()
    options = {"target": SimpleNamespace(url="https://example.com"), "extract": True,
               "schema_path": "missing.json"}
    with patch("src.gui.app.load_schema_file", side_effect=ValueError("esquema inválido")):
        app._run(options)
    assert list(app.messages.queue) == [("error", "esquema inválido"), ("done", None)]
