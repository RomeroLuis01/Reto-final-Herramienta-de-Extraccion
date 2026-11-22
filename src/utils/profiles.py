"""
Utilidades compartidas — Gestión de perfiles de sitios.

Cada sitio analizado genera un perfil JSON reutilizable y sanitizado que
se recupera automáticamente en análisis subsecuentes. Los perfiles sirven
como base para la reutilización de configuraciones y comparación de cambios (Avance 3).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from src.monitor.logger import get_logger
from src.utils.security import sanitize_for_persistence, sanitize_url

logger = get_logger("utils.profiles")


class ProfileManager:
    """
    Gestiona el ciclo de vida de los perfiles técnicos de sitios web.

    Un perfil es un documento JSON sanitizado que conserva el resultado
    del diagnóstico de un sitio, sus selectores, mecanismos y observaciones.
    Se identifica por el dominio del sitio analizado.
    """

    def __init__(self, profiles_dir: Path) -> None:
        self.profiles_dir = profiles_dir
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def save(self, diagnosis: dict[str, Any]) -> Path:
        """
        Persiste el diagnóstico sanitizado como perfil reutilizable del sitio.

        Si ya existe un perfil previo, agrega el nuevo análisis al
        historial del perfil (máximo 10 entradas guardadas).
        """
        clean_diagnosis = sanitize_for_persistence(diagnosis)
        url = sanitize_url(str(clean_diagnosis.get("url", "")))
        domain = self._domain_key(url)
        profile_path = self.profiles_dir / f"{domain}.json"

        profile = self.load(url) or self._empty_profile(url, domain)

        previous_diagnosis = profile.get("current_diagnosis") or None
        profile["diagnosis_changes"] = self.compare_diagnoses(
            previous_diagnosis, clean_diagnosis
        )
        profile["url"] = url
        profile["label"] = clean_diagnosis.get("label") or profile.get("label", "")
        profile["last_analysis"] = clean_diagnosis.get("diagnosis_timestamp")
        profile["analysis_count"] = profile.get("analysis_count", 0) + 1

        # Campos estructurados requeridos por perfilamiento automático
        html = clean_diagnosis.get("html_analysis", {}) or {}
        via = clean_diagnosis.get("viability", {}) or {}
        net = clean_diagnosis.get("network", {}) or {}
        # Solo la lista declarada explícitamente acredita autorización; una
        # petición observada en el navegador no la acredita.
        if isinstance(net.get("authorized_endpoints"), list):
            net["authorized_endpoints"] = [sanitize_url(str(endpoint))
                                           for endpoint in net["authorized_endpoints"]]
        prot = clean_diagnosis.get("protection", {}) or {}
        pag = clean_diagnosis.get("pagination", {}) or {}

        profile["identified_structure"] = {
            "render_type": html.get("render_type"),
            "static_html_size": html.get("static_html_size"),
            "dynamic_dom_size": html.get("dynamic_dom_size"),
            "dom_difference": html.get("dom_difference"),
            "tables_count": html.get("tables_count"),
            "forms_count": html.get("forms_count"),
        }
        profile["detected_technologies"] = clean_diagnosis.get("technologies", {})
        profile["loading_mechanisms"] = clean_diagnosis.get("acquisition_mechanisms", {})
        profile["observed_endpoints"] = net.get("endpoints_observed", [])
        profile["authorized_endpoints"] = net.get("authorized_endpoints", [])
        profile["endpoint_classification_version"] = 1
        profile["identified_selectors"] = clean_diagnosis.get("candidate_selectors", {})
        profile["pagination_mechanisms"] = pag
        profile["protection_mechanisms"] = {
            "captcha_detected": prot.get("captcha_detected", False),
            "captcha_type": prot.get("captcha_type"),
            "auth_required": prot.get("auth_required", False),
            "rate_limiting": prot.get("rate_limiting", False),
            "waf_detected": prot.get("waf_detected", False),
            "waf_type": prot.get("waf_type"),
            "robots_txt_status": prot.get("robots_txt_status"),
            "robots_crawl_allowed": prot.get("robots_crawl_allowed"),
            "blocking_barrier": prot.get("blocking_barrier", False),
        }
        profile["technical_feasibility"] = {
            "status": via.get("technical_feasibility"),
            "score": via.get("feasibility_score"),
            "recommended_strategies": via.get("recommended_strategies", []),
            "blocked_by_barrier": via.get("blocked_by_barrier", False),
        }
        profile["observations"] = {
            "limitations": via.get("limitations", []),
            "notes": via.get("notes", []),
            "errors": clean_diagnosis.get("errors", []),
        }
        profile["reusable_config"] = {
            "label": profile["label"],
            "recommended_use_selenium": (
                html.get("render_type") in ("dynamic_probable", "spa_probable", "mixed_probable")
                or pag.get("infinite_scroll", False)
            ),
            "known_selectors": profile["identified_selectors"],
            "known_endpoints": profile["authorized_endpoints"],
        }

        history: list[dict[str, Any]] = profile.setdefault("history", [])
        history.append({
            "timestamp": clean_diagnosis.get("diagnosis_timestamp"),
            "access_status": clean_diagnosis.get("access_status"),
            "technologies": clean_diagnosis.get("technologies", {}),
            "protection": prot,
            "viability": via,
        })
        profile["history"] = history[-10:]
        profile["current_diagnosis"] = clean_diagnosis

        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2, default=str)

        logger.info("Perfil guardado: %s", profile_path.name)
        return profile_path

    @staticmethod
    def _changes(previous: Any, current: Any) -> dict[str, Any]:
        """Compara estructuras sin confundir un dato ausente con una lista vacía."""
        if previous is None or current is None:
            return {"available": False}
        def items(value: Any) -> set[str]:
            if isinstance(value, dict):
                return {json.dumps({key: val}, sort_keys=True, ensure_ascii=False, default=str)
                        for key, val in value.items()}
            if isinstance(value, list):
                return {json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
                        for item in value}
            return {json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)}
        before, after = items(previous), items(current)
        return {"available": True, "added": [json.loads(v) for v in sorted(after - before)],
                "removed": [json.loads(v) for v in sorted(before - after)]}

    @classmethod
    def compare_diagnoses(cls, previous: dict[str, Any] | None,
                          current: dict[str, Any]) -> dict[str, Any]:
        """Compara selectores y endpoints observados en dos diagnósticos."""
        if not previous:
            return {"baseline_available": False}
        old_network = previous.get("network") or {}
        new_network = current.get("network") or {}
        return {
            "baseline_available": True,
            "selectors": cls._changes(previous.get("candidate_selectors"),
                                      current.get("candidate_selectors")),
            # Conserva la clave histórica como alias de endpoints observados.
            "endpoints": cls._changes(old_network.get("endpoints_observed"),
                                      new_network.get("endpoints_observed")),
            "observed_endpoints": cls._changes(old_network.get("endpoints_observed"),
                                               new_network.get("endpoints_observed")),
            "authorized_endpoints": cls._changes(old_network.get("authorized_endpoints"),
                                                 new_network.get("authorized_endpoints")),
        }

    @staticmethod
    def _json_paths(value: Any, prefix: str = "$") -> set[str]:
        """Describe claves y tipos sin almacenar valores provenientes del sitio."""
        kind = ("object" if isinstance(value, dict) else "array" if isinstance(value, list)
                else "null" if value is None else "boolean" if isinstance(value, bool)
                else "number" if isinstance(value, (int, float)) else "string")
        paths = {f"{prefix}: {kind}"}
        if isinstance(value, dict):
            for key, child in value.items():
                paths.update(ProfileManager._json_paths(child, f"{prefix}.{key}"))
        elif isinstance(value, list):
            for child in value:
                paths.update(ProfileManager._json_paths(child, f"{prefix}[]"))
        return paths

    def save_extraction(self, url: str, schema: dict[str, Any],
                        records: list[dict[str, Any]], json_pages: list[Any],
                        execution_id: str,
                        configured_endpoints: list[str] | None = None) -> dict[str, Any]:
        """Guarda la instantánea y devuelve diferencias frente a la extracción anterior."""
        safe_url = sanitize_url(url)
        domain = self._domain_key(safe_url)
        profile = self.load(safe_url) or self._empty_profile(safe_url, domain)
        fields = schema.get("fields") or []
        snapshot = {
            "execution_id": execution_id,
            "fields": sorted(str(f.get("name")) for f in fields if f.get("name")),
            "selectors": {str(f["name"]): {"selector": f.get("selector"),
                          "selector_type": f.get("selector_type")}
                          for f in fields if f.get("name")},
            "base_selector": schema.get("base_selector"),
            "columns": sorted({key for record in records for key in record
                               if not key.startswith("_")}),
            "json_structure": sorted(set().union(*(self._json_paths(page) for page in json_pages)))
                              if json_pages else None,
            "record_count": len(records),
            # None conserva la diferencia entre una ejecución antigua y una
            # configuración conocida sin endpoints declarados.
            "configured_endpoints": (sorted({sanitize_url(str(endpoint)) for endpoint in configured_endpoints})
                                     if configured_endpoints is not None else None),
        }
        previous = profile.get("extraction_snapshot")
        changes: dict[str, Any] = {"baseline_available": bool(previous)}
        if previous:
            for key in ("fields", "selectors", "base_selector", "columns", "json_structure", "configured_endpoints"):
                changes[key] = self._changes(previous.get(key), snapshot.get(key))
            old_count = previous.get("record_count")
            changes["record_count"] = ({"available": True, "previous": old_count,
                                        "current": len(records), "difference": len(records) - old_count}
                                       if isinstance(old_count, int) else {"available": False})
        profile["extraction_snapshot"] = snapshot
        profile["extraction_changes"] = changes
        profile = sanitize_for_persistence(profile)
        path = self.profiles_dir / f"{domain}.json"
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(profile, stream, ensure_ascii=False, indent=2, default=str)
        return changes

    def load(self, url: str) -> Optional[dict[str, Any]]:
        """
        Carga el perfil existente de un sitio, si lo hay y es válido.

        Returns:
            Diccionario del perfil o None si no existe o está corrupto.
        """
        domain = self._domain_key(url)
        profile_path = self.profiles_dir / f"{domain}.json"

        if not profile_path.exists():
            return None

        try:
            with open(profile_path, encoding="utf-8") as f:
                profile = json.load(f)
            if not isinstance(profile, dict):
                logger.warning("Perfil corrupto (estructura no válida): %s", profile_path.name)
                return None
            if profile.get("endpoint_classification_version") != 1:
                # Los perfiles anteriores llamaban autorizados a los endpoints
                # observados. Conservarlos como evidencia sin atribuir permiso.
                legacy = profile.get("authorized_endpoints")
                if legacy:
                    profile["legacy_endpoints_unverified"] = legacy
                diagnosis = profile.get("current_diagnosis") or {}
                network = diagnosis.get("network") or {}
                profile["observed_endpoints"] = network.get("endpoints_observed", [])
                profile["authorized_endpoints"] = []
                reusable = profile.get("reusable_config")
                if isinstance(reusable, dict):
                    reusable["known_endpoints"] = []
                profile["endpoint_classification_version"] = 1
            logger.info(
                "Perfil previo cargado (%s análisis previos).",
                profile.get("analysis_count", 0),
            )
            return profile
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("No se pudo cargar el perfil (%s): %s", profile_path.name, exc)
            return None

    def get_reusable_config(self, url: str) -> Optional[dict[str, Any]]:
        """
        Recupera la configuración reutilizable de un perfil existente.
        """
        profile = self.load(url)
        if not profile:
            return None
        return profile.get("reusable_config") or {
            "label": profile.get("label", ""),
            "known_selectors": profile.get("identified_selectors", {}),
            "known_endpoints": profile.get("authorized_endpoints", []),
        }

    def list_profiles(self) -> list[dict[str, Any]]:
        """
        Lista todos los perfiles almacenados con sus metadatos básicos.
        """
        summaries: list[dict[str, Any]] = []
        for profile_file in sorted(self.profiles_dir.glob("*.json")):
            try:
                with open(profile_file, encoding="utf-8") as f:
                    p = json.load(f)
                if not isinstance(p, dict):
                    continue
                summaries.append({
                    "file": profile_file.name,
                    "url": sanitize_url(str(p.get("url", ""))),
                    "domain": p.get("domain"),
                    "label": p.get("label", ""),
                    "first_analysis": p.get("first_analysis"),
                    "last_analysis": p.get("last_analysis"),
                    "analysis_count": p.get("analysis_count", 0),
                })
            except Exception as exc:
                logger.debug("Perfil omitido por error de lectura (%s): %s", profile_file.name, exc)
        return summaries

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------

    @staticmethod
    def _domain_key(url: str) -> str:
        """Genera una clave de archivo segura a partir del dominio (sin credenciales)."""
        try:
            netloc = urlparse(url).netloc or url
            host_port = netloc.rsplit("@", 1)[-1]
            safe = host_port.replace(".", "_").replace(":", "_").replace("/", "_")
            return safe[:60] or "unknown"
        except Exception:
            return "unknown"

    @staticmethod
    def _empty_profile(url: str, domain: str) -> dict[str, Any]:
        """Crea la estructura base de un perfil nuevo."""
        return {
            "url": sanitize_url(url),
            "domain": domain,
            "label": "",
            "first_analysis": datetime.now().isoformat(),
            "last_analysis": None,
            "analysis_count": 0,
            "history": [],
            "current_diagnosis": {},
        }
