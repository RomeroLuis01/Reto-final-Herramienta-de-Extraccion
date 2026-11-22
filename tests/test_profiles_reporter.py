"""
Tests — Utilidades: Perfiles y reportes.

Verifica la persistencia, carga y listado de perfiles,
y la generación de reportes en formato JSON y TXT.
"""

import json
import tempfile
from pathlib import Path

import pytest

from src.monitor.reporter import DiagnosticReporter
from src.utils.profiles import ProfileManager


# ---------------------------------------------------------------------------
# Diagnóstico de prueba
# ---------------------------------------------------------------------------

SAMPLE_DIAGNOSIS = {
    "url": "https://example.com",
    "label": "Test",
    "diagnosis_timestamp": "2026-11-10T20:00:00",
    "access_status": "accesible",
    "http": {
        "status_code": 200,
        "response_time_s": 0.5,
        "content_type": "text/html; charset=utf-8",
        "server": "nginx",
        "is_https": True,
        "redirects": [],
        "cookies_count": 2,
    },
    "html_analysis": {
        "render_type": "static",
        "static_html_size": 50000,
        "meta_tags_count": 5,
        "scripts_count": 3,
        "forms_count": 0,
        "tables_count": 1,
    },
    "technologies": {
        "js_frameworks_confirmed": [],
        "js_frameworks_probable": ["jQuery"],
        "cms": None,
        "render_type": "static",
    },
    "network": {
        "rest_api_detected": False,
        "graphql_detected": False,
        "xhr_fetch_detected": False,
        "websocket_detected": False,
        "endpoints_observed": [],
    },
    "pagination": {
        "traditional_pagination": True,
        "dynamic_pagination": False,
        "load_more_button": False,
        "infinite_scroll": False,
    },
    "protection": {
        "captcha_detected": False,
        "access_blocked": False,
        "auth_required": False,
        "rate_limiting": False,
        "waf_detected": False,
        "robots_txt_status": "accesible",
        "robots_crawl_allowed": True,
        "restrictions": [],
        "incidents": [],
    },
    "viability": {
        "technical_feasibility": "viable",
        "feasibility_score": 85,
        "recommended_strategies": ["requests + BeautifulSoup (HTML estático)"],
        "limitations": [],
        "notes": [],
    },
    "errors": [],
}


class TestProfileManager:
    """Tests del gestor de perfiles."""

    def test_save_and_load_profile(self, tmp_path: Path) -> None:
        manager = ProfileManager(tmp_path)
        manager.save(SAMPLE_DIAGNOSIS)
        profile = manager.load("https://example.com")
        assert profile is not None
        assert profile["url"] == "https://example.com"

    def test_profile_analysis_count_increments(self, tmp_path: Path) -> None:
        manager = ProfileManager(tmp_path)
        manager.save(SAMPLE_DIAGNOSIS)
        manager.save(SAMPLE_DIAGNOSIS)
        profile = manager.load("https://example.com")
        assert profile is not None
        assert profile["analysis_count"] == 2

    def test_profile_history_stored(self, tmp_path: Path) -> None:
        manager = ProfileManager(tmp_path)
        manager.save(SAMPLE_DIAGNOSIS)
        profile = manager.load("https://example.com")
        assert profile is not None
        assert len(profile["history"]) >= 1

    def test_history_capped_at_10(self, tmp_path: Path) -> None:
        manager = ProfileManager(tmp_path)
        for _ in range(15):
            manager.save(SAMPLE_DIAGNOSIS)
        profile = manager.load("https://example.com")
        assert profile is not None
        assert len(profile["history"]) <= 10

    def test_load_nonexistent_returns_none(self, tmp_path: Path) -> None:
        manager = ProfileManager(tmp_path)
        profile = manager.load("https://does-not-exist.com")
        assert profile is None

    def test_list_profiles(self, tmp_path: Path) -> None:
        manager = ProfileManager(tmp_path)
        manager.save(SAMPLE_DIAGNOSIS)
        profiles = manager.list_profiles()
        assert len(profiles) == 1
        assert profiles[0]["url"] == "https://example.com"

    def test_profile_file_is_valid_json(self, tmp_path: Path) -> None:
        manager = ProfileManager(tmp_path)
        path = manager.save(SAMPLE_DIAGNOSIS)
        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_corrupt_profile_handled_gracefully(self, tmp_path: Path) -> None:
        manager = ProfileManager(tmp_path)
        corrupt_path = tmp_path / "example_com.json"
        corrupt_path.write_text("{corrupt json", encoding="utf-8")
        assert manager.load("https://example.com") is None
        assert manager.list_profiles() == []
        # Guardar encima debe recuperarse sin excepción
        manager.save(SAMPLE_DIAGNOSIS)
        assert manager.load("https://example.com") is not None

    def test_reusable_config_retrieved_from_profile(self, tmp_path: Path) -> None:
        manager = ProfileManager(tmp_path)
        manager.save(SAMPLE_DIAGNOSIS)
        reusable = manager.get_reusable_config("https://example.com")
        assert reusable is not None
        assert reusable["label"] == "Test"


