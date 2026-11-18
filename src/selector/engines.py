"""Motores de selección CSS/XPath/regex/atributo/JSONPath (Módulo 4)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

if TYPE_CHECKING:  # Evita import circular con schema.py
    from src.selector.schema import FieldSelector


def make_soup(html: str) -> BeautifulSoup:
    """BeautifulSoup con lxml y fallback seguro."""
    try:
        return BeautifulSoup(html or "", "lxml")
    except Exception:  # pragma: no cover
        return BeautifulSoup(html or "", "html.parser")


def json_lookup(data: Any, path: str) -> list[Any]:
    """
    Consulta simple tipo JSONPath con puntos, índices y comodines.

    Ejemplos: ``data.items.title``, ``results[0].name``, ``items[*].id``.
    """
    if not path or path in ("$", "root"):
        return [data]
    tokens = re.findall(r"[^.\[\]]+|\[\*\]|\[\d+\]", path)
    current: list[Any] = [data]

    for token in tokens:
        next_values: list[Any] = []
        if token.startswith("["):
            for item in current:
                if isinstance(item, (list, tuple)):
                    if token == "[*]":
                        next_values.extend(item)
                    else:
                        index = int(token[1:-1])
                        if -len(item) <= index < len(item):
                            next_values.append(item[index])
                elif isinstance(item, dict) and token == "[*]":
                    next_values.extend(item.values())
        else:
            for item in current:
                if isinstance(item, dict) and token in item:
                    next_values.append(item[token])
                elif isinstance(item, (list, tuple)):
                    for sub in item:
                        if isinstance(sub, dict) and token in sub:
                            next_values.append(sub[token])
        current = next_values
        if not current:
            break
    return current


def _attribute_value(
    node: Any,
    attribute: str,
    base_url: str = "",
) -> Any:
    """Obtiene texto o atributo de un nodo BeautifulSoup."""
    getter = getattr(node, "get", None)
    if attribute in ("text", "texto", "inner_text", ""):
        text_node = getattr(node, "get_text", None)
        return text_node(" ", strip=True) if text_node else str(node).strip()
    if getter is None:
        return None
    value = getter(attribute)
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        value = " ".join(str(v) for v in value)
    value = str(value).strip()
    if attribute in ("href", "src", "action") and base_url and value:
        if not value.startswith(("http://", "https://", "mailto:", "javascript:", "#")):
            value = urljoin(base_url, value)
    return value


def extract_field_values(
    source: Any,
    field: "FieldSelector",
    *,
    base_url: str = "",
    content_format: str = "html",
) -> list[Any]:
    """
    Extrae todos los valores de un campo según su tipo de selector.

    Args:
        source: HTML (str) o datos estructurados (dict/list).
        field: Definición del campo.
        base_url: URL base para resolver enlaces relativos.
        content_format: html, json, xml, csv o texto.

    Returns:
        Lista de valores encontrados (vacía si no hay coincidencias).
    """
    if content_format in ("json", "api") or field.selector_type == "json_path":
        values = json_lookup(source, field.selector)
        return [v for v in values if v is not None]

    if isinstance(source, str):
        html = source
    else:
        html = str(source)

    if field.selector_type == "xpath":
        try:
            from lxml import html as lxml_html

            root = lxml_html.fromstring(html)
            nodes = root.xpath(field.selector)
            values = [
                node.text_content().strip()
                if hasattr(node, "text_content")
                else str(node).strip()
                for node in nodes
            ]
            return [v for v in values if v != ""]
        except Exception:
            return []

    if field.selector_type == "regex":
        values = []
        for match in re.finditer(field.selector, html, flags=re.IGNORECASE):
            value = match.group(1) if match.groups() else match.group(0)
            values.append(value.strip())
        return values

    soup = source if isinstance(source, BeautifulSoup) else make_soup(html)
    try:
        nodes = soup.select(field.selector)
    except Exception:
        return []
    values = []
    for node in nodes:
        value = _attribute_value(node, field.attribute, base_url)
        if value is not None and value != "":
            values.append(value)
    return values


def select_containers(
    source: Any,
    selector: str,
    *,
    content_format: str = "html",
) -> list[Any]:
    """Selecciona los contenedores de registros (base_selector)."""
    if not selector or selector in ("$", "root"):
        return [source]
    if content_format in ("json", "api"):
        found = json_lookup(source, selector)
        containers: list[Any] = []
        for item in found:
            if isinstance(item, list):
                containers.extend(item)
            else:
                containers.append(item)
        return containers
    soup = source if isinstance(source, BeautifulSoup) else make_soup(str(source))
    try:
        return soup.select(selector)
    except Exception:
        return []
