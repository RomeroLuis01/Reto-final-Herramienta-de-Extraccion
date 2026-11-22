"""Interacción humana simulada; no se contactan sitios públicos."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import pytest

from src.config.settings import ProjectConfig, register_target
from src.extractor.manual_session import acquire_manual, manual_eligible
from src.extractor.base import ExtractionBatch, PageSnapshot
from src.extractor.extraction_service import ExtractionService


def diagnosis(status=403, challenge=True, robots=True, **protection):
    return {"http": {"status_code": status, "security_headers": {"Cf-Mitigated": "challenge" if challenge else ""}},
            "protection": {"robots_crawl_allowed": robots, "blocking_barrier": True, **protection},
            "viability": {"blocked_by_barrier": True}, "access_status": "restringido_http_403"}


@pytest.mark.parametrize("diagnosis_value,expected", [
    (diagnosis(), True), (diagnosis(challenge=False), False),
    (diagnosis(robots=False), False), (diagnosis(status=429), False),
    (diagnosis(status=503), False), (diagnosis(ssrf_blocked=True), False),
    (diagnosis(rate_limiting=True), False),
    (diagnosis(status=401, challenge=False, auth_required=True), True),
])
def test_eligibility(diagnosis_value, expected):
    assert manual_eligible(register_target("https://example.com/data"), diagnosis_value) is expected


def performance(status=200, url="https://example.com/data", headers=None, loader="loader", frame="main"):
    return {"message": json.dumps({"message": {"method": "Network.responseReceived", "params": {
        "type": "Document", "frameId": frame, "loaderId": loader, "response": {"url": url, "status": status, "headers": headers or {}}}}})}


def prepare_dom(status=200, url="https://example.com/data"):
    dom = MagicMock()
    driver = dom._driver
    driver.current_url = url
    driver.page_source = "<html><body><article>Dato autorizado</article></body></html>"
    driver.find_element.return_value.text = "Dato autorizado"
    driver.find_elements.return_value = []
    driver.execute_cdp_cmd.return_value = {"frameTree": {"frame": {"id": "main", "loaderId": "loader"}}}
    driver.get_log.return_value = [performance(status, url)]
    return dom


def acquire(dom, callback=lambda: True):
    target = register_target("https://example.com/data")
    with patch("src.extractor.manual_session.validate_url_security", return_value=(True, "ok")), \
         patch("src.extractor.manual_session.DomInspector") as cls, \
         patch("src.extractor.manual_session.HtmlAnalyzer") as analyzer:
        cls.return_value.__enter__.return_value = dom
        analyzer.return_value.analyze.return_value = {}
        result = acquire_manual(target, ProjectConfig(), callback)
        assert cls.call_args.args[0].headless is False
        return result


def test_manual_success_uses_same_browser_and_captures_verified_document():
    dom = prepare_dom()
    result = acquire(dom)
    assert result.pages[0].status_code == 200
    assert result.pages[0].html == dom._driver.page_source
    dom._driver.get.assert_called_once_with("https://example.com/data")
    assert dom._driver.get_log.call_count == 1


def test_cancel_does_not_extract():
    dom = prepare_dom()
    result = acquire(dom, lambda: False)
    assert not result.pages and "cancelada" in result.errors[0]


@pytest.mark.parametrize("status", [403, 429, 503])
def test_failed_http_cannot_continue(status):
    result = acquire(prepare_dom(status))
    assert not result.pages
    assert f"HTTP {status}" in result.errors[0]
    assert "recargue" not in result.errors[0].lower()


def test_unknown_status_and_wrong_origin_cannot_continue():
    dom = prepare_dom()
    dom._driver.get_log.return_value = []
    result = acquire(dom)
    assert not result.pages
    assert "recargue" in result.errors[0].lower()
    assert not acquire(prepare_dom(url="https://other.example/data")).pages


def test_initial_response_is_accepted_without_reload():
    dom = prepare_dom()
    result = acquire(dom)
    assert result.pages and result.pages[0].status_code == 200


def test_response_from_an_old_loader_or_another_frame_is_rejected():
    for event in (performance(loader="old"), performance(frame="other")):
        dom = prepare_dom()
        dom._driver.get_log.return_value = [event]
        assert not acquire(dom).pages


def test_missing_current_loader_is_rejected():
    dom = prepare_dom()
    dom._driver.execute_cdp_cmd.return_value = {"frameTree": {"frame": {"id": "main"}}}
    assert not acquire(dom).pages


def test_http_200_with_challenge_header_is_not_success():
    dom = prepare_dom()
    dom._driver.get_log.return_value = [performance(200, headers={"Cf-Mitigated": "challenge"})]
    assert not acquire(dom).pages


def test_challenge_remaining_cannot_continue():
    dom = prepare_dom()
    dom._driver.find_elements.return_value = [object()]
    assert not acquire(dom).pages


def test_dom_check_failure_is_not_treated_as_clear():
    dom = prepare_dom()
    dom._driver.find_elements.side_effect = RuntimeError("falló lectura")
    assert not acquire(dom).pages


def test_manual_retry_keeps_session_and_requires_new_response():
    dom = prepare_dom()
    dom._driver.find_elements.side_effect = [[object()]] + [[]] * 100
    dom._driver.get_log.side_effect = [[performance()], [performance(loader="new")]]
    dom._driver.execute_cdp_cmd.side_effect = [
        {"frameTree": {"frame": {"id": "main", "loaderId": "loader"}}},
        {"frameTree": {"frame": {"id": "main", "loaderId": "new"}}},
        {"frameTree": {"frame": {"id": "main", "loaderId": "new"}}},
    ]
    target = register_target("https://example.com/data")
    retry = MagicMock(return_value=True)
    with patch("src.extractor.manual_session.validate_url_security", return_value=(True, "ok")), \
         patch("src.extractor.manual_session.DomInspector") as cls, \
         patch("src.extractor.manual_session.HtmlAnalyzer") as analyzer:
        cls.return_value.__enter__.return_value = dom
        analyzer.return_value.analyze.return_value = {}
        result = acquire_manual(target, ProjectConfig(), lambda: True, retry)
    assert len(result.pages) == 1
    assert dom._driver.get.call_count == 1
    assert "Submit" in retry.call_args.args[0]
    assert 0 < retry.call_args.args[1] <= 300


def test_manual_retry_cancel_keeps_barrier_and_does_not_read_tokens():
    dom = prepare_dom()
    dom._driver.find_elements.return_value = [object()]
    target = register_target("https://example.com/data")
    with patch("src.extractor.manual_session.validate_url_security", return_value=(True, "ok")), \
         patch("src.extractor.manual_session.DomInspector") as cls:
        cls.return_value.__enter__.return_value = dom
        result = acquire_manual(target, ProjectConfig(), lambda: True, lambda _reason, _remaining: False)
    assert not result.pages
    assert any("cancelada" in error for error in result.errors)
    dom._driver.get_cookies.assert_not_called()


@pytest.mark.parametrize("status", [403, 429, 503])
def test_manual_retry_does_not_accept_hard_http_barriers(status):
    dom = prepare_dom(status)
    target = register_target("https://example.com/data")
    retry = MagicMock(return_value=False)
    with patch("src.extractor.manual_session.validate_url_security", return_value=(True, "ok")), \
         patch("src.extractor.manual_session.DomInspector") as cls:
        cls.return_value.__enter__.return_value = dom
        result = acquire_manual(target, ProjectConfig(), lambda: True, retry)
    assert not result.pages
    retry.assert_not_called()
    assert any(f"HTTP {status}" in error for error in result.errors)


def test_manual_retry_does_not_accept_unauthorized_url():
    dom = prepare_dom(url="https://other.example/data")
    target = register_target("https://example.com/data")
    retry = MagicMock(return_value=True)
    with patch("src.extractor.manual_session.validate_url_security", return_value=(True, "ok")), \
         patch("src.extractor.manual_session.DomInspector") as cls:
        cls.return_value.__enter__.return_value = dom
        result = acquire_manual(target, ProjectConfig(), lambda: True, retry)
    assert not result.pages
    retry.assert_not_called()


def test_manual_retry_does_not_accept_ssrf():
    dom = prepare_dom()
    target = register_target("https://example.com/data")
    retry = MagicMock(return_value=True)
    with patch("src.extractor.manual_session.validate_url_security", side_effect=[(True, "ok"), (False, "ssrf")]), \
         patch("src.extractor.manual_session.DomInspector") as cls:
        cls.return_value.__enter__.return_value = dom
        result = acquire_manual(target, ProjectConfig(), lambda: True, retry)
    assert not result.pages
    retry.assert_not_called()


def test_script_only_cannot_prove_challenge_was_accepted():
    dom = prepare_dom()
    target = register_target("https://example.com/data")
    with patch("src.extractor.manual_session.validate_url_security", return_value=(True, "ok")), \
         patch("src.extractor.manual_session.DomInspector") as cls, \
         patch("src.extractor.manual_session.HtmlAnalyzer") as analyzer:
        cls.return_value.__enter__.return_value = dom
        analyzer.return_value.analyze.return_value = {"captcha_indicators": ["script"]}
        result = acquire_manual(target, ProjectConfig(), lambda: True)
    assert not result.pages


def test_retry_requires_new_http_evidence_for_current_navigation():
    dom = prepare_dom()
    dom._driver.find_elements.side_effect = [[object()]] + [[]] * 100
    dom._driver.get_log.side_effect = [[performance()], []]
    target = register_target("https://example.com/data")
    answers = iter([True, False])
    with patch("src.extractor.manual_session.validate_url_security", return_value=(True, "ok")), \
         patch("src.extractor.manual_session.DomInspector") as cls, \
         patch("src.extractor.manual_session.HtmlAnalyzer") as analyzer:
        cls.return_value.__enter__.return_value = dom
        analyzer.return_value.analyze.return_value = {}
        result = acquire_manual(target, ProjectConfig(), lambda: True, lambda *_: next(answers))
    assert not result.pages
    assert any("respuesta HTTP" in error for error in result.errors)


def test_manual_timeout_is_global():
    dom = prepare_dom()
    dom._driver.find_elements.return_value = [object()]
    target = register_target("https://example.com/data")
    with patch("src.extractor.manual_session.validate_url_security", return_value=(True, "ok")), \
         patch("src.extractor.manual_session.DomInspector") as cls, \
         patch("src.extractor.manual_session.time.monotonic", side_effect=[0, 1, 2, 301]):
        cls.return_value.__enter__.return_value = dom
        result = acquire_manual(target, ProjectConfig(), lambda: True, lambda *_: True)
    assert not result.pages
    assert any("tiempo agotado" in error for error in result.errors)


def test_service_runs_pipeline_on_verified_manual_snapshot(tmp_path):
    from src.config.settings import PathConfig
    target = register_target("https://example.com/data")
    config = ProjectConfig(paths=PathConfig(base_dir=tmp_path))
    html = "<main><article><p>Uno</p></article><article><p>Dos</p></article></main>"
    batch = ExtractionBatch(method="selenium_manual", pages=[PageSnapshot(
        url=target.url, html=html, final_url=target.url, method="selenium_manual", status_code=200)])
    with patch("src.extractor.manual_session.acquire_manual", return_value=batch) as manual, \
         patch.object(ExtractionService, "_acquire", side_effect=AssertionError("no debe abrir otra sesión")):
        result = ExtractionService(config).run(target, diagnosis=diagnosis(), manual_wait=lambda: True,
                                               formats=["json"], max_pages=1)
    assert result["status"] == "ok"
    assert result["manual_verification"] is True
    assert result["raw_records"] == 2
    manual.assert_called_once()


@pytest.mark.parametrize("record_limit,limit_reached", [(2, True), (3, False)])
def test_manual_reports_capture_scope_and_possible_record_limit(tmp_path, record_limit, limit_reached):
    from pathlib import Path
    from src.config.settings import PathConfig

    target = register_target("https://example.com/data")
    config = ProjectConfig(paths=PathConfig(base_dir=tmp_path))
    html = "<main><article><p>Uno</p></article><article><p>Dos</p></article></main>"
    batch = ExtractionBatch(method="selenium_manual", pages=[PageSnapshot(
        url=target.url, html=html, final_url=target.url, method="selenium_manual", status_code=200)])
    with patch("src.extractor.manual_session.acquire_manual", return_value=batch):
        result = ExtractionService(config).run(
            target, diagnosis=diagnosis(), manual_wait=lambda: True,
            formats=["json"], max_pages=3, max_records=record_limit,
        )
    assert result["status"] == "ok"
    coverage = result["coverage"]
    assert coverage["requested_max_pages"] == 3
    assert coverage["captured_pages"] == 1
    assert coverage["record_limit_reached"] is limit_reached
    assert any("únicamente la página verificada" in warning for warning in coverage["warnings"])
    assert any("posible que existan más" in warning for warning in coverage["warnings"]) is limit_reached
    persisted = json.loads(Path(result["report_files"]["json"]).read_text(encoding="utf-8"))
    assert persisted["coverage"] == coverage
    report_text = Path(result["report_files"]["txt"]).read_text(encoding="utf-8")
    assert "Páginas máximas solicitadas: 3" in report_text
    assert "Páginas capturadas         : 1" in report_text
    assert "no demuestra que se haya extraído toda la información" in report_text


@pytest.mark.parametrize("diag", [diagnosis(challenge=False), diagnosis(robots=False), diagnosis(status=429)])
def test_service_never_opens_manual_session_for_hard_barriers(tmp_path, diag):
    from src.config.settings import PathConfig
    target = register_target("https://example.com/data")
    with patch("src.extractor.manual_session.acquire_manual") as manual:
        result = ExtractionService(ProjectConfig(paths=PathConfig(base_dir=tmp_path))).run(
            target, diagnosis=diag, manual_wait=lambda: True)
    assert result["status"] == "blocked"
    manual.assert_not_called()


def test_service_cancel_stays_blocked(tmp_path):
    from src.config.settings import PathConfig
    target = register_target("https://example.com/data")
    batch = ExtractionBatch(method="selenium_manual", errors=["Verificación manual cancelada."])
    with patch("src.extractor.manual_session.acquire_manual", return_value=batch):
        result = ExtractionService(ProjectConfig(paths=PathConfig(base_dir=tmp_path))).run(
            target, diagnosis=diagnosis(), manual_wait=lambda: False)
    assert result["status"] == "blocked"
    assert any("cancelada" in message for message in result["errors"])


@pytest.mark.selenium
def test_manual_chrome_retry_after_user_completes_form(tmp_path, monkeypatch):
    from src.inspector.dom_inspector import DomInspector

    state = {"submitted": False, "cookie_reused": False}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["cookie_reused"] |= "manual_test_session=present" in self.headers.get("Cookie", "")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Set-Cookie", "manual_test_session=present; Path=/; HttpOnly")
            self.end_headers()
            body = (b"<html><body><article><p>Dato autorizado</p></article></body></html>"
                    if state["submitted"] and state["cookie_reused"]
                    else b"<html><body><div class='g-recaptcha'>Verificacion</div></body></html>")
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = register_target(f"http://127.0.0.1:{server.server_port}/data", allow_private_network=True)
    active = {}

    class ObservableDom(DomInspector):
        def __enter__(self):
            active["dom"] = super().__enter__()
            return active["dom"]

    monkeypatch.setattr("src.extractor.manual_session.DomInspector", ObservableDom)

    def retry(reason, _remaining):
        assert "Submit" in reason
        state["submitted"] = True
        active["dom"]._driver.get(target.url)
        return True

    try:
        result = acquire_manual(target, ProjectConfig(), lambda: True, retry)
        assert result.pages and result.pages[0].status_code == 200
        assert state["cookie_reused"]
        assert "manual_test_session" not in str(result.to_dict())
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.selenium
@pytest.mark.parametrize("resolve,expected", [(True, "ok"), (False, "blocked")])
def test_manual_chrome_local_real_session(tmp_path, monkeypatch, resolve, expected):
    """Una navegación local simulada representa la recarga que hace la persona."""
    from src.config.settings import PathConfig
    from src.inspector.dom_inspector import DomInspector

    state = {"resolved": False, "requests": 0, "cookie_reused": False}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"] += 1
            cookie = self.headers.get("Cookie", "")
            state["cookie_reused"] |= "manual_test_session=present" in cookie
            if state["resolved"] and state["cookie_reused"]:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<html><body><main><article><p>Dato autorizado</p></article></main></body></html>")
            else:
                self.send_response(403)
                self.send_header("Cf-Mitigated", "challenge")
                self.send_header("Set-Cookie", "manual_test_session=present; Path=/; HttpOnly")
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<html><body><div id='challenge-form'>Verificacion</div></body></html>")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = register_target(f"http://127.0.0.1:{server.server_port}/data", allow_private_network=True)
    active = {}

    class ObservableDom(DomInspector):
        def __enter__(self):
            active["dom"] = super().__enter__()
            return active["dom"]

    monkeypatch.setattr("src.extractor.manual_session.DomInspector", ObservableDom)

    def simulate_user():
        state["resolved"] = resolve
        active["dom"]._driver.get(target.url)
        return True

    try:
        config = ProjectConfig(paths=PathConfig(base_dir=tmp_path))
        result = ExtractionService(config).run(target, diagnosis=diagnosis(),
                                               manual_wait=simulate_user, formats=["json"], max_pages=1)
        assert result["status"] == expected, result.get("errors")
        if resolve:
            assert result["raw_records"] == 1
            assert result["pages"][0]["status_code"] == 200
            assert result["pages"][0]["method"] == "selenium_manual"
        else:
            assert result["outputs"] == {}
        assert state["requests"] >= 2
        assert state["cookie_reused"]
        assert "manual_test_session" not in str(result)
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.selenium
def test_manual_chrome_initial_success_without_reload(tmp_path):
    from src.config.settings import PathConfig

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body><article><p>Dato autorizado</p></article></body></html>")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = register_target(f"http://127.0.0.1:{server.server_port}/data", allow_private_network=True)
    try:
        config = ProjectConfig(paths=PathConfig(base_dir=tmp_path))
        result = ExtractionService(config).run(
            target, diagnosis=diagnosis(), manual_wait=lambda: True, formats=["json"], max_pages=1
        )
        assert result["status"] == "ok", result.get("errors")
        assert result["pages"][0]["status_code"] == 200
        assert result["raw_records"] == 1
    finally:
        server.shutdown()
        server.server_close()

@pytest.mark.selenium
@pytest.mark.parametrize("initial_check", [False, True])
def test_manual_chrome_post_confirmation_same_session(tmp_path, monkeypatch, initial_check):
    from src.inspector.dom_inspector import DomInspector
    state = {"posts": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body><form method='post'><div class='g-recaptcha'>Complete</div><button id='send' type='submit'>Submit</button></form></body></html>")

        def do_POST(self):
            state["posts"] += 1
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body><article><p>Verification Success</p></article></body></html>")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = register_target(f"http://127.0.0.1:{server.server_port}/data", allow_private_network=True)
    active = {}

    class ObservableDom(DomInspector):
        def __enter__(self):
            active["dom"] = super().__enter__()
            return active["dom"]

    monkeypatch.setattr("src.extractor.manual_session.DomInspector", ObservableDom)
    def submit():
        active["dom"]._driver.find_element("id", "send").click()
        return True
    try:
        result = acquire_manual(target, ProjectConfig(), (lambda: True) if initial_check else submit,
                                (lambda *_: submit()) if initial_check else None)
        assert result.pages, result.errors
        assert "Verification Success" in result.pages[0].html
        assert state["posts"] == 1
    finally:
        server.shutdown()
        server.server_close()


def test_retry_reuses_http_evidence_only_for_same_document():
    dom = prepare_dom()
    dom._driver.get_log.side_effect = [[performance()], []]
    body_empty = MagicMock(text="")
    body_ready = MagicMock(text="Dato autorizado")
    dom._driver.find_element.side_effect = [body_empty, body_ready]
    target = register_target("https://example.com/data")
    with patch("src.extractor.manual_session.validate_url_security", return_value=(True, "ok")), \
         patch("src.extractor.manual_session.DomInspector") as cls, \
         patch("src.extractor.manual_session.HtmlAnalyzer") as analyzer:
        cls.return_value.__enter__.return_value = dom
        analyzer.return_value.analyze.return_value = {}
        result = acquire_manual(target, ProjectConfig(), lambda: True, lambda *_: True)
    assert len(result.pages) == 1
    assert dom._driver.get_log.call_count == 2


def test_changed_document_during_verification_is_never_captured():
    dom = prepare_dom()
    dom._driver.execute_cdp_cmd.side_effect = [
        {"frameTree": {"frame": {"id": "main", "loaderId": "loader"}}},
        {"frameTree": {"frame": {"id": "main", "loaderId": "other"}}},
    ]
    target = register_target("https://example.com/data")
    with patch("src.extractor.manual_session.validate_url_security", return_value=(True, "ok")), \
         patch("src.extractor.manual_session.DomInspector") as cls, \
         patch("src.extractor.manual_session.HtmlAnalyzer") as analyzer:
        cls.return_value.__enter__.return_value = dom
        analyzer.return_value.analyze.return_value = {}
        result = acquire_manual(target, ProjectConfig(), lambda: True)
    assert not result.pages
    assert "cambió" in result.errors[0]


def test_verified_confirmation_without_records_reports_no_dataset(tmp_path):
    from src.config.settings import PathConfig
    target = register_target("https://example.com/data")
    config = ProjectConfig(paths=PathConfig(base_dir=tmp_path))
    batch = ExtractionBatch(method="selenium_manual", pages=[PageSnapshot(
        url=target.url, html="<html><body>Verification Success... Hooray!</body></html>",
        final_url=target.url, method="selenium_manual", status_code=200)])
    with patch("src.extractor.manual_session.acquire_manual", return_value=batch):
        result = ExtractionService(config).run(target, diagnosis=diagnosis(), manual_wait=lambda: True,
                                               formats=["json"], max_pages=1)
    assert result["status"] == "error"
    assert result["manual_verification"] is True
    assert result["outputs"] == {}
    assert any("no se encontraron registros extraíbles" in error for error in result["errors"])


def test_evidence_does_not_keep_sensitive_url_parameters():
    from src.extractor.manual_session import _verified_status
    target = "https://example.com/data?token=secret_value"
    dom = prepare_dom(url=target)
    dom._driver.get_log.return_value = [performance(url=target)]
    evidence = {}
    assert _verified_status(dom._driver, target, evidence) == 200
    assert "secret_value" not in repr(evidence)
    assert len(evidence) == 1
