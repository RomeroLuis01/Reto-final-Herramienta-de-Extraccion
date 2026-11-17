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

        profile["url"] = url
        profile["label"] = clean_diagnosis.get("label") or profile.get("label", "")
        profile["last_analysis"] = clean_diagnosis.get("diagnosis_timestamp")
        profile["analysis_count"] = profile.get("analysis_count", 0) + 1

        # Campos estructurados requeridos por perfilamiento automático
        html = clean_diagnosis.get("html_analysis", {}) or {}
        via = clean_diagnosis.get("viability", {}) or {}
        net = clean_diagnosis.get("network", {}) or {}
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
        profile["authorized_endpoints"] = net.get("endpoints_observed", [])
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
