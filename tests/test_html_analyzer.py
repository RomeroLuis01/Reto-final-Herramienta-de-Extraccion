"""
Tests — Módulo 2: Inspector HTML estático.

Verifica la detección de frameworks, paginación, CAPTCHA,
autenticación y tipo de renderizado usando HTML de prueba.
"""

import pytest
from src.inspector.html_analyzer import HtmlAnalyzer


# ---------------------------------------------------------------------------
# HTML de prueba
# ---------------------------------------------------------------------------

HTML_STATIC = """<!DOCTYPE html>
<html>
<head>
    <title>Sitio de prueba estático</title>
    <meta name="generator" content="WordPress 6.4">
    <link rel="stylesheet" href="/wp-content/themes/tema/style.css">
</head>
<body>
    <h1>Bienvenido</h1>
    <p>Contenido estático de prueba.</p>
    <table id="datos">
        <thead><tr><th>Nombre</th><th>Valor</th></tr></thead>
        <tr><td>Fila 1</td><td>100</td></tr>
    </table>
    <nav class="pagination">
        <a href="/page/1" class="page-link">1</a>
        <a href="/page/2" class="page-link">2</a>
        <a href="/page/3" rel="next">Siguiente</a>
    </nav>
</body>
</html>"""

HTML_REACT = """<!DOCTYPE html>
<html>
<head><title>React App</title></head>
<body>
    <div id="root" data-reactroot></div>
    <script src="/static/js/react.min.js"></script>
    <script src="/static/js/react-dom.min.js"></script>
    <script>window.__NEXT_DATA__ = {"props":{}}</script>
</body>
</html>"""

HTML_CAPTCHA = """<!DOCTYPE html>
<html>
<body>
    <form>
        <input type="text" name="usuario">
        <div class="g-recaptcha" data-sitekey="xxxx"></div>
        <button type="submit">Enviar</button>
    </form>
    <script src="https://www.google.com/recaptcha/api.js"></script>
</body>
</html>"""

HTML_LOGIN = """<!DOCTYPE html>
<html>
<body>
    <form action="/login" method="POST" id="login-form">
        <input type="text" name="usuario" placeholder="Usuario">
        <input type="password" name="contraseña" placeholder="Contraseña">
        <button type="submit">Iniciar sesión</button>
    </form>
</body>
</html>"""

HTML_INFINITE_SCROLL = """<!DOCTYPE html>
<html>
<head>
    <script src="/js/infinite-scroll.min.js"></script>
</head>
<body>
    <div class="items-container" data-infinite-scroll="true">
        <div class="item">Item 1</div>
        <div class="item">Item 2</div>
    </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHtmlAnalyzer:
    """Tests del analizador HTML estático."""

    def test_static_site_detection(self) -> None:
        analyzer = HtmlAnalyzer(HTML_STATIC)
        result = analyzer.analyze()
        assert result["render_type"] == "static"
        assert result["static_html_size"] > 0

    def test_title_extraction(self) -> None:
        analyzer = HtmlAnalyzer(HTML_STATIC)
        result = analyzer.analyze()
        assert result["title"] == "Sitio de prueba estático"

    def test_meta_generator_wordpress(self) -> None:
        analyzer = HtmlAnalyzer(HTML_STATIC)
        result = analyzer.analyze()
        assert result["meta_generator"] is not None
        assert "WordPress" in result["meta_generator"]

    def test_table_detection(self) -> None:
        analyzer = HtmlAnalyzer(HTML_STATIC)
        result = analyzer.analyze()
        assert result["tables_count"] == 1
        assert result["tables"][0]["has_thead"] is True

    def test_pagination_detected(self) -> None:
        analyzer = HtmlAnalyzer(HTML_STATIC)
        result = analyzer.analyze()
        assert result["pagination"]["detected"] is True
        assert result["pagination"].get("has_rel_next") is True

    def test_react_framework_detected(self) -> None:
        analyzer = HtmlAnalyzer(HTML_REACT)
        result = analyzer.analyze()
        fw = result["js_frameworks"]
        # React debe detectarse con al menos evidencia probable
        assert "React" in fw or "Next.js" in fw

    def test_captcha_detected(self) -> None:
        analyzer = HtmlAnalyzer(HTML_CAPTCHA)
        result = analyzer.analyze()
        assert len(result["captcha_indicators"]) > 0
        indicators = [c["indicator"] for c in result["captcha_indicators"]]
        assert any("recaptcha" in i for i in indicators)

    def test_login_form_detected(self) -> None:
        analyzer = HtmlAnalyzer(HTML_LOGIN)
        result = analyzer.analyze()
        assert result["auth_indicators"]["has_login_form"] is True
        # Debe haber al menos un formulario con contraseña
        assert any(f["has_password"] for f in result["forms"])

    def test_infinite_scroll_detected(self) -> None:
        analyzer = HtmlAnalyzer(HTML_INFINITE_SCROLL)
        result = analyzer.analyze()
        assert result["infinite_scroll_hints"]["detected"] is True

    def test_scripts_count(self) -> None:
        analyzer = HtmlAnalyzer(HTML_REACT)
        result = analyzer.analyze()
        assert result["scripts_count"] >= 2

    def test_empty_html(self) -> None:
        analyzer = HtmlAnalyzer("")
        result = analyzer.analyze()
        assert result["static_html_size"] == 0
        assert result["scripts_count"] == 0

    def test_meta_tags_extraction(self) -> None:
        html = '<html><head><meta name="description" content="Test site"></head><body></body></html>'
        analyzer = HtmlAnalyzer(html)
        result = analyzer.analyze()
        assert "description" in result["meta_tags"]
        assert result["meta_tags"]["description"] == "Test site"

    def test_static_detection_is_probable_never_confirmed_and_no_prose_false_positives(self) -> None:
        html = """<!DOCTYPE html>
<html>
<body>
  <p>En este articulo comparamos React, Svelte, Tailwind y jQuery usando clases tw-note.</p>
  <script src="/static/js/react.min.js"></script>
</body>
</html>"""
        analyzer = HtmlAnalyzer(html)
        result = analyzer.analyze()
        fw = result["js_frameworks"]
        assert fw["React"]["confidence"] == "probable"
        assert "Svelte" not in fw
        assert "Tailwind CSS" not in fw
        assert "jQuery" not in fw

