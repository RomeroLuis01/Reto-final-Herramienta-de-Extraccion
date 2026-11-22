from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from src.config.settings import AnalysisTarget, PathConfig, ProjectConfig
from src.extractor.extraction_service import ExtractionService


class StructuredHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/namespaced.xml":
            body = ("<catalogo xmlns:p='https://example.org/schema/v1'>"
                    "<p:persona p:id='1'><p:nombre>Ana</p:nombre></p:persona>"
                    "<p:persona p:id='2'><p:nombre>Luis</p:nombre></p:persona>"
                    "</catalogo>").encode()
            content_type = "application/xml"
        elif self.path == "/datos.csv":
            body = "id,nombre\n1,Ana\n2,Luis\n".encode()
            content_type = "text/csv"
        else:
            body = ("<personas><persona id='1'><nombre>Ana</nombre></persona>"
                    "<persona id='2'><nombre>Luis</nombre></persona></personas>").encode()
            content_type = "application/xml"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def structured_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), StructuredHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


@pytest.mark.parametrize("extension", ["csv", "xml"])
def test_structured_source_full_flow(structured_server: str, tmp_path: Path, extension: str):
    url = f"{structured_server}/datos.{extension}"
    config = ProjectConfig(paths=PathConfig(base_dir=tmp_path))
    diagnosis = {
        "access_status": "accesible",
        "acquisition_mechanisms": {"content_format": extension},
        "protection": {},
        "viability": {},
    }
    summary = ExtractionService(config).run(
        AnalysisTarget(url=url, allow_private_network=True),
        diagnosis=diagnosis,
        strategy="requests",
        delay_s=0,
        formats=["json"],
        output_dir=tmp_path / "output",
    )
    assert summary["status"] == "ok", summary["errors"]
    assert summary["raw_records"] == 2
    records = json.loads(Path(summary["outputs"]["json"]["raw"]).read_text(encoding="utf-8"))
    assert [(r["id"], r["nombre"]) for r in records] == [("1", "Ana"), ("2", "Luis")]


def test_xml_namespaced_fields_full_extraction(structured_server, tmp_path):
    namespace = "{https://example.org/schema/v1}"
    url = f"{structured_server}/namespaced.xml"
    schema = {"name": "namespaced", "base_selector": "", "fields": [
        {"name": "id", "selector": namespace + "id", "selector_type": "json_path"},
        {"name": "nombre", "selector": namespace + "nombre", "selector_type": "json_path"},
    ]}
    config = ProjectConfig(paths=PathConfig(base_dir=tmp_path))
    diagnosis = {"access_status": "accesible", "protection": {}, "viability": {},
                 "acquisition_mechanisms": {"content_format": "xml"}}
    summary = ExtractionService(config).run(
        AnalysisTarget(url=url, allow_private_network=True), diagnosis=diagnosis,
        schema_config=schema, strategy="requests", delay_s=0, formats=["json"])
    assert summary["status"] == "ok", summary["errors"]
    raw = json.loads(Path(summary["outputs"]["json"]["raw"]).read_text(encoding="utf-8"))
    assert [(r["id"], r["nombre"]) for r in raw] == [("1", "Ana"), ("2", "Luis")]
    assert raw[0]["_field_meta"]["nombre"]["selector"] == namespace + "nombre"


def test_xml_repeated_children_attributes_and_nested_text():
    from src.extractor._http_utils import parse_body
    from src.selector.engines import json_lookup
    xml = ("<catalogo><item id='1'><tag>A</tag><tag>B</tag>"
           "<titulo lang='es'>Libro</titulo></item>"
           "<item id='2'><tag>C</tag><titulo lang='en'>Book</titulo></item></catalogo>")
    data = parse_body(xml, "xml")
    assert data[0]["id"] == "1"
    assert data[0]["tag"] == ["A", "B"]
    assert data[0]["titulo"] == {"lang": "es", "#text": "Libro"}
    assert json_lookup(data[0], "titulo.#text") == ["Libro"]
    assert json_lookup(data[0], "tag[*]") == ["A", "B"]


def test_namespaced_path_token_does_not_change_json_paths():
    from src.selector.engines import json_lookup
    uri = "{https://example.org/schema/v1}"
    assert json_lookup({uri + "child": {uri + "name": "valor"}},
                       uri + "child." + uri + "name") == ["valor"]
    assert json_lookup({"a.b": "literal", "a": {"b": "ruta"}}, "a.b") == ["ruta"]
    assert json_lookup({"items": [{"name": "Ana"}, {"name": "Luis"}]},
                       "items[*].name") == ["Ana", "Luis"]