class TestDiagnosticReporter:
    """Tests del generador de reportes."""

    def test_generates_json_report(self, tmp_path: Path) -> None:
        reporter = DiagnosticReporter(tmp_path)
        paths = reporter.generate(SAMPLE_DIAGNOSIS)
        assert paths["json"].exists()

    def test_generates_txt_report(self, tmp_path: Path) -> None:
        reporter = DiagnosticReporter(tmp_path)
        paths = reporter.generate(SAMPLE_DIAGNOSIS)
        assert paths["txt"].exists()

    def test_json_report_is_valid(self, tmp_path: Path) -> None:
        reporter = DiagnosticReporter(tmp_path)
        paths = reporter.generate(SAMPLE_DIAGNOSIS)
        with open(paths["json"]) as f:
            data = json.load(f)
        assert data["url"] == "https://example.com"

    def test_txt_report_has_content(self, tmp_path: Path) -> None:
        reporter = DiagnosticReporter(tmp_path)
        paths = reporter.generate(SAMPLE_DIAGNOSIS)
        content = paths["txt"].read_text(encoding="utf-8")
        assert "REPORTE DE DIAGNÓSTICO" in content
        assert "https://example.com" in content

    def test_report_filename_contains_domain(self, tmp_path: Path) -> None:
        reporter = DiagnosticReporter(tmp_path)
        paths = reporter.generate(SAMPLE_DIAGNOSIS)
        assert "example" in paths["json"].name

    def test_multiple_reports_dont_overwrite(self, tmp_path: Path) -> None:
        reporter = DiagnosticReporter(tmp_path)
        paths1 = reporter.generate(SAMPLE_DIAGNOSIS)
        paths2 = reporter.generate(SAMPLE_DIAGNOSIS)
        assert paths1["json"] != paths2["json"]
        assert paths1["txt"] != paths2["txt"]

    def test_report_redacts_secrets_and_includes_confidence_sections(self, tmp_path: Path) -> None:
        reporter = DiagnosticReporter(tmp_path)
        diag = {
            **SAMPLE_DIAGNOSIS,
            "url": "https://example.com/?token=TOPSECRET",
            "http": {
                **SAMPLE_DIAGNOSIS["http"],
                "security_headers": {"Set-Cookie": "sid=SECRETCOOKIE"},
            },
            "confidence_summary": {
                "confirmed": ["HTTP 200 (html)"],
                "probable": ["jQuery"],
                "unidentified": ["CMS"],
            },
        }
        paths = reporter.generate(diag)
        json_text = paths["json"].read_text(encoding="utf-8")
        txt_text = paths["txt"].read_text(encoding="utf-8")
        assert "TOPSECRET" not in json_text
        assert "SECRETCOOKIE" not in json_text
        assert "CONFIRMADA" in txt_text
        assert "PROBABLE" in txt_text
        assert "NO IDENTIFICADA" in txt_text


