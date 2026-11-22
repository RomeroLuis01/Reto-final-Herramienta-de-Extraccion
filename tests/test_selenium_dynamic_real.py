"""Pruebas locales de extracción con Chrome real (requiere Chrome y ChromeDriver)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest
from selenium import webdriver
from selenium.common.exceptions import WebDriverException

from src.config.settings import AnalysisTarget, PathConfig, ProjectConfig
from src.extractor.extraction_service import ExtractionService


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/scroll":
            body = '''<html><body><main id="feed" style="height:1800px">
                <div class="item">Inicial</div></main><script>
                let count = 0;
                window.addEventListener('scroll', () => {
                  if (count >= 3) return;
                  count++;
                  const item = document.createElement('div');
                  item.className = 'item'; item.textContent = 'Scroll ' + count + ' contenido adicional';
                  item.style.height = '800px'; document.getElementById('feed').appendChild(item);
                  document.getElementById('feed').style.height = (1800 + 800 * count) + 'px';
                });</script></body></html>'''
        elif path == "/button":
            body = '''<html><body><main id="feed"><div class="item">Inicial</div></main>
                <button id="more" onclick="let e=document.createElement('div'); e.className='item';
                e.textContent='Cargado'; document.getElementById('feed').appendChild(e); this.remove();">
                Cargar más</button></body></html>'''
        elif path == "/page2":
            body = '<html><body><div class="item">Página 2</div></body></html>'
        else:
            body = '''<html><body><div class="item">Página 1</div>
                      <a rel="next" href="/page2">Siguiente</a></body></html>'''
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def local_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


@pytest.fixture(scope="module")
def chrome_available():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    try:
        driver = webdriver.Chrome(options=options)
    except WebDriverException as exc:
        pytest.skip(f"Chrome/ChromeDriver no disponible: {type(exc).__name__}")
    driver.quit()


@pytest.mark.selenium
@pytest.mark.parametrize(("path", "rules", "mechanism", "expected"), [
    ("/scroll", {"infinite_scroll": True}, "scroll_infinito", "Scroll 2"),
    ("/button", {"load_more_selector": "#more"}, "cargar_mas", "Cargado"),
    ("/page1", {}, "paginacion", "Página 2"),
])
def test_chrome_dynamic_extraction(local_site, chrome_available, tmp_path, path, rules, mechanism, expected):
    url = local_site + path
    target = AnalysisTarget(url=url, allow_private_network=True, extraction_rules=rules)
    config = ProjectConfig(paths=PathConfig(base_dir=tmp_path))
    config.selenium.headless = True
    config.extraction.delay_s = 0
    diagnosis = {"access_status": "accesible", "protection": {}, "viability": {},
                 "pagination": {"pagination_selector": "a[rel='next']"},
                 "acquisition_mechanisms": {"content_format": "html"}}
    schema = {"name": "items", "base_selector": ".item", "fields": [
        {"name": "text", "selector": ".item", "selector_type": "css"}]}
    result = ExtractionService(config).run(target, diagnosis=diagnosis, schema_config=schema,
        strategy="selenium", max_pages=2, max_records=20, delay_s=0, formats=["json"])
    assert result["status"] == "ok", result["errors"]
    assert result["coverage"]["captured_pages"] == 2
    assert result["coverage"]["mechanism"] == mechanism
    assert result["coverage"]["stopped_reason"] == "límite de capturas alcanzado"
    raw = json.loads(Path(result["outputs"]["json"]["raw"]).read_text(encoding="utf-8"))
    assert any(expected in row["text"] for row in raw)
