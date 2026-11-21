"""
Tests del Avance 2 — Extracción, selección, transformación y almacenamiento.

Usa un servidor HTTP local controlado (127.0.0.1) para verificar los cuatro
mecanismos sin depender de internet ni generar cargas externas.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock, patch

import pytest

from src.config.settings import AnalysisTarget, ProjectConfig
from src.extractor.api_extractor import ApiExtractor
from src.extractor.base import ExtractionBatch, ExtractorFactory, PageSnapshot
from src.extractor.extraction_service import ExtractionService
from src.extractor.requests_extractor import RequestsExtractor
from src.monitor.extraction_reporter import ExtractionReporter
from src.selector.builder import SchemaBuilder
from src.selector.schema import ExtractionSchema, FieldSelector
from src.storage.adapters import StorageFactory
from src.transformer.pipeline import (
    DataCleaner,
    Deduplicator,
    LineageEnricher,
    Normalizer,
    TransformationPipeline,
    Validator,
)

PAGE_1 = """
<html><body>
  <h1>Catálogo</h1>
  <div class="product">
    <span class="title"> Libro A </span>
    <span class="price">$ 10.50</span>
    <a class="link" href="/libro-a">ver</a>
  </div>
  <div class="product">
    <span class="title">Libro B</span>
    <span class="price">$ 20.00</span>
    <a class="link" href="/libro-b">ver</a>
  </div>
  <a rel="next" href="/page2.html">Siguiente</a>
</body></html>
"""

PAGE_2 = """
<html><body>
  <div class="product">
    <span class="title">Libro C</span>
    <span class="price">$ 30.00</span>
    <a class="link" href="/libro-c">ver</a>
  </div>
  <div class="product">
    <span class="title">Libro D</span>
    <span class="price">$ 40.00</span>
    <a class="link" href="/libro-d">ver</a>
  </div>
</body></html>
"""

SCHEMA_CONFIG = {
    "name": "productos",
    "base_selector": "div.product",
    "fields": [
        {
            "name": "titulo",
            "selector": "span.title",
            "selector_type": "css",
            "attribute": "text",
            "required": True,
            "transform": "strip",
        },
        {
            "name": "precio",
            "selector": "span.price",
            "selector_type": "css",
            "transform": "number",
        },
        {
            "name": "url",
            "selector": "a.link",
            "selector_type": "css",
            "attribute": "href",
        },
    ],
}


class _Handler(BaseHTTPRequestHandler):
    """Servidor determinista con HTML paginado y endpoint JSON."""

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/items":
            page = int(parse_qs(parsed.query).get("page", ["1"])[0])
            payload = {"data": [{"id": page, "name": f"Item {page}"}]}
            body = json.dumps(payload).encode("utf-8")
            content_type = "application/json"
        elif path == "/page2.html":
            body = PAGE_2.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            body = PAGE_1.encode("utf-8")
            content_type = "text/html; charset=utf-8"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Silencia logs del servidor de prueba."""


@pytest.fixture
def local_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def _diagnosis(url: str, **overrides: object) -> dict:
    diagnosis = {
        "url": url,
        "domain": urlparse(url).netloc,
        "diagnosis_timestamp": "2026-11-17T12:00:00",
        "access_status": "accesible",
        "selenium_status": "skipped_by_user",
        "acquisition_mechanisms": {
            "content_format": "html",
            "render_type": "static",
            "js_generated_content": False,
        },
        "technologies": {"render_type": "static"},
        "network": {"rest_api_detected": False, "endpoints_observed": []},
        "pagination": {"pagination_selector": "a[rel='next']", "infinite_scroll": False},
        "candidate_selectors": {"tables": []},
        "protection": {
            "blocking_barrier": False,
            "robots_crawl_allowed": True,
            "restrictions": [],
        },
        "viability": {
            "recommended_strategies": ["requests + BeautifulSoup (HTML estático)"],
            "blocked_by_barrier": False,
        },
        "errors": [],
    }
    for key, value in overrides.items():
        if key in ("protection", "viability") and isinstance(value, dict):
            diagnosis[key].update(value)
        else:
            diagnosis[key] = value
    return diagnosis


