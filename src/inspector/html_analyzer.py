"""
Módulo 2 — Analizador de HTML estático.

Analiza la estructura HTML recibida de la respuesta HTTP inicial
usando BeautifulSoup, sin ejecutar JavaScript. Identifica:
- Estructura general del documento.
- Scripts y frameworks probables basados en evidencia técnica real.
- Formularios, tablas, elementos de paginación e interacción.
- Pistas de APIs, formatos embebidos y dependencias temporales.
- Indicadores de contenido dinámico y CAPTCHA.

NOTA: La detección estática sobre HTML clasifica tecnologías como
"probable" (nunca "confirmado" únicamente por firmas HTML).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from src.monitor.logger import get_logger
from src.utils.security import sanitize_url

logger = get_logger("inspector.html")

# ---------------------------------------------------------------------------
# Reglas de detección basada en evidencia estructural (HTML estático)
# ---------------------------------------------------------------------------

_SCRIPT_SRC_FRAMEWORKS: dict[str, tuple[str, ...]] = {
    "React": ("react.js", "react.min.js", "react.production", "react-dom"),
    "Next.js": ("/_next/static", "/_next/chunks", "next/dist"),
    "Vue.js": ("vue.js", "vue.min.js", "vue.global", "vue.runtime", "vue@"),
    "Nuxt.js": ("/_nuxt/", "nuxt.js"),
    "Angular": ("angular.js", "angular.min.js", "main-es2015", "@angular/core"),
    "Svelte": ("svelte/", "svelte.js"),
    "Gatsby": ("gatsby-", "commons-"),
    "Ember.js": ("ember.js", "ember.min.js"),
    "Backbone.js": ("backbone.js", "backbone.min.js"),
    "jQuery": ("jquery.js", "jquery.min.js", "jquery-"),
    "Bootstrap": ("bootstrap.js", "bootstrap.min.js", "bootstrap.bundle"),
    "WordPress": ("/wp-content/", "/wp-includes/"),
    "Shopify": ("cdn.shopify.com",),
    "Drupal": ("/sites/default/files/", "drupal.js"),
    "Joomla": ("/media/jui/", "/media/system/js/"),
}

_CSS_HREF_FRAMEWORKS: dict[str, tuple[str, ...]] = {
    "Bootstrap": ("bootstrap.css", "bootstrap.min.css", "bootstrap@"),
    "Tailwind CSS": ("tailwind.css", "tailwind.min.css", "tailwindcss"),
    "WordPress": ("/wp-content/themes/", "/wp-content/plugins/"),
}

_ATTR_FRAMEWORKS: dict[str, tuple[str, ...]] = {
    "React": ("data-reactroot", "data-reactid"),
    "Vue.js": ("data-v-app",),
    "Angular": ("ng-version", "ng-app", "ng-controller"),
    "Ember.js": ("data-ember-action",),
}

_INLINE_JS_FRAMEWORKS: dict[str, tuple[str, ...]] = {
    "Next.js": ("__NEXT_DATA__",),
    "Nuxt.js": ("window.__NUXT__", "__NUXT_DATA__"),
    "Gatsby": ("window.___gatsby", "___gatsby"),
    "Shopify": ("Shopify.theme",),
    "Drupal": ("Drupal.settings",),
}

_CAPTCHA_SIGNATURES: tuple[str, ...] = (
    "g-recaptcha",
    "recaptcha/api.js",
    "h-captcha",
    "hcaptcha.com",
    "cf-turnstile",
    "challenges.cloudflare.com/turnstile",
    "altcha",
    "arkoselabs",
    "funcaptcha",
    "datadome",
)

_PAGINATION_PATTERNS: list[str] = [
    "pagination",
    "pager",
    "paginator",
    "page-nav",
    "next-page",
    "prev-page",
    "page-numbers",
    "page-link",
    "pagenavi",
]

_LOAD_MORE_PATTERNS: tuple[str, ...] = (
    "load-more",
    "loadmore",
    "load_more",
    "see-more",
    "show-more",
    "ver-mas",
    "cargar-mas",
    "cargar más",
)

_INFINITE_SCROLL_PATTERNS: tuple[str, ...] = (
    "infinite-scroll",
    "infinite_scroll",
    "infinitescroll",
    "endless-scroll",
    "data-infinite-scroll",
    "intersectionobserver",
    "lazyload",
    "lazy-load",
)

_AUTH_PATTERNS: tuple[str, ...] = (
    "login",
    "signin",
    "sign-in",
    "password",
    "oauth",
    "autentic",
    "iniciar sesión",
)


# ---------------------------------------------------------------------------
# Analizador HTML
# ---------------------------------------------------------------------------


class HtmlAnalyzer:
    """
    Analiza la estructura HTML estática de una página web.

    No ejecuta JavaScript: trabaja únicamente sobre el HTML
    recibido en la respuesta HTTP inicial.
    """

    def __init__(self, html: str, base_url: str = "") -> None:
        self._html = html or ""
        self._base_url = base_url
        self._soup = BeautifulSoup(self._html, "lxml")

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def analyze(self) -> dict[str, Any]:
        """
        Ejecuta el análisis completo del HTML estático.

        Returns:
            Diccionario con todos los resultados del análisis.
        """
        result: dict[str, Any] = {}

        body = self._soup.find("body")
        body_text = body.get_text(" ", strip=True) if body else self._soup.get_text(" ", strip=True)

        result["static_html_size"] = len(self._html.encode("utf-8"))
        result["static_text_size"] = len(body_text)
        result["meta_tags"] = self._extract_meta_tags()
        result["meta_tags_count"] = len(result["meta_tags"])
        result["title"] = self._get_title()
        result["meta_generator"] = result["meta_tags"].get("generator")
        result["scripts"] = self._extract_scripts()
        result["scripts_count"] = len(result["scripts"])
        result["stylesheets"] = self._extract_stylesheets()
        result["forms"] = self._extract_forms()
        result["forms_count"] = len(result["forms"])
        result["tables"] = self._extract_tables()
        result["tables_count"] = len(result["tables"])
        result["links_count"] = len(self._soup.find_all("a", href=True))
        result["images_count"] = len(self._soup.find_all("img"))
        result["iframes"] = self._extract_iframes()
        result["js_frameworks"] = self._detect_frameworks(
            result["scripts"], result["stylesheets"], result["meta_generator"]
        )
        result["cms_probable"] = self._detect_cms_probable(
            result["js_frameworks"], result["meta_generator"]
        )
        result["captcha_indicators"] = self._detect_captcha(
            result["scripts"], result["iframes"]
        )
        result["pagination"] = self._detect_pagination()
        result["load_more"] = self._detect_load_more()
        result["infinite_scroll_hints"] = self._detect_infinite_scroll_hints()
        result["auth_indicators"] = self._detect_auth_indicators(result["forms"])
        result["api_hints"] = self._detect_api_hints()
        result["inline_api_calls"] = self._detect_inline_api_calls()
        result["embedded_data_formats"] = self._detect_embedded_formats()
        result["interaction_indicators"] = self._detect_interactions(result["forms"])
        result["time_dependent_hints"] = self._detect_time_dependent_hints()
        result["candidate_selectors"] = self._extract_candidate_selectors(
            result["tables"], result["pagination"], result["forms"]
        )
        result["data_attributes"] = self._detect_data_attributes()
        result["noscript_warning"] = self._detect_noscript_warning()
        result["render_type"] = self._infer_render_type(result, body_text)

        logger.debug(
            "HTML analizado: %d bytes, %d scripts, tipo=%s",
            result["static_html_size"],
            result["scripts_count"],
            result["render_type"],
        )
        return result

    # ------------------------------------------------------------------
    # Extracción de elementos
    # ------------------------------------------------------------------

    def _get_title(self) -> str:
        tag = self._soup.find("title")
        return tag.get_text(strip=True) if tag else ""

    def _extract_meta_tags(self) -> dict[str, str]:
        """Extrae metaetiquetas relevantes del documento (sin valores sensibles)."""
        meta: dict[str, str] = {}
        for tag in self._soup.find_all("meta"):
            name = tag.get("name", tag.get("property", tag.get("http-equiv", "")))
            content = tag.get("content", "")
            if name:
                name_l = str(name).lower()
                if "csrf" in name_l or "token" in name_l:
                    meta[name_l] = "[REDACTED]"
                else:
                    meta[name_l] = str(content)
        return meta

    def _extract_scripts(self) -> list[dict[str, Any]]:
        """Extrae metadatos de los elementos <script>."""
        scripts: list[dict[str, Any]] = []
        for tag in self._soup.find_all("script"):
            src = sanitize_url(str(tag.get("src", ""))) if tag.get("src") else ""
            script_type = str(tag.get("type", "text/javascript"))
            has_inline_content = bool(tag.string and tag.string.strip())
            scripts.append({
                "src": src,
                "type": script_type,
                "has_inline": has_inline_content,
                "async": tag.has_attr("async"),
                "defer": tag.has_attr("defer"),
            })
        return scripts

    def _extract_stylesheets(self) -> list[str]:
        """Extrae URLs sanitizadas de hojas de estilo CSS."""
        sheets: list[str] = []
        for tag in self._soup.find_all("link", rel=lambda r: r and "stylesheet" in r):
            href = tag.get("href", "")
            if href:
                sheets.append(sanitize_url(str(href)))
        return sheets

    def _extract_forms(self) -> list[dict[str, Any]]:
        """Extrae metadatos estructurales de los formularios presentes."""
        forms: list[dict[str, Any]] = []
        for form in self._soup.find_all("form"):
            inputs = form.find_all(["input", "textarea", "select"])
            has_csrf = any(
                "csrf" in str(inp.get("name", "")).lower()
                for inp in inputs
            )
            forms.append({
                "action": sanitize_url(str(form.get("action", ""))) if form.get("action") else "",
                "method": str(form.get("method", "get")).upper(),
                "id": str(form.get("id", "")),
                "inputs_count": len(inputs),
                "has_password": bool(form.find("input", {"type": "password"})),
                "has_csrf_field": has_csrf,
            })
        return forms

    def _extract_tables(self) -> list[dict[str, Any]]:
        """Extrae información básica de tablas HTML."""
        tables: list[dict[str, Any]] = []
        for table in self._soup.find_all("table"):
            rows = table.find_all("tr")
            tables.append({
                "id": str(table.get("id", "")),
                "class": " ".join(table.get("class", [])),
                "rows": len(rows),
                "has_thead": bool(table.find("thead")),
            })
        return tables

    def _extract_iframes(self) -> list[str]:
        """Extrae src sanitizado de iframes."""
        return [
            sanitize_url(str(iframe.get("src", "")))
            for iframe in self._soup.find_all("iframe")
            if iframe.get("src")
        ]

    # ------------------------------------------------------------------
    # Detección de tecnologías (clasificación estática -> "probable")
    # ------------------------------------------------------------------

    def _detect_frameworks(
        self,
        scripts: list[dict[str, Any]],
        stylesheets: list[str],
        meta_generator: str | None,
    ) -> dict[str, dict[str, Any]]:
        """
        Detecta frameworks y CMS probables mediante evidencia estructural real
        en <script src>, <link href>, atributos DOM y scripts de hidratación.

        Nota: Toda detección basada únicamente en HTML estático se marca como
        'probable'; solo la ejecución en DOM puede confirmarla.
        """
        evidence_map: dict[str, list[str]] = {}

        def _add(fw: str, ev: str) -> None:
            lst = evidence_map.setdefault(fw, [])
            if ev not in lst:
                lst.append(ev)

        # 1. Evidencia en <script src="...">
        for sc in scripts:
            src_l = str(sc.get("src", "")).lower()
            if not src_l:
                continue
            for fw, patterns in _SCRIPT_SRC_FRAMEWORKS.items():
                for pat in patterns:
                    if pat in src_l:
                        _add(fw, f"script:{pat}")

        # 2. Evidencia en <link rel="stylesheet" href="...">
        for href in stylesheets:
            href_l = href.lower()
            for fw, patterns in _CSS_HREF_FRAMEWORKS.items():
                for pat in patterns:
                    if pat in href_l:
                        _add(fw, f"css:{pat}")

        # 3. Evidencia en atributos HTML específicos
        for fw, attrs in _ATTR_FRAMEWORKS.items():
            for attr in attrs:
                if self._soup.find(attrs={attr: True}):
                    _add(fw, f"attr:{attr}")

        if self._soup.find(id="__NEXT_DATA__"):
            _add("Next.js", "id:__NEXT_DATA__")
        if self._soup.find(id="__nuxt"):
            _add("Nuxt.js", "id:__nuxt")
        if self._soup.find(id="___gatsby"):
            _add("Gatsby", "id:___gatsby")

        # Vue scoped CSS attribute (data-v-xxxxxx)
        for tag in self._soup.find_all(True):
            for attr in tag.attrs:
                if str(attr).startswith("data-v-"):
                    _add("Vue.js", "attr:data-v-*")
                    break
            if "Vue.js" in evidence_map:
                break

        # 4. Evidencia en scripts inline (solo patrones de hidratación/objetos)
        inline_text = "\n".join(
            tag.string for tag in self._soup.find_all("script") if tag.string
        )
        for fw, markers in _INLINE_JS_FRAMEWORKS.items():
            for marker in markers:
                if marker in inline_text:
                    _add(fw, f"inline:{marker}")

        # 5. Evidencia en meta generator (WordPress / Drupal / Joomla)
        if meta_generator:
            gen_l = meta_generator.lower()
            for cms in ("WordPress", "Drupal", "Joomla", "Shopify"):
                if cms.lower() in gen_l:
                    _add(cms, f"meta:generator={meta_generator}")

        # 6. Clases utilitarias típicas de Tailwind CSS combinadas (flex + px-/py-/text-)
        if "Tailwind CSS" not in evidence_map:
            class_tokens: set[str] = set()
            for tag in self._soup.find_all(class_=True):
                for c in tag.get("class", []):
                    class_tokens.add(str(c))
            tw_hits = sum(
                1
                for tok in class_tokens
                if re.match(r"^(bg|text|px|py|mx|my|flex|grid|rounded)-", tok)
            )
            if tw_hits >= 5 and any(
                t in class_tokens for t in ("flex", "grid", "hidden")
            ):
                _add("Tailwind CSS", "classes:utility_pattern")

        return {
            fw: {
                "confidence": "probable",
                "evidence": evs[:5],
            }
            for fw, evs in evidence_map.items()
        }

    @staticmethod
    def _detect_cms_probable(
        frameworks: dict[str, dict[str, Any]], meta_generator: str | None
    ) -> str | None:
        """Determina el CMS probable a partir de evidencia de HTML/meta."""
        for cms in ("WordPress", "Drupal", "Joomla", "Shopify"):
            if cms in frameworks:
                return cms
            if meta_generator and cms.lower() in meta_generator.lower():
                return cms
        return None

    def _detect_captcha(
        self, scripts: list[dict[str, Any]], iframes: list[str]
    ) -> list[dict[str, str]]:
        """Detecta indicadores estructurales de CAPTCHA en HTML, scripts e iframes."""
        found: list[dict[str, str]] = []
        seen: set[str] = set()

        sources = (
            [str(s.get("src", "")).lower() for s in scripts if s.get("src")]
            + [i.lower() for i in iframes]
            + [self._html.lower()]
        )
        combined = "\n".join(sources)

        for sig in _CAPTCHA_SIGNATURES:
            if sig in combined and sig not in seen:
                seen.add(sig)
                found.append({"indicator": sig, "confidence": "probable"})

        # Elementos con clase o id explícito de captcha
        if not found:
            cap_el = self._soup.find(
                class_=re.compile(r"\bcaptcha\b", re.IGNORECASE)
            ) or self._soup.find(id=re.compile(r"\bcaptcha\b", re.IGNORECASE))
            if cap_el:
                found.append({"indicator": "captcha-element", "confidence": "probable"})

        return found

    def _detect_pagination(self) -> dict[str, Any]:
        """Detecta elementos de paginación en el HTML."""
        result: dict[str, Any] = {
            "detected": False,
            "type": None,
            "selectors": [],
        }

        for pattern in _PAGINATION_PATTERNS:
            elements = self._soup.find_all(
                class_=re.compile(pattern, re.IGNORECASE)
            ) + self._soup.find_all(id=re.compile(pattern, re.IGNORECASE))
            if elements:
                result["detected"] = True
                result["type"] = "traditional"
                sel = f".{pattern}"
                if sel not in result["selectors"]:
                    result["selectors"].append(sel)

        next_link = self._soup.find("a", rel=lambda r: r and "next" in r)
        prev_link = self._soup.find("a", rel=lambda r: r and "prev" in r)
        if next_link or prev_link:
            result["detected"] = True
            result["type"] = result["type"] or "traditional"
            result["has_rel_next"] = bool(next_link)
            result["has_rel_prev"] = bool(prev_link)

        return result

    def _detect_load_more(self) -> dict[str, Any]:
        """Detecta botones o enlaces de 'cargar más' contenido."""
        result: dict[str, Any] = {"detected": False, "selectors": []}
        candidates = self._soup.find_all(["button", "a"])
        for el in candidates:
            cls_id = " ".join(el.get("class", [])) + " " + str(el.get("id", ""))
            txt = el.get_text(" ", strip=True)
            haystack = f"{cls_id} {txt}".lower()
            for pattern in _LOAD_MORE_PATTERNS:
                if pattern in haystack:
                    result["detected"] = True
                    if pattern not in result["selectors"]:
                        result["selectors"].append(pattern)
        return result

    def _detect_infinite_scroll_hints(self) -> dict[str, Any]:
        """Detecta indicadores de scroll infinito en atributos o scripts."""
        result: dict[str, Any] = {"detected": False, "indicators": []}
        html_lower = self._html.lower()
        for pattern in _INFINITE_SCROLL_PATTERNS:
            if pattern in html_lower:
                result["detected"] = True
                result["indicators"].append(pattern)
        return result

    def _detect_auth_indicators(self, forms: list[dict[str, Any]]) -> dict[str, Any]:
        """Detecta indicadores de autenticación o sesión requerida."""
        result: dict[str, Any] = {
            "detected": False,
            "has_login_form": False,
            "indicators": [],
        }

        if any(f.get("has_password") for f in forms):
            result["has_login_form"] = True
            result["detected"] = True
            result["indicators"].append("Formulario con campo de contraseña")

        # Buscar enlaces o botones explícitos de login/sign-in
        for el in self._soup.find_all(["a", "button"]):
            href = str(el.get("href", "")).lower()
            txt = el.get_text(" ", strip=True).lower()
            for pat in _AUTH_PATTERNS:
                if pat in href or pat in txt:
                    if pat not in result["indicators"]:
                        result["indicators"].append(f"control:{pat}")
                    break

        result["detected"] = result["has_login_form"] or len(result["indicators"]) >= 2
        return result

    def _detect_api_hints(self) -> list[str]:
        """Detecta pistas de endpoints API en el HTML estático."""
        hints: list[str] = []
        api_patterns = [
            r"/api/v?\d*",
            r"/graphql",
            r"api[_\-]endpoint",
            r"restapi",
            r"wp-json",
        ]
        html_lower = self._html.lower()
        for pattern in api_patterns:
            if re.search(pattern, html_lower):
                hints.append(pattern)
        return hints

    def _detect_inline_api_calls(self) -> dict[str, Any]:
        """Inspecciona scripts inline para detectar fetch/XHR/axios/GraphQL."""
        inline_js = "\n".join(
            tag.string for tag in self._soup.find_all("script") if tag.string
        )
        fetch_used = bool(re.search(r"\bfetch\s*\(", inline_js))
        xhr_used = "XMLHttpRequest" in inline_js or "$.ajax" in inline_js or "axios." in inline_js
        graphql_used = "/graphql" in inline_js.lower() or "query {" in inline_js

        endpoints = re.findall(
            r"""['"]((?:/|https?://)[^'"\s]{1,120}(?:/api/|/graphql|/v\d+/|\.json)[^'"\s]{0,60})['"]""",
            inline_js,
            flags=re.IGNORECASE,
        )
        safe_endpoints = [sanitize_url(ep) for ep in dict.fromkeys(endpoints)][:10]

        return {
            "fetch_in_inline_js": fetch_used,
            "xhr_ajax_in_inline_js": xhr_used,
            "graphql_in_inline_js": graphql_used,
            "endpoints_in_scripts": safe_endpoints,
        }

    def _detect_embedded_formats(self) -> dict[str, Any]:
        """Detecta formatos de datos embebidos o enlazados (JSON-LD, JSON, XML, CSV, RSS)."""
        json_ld = len(self._soup.find_all("script", {"type": "application/ld+json"}))
        json_scripts = len(self._soup.find_all("script", {"type": "application/json"}))

        linked_formats: dict[str, list[str]] = {}
        ext_map = {
            "json": (".json",),
            "xml": (".xml",),
            "csv": (".csv",),
            "rss_atom": (".rss", ".atom", "/feed"),
            "pdf": (".pdf",),
        }
        for a in self._soup.find_all("a", href=True):
            href = str(a["href"])
            path = urlparse(href).path.lower()
            for fmt, exts in ext_map.items():
                if any(path.endswith(ext) for ext in exts):
                    linked_formats.setdefault(fmt, [])
                    safe_href = sanitize_url(href)
                    if safe_href not in linked_formats[fmt] and len(linked_formats[fmt]) < 5:
                        linked_formats[fmt].append(safe_href)

        return {
            "json_ld_blocks": json_ld,
            "json_data_scripts": json_scripts,
            "linked_data_files": linked_formats,
        }

    def _detect_interactions(self, forms: list[dict[str, Any]]) -> dict[str, Any]:
        """Detecta controles que sugieren contenido condicionado por interacción."""
        tabs = len(self._soup.find_all(attrs={"role": "tab"}))
        accordions = len(self._soup.find_all("details")) + len(
            self._soup.find_all(attrs={"aria-expanded": True})
        )
        modals = len(self._soup.find_all(attrs={"role": "dialog"}))
        selects = len(self._soup.find_all("select"))
        onclicks = len(self._soup.find_all(attrs={"onclick": True}))
        search_forms = sum(1 for f in forms if not f.get("has_password"))

        requires_interaction = any([tabs, accordions, modals, selects, onclicks])
        return {
            "requires_interaction_probable": requires_interaction,
            "tabs_count": tabs,
            "accordions_count": accordions,
            "modals_count": modals,
            "dropdown_filters_count": selects,
            "inline_event_handlers_count": onclicks,
            "interactive_forms_count": search_forms,
        }

    def _detect_time_dependent_hints(self) -> dict[str, Any]:
        """Detecta indicios de contenido dependiente del tiempo de ejecución."""
        inline_js = "\n".join(
            tag.string for tag in self._soup.find_all("script") if tag.string
        )
        has_refresh = bool(
            self._soup.find("meta", attrs={"http-equiv": re.compile("^refresh$", re.I)})
        )
        return {
            "has_meta_refresh": has_refresh,
            "uses_set_interval": "setInterval(" in inline_js,
            "uses_set_timeout": "setTimeout(" in inline_js,
            "uses_eventsource_sse": "EventSource" in inline_js,
            "uses_websocket": "new WebSocket" in inline_js,
        }

    @staticmethod
    def _extract_candidate_selectors(
        tables: list[dict[str, Any]],
        pagination: dict[str, Any],
        forms: list[dict[str, Any]],
    ) -> dict[str, list[str]]:
        """Registra selectores estructurales identificados para perfilamiento."""
        table_sels = [
            f"table#{t['id']}" if t.get("id") else (
                f"table.{t['class'].split()[0]}" if t.get("class") else "table"
            )
            for t in tables[:10]
        ]
        form_sels = [f"form#{f['id']}" if f.get("id") else "form" for f in forms[:10]]
        return {
            "tables": list(dict.fromkeys(table_sels)),
            "pagination": list(pagination.get("selectors", [])),
            "forms": list(dict.fromkeys(form_sels)),
        }

    def _detect_data_attributes(self) -> list[str]:
        """Detecta atributos data-* relevantes que puedan indicar JS frameworks."""
        data_attrs: set[str] = set()
        for tag in self._soup.find_all(True):
            for attr in tag.attrs:
                if str(attr).startswith("data-"):
                    data_attrs.add(str(attr))
        return sorted(list(data_attrs))[:20]

    def _detect_noscript_warning(self) -> bool:
        """Detecta avisos <noscript> indicando dependencia estricta de JS."""
        for ns in self._soup.find_all("noscript"):
            txt = ns.get_text(" ", strip=True).lower()
            if any(k in txt for k in ("javascript", "enable js", "habilitar javascript")):
                return True
        return False

    # ------------------------------------------------------------------
    # Inferencia de tipo de renderizado
    # ------------------------------------------------------------------

    def _infer_render_type(self, analysis: dict[str, Any], body_text: str) -> str:
        """
        Infiere el tipo de renderizado probable basándose en evidencia estructural.

        Returns:
            'static', 'dynamic_probable', 'spa_probable', o 'mixed_probable'.
        """
        frameworks = analysis.get("js_frameworks", {})
        spa_frameworks = {"React", "Vue.js", "Angular", "Svelte", "Next.js", "Nuxt.js", "Gatsby"}
        has_spa = any(fw in frameworks for fw in spa_frameworks)
        has_spa_mount = bool(self._soup.find(id=re.compile(r"^(root|app|__next|__nuxt)$")))
        noscript_js = analysis.get("noscript_warning", False)
        inline_api = analysis.get("inline_api_calls", {})
        has_ajax = inline_api.get("fetch_in_inline_js") or inline_api.get("xhr_ajax_in_inline_js")

        body_elements = len(self._soup.find_all(True)) if self._soup else 0

        if (has_spa or has_spa_mount or noscript_js) and len(body_text) < 500 and body_elements < 35:
            return "spa_probable"
        if has_spa or has_ajax:
            return "dynamic_probable"
        if analysis.get("scripts_count", 0) > 10:
            return "mixed_probable"
        return "static"
