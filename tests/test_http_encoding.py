from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.extractor.requests_extractor import RequestsExtractor
from src.inspector.http_inspector import HttpInspector
from src.utils.http_encoding import decode_http_body


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/header":
            body, content_type = '<p>“México”</p>'.encode('utf-8'), 'text/html; charset=utf-8'
        elif self.path == "/meta":
            body, content_type = '<meta charset="utf-8"><p>“México”</p>'.encode('utf-8'), 'text/html'
        else:
            body, content_type = '<p>café</p>'.encode('iso-8859-1'), 'text/html; charset=iso-8859-1'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


@pytest.mark.parametrize(('path', 'expected'), [('/header', '“México”'),
                                                  ('/meta', '“México”'),
                                                  ('/latin', 'café')])
def test_diagnosis_and_extraction_decode_server_text(local_server, path, expected):
    url = local_server + path
    inspector = HttpInspector(allow_private_network=True)
    try:
        result = inspector.inspect(url, check_robots=False)
    finally:
        inspector.close()
    assert expected in result['_raw_body']
    batch = RequestsExtractor(allow_private_network=True).extract(url, {'max_pages': 1, 'delay_s': 0})
    assert batch.pages and expected in batch.pages[0].html


def test_invalid_charset_or_bytes_are_reported(caplog):
    assert '\ufffd' in decode_http_body(b'\xff', 'text/plain; charset=utf-8')
    assert 'incompatibles' in caplog.text
    assert decode_http_body(b'abc', 'text/plain; charset=unknown-charset') == 'abc'
    assert 'desconocido' in caplog.text