# ---------------------------------------------------------------------------
# M3 — Extractores y fábrica
# ---------------------------------------------------------------------------


def test_extractor_factory_registers_and_selects() -> None:
    import src.extractor  # noqa: F401 — registra motores en la fábrica

    assert set(ExtractorFactory.available()) == {"api", "requests", "selenium"}
    assert ExtractorFactory.select(_diagnosis("https://example.com")) == "requests"
    assert isinstance(
        ExtractorFactory.create("selenium"),
        type(ExtractorFactory.create("selenium")),
    )
    with pytest.raises(ValueError):
        ExtractorFactory.create("desconocido")


def test_extractor_factory_selects_api_and_selenium() -> None:
    api_diagnosis = _diagnosis("https://example.com")
    api_diagnosis["acquisition_mechanisms"]["content_format"] = "json"
    api_diagnosis["network"]["rest_api_detected"] = True
    api_diagnosis["viability"]["recommended_strategies"] = ["API REST directa"]
    assert ExtractorFactory.select(api_diagnosis) == "api"

    dynamic_diagnosis = _diagnosis("https://example.com")
    dynamic_diagnosis["technologies"]["render_type"] = "dynamic_probable"
    dynamic_diagnosis["viability"]["recommended_strategies"] = ["Selenium (dinámico)"]
    assert ExtractorFactory.select(dynamic_diagnosis) == "selenium"


