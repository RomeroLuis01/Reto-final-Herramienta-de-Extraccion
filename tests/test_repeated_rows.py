"""Inferencia de filas modernas y preservación de formatos existentes."""

import pytest

from src.extractor.base import PageSnapshot
from src.selector.builder import SchemaBuildError, SchemaBuilder


def build(html):
    return SchemaBuilder(sample_page=PageSnapshot(url="https://example.com/", html=html)).build()


def test_repeated_rows_are_selected_instead_of_navigation():
    html = '''<nav><ul><li><a class="relative whitespace-nowrap" href="/problems">Problemas</a></li>
      <li><a class="relative whitespace-nowrap" href="/contest">Concursos</a></li>
      <li><a class="relative whitespace-nowrap" href="/discuss">Discusión</a></li></ul></nav>
      <main><div role="table"><div role="rowgroup">
      <div role="row" class="flex row"><div><a href="/problems/one">Primer problema</a></div><div data-field="aceptacion">73.7%</div><div data-field="dificultad">Fácil</div></div>
      <div role="row" class="flex alternate"><div><a href="/problems/two">Segundo problema</a></div><div data-field="aceptacion">58.1%</div><div data-field="dificultad">Media</div></div>
      </div></div></main>'''
    schema = build(html)
    records = schema.apply(html, base_url="https://example.com")
    assert [(row["titulo"], row["enlace"], row["aceptacion"], row["dificultad"]) for row in records] == [
        ("Primer problema", "https://example.com/problems/one", "73.7%", "Fácil"),
        ("Segundo problema", "https://example.com/problems/two", "58.1%", "Media"),
    ]


def test_existing_product_fields_remain_inferred():
    html = '''<main><article class="product_pod"><h3><a href="/one">Uno</a></h3><p class="price_color">$10</p><p class="instock">Disponible</p></article>
      <article class="product_pod"><h3><a href="/two">Dos</a></h3><p class="price_color">$20</p><p class="instock">Disponible</p></article></main>'''
    schema = build(html)
    records = schema.apply(html, base_url="https://example.com")
    assert {"h3", "a", "price_color", "instock"} <= {field.name for field in schema.fields}
    assert len(records) == 2
    assert records[0]["price_color"] == "$10"


def test_explicit_schema_is_not_replaced():
    schema = SchemaBuilder().build({"name": "personalizado", "base_selector": "li",
                                    "fields": [{"name": "dato", "selector": "span"}]})
    assert schema.base_selector == "li"
    assert [field.name for field in schema.fields] == ["dato"]


def test_navigation_alone_is_not_a_dataset():
    html = "<nav><ul><li><a href='/a'>Inicio</a></li><li><a href='/b'>Ayuda</a></li></ul></nav>"
    with pytest.raises(SchemaBuildError):
        build(html)


def test_alternating_classes_without_roles_include_all_rows_not_other_siblings():
    html = '''<nav><ul><li><a href="/menu">Menú</a></li></ul></nav><main><section>
      <h2>Ejercicios</h2><div class="heading">Título y estado</div>
      <div class="row light"><div><a href="/one">Uno</a></div><div data-field="aceptacion">10%</div><div data-field="dificultad">Fácil</div></div>
      <div class="row dark"><div><a href="/two">Dos</a></div><div data-field="aceptacion">20%</div><div data-field="dificultad">Media</div></div>
      <div class="row light"><div><a href="/three">Tres</a></div><div data-field="aceptacion">30%</div><div data-field="dificultad">Difícil</div></div>
      <div class="row dark"><div><a href="/four">Cuatro</a></div><div data-field="aceptacion">40%</div><div data-field="dificultad">Fácil</div></div>
      <div class="pagination"><a href="/next">Siguiente</a></div>
      </section></main>'''
    schema = build(html)
    records = schema.apply(html, base_url="https://example.com")
    assert [record["titulo"] for record in records] == ["Uno", "Dos", "Tres", "Cuatro"]
    assert [record["enlace"] for record in records] == [
        "https://example.com/one", "https://example.com/two",
        "https://example.com/three", "https://example.com/four",
    ]
    assert [record["aceptacion"] for record in records] == ["10%", "20%", "30%", "40%"]


def test_repeated_rows_use_html_tags_and_stable_semantic_attributes():
    html = '''<main><section>
      <div><a href="/one">Uno</a><p>Primero</p><p>Otro</p><span aria-label="Dificultad">Fácil</span><span aria-label="Título uno">1</span></div>
      <div><a href="/two">Dos</a><p>Segundo</p><p>Otro más</p><span aria-label="Dificultad">Media</span><span aria-label="Título dos">2</span></div>
      </section></main>'''
    schema = build(html)
    assert [field.name for field in schema.fields] == [
        "titulo", "enlace", "p", "p_2", "dificultad", "span"
    ]
    records = schema.apply(html, base_url="https://example.com")
    assert [(row["p"], row["p_2"], row["dificultad"], row["span"]) for row in records] == [
        ("Primero", "Otro", "Fácil", "1"), ("Segundo", "Otro más", "Media", "2")
    ]


def test_repeated_rows_duplicate_semantic_labels_do_not_overwrite_fields():
    html = '''<main>
      <div><a href="/one">Uno</a><p data-field="titulo">Primero</p><p data-field="titulo">Otro</p></div>
      <div><a href="/two">Dos</a><p data-field="titulo">Segundo</p><p data-field="titulo">Más</p></div>
      </main>'''
    schema = build(html)
    assert [field.name for field in schema.fields] == ["titulo", "enlace", "titulo_2", "titulo_3"]
    records = schema.apply(html, base_url="https://example.com")
    assert [record["titulo_3"] for record in records] == ["Otro", "Más"]


def test_custom_components_exclude_role_navigation():
    html = """<div role="navigation"><product-card><a href="/menu">Menú</a></product-card></div>
    <main><product-card><a href="/one">Uno</a></product-card>
    <product-card><a href="/two">Dos</a></product-card></main>"""
    schema = SchemaBuilder(sample_page=PageSnapshot(url="https://example.com", html=html)).build()
    records = schema.apply(html, base_url="https://example.com")
    assert [row["titulo"] for row in records] == ["Uno", "Dos"]
