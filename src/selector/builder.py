"""Construcción automática de esquemas desde perfiles y diagnósticos (Avance 2)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.extractor.base import PageSnapshot
from src.monitor.logger import get_logger
from src.selector.engines import make_soup
from src.selector.schema import ExtractionSchema, FieldSelector

logger = get_logger("selector.builder")


class SchemaBuildError(RuntimeError):
    """Se lanza cuando no es posible inferir un esquema de forma segura."""


_GENERIC_RECORD_SELECTORS = (
    "article",
    "div.product",
    "li.product",
    "div.quote",
    "[class*='product']",
    ".card",
    ".item",
    ".result",
    ".entry",
    "tr",
    "li",
)

_DATA_KEYS = ("data", "results", "items", "records", "products", "posts", "features")


class SchemaBuilder:
    """
    Genera un `ExtractionSchema` reutilizando el conocimiento del Avance 1.

    El perfil aporta los selectores candidatos y el diagnóstico aporta el
    formato y la paginación. Si se entrega una página de muestra, el builder
    puede inferir nombres de campos reales (encabezados de tabla, clases,
    claves JSON).
    """

    def __init__(
        self,
        diagnosis: dict[str, Any] | None = None,
        *,
        profile: dict[str, Any] | None = None,
        sample_page: PageSnapshot | None = None,
    ) -> None:
        self.diagnosis = diagnosis or {}
        self.profile = profile or {}
        self.sample_page = sample_page

    def build(self, explicit: dict[str, Any] | None = None) -> ExtractionSchema:
        """Construye el esquema explícito o lo infiere automáticamente."""
        if explicit:
            schema = ExtractionSchema.from_dict(explicit)
            errors = schema.validate()
            if errors:
                raise SchemaBuildError("; ".join(errors))
            return schema

        content_format = self._content_format()
        if content_format in ("json", "api"):
            schema = self._build_json_schema()
        else:
            schema = self._build_html_schema()

        pagination = self.diagnosis.get("pagination") or {}
        schema.pagination_selector = str(pagination.get("pagination_selector") or "")
        schema.next_page_selector = str(pagination.get("pagination_selector") or "")
        schema.metadata.update(
            {
                "origin": "auto",
                "content_format": content_format,
                "render_type": (self.diagnosis.get("technologies") or {}).get(
                    "render_type"
                ),
            }
        )
        return schema

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _content_format(self) -> str:
        if self.sample_page and self.sample_page.content_format:
            return self.sample_page.content_format
        acquisition = self.diagnosis.get("acquisition_mechanisms") or {}
        return str(acquisition.get("content_format") or "html")

    def _candidate_table_selectors(self) -> list[str]:
        candidates: list[str] = []
        sources = (
            (self.profile.get("identified_selectors") or {}).get("tables", []),
            (self.diagnosis.get("candidate_selectors") or {}).get("tables", []),
        )
        for group in sources:
            for selector in group or []:
                if selector and selector not in candidates:
                    candidates.append(str(selector))
        return candidates

    def _build_html_schema(self) -> ExtractionSchema:
        html = self.sample_page.html if self.sample_page else ""
        if not html:
            raise SchemaBuildError(
                "No hay HTML de muestra para inferir campos. Proporcione un esquema con --schema."
            )
        soup = make_soup(html)

        for table_selector in self._candidate_table_selectors():
            try:
                tables = soup.select(table_selector)
            except Exception:
                continue
            for table in tables:
                schema = self._schema_from_table(table, table_selector)
                if schema:
                    return schema

        best: ExtractionSchema | None = None
        best_score = 0
        for selector in _GENERIC_RECORD_SELECTORS:
            try:
                nodes = soup.select(selector)
            except Exception:
                continue
            schema = self._schema_from_generic(nodes, selector)
            score = len(schema.fields) * 10 + min(len(nodes), 20)
            if schema.fields and score > best_score:
                best, best_score = schema, score

        if best is None:
            raise SchemaBuildError(
                "No fue posible inferir campos automáticamente. "
                "Cree un archivo JSON con 'base_selector' y 'fields' y páselo con --schema."
            )
        logger.info(
            "Esquema automático '%s': base=%s, campos=%d",
            best.name,
            best.base_selector,
            len(best.fields),
        )
        return best

    @staticmethod
    def _schema_from_table(table: Any, table_selector: str) -> ExtractionSchema | None:
        rows = table.find_all("tr")
        if not rows:
            return None
        header_cells = table.find_all("th")
        if header_cells:
            headers = [h.get_text(" ", strip=True) for h in header_cells]
        else:
            data_cells = rows[0].find_all(["td", "th"])
            headers = [f"Columna {i + 1}" for i in range(len(data_cells))]
        headers = [h or f"Columna {i + 1}" for i, h in enumerate(headers)]
        if not headers:
            return None

        fields = [
            FieldSelector(
                name=SchemaBuilder._safe_name(header, index),
                selector=f"td:nth-of-type({index + 1})",
                selector_type="css",
                attribute="text",
            )
            for index, header in enumerate(headers)
        ]
        base = f"{table_selector} tbody tr" if table_selector != "table" else "table tbody tr"
        return ExtractionSchema(
            name="tabla_detectada",
            description="Esquema inferido de tabla HTML",
            base_selector=base,
            fields=fields,
            metadata={"table_selector": table_selector},
        )

    @staticmethod
    def _schema_from_generic(nodes: list[Any], selector: str) -> ExtractionSchema:
        schema = ExtractionSchema(
            name=f"automatico_{selector.replace(' ', '_').replace('[', '').replace(']', '')}",
            description="Esquema inferido de contenedores repetidos",
            base_selector=selector,
        )
        if not nodes:
            return schema
        seen: set[str] = set()
        for node in nodes[:3]:
            for element in node.find_all(["span", "a", "h2", "h3", "h4", "small", "time", "p", "td"]):
                text = element.get_text(" ", strip=True)
                if not text or len(text) > 120:
                    continue
                classes = [str(c) for c in element.get("class", []) if str(c)]
                if classes:
                    field_selector = "." + ".".join(classes[:2])
                    key = field_selector
                else:
                    field_selector = element.name
                    key = str(element.name)
                if key in seen:
                    continue
                seen.add(key)
                schema.add_field(
                    FieldSelector(
                        name=SchemaBuilder._safe_name(
                            classes[0] if classes else str(element.name),
                            len(schema.fields),
                        ),
                        selector=field_selector,
                        selector_type="css",
                        attribute=(
                            "href"
                            if element.name == "a" and element.get("href")
                            else "text"
                        ),
                    )
                )
                if len(schema.fields) >= 10:
                    break
            if len(schema.fields) >= 10:
                break
        return schema

    def _build_json_schema(self) -> ExtractionSchema:
        sample = self.sample_page.data if self.sample_page else None
        if sample is None:
            raise SchemaBuildError(
                "No hay datos JSON de muestra para inferir campos. Proporcione --schema."
            )
        base_selector = ""
        items: Any = sample
        if isinstance(sample, dict):
            for key in _DATA_KEYS:
                if isinstance(sample.get(key), list):
                    base_selector = key
                    items = sample[key]
                    break
            else:
                items = sample
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list) or not items:
            raise SchemaBuildError("La respuesta JSON no contiene registros listos para extraer.")

        first = items[0]
        if not isinstance(first, dict):
            raise SchemaBuildError("Los registros JSON no son objetos con campos nombrados.")
        fields = [
            FieldSelector(
                name=self._safe_name(key, index),
                selector=key,
                selector_type="json_path",
                attribute="text",
            )
            for index, key in enumerate(list(first.keys())[:15])
        ]
        return ExtractionSchema(
            name="api_automatico",
            description="Esquema inferido de respuesta JSON",
            base_selector=base_selector,
            fields=fields,
            metadata={"origin": "auto_json", "root_keys": list(first.keys())[:15]},
        )

    @staticmethod
    def _safe_name(raw: str, index: int) -> str:
        name = re.sub(r"[^a-zA-Z0-9áéíóúñÁÉÍÓÚÑ_-]+", "_", str(raw or "").strip())
        name = name.strip("_").lower()
        return name or f"campo_{index + 1}"


def load_schema_file(path: str | Path) -> dict[str, Any]:
    """Carga un esquema declarativo desde un archivo JSON."""
    schema_path = Path(path)
    with open(schema_path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("El archivo de esquema debe contener un objeto JSON.")
    return data