def test_service_reports_empty_selection(local_server: str, tmp_path: Path) -> None:
    from src.config.settings import PathConfig

    target = AnalysisTarget(url=local_server, allow_private_network=True)
    service = ExtractionService(ProjectConfig(paths=PathConfig(base_dir=tmp_path)))
    summary = service.run(
        target, diagnosis=_diagnosis(local_server), strategy="requests", max_pages=1,
        schema_config={"base_selector": ".inexistente", "fields": [
            {"name": "nombre", "selector": ".nombre"}]},
        formats=["json"],
    )
    assert summary["status"] == "error"
    assert summary["raw_records"] == 0
    assert "No se seleccionaron registros" in summary["errors"][-1]
    assert summary["outputs"] == {}
    assert "No se seleccionaron registros" in Path(summary["report_files"]["txt"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("strategy,expects_fallback", [("selenium", False), ("auto", True), (None, True)])
def test_selenium_fallback_is_automatic_only(strategy, expects_fallback, tmp_path: Path) -> None:
    from src.config.settings import PathConfig

    target = AnalysisTarget(url="https://example.com")
    service = ExtractionService(ProjectConfig(paths=PathConfig(base_dir=tmp_path)))
    selenium = MagicMock(method="selenium")
    selenium.is_applicable.return_value = True
    selenium.extract.return_value = ExtractionBatch(method="selenium", errors=["Chrome no disponible"])
    requests = MagicMock(method="requests")
    requests.extract.return_value = ExtractionBatch(
        method="requests", pages=[PageSnapshot(url=target.url, html="<html></html>")]
    )
    with patch.object(ExtractorFactory, "create_for_diagnosis", return_value=selenium), \
         patch.object(ExtractorFactory, "create", return_value=requests) as create:
        batch = service._acquire(target, _diagnosis(target.url), {}, strategy)
    assert create.called is expects_fallback
    assert batch.method == ("requests" if expects_fallback else "selenium")
    assert any("Chrome no disponible" in error for error in batch.errors)
    if expects_fallback:
        assert "requests" in batch.errors[0]


def test_selenium_block_does_not_trigger_fallback(tmp_path: Path) -> None:
    from src.config.settings import PathConfig

    target = AnalysisTarget(url="https://example.com")
    service = ExtractionService(ProjectConfig(paths=PathConfig(base_dir=tmp_path)))
    selenium = MagicMock(method="selenium")
    selenium.is_applicable.return_value = True
    selenium.extract.return_value = ExtractionBatch(method="selenium", errors=["URL bloqueada por SSRF"])
    with patch.object(ExtractorFactory, "create_for_diagnosis", return_value=selenium), \
         patch.object(ExtractorFactory, "create") as create:
        batch = service._acquire(target, _diagnosis(target.url), {}, "auto")
    create.assert_not_called()
    assert batch.errors == ["URL bloqueada por SSRF"]


def test_requests_extractor_pagination_local(local_server: str) -> None:
    extractor = RequestsExtractor(allow_private_network=True)
    batch = extractor.extract(
        f"{local_server}/",
        {"diagnosis": _diagnosis(local_server), "max_pages": 2, "delay_s": 0},
    )
    assert batch.errors == []
    assert len(batch.pages) == 2
    assert [p.page_number for p in batch.pages] == [1, 2]
    assert all(p.content_format == "html" for p in batch.pages)
    assert "/page2.html" in batch.pages[1].url


def test_requests_extractor_parses_structured_json(local_server: str) -> None:
    diagnosis = _diagnosis(f"{local_server}/api/items")
    diagnosis["acquisition_mechanisms"]["content_format"] = "json"
    extractor = RequestsExtractor(allow_private_network=True)
    batch = extractor.extract(
        f"{local_server}/api/items",
        {"diagnosis": diagnosis, "max_pages": 1, "delay_s": 0},
    )
    assert batch.errors == []
    assert batch.pages[0].content_format == "json"
    assert batch.pages[0].data == {"data": [{"id": 1, "name": "Item 1"}]}


def test_api_extractor_local_json(local_server: str) -> None:
    extractor = ApiExtractor(allow_private_network=True)
    batch = extractor.extract(
        f"{local_server}/api/items",
        {
            "diagnosis": _diagnosis(local_server),
            "endpoints": [f"{local_server}/api/items"],
            "api_page_param": "page",
            "max_pages": 2,
            "delay_s": 0,
        },
    )
    assert batch.errors == []
    assert len(batch.pages) == 2
    assert batch.pages[0].data == [{"id": 1, "name": "Item 1"}]
    assert batch.pages[1].data == [{"id": 2, "name": "Item 2"}]


@pytest.mark.parametrize("strategy", ["auto", "api"])
@pytest.mark.parametrize("observed", [[], ["http://other.example/api"]])
def test_service_extracts_direct_json_without_observed_endpoints(
    local_server: str, tmp_path: Path, strategy: str, observed: list[str]
) -> None:
    from src.config.settings import PathConfig

    url = f"{local_server}/api/items"
    target = AnalysisTarget(url=url, allow_private_network=True)
    diagnosis = _diagnosis(url)
    diagnosis["acquisition_mechanisms"]["content_format"] = "json"
    diagnosis["viability"]["recommended_strategies"] = [
        "Consumo directo de formato estructurado (JSON)"
    ]
    diagnosis["network"]["endpoints_observed"] = observed
    config = ProjectConfig(paths=PathConfig(base_dir=tmp_path))
    summary = ExtractionService(config).run(
        target, diagnosis=diagnosis, strategy=strategy, max_pages=3,
        delay_s=0, formats=["json"],
    )
    assert summary["status"] == "ok", summary["errors"]
    assert summary["extraction_config"]["strategy"] == "api"
    assert summary["raw_records"] == 1
    assert len(summary["pages"]) == 1
    records = json.loads(Path(summary["outputs"]["json"]["raw"]).read_text(encoding="utf-8"))
    assert records[0]["name"] == "Item 1"


def test_api_stops_when_page_parameter_is_ignored(local_server: str) -> None:
    extractor = ApiExtractor(allow_private_network=True)
    batch = extractor.extract(
        f"{local_server}/api/items?page=1",
        {"diagnosis": _diagnosis(local_server), "api_page_param": "ignored",
         "max_pages": 4, "delay_s": 0},
    )
    assert batch.errors == []
    assert len(batch.pages) == 1


def test_selenium_extractor_handles_infinite_scroll(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.extractor.selenium_extractor import SeleniumExtractor

    class FakeDomInspector:
        """Simula el DOM ya renderizado sin iniciar Chrome."""

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self._html = [PAGE_1, PAGE_2]
            self._scrolls = [True, False]

        def __enter__(self) -> "FakeDomInspector":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def inspect(self, url: str, **_kwargs: object) -> dict:
            return {"final_url": url, "error": None, "ssrf_blocked": False}

        def get_rendered_html(self) -> str:
            return self._html.pop(0) if self._html else PAGE_2

        def scroll_to_bottom(self, wait_s: float = 0.6) -> bool:
            return self._scrolls.pop(0) if self._scrolls else False

    monkeypatch.setattr(
        "src.extractor.selenium_extractor.DomInspector", FakeDomInspector
    )
    diagnosis = _diagnosis("https://example.com")
    diagnosis["technologies"]["render_type"] = "dynamic_probable"
    extractor = SeleniumExtractor()

    assert extractor.is_applicable(diagnosis)
    batch = extractor.extract(
        "https://example.com",
        {
            "diagnosis": diagnosis,
            "max_pages": 2,
            "delay_s": 0,
            "infinite_scroll": True,
        },
    )
    assert batch.errors == []
    assert len(batch.pages) == 2
    assert all(page.method == "selenium" for page in batch.pages)
    assert batch.metadata["infinite_scroll"] is True


def test_extractors_stop_on_blocking_barrier() -> None:
    from src.extractor.base import ExtractionBlockedError

    diagnosis = _diagnosis(
        "https://example.com",
        protection={"blocking_barrier": True, "restrictions": ["CAPTCHA detectado"]},
    )
    with pytest.raises(ExtractionBlockedError):
        RequestsExtractor().extract(
            "https://example.com", {"diagnosis": diagnosis, "max_pages": 1}
        )


# ---------------------------------------------------------------------------
# M4 — Esquemas y selectores
# ---------------------------------------------------------------------------


def test_schema_applies_css_and_attribute_selectors(local_server: str) -> None:
    schema = ExtractionSchema.from_dict(SCHEMA_CONFIG)
    assert schema.validate() == []
    records = schema.apply(
        PAGE_1,
        content_format="html",
        base_url=f"{local_server}/",
        page_url=f"{local_server}/",
        method="requests",
        page_number=1,
    )
    assert len(records) == 2
    assert records[0]["titulo"] == "Libro A"
    assert records[0]["precio"] == "$ 10.50"
    assert records[0]["url"] == f"{local_server}/libro-a"
    assert records[0]["_source"]["method"] == "requests"
    assert records[0]["_field_meta"]["titulo"]["transform"] == "strip"


def test_schema_applies_json_path() -> None:
    schema = ExtractionSchema(
        name="api",
        base_selector="data",
        fields=[
            FieldSelector("id", "id", "json_path"),
            FieldSelector("nombre", "name", "json_path"),
        ],
    )
    records = schema.apply(
        {"data": [{"id": 1, "name": "Uno"}, {"id": 2, "name": "Dos"}]},
        content_format="json",
    )
    assert records == [
        {"id": 1, "nombre": "Uno", "_source": {"url": "", "page_number": 1, "method": "requests", "acquired_at": ""}, "_field_meta": {
            "id": {"selector": "id", "selector_type": "json_path", "attribute": "text", "transform": "", "required": False},
            "nombre": {"selector": "name", "selector_type": "json_path", "attribute": "text", "transform": "", "required": False},
        }},
        {"id": 2, "nombre": "Dos", "_source": {"url": "", "page_number": 1, "method": "requests", "acquired_at": ""}, "_field_meta": {
            "id": {"selector": "id", "selector_type": "json_path", "attribute": "text", "transform": "", "required": False},
            "nombre": {"selector": "name", "selector_type": "json_path", "attribute": "text", "transform": "", "required": False},
        }},
    ]


def test_schema_supports_xpath_and_regex_selectors() -> None:
    schema = ExtractionSchema(
        name="selectores_avanzados",
        base_selector="div.product",
        fields=[
            FieldSelector("titulo", "//span[@class='title']/text()", "xpath"),
            FieldSelector("precio", r"\$\s*([\d.]+)", "regex"),
        ],
    )
    records = schema.apply(PAGE_1, content_format="html")
    assert records[0]["titulo"] == "Libro A"
    assert records[0]["precio"] == "10.50"


def test_schema_builder_infers_fields_from_sample_html(local_server: str) -> None:
    from src.extractor.base import PageSnapshot

    page = PageSnapshot(
        url=f"{local_server}/",
        html=PAGE_1,
        content_format="html",
        method="requests",
    )
    schema = SchemaBuilder(_diagnosis(local_server), sample_page=page).build()
    assert schema.base_selector == "div.product"
    assert {"title", "link"} <= {f.name for f in schema.fields}


# ---------------------------------------------------------------------------
# M5 — Transformación, calidad y linaje
# ---------------------------------------------------------------------------


def test_transformation_pipeline_adds_full_lineage_and_quality() -> None:
    records = [
        {
            "titulo": "  Producto  ",
            "precio": "$ 10.50",
            "_source": {
                "url": "https://example.com/p1",
                "method": "requests",
                "acquired_at": "2026-11-17T12:00:00",
                "page_number": 1,
            },
            "_field_meta": {"titulo": {}, "precio": {}},
        },
        {
            "titulo": "  Producto  ",
            "precio": "$ 10.50",
            "_source": {"url": "https://example.com/p2", "method": "requests"},
            "_field_meta": {"titulo": {}, "precio": {}},
        },
        {
            "titulo": "   ",
            "precio": None,
            "_source": {"url": "https://example.com/p3", "method": "requests"},
            "_field_meta": {"titulo": {}},
        },
    ]
    pipeline = (
        TransformationPipeline()
        .add(DataCleaner())
        .add(Normalizer(field_transforms={"precio": "number"}))
        .add(Validator(["titulo"]))
        .add(Deduplicator(["titulo"]))
        .add(LineageEnricher(execution_id="exec-123", default_source_url="https://example.com"))
    )
    processed = pipeline.run(records)

    assert len(processed) == 2
    expected_keys = {
        "source_url",
        "acquisition_date",
        "extraction_method",
        "source_field",
        "validation_status",
        "applied_transformations",
        "execution_id",
    }
    for record in processed:
        assert set(record["_lineage"]) == expected_keys
        assert record["_lineage"]["execution_id"] == "exec-123"
    assert processed[0]["titulo"] == "Producto"
    assert processed[0]["precio"] == 10.5
    assert pipeline.last_report["duplicates_removed"] == 1
    assert pipeline.last_report["valid_records"] == 1
    assert pipeline.last_report["invalid_records"] == 1


# ---------------------------------------------------------------------------
# M6 — Almacenamiento
# ---------------------------------------------------------------------------


def test_storage_formats_save_raw_and_processed(tmp_path: Path) -> None:
    data = [
        {
            "titulo": "Libro A",
            "precio": 10.5,
            "_lineage": {
                "source_url": "https://example.com",
                "acquisition_date": "2026-11-17T12:00:00",
                "extraction_method": "requests",
                "source_field": "titulo, precio",
                "validation_status": "valido",
                "applied_transformations": ["clean", "validate"],
                "execution_id": "exec-1",
            },
        }
    ]
    for fmt in ("json", "csv", "excel", "sqlite"):
        storage = StorageFactory.create(fmt, tmp_path)  # type: ignore[arg-type]
        raw = storage.save(data, "raw", table="raw", database="test")
        processed = storage.save(data, "processed", table="processed", database="test")
        assert Path(raw).exists()
        assert Path(processed).exists()

    with sqlite3.connect(tmp_path / "test.sqlite") as connection:
        rows = connection.execute("SELECT titulo FROM processed").fetchall()
    assert rows == [("Libro A",)]

    csv_text = (tmp_path / "processed.csv").read_text(encoding="utf-8-sig")
    assert "titulo" in csv_text and "_lineage" in csv_text



# ---------------------------------------------------------------------------
# Servicio completo y reportes
# ---------------------------------------------------------------------------


def test_extraction_service_end_to_end_local(local_server: str, tmp_path: Path) -> None:
    config = ProjectConfig()
    config.extraction.delay_s = 0.0
    target = AnalysisTarget(
        url=f"{local_server}/",
        label="Catálogo local",
        allow_private_network=True,
        respect_robots_txt=True,
    )
    service = ExtractionService(config)
    summary = service.run(
        target,
        diagnosis=_diagnosis(f"{local_server}/"),
        schema_config=SCHEMA_CONFIG,
        strategy="requests",
        max_pages=2,
        max_records=10,
        delay_s=0,
        formats=["json", "csv"],
        output_dir=tmp_path / "data",
    )

    assert summary["status"] == "ok", summary["errors"]
    assert summary["raw_records"] == 4
    assert summary["processed_records"] == 4
    assert summary["lineage_coverage"]["records_with_lineage"] == 4
    assert summary["quality"]["valid_records"] == 4

    raw_path = Path(summary["outputs"]["json"]["raw"])
    processed_path = Path(summary["outputs"]["json"]["processed"])
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    processed = json.loads(processed_path.read_text(encoding="utf-8"))
    assert "_lineage" not in raw[0]
    assert "_lineage" in processed[0]
    assert processed[0]["_lineage"]["extraction_method"] == "requests"

    report_json = Path(summary["report_files"]["json"])
    report_txt = Path(summary["report_files"]["txt"])
    assert report_json.exists() and report_txt.exists()
    assert "REPORTE DE EXTRACCIÓN" in report_txt.read_text(encoding="utf-8")


def test_extraction_service_blocks_on_barrier_and_robots(
    local_server: str, tmp_path: Path
) -> None:
    target = AnalysisTarget(url=f"{local_server}/", allow_private_network=True)
    service = ExtractionService(ProjectConfig())

    blocked = service.run(
        target,
        diagnosis=_diagnosis(
            f"{local_server}/",
            protection={"blocking_barrier": True, "restrictions": ["CAPTCHA"]},
        ),
        output_dir=tmp_path / "data",
    )
    assert blocked["status"] == "blocked"
    assert blocked["outputs"] == {}
    assert Path(blocked["report_files"]["json"]).exists()

    robots_blocked = service.run(
        target,
        diagnosis=_diagnosis(
            f"{local_server}/",
            protection={"robots_crawl_allowed": False},
        ),
        output_dir=tmp_path / "data",
    )
    assert robots_blocked["status"] == "blocked"
    assert "robots.txt" in robots_blocked["errors"][0]


def test_extraction_reporter_writes_sanitized_files(tmp_path: Path) -> None:
    reporter = ExtractionReporter(tmp_path)
    paths = reporter.generate(
        {
            "url": "https://user:token@example.com/path?api_key=secret",
            "domain": "example.com",
            "status": "ok",
            "outputs": {"json": {"raw": "/tmp/raw.json", "processed": "/tmp/p.json"}},
            "errors": [],
        }
    )
    json_text = paths["json"].read_text(encoding="utf-8")
    txt_text = paths["txt"].read_text(encoding="utf-8")
    assert "secret" not in json_text
    assert "token" not in json_text
    assert "REPORTE DE EXTRACCIÓN" in txt_text
