"""
Tests — Módulo 2: DomInspector y validación real de Selenium.

Separa explícitamente:
- Pruebas unitarias con WebDriver simulado (mocks).
- Pruebas reales con Chrome headless contra páginas locales controladas
  (marcadas con @pytest.mark.selenium).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from selenium.common.exceptions import TimeoutException, WebDriverException

from src.config.settings import SeleniumConfig
from src.inspector.dom_inspector import DomInspector


# ---------------------------------------------------------------------------
# Pruebas unitarias (sin navegador real)
# ---------------------------------------------------------------------------


class TestDomInspectorUnit:
    """Pruebas unitarias de DomInspector mediante mocks."""

    def test_requires_context_manager_or_driver(self) -> None:
        inspector = DomInspector()
        res = inspector.inspect("https://example.com")
        assert res["selenium_available"] is False
        assert "no inicializado" in res["error"].lower()

    def test_blocks_ssrf_before_starting_navigation(self) -> None:
        inspector = DomInspector()
        mock_driver = MagicMock()
        inspector._driver = mock_driver
        res = inspector.inspect("http://127.0.0.1:8080/admin")
        assert res["ssrf_blocked"] is True
        mock_driver.get.assert_not_called()

    def test_blocks_ssrf_post_navigation_redirect(self) -> None:
        inspector = DomInspector(allow_private_network=False, wait_after_load_s=0)
        mock_driver = MagicMock()
        mock_driver.current_url = "http://169.254.169.254/latest/meta-data/"
        inspector._driver = mock_driver

        with (
            patch(
                "src.inspector.dom_inspector.validate_url_security",
                side_effect=[(True, "ok"), (False, "Link-local bloqueado")],
            ),
            patch("src.inspector.dom_inspector.WebDriverWait") as mock_wait,
        ):
            mock_wait.return_value.until.return_value = True
            res = inspector.inspect("https://example.com")

        assert res["ssrf_blocked"] is True
        mock_driver.get.assert_any_call("about:blank")

    def test_closes_driver_and_service_even_on_error(self) -> None:
        mock_driver = MagicMock()
        mock_service = MagicMock()
        mock_driver.get.side_effect = WebDriverException("fallo simulado")

        inspector = DomInspector(allow_private_network=True, wait_after_load_s=0)
        with patch.object(inspector, "_create_driver", return_value=mock_driver):
            with inspector as dom:
                dom._service = mock_service
                res = dom.inspect("http://127.0.0.1:8000/")
                assert res["selenium_available"] is False
                assert "error" in res and res["error"] is not None

        mock_driver.quit.assert_called_once()
        mock_service.stop.assert_called_once()
        assert inspector._driver is None
        assert inspector._service is None

    def test_handles_timeout_exception_cleanly(self) -> None:
        mock_driver = MagicMock()
        mock_driver.get.side_effect = TimeoutException("timeout simulado")

        inspector = DomInspector(allow_private_network=True, wait_after_load_s=0)
        with patch.object(inspector, "_create_driver", return_value=mock_driver):
            with inspector as dom:
                res = dom.inspect("http://127.0.0.1:8000/")

        assert res["selenium_available"] is False
        assert "timeout" in res["error"].lower()
        mock_driver.quit.assert_called_once()

    def test_network_log_classification_and_sanitization(self) -> None:
        """Verifica clasificación de XHR/Fetch, método, status, Content-Type y sanitización."""
        perf_logs = [
            {
                "message": json.dumps({
                    "message": {
                        "method": "Network.requestWillBeSent",
                        "params": {
                            "requestId": "req-1",
                            "type": "Fetch",
                            "request": {
                                "url": "https://example.com/api/v1/products?token=SECRET123&page=1",
                                "method": "GET",
                            },
                        },
                    }
                })
            },
            {
                "message": json.dumps({
                    "message": {
                        "method": "Network.responseReceived",
                        "params": {
                            "requestId": "req-1",
                            "response": {
                                "status": 200,
                                "mimeType": "application/json",
                            },
                        },
                    }
                })
            },
            {
                "message": json.dumps({
                    "message": {
                        "method": "Network.requestWillBeSent",
                        "params": {
                            "requestId": "req-2",
                            "type": "XHR",
                            "request": {
                                "url": "https://www.google-analytics.com/collect?v=1",
                                "method": "POST",
                            },
                        },
                    }
                })
            },
            {
                "message": json.dumps({
                    "message": {
                        "method": "Network.requestWillBeSent",
                        "params": {
                            "requestId": "req-3",
                            "type": "Fetch",
                            "request": {
                                "url": "https://example.com/graphql",
                                "method": "POST",
                                "postData": '{"query":"{ items { id } }","operationName":"GetItems"}',
                            },
                        },
                    }
                })
            },
        ]

        mock_driver = MagicMock()
        mock_driver.get_log.return_value = perf_logs
        mock_driver.execute_script.return_value = []

        inspector = DomInspector(allow_private_network=True, wait_after_load_s=0)
        inspector._driver = mock_driver
        result: dict[str, Any] = {}
        inspector._analyze_network_logs(result)

        assert result["xhr_fetch_detected"] is True
        assert result["rest_api_detected"] is True
        assert result["graphql_detected"] is True
        assert "GET" in result["http_methods_observed"]
        assert "POST" in result["http_methods_observed"]
        assert "application/json" in result["content_types_observed"]

        # Verificar sanitización del token en la URL capturada
        joined_urls = " ".join(result["endpoints_observed"])
        assert "SECRET123" not in joined_urls
        assert "page=1" in joined_urls

        # Verificar que analytics se clasifica como telemetría y API como datos
        reqs = {r["url"]: r for r in result["network_requests"]}
        api_entry = next(v for k, v in reqs.items() if "/api/v1/products" in k)
        assert api_entry["status_code"] == 200
        assert api_entry["possible_data_relation"] == "probable_api_datos"
        ga_entry = next(v for k, v in reqs.items() if "google-analytics" in k)
        assert ga_entry["possible_data_relation"] == "telemetria_o_recurso"


# ---------------------------------------------------------------------------
# Pruebas con Chrome real sobre páginas locales controladas
# ---------------------------------------------------------------------------


def _start_test_server() -> tuple[HTTPServer, int]:
    class ControlledHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/api/v1/catalog":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"items": ["Item A", "Item B", "Item C"]}')
                return

            if self.path == "/dynamic":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                html = """<!DOCTYPE html>