class TestChangeDetection:
    def test_diagnosis_changes_distinguish_missing_and_empty(self, tmp_path):
        manager = ProfileManager(tmp_path)
        first = {**SAMPLE_DIAGNOSIS, "candidate_selectors": [".old"],
                 "network": {"endpoints_observed": ["https://example.com/old"]}}
        second = {**first, "candidate_selectors": [".new"],
                  "network": {"endpoints_observed": []}}
        manager.save(first)
        manager.save(second)
        changes = manager.load(first["url"])["diagnosis_changes"]
        assert changes["selectors"]["added"] == [".new"]
        assert changes["selectors"]["removed"] == [".old"]
        assert changes["endpoints"]["removed"] == ["https://example.com/old"]
        assert not manager.compare_diagnoses({}, second)["baseline_available"]
        assert not manager._changes(None, [])["available"]

    def test_extraction_comparison_and_persistence(self, tmp_path):
        manager = ProfileManager(tmp_path)
        first_schema = {"base_selector": ".old", "fields": [
            {"name": "title", "selector": "h1", "selector_type": "css"},
            {"name": "price", "selector": ".price", "selector_type": "css"}]}
        second_schema = {"base_selector": ".new", "fields": [
            {"name": "title", "selector": "h2", "selector_type": "css"},
            {"name": "stock", "selector": ".stock", "selector_type": "css"}]}
        assert not manager.save_extraction("https://example.com", first_schema,
            [{"title": "secret", "price": 1}], [{"items": [{"title": "secret"}]}], "one")["baseline_available"]
        changes = manager.save_extraction("https://example.com", second_schema,
            [{"title": "other", "stock": True}, {"title": "another", "stock": False}],
            [{"items": [{"title": "other", "stock": True}]}], "two")
        assert changes["fields"]["removed"] == ["price"]
        assert changes["columns"]["added"] == ["stock"]
        assert changes["selectors"]["added"]
        assert changes["base_selector"]["removed"] == [".old"]
        assert "$.items[].stock: boolean" in changes["json_structure"]["added"]
        assert changes["record_count"]["difference"] == 1
        stored = (tmp_path / "example_com.json").read_text(encoding="utf-8")
        assert "secret" not in stored and "another" not in stored
        assert manager.load("https://example.com")["extraction_changes"] == changes


def test_profile_separates_observed_and_explicit_authorized_endpoints(tmp_path):
    manager = ProfileManager(tmp_path)
    first = {**SAMPLE_DIAGNOSIS, "network": {
        "endpoints_observed": ["https://example.com/observed?token=secret"],
        "authorized_endpoints": ["https://example.com/allowed?token=secret"],
    }}
    second = {**first, "network": {
        "endpoints_observed": ["https://example.com/new"],
        "authorized_endpoints": [],
    }}
    manager.save(first)
    profile = manager.load(first["url"])
    assert "observed" in profile["observed_endpoints"][0]
    assert "allowed" in profile["authorized_endpoints"][0]
    assert "secret" not in (tmp_path / "example_com.json").read_text(encoding="utf-8")
    manager.save(second)
    changes = manager.load(first["url"])["diagnosis_changes"]
    assert changes["observed_endpoints"]["added"] == ["https://example.com/new"]
    assert len(changes["authorized_endpoints"]["removed"]) == 1
    assert changes["endpoints"] == changes["observed_endpoints"]


def test_legacy_endpoint_labels_are_not_treated_as_authorization(tmp_path):
    manager = ProfileManager(tmp_path)
    legacy = {
        "url": "https://example.com", "authorized_endpoints": ["https://example.com/api"],
        "current_diagnosis": {"network": {"endpoints_observed": ["https://example.com/api"]}},
        "reusable_config": {"known_endpoints": ["https://example.com/api"]},
    }
    (tmp_path / "example_com.json").write_text(json.dumps(legacy), encoding="utf-8")
    profile = manager.load(legacy["url"])
    assert profile["legacy_endpoints_unverified"] == legacy["authorized_endpoints"]
    assert profile["observed_endpoints"] == legacy["authorized_endpoints"]
    assert profile["authorized_endpoints"] == []
    assert manager.get_reusable_config(legacy["url"])["known_endpoints"] == []
    manager.save(SAMPLE_DIAGNOSIS)
    stored = manager.load(legacy["url"])
    assert stored["legacy_endpoints_unverified"] == legacy["authorized_endpoints"]
    assert stored["authorized_endpoints"] == []
    assert not stored["diagnosis_changes"]["authorized_endpoints"]["available"]
