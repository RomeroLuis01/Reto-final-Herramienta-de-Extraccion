"""Inferencia de encabezados y extracción de tablas HTML."""

from src.extractor.base import PageSnapshot
from src.selector.builder import SchemaBuilder


def extract(html):
    schema = SchemaBuilder(sample_page=PageSnapshot(url="https://example.com", html=html)).build()
    records = schema.apply(html, base_url="https://example.com")
    return schema, [{k: v for k, v in record.items() if not k.startswith("_")} for record in records]


def test_thead_headers_and_row_headers_are_kept_separate():
    schema, records = extract("""<table><thead><tr><th>País</th><th>Población</th></tr></thead>
    <tbody><tr><th>México</th><td>100</td></tr><tr><th>Chile</th><td>20</td></tr></tbody></table>""")
    assert [f.name for f in schema.fields] == ["país", "población"]
    assert records == [{"país": "México", "población": "100"}, {"país": "Chile", "población": "20"}]


def test_duplicate_headers_keep_both_fields_and_rows_without_tbody():
    schema, records = extract("""<table><tr><th>Precio</th><th>Precio</th><th>Precio 2</th></tr>
    <tr><td>10</td><td>20</td><td>30</td></tr></table>""")
    assert [f.name for f in schema.fields] == ["precio", "precio_2", "precio_2_2"]
    assert records == [{"precio": "10", "precio_2": "20", "precio_2_2": "30"}]


def test_no_headers_are_not_invented_from_first_row():
    schema, records = extract("""<table><tbody><tr><td>Uno</td><td>Dos</td></tr>
    <tr><td>Tres</td><td>Cuatro</td></tr></tbody></table>""")
    assert [f.name for f in schema.fields] == ["td_1", "td_2"]
    assert records == [{"td_1": "Uno", "td_2": "Dos"},
                       {"td_1": "Tres", "td_2": "Cuatro"}]


def test_missing_header_uses_actual_cell_tag_without_duplicate_names():
    schema, records = extract("""<table><thead><tr><th>td_2</th><th></th></tr></thead>
      <tbody><tr><th>Primero</th><td>Segundo</td></tr></tbody></table>""")
    assert [field.name for field in schema.fields] == ["td_2", "td_2_2"]
    assert records == [{"td_2": "Primero", "td_2_2": "Segundo"}]


def test_navigation_table_does_not_contaminate_selected_table():
    schema, records = extract("""<nav><table><tr><td>Menú</td><td>Inicio</td></tr></table></nav>
    <main><table><tr><th>Producto</th><th>Precio</th></tr>
    <tr><td>Libro</td><td>20</td></tr><tr><td>Lápiz</td><td>5</td></tr></table></main>""")
    assert records == [{"producto": "Libro", "precio": "20"}, {"producto": "Lápiz", "precio": "5"}]


def test_navigation_only_table_is_not_inferred():
    import pytest
    from src.selector.builder import SchemaBuildError
    with pytest.raises(SchemaBuildError):
        extract('<div role="navigation"><table><tr><td>Inicio</td></tr></table></div>')
