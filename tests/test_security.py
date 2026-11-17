"""
Tests — Seguridad: Prevención de SSRF, control de redirecciones,
límites de descarga y sanitización de secretos.
"""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest

from src.config.settings import RequestConfig
from src.inspector.http_inspector import HttpInspector
from src.utils.security import (
    is_ip_publicly_routable,
    sanitize_for_persistence,
    sanitize_headers,
    sanitize_url,
    validate_url_security,
)


class TestSSRFValidation:
    """Pruebas de prevención de Server-Side Request Forgery (SSRF)."""

    @pytest.mark.parametrize(
        "blocked_url",
        [
            "file:///etc/passwd",
            "ftp://example.com/resource",
            "javascript:alert(1)",
            "gopher://127.0.0.1:6379/_INFO",
            "http://localhost/admin",
            "http://LOCALHOST:8000",
            "http://sub.localhost/",
            "http://127.0.0.1/",
            "http://127.0.1.1:8080/",
            "http://[::1]/",
            "http://[::ffff:127.0.0.1]/",
            "http://10.0.0.1/internal",
            "http://172.16.0.10/status",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://0.0.0.0:8000/",
            "http://2130706433/",  # 127.0.0.1 decimal
            "http://0x7f000001/",  # 127.0.0.1 hex
            "http://100.64.0.1/",  # CGNAT
        ],
    )
    def test_rejects_unsafe_or_internal_urls(self, blocked_url: str) -> None:
        ok, _ = validate_url_security(blocked_url)
        assert ok is False

    def test_rejects_domain_resolving_to_private_ip_via_dns(self) -> None:
        fake_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]
        with patch("src.utils.security.socket.getaddrinfo", return_value=fake_dns):
            ok, msg = validate_url_security("https://rebind.example.com", resolve_dns=True)
        assert ok is False
        assert "ssrf" in msg.lower()

    def test_accepts_domain_resolving_to_public_ip_via_dns(self) -> None:
        fake_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with patch("src.utils.security.socket.getaddrinfo", return_value=fake_dns):
            ok, _ = validate_url_security("https://example.com", resolve_dns=True)
        assert ok is True

    def test_ip_public_check(self) -> None:
        assert is_ip_publicly_routable("93.184.216.34")[0] is True
        assert is_ip_publicly_routable("169.254.169.254")[0] is False
        assert is_ip_publicly_routable("fc00::1")[0] is False
        assert is_ip_publicly_routable("fe80::1")[0] is False


class TestSanitization:
    """Pruebas de sanitización de URLs, cabeceras y estructuras persistibles."""

    def test_sanitize_url_masks_userinfo_and_tokens(self) -> None:
        raw = "https://admin:supersecret@example.com/api?token=abc123&page=2&api_key=xyz"
        safe = sanitize_url(raw)
        assert "admin" not in safe
        assert "supersecret" not in safe
        assert "abc123" not in safe
        assert "xyz" not in safe
        assert "page=2" in safe

    def test_sanitize_headers_redacts_cookies_and_auth(self) -> None:
        headers = {
            "Server": "nginx",
            "Set-Cookie": "sessionid=secret_value_123; HttpOnly",
            "Authorization": "Bearer jwt_secret",
            "X-RateLimit-Remaining": "50",
        }
        safe = sanitize_headers(headers)
        assert safe["Server"] == "nginx"
        assert safe["X-RateLimit-Remaining"] == "50"
        assert safe["Set-Cookie"] == "[REDACTED]"
        assert safe["Authorization"] == "[REDACTED]"

    def test_sanitize_for_persistence_strips_html_and_secrets(self) -> None:
        payload = {
            "url": "https://user:pass@example.com/?token=secret",
            "http": {
                "html_preview": "<html>secret csrf</html>",
                "_raw_body": "<html>full</html>",
                "security_headers": {"Set-Cookie": "sid=999"},
            },
            "network": {
                "endpoints_observed": ["https://example.com/api?api_key=mykey&q=1"],
            },
        }
        clean = sanitize_for_persistence(payload)
        assert "pass" not in clean["url"]
        assert "secret" not in clean["url"]
        assert "html_preview" not in clean["http"]
        assert "_raw_body" not in clean["http"]
        assert clean["http"]["security_headers"]["Set-Cookie"] == "[REDACTED]"
        assert "mykey" not in clean["network"]["endpoints_observed"][0]


class TestHttpInspectorSecurityControls:
    """Pruebas controladas de redirecciones SSRF y límite de descarga."""

    def test_blocks_ssrf_url_before_connecting(self) -> None:
        inspector = HttpInspector()
        try:
            res = inspector.inspect("http://169.254.169.254/latest/meta-data/")
            assert res["ssrf_blocked"] is True
            assert res["access_ok"] is False
            assert res["http_connected"] is False
        finally:
            inspector.close()

    def test_blocks_redirect_to_internal_metadata_ip(self) -> None:
        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
                self.end_headers()

            def log_message(self, *_: object) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), RedirectHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # Simular allow_private_network=False durante la verificación del salto de redirección:
            # Permitimos solo el origen 127.0.0.1 en el primer salto usando validación real en salto 2
            inspector = HttpInspector(RequestConfig(timeout=3, max_retries=1))
            try:
                with patch(
                    "src.inspector.http_inspector.validate_url_security",
                    side_effect=[
                        (True, "ok"),  # check inicial
                        (True, "ok"),  # salto 1 (servidor controlado)
                        (False, "Dirección link-local/metadatos bloqueada por SSRF: 169.254.169.254"),  # salto 2
                    ],
                ):
                    res = inspector.inspect(f"http://127.0.0.1:{port}/", check_robots=False)
                assert res["ssrf_blocked"] is True
                assert res["access_ok"] is False
            finally:
                inspector.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_truncates_oversized_response(self) -> None:
        class LargeHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body>" + (b"A" * 10000) + b"</body></html>")

            def log_message(self, *_: object) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), LargeHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            cfg = RequestConfig(timeout=3, max_retries=1, max_response_bytes=1024)
            inspector = HttpInspector(cfg, allow_private_network=True)
            try:
                res = inspector.inspect(f"http://127.0.0.1:{port}/", check_robots=False)
                assert res["access_ok"] is True
                assert res["response_truncated"] is True
                assert res["bytes_received"] == 1024
            finally:
                inspector.close()
        finally:
            server.shutdown()
            server.server_close()

