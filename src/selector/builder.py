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
                all_nodes = soup.select(selector)
            except Exception:
                continue
            nodes = [node for node in all_nodes if not self._is_navigation(node)]
            if len(nodes) != len(all_nodes):
                selector = f":is({selector}):not(nav *, header *, footer *, aside *, [role='navigation'] *)"
            schema = self._schema_from_generic(nodes, selector)
            score = len(schema.fields) * 10 + min(len(nodes), 20)
            if schema.fields and score > best_score:
                best, best_score = schema, score

        # Algunas aplicaciones modernas representan cada fila con divs, no con
        # tablas ni listas HTML. Solo inferir grupos de hermanos con contenido
        # estructurado y enlaces con texto; nunca agrupar por clase global.
        for schema, count in self._repeated_div_schemas(soup):
            score = len(schema.fields) * 10 + min(count, 20)
            if score > best_score:
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
    def _is_navigation(node: Any) -> bool:
        return bool(node.find_parent(["nav", "header", "footer", "aside"]) or
                    node.find_parent(attrs={"role": "navigation"}))

    @staticmethod
    def _element_path(node: Any, stop: Any | None = None) -> str:
        """Ruta estructural acotada, sin usar clases de presentación como campos."""
        parts = []
        while node is not None and getattr(node, "name", None) not in (None, "[document]") and node is not stop:
            siblings = node.find_previous_siblings(node.name)
            parts.append(f"{node.name}:nth-of-type({len(siblings) + 1})")
            node = node.parent
        return " > ".join(reversed(parts))

    @classmethod
    def _repeated_div_schemas(cls, soup: Any) -> list[tuple[ExtractionSchema, int]]:
        candidates: list[tuple[ExtractionSchema, int]] = []
        for parent in soup.find_all(True):
            if parent.name in ("nav", "header", "footer", "aside") or cls._is_navigation(parent):
                continue
            groups: list[tuple[str, list[Any]]] = []
            current: list[Any] = []
            signature: tuple[Any, ...] | None = None
            # Solo agrupar secuencias contiguas de hermanos estructuralmente
            # compatibles; una cabecera u otro elemento corta la secuencia.
            for child in parent.find_all(recursive=False):
                role = str(child.get("role") or "")
                link = next((a for a in child.find_all("a", href=True)
                             if a.get_text(" ", strip=True)), None)
                cells = child.find_all(recursive=False)
                if child.name == "div" and role in ("", "row") and link and len(cells) >= 3:
                    next_signature = (role, tuple((cell.name, cell.get("data-field")) for cell in cells),
                                      cls._element_path(link, child))
                else:
                    next_signature = None
                if next_signature != signature:
                    if current:
                        groups.append((str(signature[0]), current))
                    current = []
                    signature = next_signature
                if next_signature is not None:
                    current.append(child)
            if current:
                groups.append((str(signature[0]), current))
            for role, nodes in groups:
                if len(nodes) < 2 or any(cls._is_navigation(node) for node in nodes):
                    continue
                # Los motores anteriores ya conocen estos contenedores.
                if any(word in " ".join(node.get("class", [])).lower()
                       for node in nodes for word in ("product", "quote", "card", "item", "result", "entry")):
                    continue
                sample = nodes[:3]
                links = [next((a for a in node.find_all("a", href=True)
                               if a.get_text(" ", strip=True)), None) for node in sample]
                if any(link is None for link in links):
                    continue
                children = nodes[0].find_all(recursive=False)
                if len(children) < 3:
                    continue
                # Selección exacta de los hermanos comprobados, incluso con
                # clases alternadas, sin incluir cabeceras ni otros grupos.
                positions = [len(node.find_previous_siblings()) + 1 for node in nodes]
                group_selector = ":is(" + ", ".join(f"div:nth-child({pos})" for pos in positions) + ")"
                parent_path = cls._element_path(parent)
                base = f"{parent_path} > {group_selector}" if parent_path else group_selector
                try:
                    if list(soup.select(base)) != nodes:
                        continue
                except Exception:
                    continue
                child_selector = 'body > div[role="row"]' if role else "body > div"
                link_path = cls._element_path(links[0], nodes[0])
                if not all(cls._element_path(link, node) == link_path
                           for link, node in zip(links, sample)):
                    continue
                anchor = f"{child_selector} > {link_path}"
                fields = [FieldSelector("titulo", anchor, attribute="text"),
                          FieldSelector("enlace", anchor, attribute="href")]
                used = {field.name for field in fields}
                for index, child in enumerate(children, 1):
                    if child is links[0] or child.find("a", href=True):
                        continue
                    text = child.get_text(" ", strip=True)
                    if not text or len(text) > 160:
                        continue
                    peers = [node.find_all(recursive=False) for node in sample]
                    if any(len(peer) < index or peer[index - 1].name != child.name
                           or not peer[index - 1].get_text(" ", strip=True)
                           for peer in peers):
                        continue
                    selector = f"{child_selector} > *:nth-child({index})"
                    label = child.get("data-field") or f"campo_{index}"
                    name = cls._safe_name(str(label), index - 1)
                    if name in used:
                        name = f"{name}_{index}"
                    used.add(name)
                    fields.append(FieldSelector(name, selector, attribute="text"))
                if len(fields) < 3:
                    continue
                candidates.append((ExtractionSchema(name="filas_repetidas", base_selector=base,
                                                   description="Filas repetidas con enlace y campos",
                                                   fields=fields), len(nodes)))
        return candidates

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
