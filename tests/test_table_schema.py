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
    assert [f.name for f in schema.fields] == ["columna_1", "columna_2"]
    assert records == [{"columna_1": "Uno", "columna_2": "Dos"},
                       {"columna_1": "Tres", "columna_2": "Cuatro"}]
