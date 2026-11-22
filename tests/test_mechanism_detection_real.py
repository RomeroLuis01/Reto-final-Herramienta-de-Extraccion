"""Diagnóstico de mecanismos reales contra servidor local controlado."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.config.settings import AnalysisTarget, DiagnosticParameters, PathConfig, ProjectConfig
from src.inspector.html_analyzer import HtmlAnalyzer
from src.inspector.web_inspector import WebInspector


HTML = """<!doctype html><html><body><div id="content">Inicial</div>
<button id="show">Mostrar</button>
<script>
document.getElementById('show').addEventListener('click', function() {
  document.getElementById('content').textContent = 'Contenido por interacción';
});
fetch('/api/fetch');
const xhr = new XMLHttpRequest(); xhr.open('GET', '/api/xhr'); xhr.send();
fetch('/graphql', {method:'POST', headers:{'Content-Type':'application/json'},
                   body:JSON.stringify({query:'{ records { id } }'})});
setTimeout(function() { document.getElementById('content').textContent =
 'Contenido adicional generado después del tiempo de espera controlado'; }, 600);
</script></body></html>"""


@pytest.fixture
def site():
    requests_seen = []
    class Handler(BaseHTTPRequestHandler):
        def respond(self, body, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            requests_seen.append(("GET", self.path))
            if self.path == "/robots.txt":
                self.respond(b"User-agent: *\nAllow: /\n", "text/plain")
            elif self.path.startswith("/api/"):
                self.respond(b'{"items":[{"id":1}]}', "application/json")
            else:
                self.respond(HTML.encode("utf-8"), "text/html; charset=utf-8")
        def do_POST(self):
            requests_seen.append(("POST", self.path))
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.respond(b'{"data":{"records":[{"id":1}]}}', "application/json")
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", requests_seen
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_static_hints_are_not_observed_network_or_timed_content(site, tmp_path):
    url, seen = site
    config = ProjectConfig(paths=PathConfig(base_dir=tmp_path))
    target = AnalysisTarget(url=url + "/page", allow_private_network=True)
    result = WebInspector(config).inspect(target, use_selenium=False)
    assert result["network"]["graphql_inline_hint"]
    assert result["network"]["xhr_fetch_inline_hint"]
    assert not result["network"]["graphql_network_hint"]
    assert not result["network"]["xhr_fetch_observed"]
    assert result["interactions"]["user_event_listener_hints_count"] == 1
    assert result["interactions"]["requires_interaction_probable"]
    assert result["acquisition_mechanisms"]["time_dependent_hints"]["uses_set_timeout"]
    assert not result["acquisition_mechanisms"]["timed_text_growth_heuristic"]
    assert not any(path.startswith("/api/") or path == "/graphql" for _, path in seen)


def test_scripted_user_event_hint_requires_specific_event():
    inert = HtmlAnalyzer("<script>node.addEventListener('load', f);</script>").analyze()
    click = HtmlAnalyzer("<script>node.addEventListener ('click', f);</script>").analyze()
    assert inert["interaction_indicators"]["user_event_listener_hints_count"] == 0
    assert click["interaction_indicators"]["requires_interaction_probable"]


@pytest.mark.selenium
def test_chrome_observes_network_and_timed_content_on_local_site(site, tmp_path):
    url, seen = site
    config = ProjectConfig(paths=PathConfig(base_dir=tmp_path))
    config.selenium.headless = True
    target = AnalysisTarget(url=url + "/page", allow_private_network=True,
                            parameters=DiagnosticParameters(wait_after_load_s=1.5))
    result = WebInspector(config).inspect(target, use_selenium=True)
    assert result["selenium_status"] == "executed", result["errors"]
    network = result["network"]
    assert network["xhr_fetch_observed"] and network["graphql_network_hint"]
    requests_by_path = {item["url"].removeprefix(url): item for item in network["network_requests"]}
    assert requests_by_path["/api/fetch"]["resource_type"] == "fetch"
    assert requests_by_path["/api/xhr"]["resource_type"] == "xhr"
    assert requests_by_path["/graphql"]["method"] == "POST"
    assert requests_by_path["/graphql"]["possible_data_relation"] == "probable_graphql"
    assert ("GET", "/api/fetch") in seen and ("GET", "/api/xhr") in seen
    assert ("POST", "/graphql") in seen
    assert result["acquisition_mechanisms"]["timed_text_growth_heuristic"]
    assert result["interactions"]["user_event_listener_hints_count"] == 1
    assert "authorized_endpoints" not in network
