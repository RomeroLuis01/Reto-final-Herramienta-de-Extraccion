"""Pruebas del flujo gráfico sin depender de una pantalla."""
import queue
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.gui.app import CUSTOM_SITE, TEST_SITES, ExtractionApp


def test_preset_sites_fill_url_without_changing_other_options():
    app = ExtractionApp.__new__(ExtractionApp)
    app.site = MagicMock()
    app.url = MagicMock()
    app.strategy = MagicMock()
    app.pages = MagicMock()
    app.records = MagicMock()
    assert len(TEST_SITES) == 4
    assert all("recaptcha" not in url.lower() for url in TEST_SITES.values())
    for name, url in TEST_SITES.items():
        app.site.get.return_value = name
        app._select_site()
        app.url.set.assert_called_with(url)
    app.strategy.set.assert_not_called()
    app.pages.set.assert_not_called()
    app.records.set.assert_not_called()


def test_manual_url_edit_marks_site_custom_without_overwriting_url():
    app = ExtractionApp.__new__(ExtractionApp)
    app.site = MagicMock()
    app.url = MagicMock()
    app.site.get.return_value = "NASA"
    app.url.get.return_value = "https://example.com/custom"
    app._sync_site()
    app.site.set.assert_called_once_with(CUSTOM_SITE)
    app.url.set.assert_not_called()


def test_selecting_custom_site_keeps_existing_url():
    app = ExtractionApp.__new__(ExtractionApp)
    app.site = MagicMock()
    app.url = MagicMock()
    app.site.get.return_value = CUSTOM_SITE
    app._select_site()
    app.url.set.assert_not_called()


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


def test_manual_schema_builds_and_validates_fields():
    import pytest
    from src.gui.app import build_manual_schema
    from src.selector.schema import ExtractionSchema

    config = build_manual_schema(".product", "title; h2\nlink; a; css; href")
    schema = ExtractionSchema.from_dict(config)
    assert schema.apply('<div class="product"><h2>Libro</h2><a href="/x">Ir</a></div>',
                        base_url="https://example.com")[0]["link"] == "https://example.com/x"
    for fields in ("", "title", "title; h2; invalid", "x; h2\nx; p"):
        with pytest.raises(ValueError):
            build_manual_schema(".product", fields)


def test_worker_passes_manual_schema_to_extraction():
    app = ExtractionApp.__new__(ExtractionApp)
    app.config = SimpleNamespace(paths=SimpleNamespace(profiles_dir="profiles", reports_dir="reports"))
    app.messages = queue.Queue()
    manual = {"name": "selection", "fields": [{"name": "title", "selector": "h2"}]}
    options = {"target": SimpleNamespace(url="https://example.com"), "use_selenium": False,
               "strategy": "requests", "max_pages": 1, "max_records": 5, "formats": ["json"],
               "schema_path": "", "manual_schema": manual, "extract": True}
    with patch("src.gui.app.ProfileManager"), patch("src.gui.app.WebInspector") as inspector, \
         patch("src.gui.app.DiagnosticReporter"), patch("src.gui.app.ExtractionService") as service:
        inspector.return_value.inspect.return_value = {"protection": {}}
        service.return_value.run.return_value = {"status": "ok"}
        app._run(options)
        assert service.return_value.run.call_args.kwargs["schema_config"] is manual
        assert service.return_value.run.call_args.kwargs["schema_origin"] == "interfaz"


