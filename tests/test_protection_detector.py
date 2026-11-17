"""
Tests — Módulo 2: Detector de protecciones.

Verifica la detección de CAPTCHA, bloqueos, rate limiting,
WAF y autenticación a partir de datos simulados.
"""

import pytest
from src.inspector.protection_detector import ProtectionDetector


class TestProtectionDetector:
    """Tests del detector de protecciones y bloqueos."""

    def _make_http(self, **kwargs):  # type: ignore[no-untyped-def]
        defaults = {
            "status_code": 200,
            "access_ok": True,
            "security_headers": {},
            "robots_txt": {"accessible": True, "crawl_allowed": True},
        }
        defaults.update(kwargs)
        return defaults

    def test_no_restrictions_on_clean_site(self) -> None:
        detector = ProtectionDetector()
        result = detector.analyze(self._make_http(), {}, {})
        assert result["captcha_detected"] is False
        assert result["access_blocked"] is False
        assert result["auth_required"] is False
        assert result["rate_limiting"] is False

    def test_403_detected_as_blocked(self) -> None:
        detector = ProtectionDetector()
        result = detector.analyze(self._make_http(status_code=403), {}, {})
        assert result["access_blocked"] is True
        assert len(result["incidents"]) > 0
        # Verificar que genera incidencia tipo ACCESO_BLOQUEADO
        incident_types = [i["type"] for i in result["incidents"]]
        assert "ACCESO_BLOQUEADO" in incident_types

    def test_429_detected_as_rate_limit(self) -> None:
        detector = ProtectionDetector()
        result = detector.analyze(self._make_http(status_code=429), {}, {})
        assert result["rate_limiting"] is True

    def test_401_detected_as_auth_required(self) -> None:
        detector = ProtectionDetector()
        result = detector.analyze(self._make_http(status_code=401), {}, {})
        assert result["auth_required"] is True

    def test_cloudflare_detected_via_cf_ray_header(self) -> None:
        detector = ProtectionDetector()
        http = self._make_http(security_headers={"Cf-Ray": "abc123-DFW"})
        result = detector.analyze(http, {}, {})
        assert result["waf_detected"] is True
        assert result["waf_type"] == "Cloudflare"

    def test_captcha_in_html_detected(self) -> None:
        detector = ProtectionDetector()
        html_analysis = {
            "captcha_indicators": [{"indicator": "recaptcha", "confidence": "probable"}]
        }
        result = detector.analyze(self._make_http(), html_analysis, {})
        assert result["captcha_detected"] is True
        assert result["captcha_confidence"] in ("probable", "confirmado")

    def test_captcha_in_dom_confirmed(self) -> None:
        detector = ProtectionDetector()
        dom = {"captcha_detected": True, "captcha_type": "reCAPTCHA (Google)"}
        result = detector.analyze(self._make_http(), {}, dom)
        assert result["captcha_detected"] is True
        assert result["captcha_confidence"] == "confirmado"
        assert "reCAPTCHA" in result["captcha_type"]

    def test_robots_txt_restriction_detected(self) -> None:
        detector = ProtectionDetector()
        http = self._make_http(robots_txt={"accessible": True, "crawl_allowed": False})
        result = detector.analyze(http, {}, {})
        assert result["robots_crawl_allowed"] is False
        assert any("robots" in r.lower() for r in result["restrictions"])

    def test_login_form_as_auth_indicator(self) -> None:
        detector = ProtectionDetector()
        html = {
            "auth_indicators": {"has_login_form": True, "detected": True}
        }
        result = detector.analyze(self._make_http(), html, {})
        assert result["auth_required"] is True

    def test_captcha_generates_high_severity_incident(self) -> None:
        detector = ProtectionDetector()
        dom = {"captcha_detected": True, "captcha_type": "hCaptcha"}
        result = detector.analyze(self._make_http(), {}, dom)
        incidents = result["incidents"]
        captcha_incidents = [i for i in incidents if i["type"] == "CAPTCHA"]
        assert len(captcha_incidents) > 0
        assert captcha_incidents[0]["severity"] == "ALTA"

    def test_restrictions_list_populated(self) -> None:
        detector = ProtectionDetector()
        result = detector.analyze(self._make_http(status_code=403), {}, {})
        assert len(result["restrictions"]) > 0

    def test_incidents_contain_required_fields_and_waf_vendors(self) -> None:
        detector = ProtectionDetector()
        http = self._make_http(
            status_code=429,
            security_headers={
                "Retry-After": "60",
                "X-Sucuri-ID": "sucuri-99",
            },
        )
        result = detector.analyze(http, {}, {})
        assert result["waf_detected"] is True
        assert "Sucuri" in (result["waf_type"] or "")
        assert result["rate_limiting"] is True
        assert result["blocking_barrier"] is True
        for inc in result["incidents"]:
            for key in ("type", "evidence", "severity", "description", "action"):
                assert key in inc and inc[key]