<html>
<head><title>Dynamic Controlled Page</title></head>
<body>
  <div id="app">Cargando...</div>
  <nav id="dyn-nav"></nav>
  <script>
    window.jQuery = { fn: { jquery: '3.7.1' } };
    fetch('/api/v1/catalog')
      .then(r => r.json())
      .then(data => {
        const container = document.getElementById('app');
        container.innerHTML = '<ul>' + data.items.map(i => '<li>' + i + '</li>').join('') + '</ul>'
          + '<p>Contenido generado dinamicamente mediante JavaScript para pruebas controladas.</p>';
        document.getElementById('dyn-nav').innerHTML =
          '<ul class="pagination"><li class="page-item"><a class="page-link" href="?page=2" rel="next">Siguiente</a></li></ul>';
      });
  </script>
</body>
</html>"""
                self.wfile.write(html.encode("utf-8"))
                return

            if self.path == "/scroll":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                html = """<!DOCTYPE html>
<html>
<body>
  <div id="feed" style="min-height: 1800px;">
    <p>Elemento inicial 1</p>
  </div>
  <script>
    let loaded = false;
    window.addEventListener('scroll', () => {
      if (!loaded) {
        loaded = true;
        const extra = document.createElement('div');
        extra.style.height = '1200px';
        extra.innerHTML = '<p>Nuevo bloque cargado al hacer scroll infinito controlado.</p>';
        document.getElementById('feed').appendChild(extra);
      }
    });
  </script>
</body>
</html>"""
                self.wfile.write(html.encode("utf-8"))
                return

            if self.path == "/captcha":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                html = """<!DOCTYPE html>
<html>
<body>
  <div id="challenge"></div>
  <script>
    document.getElementById('challenge').innerHTML =
      '<div class="g-recaptcha" data-sitekey="test">CAPTCHA simulado</div>';
  </script>
</body>
</html>"""
                self.wfile.write(html.encode("utf-8"))
                return

            self.send_response(404)
            self.end_headers()

        def log_message(self, *_: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), ControlledHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


@pytest.fixture(scope="module")
def controlled_server():
    server, port = _start_test_server()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


@pytest.mark.selenium
class TestDomInspectorControlledChrome:
    """Pruebas reales de Selenium + Chrome headless contra servidor local controlado."""

    def test_dynamic_js_dom_network_and_pagination(self, controlled_server: str) -> None:
        cfg = SeleniumConfig(headless=True, implicit_wait=0, page_load_timeout=15)
        static_html = "<html><body><div id='app'>Cargando...</div></body></html>"

        with DomInspector(cfg, allow_private_network=True, wait_after_load_s=1.0) as dom:
            res = dom.inspect(
                f"{controlled_server}/dynamic",
                static_html=static_html,
                static_text_size=11,
            )

        assert res["selenium_available"] is True
        assert res["error"] is None
        assert res["dynamic_dom_size"] > len(static_html)
        assert res["dynamic_text_size"] > 40
        assert res["dom_difference"] is not None
        assert res["js_generated_content"] is True
        assert "jQuery" in res["js_frameworks_confirmed"]
        assert res["xhr_fetch_detected"] is True
        assert res["rest_api_detected"] is True
        assert any("/api/v1/catalog" in ep for ep in res["endpoints_observed"])
        assert res["pagination_dom"]["detected"] is True
        assert res["page_load_time_s"] is not None and res["page_load_time_s"] > 0

    def test_infinite_scroll_detection(self, controlled_server: str) -> None:
        cfg = SeleniumConfig(headless=True, implicit_wait=0, page_load_timeout=15)
        with DomInspector(cfg, allow_private_network=True, wait_after_load_s=0.5) as dom:
            res = dom.inspect(f"{controlled_server}/scroll")

        assert res["selenium_available"] is True
        assert res["infinite_scroll_confirmed"] is True

    def test_captcha_detection_in_js_rendered_dom(self, controlled_server: str) -> None:
        cfg = SeleniumConfig(headless=True, implicit_wait=0, page_load_timeout=15)
        with DomInspector(cfg, allow_private_network=True, wait_after_load_s=0.5) as dom:
            res = dom.inspect(f"{controlled_server}/captcha")

        assert res["selenium_available"] is True
        assert res["captcha_detected"] is True
        assert "reCAPTCHA" in (res["captcha_type"] or "")