def test_start_passes_dynamic_rules_and_keeps_other_options():
    from src.config.settings import register_target

    app = ExtractionApp.__new__(ExtractionApp)
    app.root = MagicMock()
    app.config = SimpleNamespace(respect_robots_txt=True)
    app.url = MagicMock(); app.url.get.return_value = "https://example.com"
    app.label = MagicMock(); app.label.get.return_value = "Example"
    app.auth = MagicMock(); app.auth.get.return_value = "none"
    app.pages = MagicMock(); app.pages.get.return_value = "2"
    app.records = MagicMock(); app.records.get.return_value = "10"
    app.schema = MagicMock(); app.schema.get.return_value = ""
    app.manual_schema = None
    app.strategy = MagicMock(); app.strategy.get.return_value = "selenium"
    app.selenium = MagicMock(); app.selenium.get.return_value = True
    app.infinite_scroll = MagicMock(); app.infinite_scroll.get.return_value = False
    app.load_more_selector = MagicMock(); app.load_more_selector.get.return_value = " #more "
    fmt = MagicMock(); fmt.get.return_value = True
    app.formats = {"json": fmt}
    app.diagnose_button = MagicMock(); app.extract_button = MagicMock()
    app.status = MagicMock(); app._append = MagicMock()
    with patch("src.gui.app.register_target", wraps=register_target) as register, \
         patch("src.gui.app.threading.Thread") as worker:
        app._start(True)
        assert register.call_args.kwargs["extraction_rules"] == {
            "load_more_selector": "#more", "infinite_scroll": False}
        options = worker.call_args.kwargs["args"][0]
        assert options["target"].extraction_rules == register.call_args.kwargs["extraction_rules"]
        assert (options["strategy"], options["max_pages"], options["max_records"], options["formats"]) == (
            "selenium", 2, 10, ["json"])
        app.load_more_selector.get.return_value = ""
        app.infinite_scroll.get.return_value = True
        app._start(True)
        assert register.call_args.kwargs["extraction_rules"] == {"infinite_scroll": True}
        app.infinite_scroll.get.return_value = False
        app._start(True)
        assert register.call_args.kwargs["extraction_rules"] == {}


def test_dynamic_settings_reject_conflicting_mechanisms():
    app = ExtractionApp.__new__(ExtractionApp)
    app.root = MagicMock()
    app.infinite_scroll = MagicMock(); app.infinite_scroll.get.return_value = True
    app.load_more_selector = MagicMock(); app.load_more_selector.get.return_value = "#more"
    with patch("src.gui.app.messagebox.showerror") as error, \
         patch("src.gui.app.threading.Thread") as thread:
        app._start(True)
    assert "no ambos" in error.call_args.args[1]
    thread.assert_not_called()


def test_preview_candidates_show_distinct_sections_without_navigation():
    from src.gui.selection_preview import preview_candidates
    html = """<nav><ul><li><a href='/menu'>Menu</a></li><li><a href='/help'>Help</a></li></ul></nav>
    <main><table><tr><th>Name</th></tr><tr><td>Alpha</td></tr><tr><td>Beta</td></tr></table>
    <article><h2>A</h2><p>First</p></article><article><h2>B</h2><p>Second</p></article></main>"""
    candidates = preview_candidates(html, {}, "https://example.com")
    assert len(candidates) >= 2
    shown = str([candidate["examples"] for candidate in candidates])
    assert "Alpha" in shown and "First" in shown
    assert "Menu" not in shown and "Help" not in shown
    assert all(candidate["schema"]["fields"] for candidate in candidates)


def test_preview_no_sample_yields_no_candidates():
    from src.gui.selection_preview import preview_candidates
    assert preview_candidates("", {}, "https://example.com") == []


def test_candidate_fields_can_be_edited_as_manual_selection():
    from src.gui.app import build_manual_schema
    from src.gui.selection_preview import candidate_fields_text, preview_candidates
    html = "<article><h2>A</h2><p>First</p></article><article><h2>B</h2><p>Second</p></article>"
    candidate = preview_candidates(html, {}, "https://example.com")[0]["schema"]
    lines = candidate_fields_text(candidate)
    edited = build_manual_schema(candidate["base_selector"], lines.splitlines()[0])
    assert len(edited["fields"]) == 1
    assert edited["fields"][0]["name"] == candidate["fields"][0]["name"]


def test_worker_queues_preview_without_persisting_html():
    app = ExtractionApp.__new__(ExtractionApp)
    app.config = SimpleNamespace(paths=SimpleNamespace(profiles_dir="profiles", reports_dir="reports"))
    app.messages = queue.Queue()
    target = SimpleNamespace(url="https://example.com")
    options = {"target": target, "use_selenium": False, "extract": False,
               "schema_path": ""}
    html = "<article><h2>A</h2><p>First</p></article><article><h2>B</h2><p>Second</p></article>"
    with patch("src.gui.app.ProfileManager") as profiles, \
         patch("src.gui.app.WebInspector") as inspector, \
         patch("src.gui.app.DiagnosticReporter"):
        def inspect(*_args, **kwargs):
            kwargs["sample_callback"](html, {})
            return {"url": target.url}
        inspector.return_value.inspect.side_effect = inspect
        app._run(options)
        assert html not in str(profiles.return_value.save.call_args)
    events = list(app.messages.queue)
    preview = next(value for kind, value in events if kind == "selection_preview")
    assert preview[0] == target.url and preview[1]
    assert html not in str(preview)
