"""
Tests — Módulo 2: Orquestador WebInspector (flujo integrado controlado).

Verifica la coordinación completa entre HttpInspector, HtmlAnalyzer,
ProtectionDetector, DomInspector, ViabilityEvaluator, ProfileManager
y DiagnosticReporter usando un servidor HTTP local controlado.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from src.config.settings import AnalysisTarget, ProjectConfig
from src.inspector.web_inspector import WebInspector
from src.monitor.reporter import DiagnosticReporter
from src.utils.profiles import ProfileManager


def _start_web_fixture_server() -> tuple[HTTPServer, int]:
    class FixtureHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/robots.txt":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"User-agent: *\nDisallow: /private/\n")
                return

            if path == "/private/data":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body><p>Privado</p></body></html>")
                return

            if path == "/blocked":
                self.send_response(403)
                self.send_header("Content-Type", "text/html")
                self.send_header("Cf-Ray", "test-cf-123")
                self.end_headers()
                self.wfile.write(b"<html><body>Forbidden</body></html>")
                return

            if path == "/api/items.json":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"items": [{"id": 1}, {"id": 2}]}')
                return

            if path == "/static":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Set-Cookie", "sessionid=SUPER_SECRET_COOKIE; HttpOnly")
                self.end_headers()
                self.wfile.write(
                    b"<!DOCTYPE html><html><head><title>Tienda Demo</title></head>"
                    b"<body><h1>Catalogo</h1><table id='productos'><tr><th>Item</th></tr>"
                    b"<tr><td>Libro 1</td></tr></table>"
                    b"<nav class='pagination'><a href='?page=2' rel='next'>2</a></nav>"
                    b"</body></html>"
                )
                return

            self.send_response(404)
            self.end_headers()

        def log_message(self, *_: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), FixtureHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


@pytest.fixture(scope="module")
def fixture_url():
    server, port = _start_web_fixture_server()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


class TestWebInspectorControlled:
    """Pruebas integradas del orquestador WebInspector."""

    def test_static_diagnosis_and_secret_redaction(
        self, fixture_url: str, tmp_path: Path
    ) -> None:
        target = AnalysisTarget(
            url=f"{fixture_url}/static?token=SECRET_TOKEN_XYZ",
            label="Sitio Demo",
            allow_private_network=True,
        )
        inspector = WebInspector(ProjectConfig())
        diagnosis = inspector.inspect(target, use_selenium=False)

        assert diagnosis["access_status"] == "accesible"
        assert diagnosis["http"]["status_code"] == 200
        assert "html_preview" not in diagnosis["http"]
        assert "SECRET_TOKEN_XYZ" not in json.dumps(diagnosis)
        assert "SUPER_SECRET_COOKIE" not in json.dumps(diagnosis)
        assert diagnosis["protection"]["session_management_detected"] is True
        assert diagnosis["viability"]["technical_feasibility"] == "viable"

        # Guardar perfil y reporte y comprobar ausencia de secretos
        pm = ProfileManager(tmp_path / "profiles")
        profile_path = pm.save(diagnosis)
        profile_text = profile_path.read_text(encoding="utf-8")
        assert "SECRET_TOKEN_XYZ" not in profile_text
        assert "SUPER_SECRET_COOKIE" not in profile_text

        reporter = DiagnosticReporter(tmp_path / "reports")
        paths = reporter.generate(diagnosis)
        report_txt = paths["txt"].read_text(encoding="utf-8")
        assert "CONFIRMADA" in report_txt
        assert "PROBABLE" in report_txt
        assert "NO IDENTIFICADA" in report_txt
        assert "SUPER_SECRET_COOKIE" not in report_txt

    def test_stops_on_http_403_and_does_not_recommend_scraping(
        self, fixture_url: str
    ) -> None:
        target = AnalysisTarget(
            url=f"{fixture_url}/blocked",
            allow_private_network=True,
        )
        inspector = WebInspector(ProjectConfig())
        diagnosis = inspector.inspect(target, use_selenium=True)

        assert diagnosis["access_status"] == "restringido_http_403"
        assert diagnosis["selenium_status"] == "halted_http_error"
        assert diagnosis["protection"]["access_blocked"] is True
        assert diagnosis["protection"]["waf_detected"] is True
        assert diagnosis["protection"]["waf_type"] == "Cloudflare"
        assert diagnosis["viability"]["technical_feasibility"] == "no_viable"
        assert diagnosis["viability"]["recommended_strategies"] == []

    def test_stops_selenium_when_robots_txt_disallows(
        self, fixture_url: str
    ) -> None:
        target = AnalysisTarget(
            url=f"{fixture_url}/private/data",
            respect_robots_txt=True,
            allow_private_network=True,
        )
        inspector = WebInspector(ProjectConfig())
        diagnosis = inspector.inspect(target, use_selenium=True)

        assert diagnosis["protection"]["robots_crawl_allowed"] is False
        assert diagnosis["selenium_status"] == "halted_by_protection"
        assert diagnosis["viability"]["recommended_strategies"] == []

    def test_direct_json_endpoint_detected_without_selenium(
        self, fixture_url: str
    ) -> None:
        target = AnalysisTarget(
            url=f"{fixture_url}/api/items.json",
            allow_private_network=True,
        )
        inspector = WebInspector(ProjectConfig())
        diagnosis = inspector.inspect(target, use_selenium=True)

        assert diagnosis["http"]["content_format"] == "json"
        assert diagnosis["selenium_status"] == "skipped_non_html"
        assert any("JSON" in s for s in diagnosis["viability"]["recommended_strategies"])

    @pytest.mark.selenium
    def test_full_end_to_end_with_selenium_propagates_dom_metrics(
        self, fixture_url: str, tmp_path: Path
    ) -> None:
        target = AnalysisTarget(
            url=f"{fixture_url}/static",
            label="E2E Demo",
            allow_private_network=True,
        )
        inspector = WebInspector(ProjectConfig())
        diagnosis = inspector.inspect(target, use_selenium=True)

        assert diagnosis["selenium_status"] == "executed"
        assert diagnosis["html_analysis"]["dynamic_dom_size"] is not None
        assert diagnosis["html_analysis"]["dom_difference"] is not None

        reporter = DiagnosticReporter(tmp_path)
        paths = reporter.generate(diagnosis)
        txt = paths["txt"].read_text(encoding="utf-8")
        assert "Tamaño DOM dinámico (bytes): None" not in txt
