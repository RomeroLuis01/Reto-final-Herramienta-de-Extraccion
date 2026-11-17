"""
Tests — Módulo 1: Configuración del proyecto.

Verifica la validación de URLs (incluyendo reglas anti-SSRF por defecto),
registro de objetivos, parámetros de diagnóstico y carga de configuración.
"""

import pytest
from src.config.settings import (
    AnalysisTarget,
    DiagnosticParameters,
    ProjectConfig,
    get_config,
    normalize_url,
    register_target,
    validate_url,
)


class TestUrlValidation:
    """Pruebas de validación sintáctica y de seguridad de URL."""

    def test_valid_https_url(self) -> None:
        valid, msg = validate_url("https://www.example.com")
        assert valid is True
        assert "válida" in msg.lower()

    def test_valid_http_url(self) -> None:
        valid, _ = validate_url("http://example.com")
        assert valid is True

    def test_valid_url_with_path(self) -> None:
        valid, _ = validate_url("https://www.example.com/path/to/page?q=test")
        assert valid is True

    def test_empty_url_is_invalid(self) -> None:
        valid, msg = validate_url("")
        assert valid is False
        assert "vacía" in msg.lower()

    def test_whitespace_only_is_invalid(self) -> None:
        valid, _ = validate_url("   ")
        assert valid is False

    def test_ftp_scheme_is_rejected(self) -> None:
        valid, msg = validate_url("ftp://example.com/file")
        assert valid is False
        assert "esquema" in msg.lower() or "permitido" in msg.lower()

    def test_file_scheme_is_rejected(self) -> None:
        valid, _ = validate_url("file:///etc/passwd")
        assert valid is False

    def test_missing_scheme_is_invalid_before_normalization(self) -> None:
        """validate_url requiere esquema http/https; normalize_url agrega https://."""
        valid, _ = validate_url("example.com")
        assert valid is False
        assert normalize_url("example.com") == "https://example.com"

    def test_no_domain_is_invalid(self) -> None:
        valid, _ = validate_url("https://")
        assert valid is False

    def test_localhost_is_rejected_by_default_ssrf(self) -> None:
        valid, msg = validate_url("http://localhost:8080")
        assert valid is False
        assert "ssrf" in msg.lower() or "bloqueado" in msg.lower()

    def test_loopback_ip_is_rejected_by_default_ssrf(self) -> None:
        valid, _ = validate_url("http://127.0.0.1:8080")
        assert valid is False

    def test_url_with_subdomain(self) -> None:
        valid, _ = validate_url("https://api.example.com/v2/data")
        assert valid is True


class TestAnalysisTarget:
    """Pruebas del modelo AnalysisTarget."""

    def test_creates_valid_target(self) -> None:
        target = AnalysisTarget(url="https://www.example.com")
        assert target.url == "https://www.example.com"

    def test_normalizes_url_without_scheme(self) -> None:
        target = AnalysisTarget(url="www.example.com")
        assert target.url.startswith("https://")

    def test_strips_whitespace(self) -> None:
        target = AnalysisTarget(url="  https://example.com  ")
        assert " " not in target.url

    def test_domain_property(self) -> None:
        target = AnalysisTarget(url="https://www.example.com/path")
        assert target.domain == "www.example.com"

    def test_scheme_property(self) -> None:
        target = AnalysisTarget(url="https://example.com")
        assert target.scheme == "https"

    def test_invalid_url_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="URL no válida"):
            AnalysisTarget(url="ftp://example.com")

    def test_ssrf_target_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="URL no válida"):
            AnalysisTarget(url="http://169.254.169.254/latest/meta-data/")

    def test_label_stored(self) -> None:
        target = AnalysisTarget(url="https://example.com", label="Mi sitio")
        assert target.label == "Mi sitio"

    def test_register_target_function(self) -> None:
        params = DiagnosticParameters(use_selenium=False)
        target = register_target(
            "https://example.com",
            label="Test",
            objectives=["Diagnóstico inicial"],
            parameters=params,
        )
        assert isinstance(target, AnalysisTarget)
        assert target.label == "Test"
        assert target.objectives == ["Diagnóstico inicial"]
        assert target.parameters.use_selenium is False


class TestProjectConfig:
    """Pruebas de configuración del proyecto."""

    def test_singleton_config(self) -> None:
        cfg1 = get_config()
        cfg2 = get_config()
        assert cfg1 is cfg2

    def test_config_has_selenium_settings(self) -> None:
        cfg = get_config()
        assert cfg.selenium.implicit_wait >= 0
        assert cfg.selenium.page_load_timeout > 0
        assert cfg.selenium.script_timeout > 0

    def test_config_has_request_settings(self) -> None:
        cfg = get_config()
        assert cfg.request.timeout > 0
        assert cfg.request.max_retries > 0
        assert cfg.request.max_redirects > 0
        assert cfg.request.max_response_bytes > 0
        assert "User-Agent" in cfg.request.headers

    def test_config_paths_created(self) -> None:
        cfg = get_config()
        assert cfg.paths.profiles_dir.exists()
        assert cfg.paths.reports_dir.exists()
        assert cfg.paths.logs_dir.exists()
