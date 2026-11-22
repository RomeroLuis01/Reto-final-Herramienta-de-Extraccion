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
        if self.path == "/datos.csv":
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
