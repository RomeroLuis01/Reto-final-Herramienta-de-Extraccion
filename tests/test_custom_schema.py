"""Inferencia prudente de componentes web repetidos."""

import pytest

from src.extractor.base import PageSnapshot
from src.selector.builder import SchemaBuildError, SchemaBuilder


def build(html: str):
    return SchemaBuilder(sample_page=PageSnapshot(url="https://example.com", html=html)).build()


def test_repeated_custom_components_extract_titles_and_urls():
    html = """<main>
      <video-card><a href='/one'><span>Primer video</span></a></video-card>
      <video-card><a href='/two'><span>Segundo video</span></a></video-card>
    </main>"""
    schema = build(html)
    records = schema.apply(html, base_url="https://example.com")
    assert schema.base_selector == "video-card"
    assert [(r["titulo"], r["enlace"]) for r in records] == [
        ("Primer video", "https://example.com/one"),
        ("Segundo video", "https://example.com/two"),
    ]


@pytest.mark.parametrize("html", [
    "<main><video-card></video-card><video-card></video-card></main>",
    "<nav><nav-item><a href='/a'>Inicio</a></nav-item>"
    "<nav-item><a href='/b'>Ayuda</a></nav-item></nav>",
    "<main><video-card><a href='/a'>Solo uno</a></video-card></main>",
    "<main><video-card><a href='/other'></a><a href='/a'>Uno</a></video-card>"
    "<video-card><a href='/other'></a><a href='/b'>Dos</a></video-card></main>",
])
def test_no_inference_for_empty_navigation_single_or_ambiguous_components(html):
    with pytest.raises(SchemaBuildError, match="no se guardaron datos"):
        build(html)
