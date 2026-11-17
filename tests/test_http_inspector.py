"""
Tests — Módulo 2: Inspector HTTP.

Pruebas de integración ligeras con sites públicos reales
(books.toscrape.com — sitio creado específicamente para scraping).
Se marcan con pytest.mark.integration para poder excluirlas.
"""

import pytest
from src.inspector.http_inspector import HttpInspector
from src.config.settings import RequestConfig


@pytest.mark.integration
class TestHttpInspectorIntegration:
    """Tests de integración del inspector HTTP (requieren red)."""

    def test_accessible_site(self) -> None:
        inspector = HttpInspector()
        try:
            result = inspector.inspect("https://books.toscrape.com/")
            assert result["access_ok"] is True
            assert result["status_code"] == 200
            assert result["response_time_s"] is not None
            assert result["is_https"] is True
        finally:
            inspector.close()

    def test_https_flag(self) -> None:
        inspector = HttpInspector()
        try:
            result = inspector.inspect("https://books.toscrape.com/")
            assert result["is_https"] is True
        finally:
            inspector.close()

    def test_content_type_detected(self) -> None:
        inspector = HttpInspector()
        try:
            result = inspector.inspect("https://books.toscrape.com/")
            assert result["content_type"] is not None
            assert "text/html" in result["content_type"]
        finally:
            inspector.close()

    def test_robots_txt_checked(self) -> None:
        inspector = HttpInspector()
        try:
            result = inspector.inspect("https://books.toscrape.com/", check_robots=True)
            assert "robots_txt" in result
            assert isinstance(result["robots_txt"], dict)
        finally:
            inspector.close()

    def test_html_preview_returned(self) -> None:
        inspector = HttpInspector()
        try:
            result = inspector.inspect("https://books.toscrape.com/")
            assert "html_preview" in result
            assert len(result["html_preview"]) > 0
            assert "<html" in result["html_preview"].lower()
            assert "books to scrape" in result["html_preview"].lower()
        finally:
            inspector.close()

    def test_connection_error_handled(self) -> None:
        """Verifica manejo de URLs inaccesibles."""
        config = RequestConfig(timeout=3, max_retries=1)
        inspector = HttpInspector(config)
        try:
            result = inspector.inspect("https://this-domain-does-not-exist-xyz.com/")
            assert result["access_ok"] is False
            assert result["error"] is not None
        finally:
            inspector.close()


class TestHttpInspectorUnit:
    """Tests unitarios del inspector HTTP (sin red)."""

    def test_inspector_created(self) -> None:
        inspector = HttpInspector()
        assert inspector is not None
        inspector.close()

    def test_custom_config(self) -> None:
        config = RequestConfig(timeout=5, max_retries=1)
        inspector = HttpInspector(config)
        assert inspector._cfg.timeout == 5
        inspector.close()

    def test_headers_include_user_agent(self) -> None:
        config = RequestConfig()
        assert "User-Agent" in config.headers
        assert len(config.headers["User-Agent"]) > 0

