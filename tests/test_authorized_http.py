"""Pruebas de autenticación contra servidores locales controlados."""
import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.config.settings import PathConfig, ProjectConfig, RequestConfig, register_target
from src.extractor._http_utils import HttpClient
from src.extractor.extraction_service import ExtractionService
from src.inspector.http_inspector import HttpInspector
from src.utils.auth import authorization


@pytest.fixture
def servers():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.server.server_port, self.path, self.headers.get("Authorization")))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{other.server_port}/destination")
                self.end_headers()
            elif self.path == "/robots.txt":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"User-agent: *\nDisallow: /blocked\n")
            elif self.path == "/protected" and not self.headers.get("Authorization"):
                self.send_response(401)
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body>ok</body></html>")
        def log_message(self, *args):
            pass
    first = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    other = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    for server in (first, other):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{first.server_port}", calls
    for server in (first, other):
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("mode", ["basic", "bearer"])
def test_authorized_inspection_and_cross_origin_redirect(monkeypatch, servers, mode):
    base, calls = servers
    monkeypatch.setenv("EXTRACTION_AUTH_USERNAME", "usuario")
    monkeypatch.setenv("EXTRACTION_AUTH_PASSWORD", "secreto")
    monkeypatch.setenv("EXTRACTION_AUTH_TOKEN", "token-secreto")
    target = register_target(base + "/protected", allow_private_network=True, auth_mode=mode)
    cfg = RequestConfig(timeout=2, max_retries=1)
    inspector = HttpInspector(cfg, allow_private_network=True, auth_mode=target.auth_mode)
    result = inspector.inspect(target.url)
    inspector.close()
    expected = ("Basic " + base64.b64encode(b"usuario:secreto").decode()
                if mode == "basic" else "Bearer token-secreto")
    assert result["status_code"] == 200
    assert calls[0][2] == expected
    assert next(call for call in calls if call[1] == "/robots.txt")[2] is None
    assert "secreto" not in str(result)
    inspector = HttpInspector(cfg, allow_private_network=True, auth_mode=mode)
    try:
        redirected = inspector.inspect(base + "/redirect")
    finally:
        inspector.close()
    assert redirected["error_type"] == "cross_origin_auth_redirect"
    client = HttpClient(cfg, allow_private_network=True, auth_mode=mode, auth_origin=target.url)
    try:
        with pytest.raises(ValueError, match="fuera del origen"):
            client.get(base + "/redirect")
    finally:
        client.close()
    assert next(call for call in calls if call[1] == "/redirect")[2] == expected
    assert not any(call[1] == "/destination" for call in calls)


def test_credentials_required_and_exact_origin(monkeypatch):
    monkeypatch.delenv("EXTRACTION_AUTH_TOKEN", raising=False)
    with pytest.raises(ValueError):
        register_target("https://example.com", auth_mode="bearer")
    monkeypatch.setenv("EXTRACTION_AUTH_TOKEN", "secret")
    assert authorization("bearer", "https://example.com", "http://example.com") == {}
    assert authorization("bearer", "https://example.com", "https://example.com:444") == {}


def test_unauthorized_response_still_blocks(servers):
    base, _ = servers
    cfg = RequestConfig(timeout=2, max_retries=1)
    inspector = HttpInspector(cfg, allow_private_network=True)
    try:
        assert inspector.inspect(base + "/protected")["status_code"] == 401
    finally:
        inspector.close()
    client = HttpClient(cfg, allow_private_network=True)
    try:
        with pytest.raises(ValueError, match="HTTP 401"):
            client.get(base + "/protected")
    finally:
        client.close()


def test_authenticated_full_extraction(monkeypatch, servers, tmp_path):
    base, calls = servers
    monkeypatch.setenv("EXTRACTION_AUTH_TOKEN", "token-secreto")
    target = register_target(base + "/protected", allow_private_network=True, auth_mode="bearer")
    config = ProjectConfig(paths=PathConfig(base_dir=tmp_path))
    config.request = RequestConfig(timeout=2, max_retries=1)
    summary = ExtractionService(config).run(
        target, use_selenium=False, strategy="requests", max_pages=1,
        schema_config={"base_selector": "body", "fields": [
            {"name": "text", "selector": "body", "attribute": "text"}]},
        formats=["json"], output_dir=tmp_path / "data")
    assert summary["status"] == "ok", summary["errors"]
    assert summary["raw_records"] == 1
    assert sum(call[2] == "Bearer token-secreto" for call in calls) >= 2
    assert "token-secreto" not in str(summary)
    for path in (tmp_path / "reports").glob("*"):
        assert "token-secreto" not in path.read_text(encoding="utf-8")
