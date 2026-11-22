"""Candidatos de selección basados exclusivamente en HTML ya diagnosticado."""
from __future__ import annotations

from typing import Any

from src.extractor.base import PageSnapshot
from src.selector.builder import SchemaBuilder, SchemaBuildError
from src.selector.engines import make_soup


def preview_candidates(html: str, diagnosis: dict[str, Any], url: str) -> list[dict[str, Any]]:
    """Devuelve esquemas candidatos y hasta tres ejemplos sin conservar el HTML."""
    if not html:
        return []
    builder = SchemaBuilder(diagnosis, sample_page=PageSnapshot(url=url, html=html))
    soup = make_soup(html)
    schemas = []
    try:
        schemas.append(builder.build())
    except SchemaBuildError:
        pass
    for table in soup.select("table"):
        if builder._is_navigation(table):
            continue
        schema = builder._schema_from_table(table, builder._element_path(table))
        if schema:
            schemas.append(schema)
    exclusion = ":not(nav *, header *, footer *, aside *, [role='navigation'] *)"
    for selector in ("article", ".product", ".quote", ".card", ".item", ".result", "li"):
        nodes = [node for node in soup.select(selector) if not builder._is_navigation(node)]
        if len(nodes) < 2:
            continue
        schema = builder._schema_from_generic(nodes, f":is({selector}){exclusion}")
        if schema.fields:
            schemas.append(schema)
    result = []
    seen = set()
    for schema in schemas:
        key = schema.base_selector
        if key in seen or schema.validate():
            continue
        seen.add(key)
        records = schema.apply(html, base_url=url)
        examples = [{k: v for k, v in row.items() if not k.startswith("_")}
                    for row in records[:3]]
        if examples:
            result.append({"schema": schema.to_dict(), "examples": examples})
        if len(result) >= 20:
            break
    return result


def candidate_fields_text(schema: dict[str, Any]) -> str:
    """Prepara campos editables con el formato del diálogo manual."""
    return "\n".join(
        "; ".join(str(field.get(key, "")) for key in
                  ("name", "selector", "selector_type", "attribute"))
        for field in schema.get("fields", []))
