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

        table_selectors = self._candidate_table_selectors()
        if not table_selectors:
            table_selectors = ["table"]
        for table_selector in table_selectors:
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

        # Los componentes web usan etiquetas propias, que no aparecen entre
        # los selectores HTML habituales. Solo considerar hermanos repetidos
        # que contengan enlaces con texto, no navegación o componentes vacíos.
        custom_tags = sorted({
            node.name for node in soup.find_all(True)
            if re.fullmatch(r"[a-z][a-z0-9]*-[a-z0-9-]+", node.name or "")
        })
        for tag in custom_tags:
            nodes = soup.find_all(tag)
            if len(nodes) < 2 or any(
                node.find_parent(["nav", "header", "footer", "aside"])
                for node in nodes
            ):
                continue
            groups: dict[int, list[Any]] = {}
            for node in nodes:
                groups.setdefault(id(node.parent), []).append(node)
            repeated = max(groups.values(), key=len)
            if len(repeated) < 2 or len(repeated) != len(nodes):
                continue

            def consistent_link(node: Any) -> bool:
                links = node.find_all("a", href=True)
                text_link = next(
                    (link for link in links if link.get_text(" ", strip=True)), None
                )
                return bool(text_link and links[0].get("href") == text_link.get("href"))

            if not all(consistent_link(node) for node in repeated[:3]):
                continue
            schema = ExtractionSchema(
                name=f"automatico_{tag}",
                description="Esquema inferido de componentes web repetidos",
                base_selector=tag,
                fields=[
                    FieldSelector("titulo", "a[href]", attribute="text"),
                    FieldSelector("enlace", "a[href]", attribute="href"),
                ],
            )
            score = len(schema.fields) * 10 + min(len(nodes), 20)
            if score > best_score:
                best, best_score = schema, score

        if best is None:
            raise SchemaBuildError(
                "No fue posible inferir campos del contenido recibido; no se guardaron datos. "
                "Compruebe que la página muestre información y seleccione un esquema JSON "
                "con 'base_selector' y 'fields' en la interfaz o mediante --schema."
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
        rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
        data_rows = [
            row for row in rows
            if row.find("td", recursive=False) and not row.find_parent("thead")
        ]
        if not data_rows:
            return None
        thead = table.find("thead", recursive=False)
        header_row = thead.find("tr") if thead else None
        if header_row is None and rows:
            cells = rows[0].find_all(["th", "td"], recursive=False)
            if cells and all(cell.name == "th" for cell in cells):
                header_row = rows[0]
        header_cells = (
            header_row.find_all(["th", "td"], recursive=False) if header_row else []
        )
        headers = [cell.get_text(" ", strip=True) for cell in header_cells]
        count = max(
            len(row.find_all(["th", "td"], recursive=False)) for row in data_rows
        )
        fields = []
        used: set[str] = set()
        for index in range(count):
            header = (
                headers[index]
                if index < len(headers) and headers[index]
                else f"Columna {index + 1}"
            )
            base_name = SchemaBuilder._safe_name(header, index)
            name = base_name
            suffix = 2
            while name in used:
                name = f"{base_name}_{suffix}"
                suffix += 1
            used.add(name)
            fields.append(
                FieldSelector(
                    name=name,
                    selector=f":is(td, th):nth-child({index + 1})",
                    selector_type="css",
                    attribute="text",
                )
            )
        base = f"{table_selector} > tbody > tr:has(> td), {table_selector} > tr:has(> td)"
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
