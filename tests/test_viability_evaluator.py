"""
Tests — Módulo 2: Evaluador de viabilidad.

Verifica la puntuación de viabilidad y las recomendaciones de estrategia
bajo diferentes escenarios de diagnóstico.
"""

import pytest
from src.inspector.viability_evaluator import ViabilityEvaluator


def _make_inputs(
    access_ok: bool = True,
    render_type: str = "static",
    captcha: bool = False,
    blocked: bool = False,
    auth: bool = False,
    rate_limit: bool = False,
    robots_allowed: bool = True,
    rest_api: bool = False,
    infinite_scroll: bool = False,
) -> tuple:  # type: ignore[type-arg]
    http = {"access_ok": access_ok, "status_code": 200 if access_ok else 503}
    html = {"render_type": render_type}
    dom = {
        "rest_api_detected": rest_api,
        "graphql_detected": False,
        "infinite_scroll_confirmed": infinite_scroll,
    }
    protection = {
        "captcha_detected": captcha,
        "access_blocked": blocked,
        "auth_required": auth,
        "rate_limiting": rate_limit,
        "robots_crawl_allowed": robots_allowed,
        "robots_txt_status": "accesible",
    }
    return http, html, dom, protection


class TestViabilityEvaluator:
    """Tests del evaluador de viabilidad técnica."""

    def test_static_accessible_site_is_viable(self) -> None:
        evaluator = ViabilityEvaluator()
        result = evaluator.evaluate(*_make_inputs())
        assert result["technical_feasibility"] == "viable"
        assert result["feasibility_score"] >= 70

    def test_recommends_requests_for_static(self) -> None:
        evaluator = ViabilityEvaluator()
        result = evaluator.evaluate(*_make_inputs(render_type="static"))
        strategies = result["recommended_strategies"]
        assert any("requests" in s.lower() or "beautifulsoup" in s.lower() for s in strategies)

    def test_recommends_selenium_for_spa(self) -> None:
        evaluator = ViabilityEvaluator()
        result = evaluator.evaluate(*_make_inputs(render_type="spa_probable"))
        strategies = result["recommended_strategies"]
        assert any("selenium" in s.lower() or "playwright" in s.lower() for s in strategies)

    def test_captcha_reduces_score_significantly(self) -> None:
        evaluator = ViabilityEvaluator()
        base = evaluator.evaluate(*_make_inputs())
        with_captcha = evaluator.evaluate(*_make_inputs(captcha=True))
        assert with_captcha["feasibility_score"] < base["feasibility_score"] - 50

    def test_blocked_site_is_not_viable(self) -> None:
        evaluator = ViabilityEvaluator()
        result = evaluator.evaluate(*_make_inputs(blocked=True))
        assert result["technical_feasibility"] in ("no_viable", "limitada")

    def test_inaccessible_site_low_score(self) -> None:
        evaluator = ViabilityEvaluator()
        result = evaluator.evaluate(*_make_inputs(access_ok=False))
        assert result["feasibility_score"] < 60

    def test_rest_api_recommends_api_strategy(self) -> None:
        evaluator = ViabilityEvaluator()
        result = evaluator.evaluate(*_make_inputs(rest_api=True))
        strategies = result["recommended_strategies"]
        assert any("api" in s.lower() for s in strategies)

    def test_infinite_scroll_adds_limitation(self) -> None:
        evaluator = ViabilityEvaluator()
        result = evaluator.evaluate(*_make_inputs(infinite_scroll=True))
        limitations = result["limitations"]
        assert any("scroll" in l.lower() for l in limitations)

    def test_robots_restriction_adds_limitation(self) -> None:
        evaluator = ViabilityEvaluator()
        result = evaluator.evaluate(*_make_inputs(robots_allowed=False))
        limitations = result["limitations"]
        assert any("robots" in l.lower() for l in limitations)

    def test_score_clamped_between_0_and_100(self) -> None:
        evaluator = ViabilityEvaluator()
        # Peor escenario posible
        result = evaluator.evaluate(*_make_inputs(
            access_ok=False, captcha=True, blocked=True, auth=True
        ))
        assert 0 <= result["feasibility_score"] <= 100

    def test_result_has_required_keys(self) -> None:
        evaluator = ViabilityEvaluator()
        result = evaluator.evaluate(*_make_inputs())
        required_keys = [
            "technical_feasibility", "feasibility_score",
            "recommended_strategies", "limitations", "notes",
            "score_breakdown", "legal_authorization_note",
        ]
        for key in required_keys:
            assert key in result

    def test_no_extraction_strategy_recommended_when_barrier_present(self) -> None:
        evaluator = ViabilityEvaluator()
        for scenario in (
            _make_inputs(captcha=True),
            _make_inputs(blocked=True),
            _make_inputs(robots_allowed=False),
            _make_inputs(auth=True),
        ):
            result = evaluator.evaluate(*scenario)
            assert result["recommended_strategies"] == []
            assert "autorización legal" in result["legal_authorization_note"].lower()

