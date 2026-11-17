"""
main.py — Punto de entrada de la Herramienta de Extracción
Inteligente y Automatizada de Información Web.

AVANCE 1: Sistema de inspección, diagnóstico y perfilamiento web.

Uso interactivo:
    python main.py

Uso por línea de comandos:
    python main.py --url https://ejemplo.com [--label "Sitio"] [--no-selenium]
    python main.py --list-profiles
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Garantizar que src/ esté en el path de Python
sys.path.insert(0, str(Path(__file__).parent))

from colorama import Fore, Style, init as colorama_init

colorama_init(autoreset=True)

from src.config.settings import (
    get_config,
    normalize_url,
    register_target,
    validate_url,
)
from src.inspector.web_inspector import WebInspector
from src.monitor.logger import get_logger, setup_logging
from src.monitor.reporter import DiagnosticReporter
from src.utils.profiles import ProfileManager
from src.utils.security import sanitize_url


def _print_banner() -> None:
    """Imprime el banner de bienvenida."""
    print(f"\n{Fore.CYAN}{'=' * 65}")
    print(f"  {'HERRAMIENTA DE EXTRACCIÓN INTELIGENTE Y AUTOMATIZADA':^61}")
    print(f"  {'Avance 1 — Sistema de Inspección y Diagnóstico Web':^61}")
    print(f"{'=' * 65}{Style.RESET_ALL}\n")


def _prompt_url() -> str:
    """Solicita la URL al usuario con normalización y validación anti-SSRF."""
    while True:
        raw_url = input(
            f"{Fore.YELLOW}  Introduce la URL a analizar{Style.RESET_ALL} "
            f"(ej. https://ejemplo.com): "
        ).strip()

        if not raw_url:
            print(f"{Fore.RED}  ✗ La URL no puede estar vacía.{Style.RESET_ALL}")
            continue

        normalized = normalize_url(raw_url)
        valid, message = validate_url(normalized)
        if not valid:
            print(f"{Fore.RED}  ✗ {message}{Style.RESET_ALL}")
        else:
            print(f"{Fore.GREEN}  ✓ URL válida y segura.{Style.RESET_ALL}\n")
            return normalized


def _prompt_options(default_label: str = "") -> dict[str, Any]:
    """Solicita opciones adicionales al usuario."""
    print(f"{Fore.CYAN}  Opciones de análisis:{Style.RESET_ALL}")

    hint = f" [{default_label}]" if default_label else ""
    label_input = input(
        f"  Etiqueta para este sitio (opcional{hint}, Enter para mantener/omitir): "
    ).strip() or default_label

    use_selenium_input = input(
        "  ¿Incluir análisis DOM dinámico con Selenium? [S/n]: "
    ).strip().lower()
    use_selenium = use_selenium_input not in ("n", "no")

    print()
    return {
        "label": label_input,
        "use_selenium": use_selenium,
    }


def _print_summary(diagnosis: dict[str, Any]) -> None:
    """Imprime un resumen ejecutivo del diagnóstico en consola."""
    print(f"\n{Fore.CYAN}{'=' * 65}")
    print("  RESUMEN DEL DIAGNÓSTICO")
    print(f"{'=' * 65}{Style.RESET_ALL}")

    url = diagnosis.get("url", "N/A")
    access = diagnosis.get("access_status", "desconocido")
    http = diagnosis.get("http", {})
    tech = diagnosis.get("technologies", {})
    protection = diagnosis.get("protection", {})
    viability = diagnosis.get("viability", {})
    errors = diagnosis.get("errors", [])

    color = Fore.GREEN if access == "accesible" else Fore.RED
    print(f"\n  {'URL:':<22} {url}")
    print(f"  {'Estado:':<22} {color}{access.upper()}{Style.RESET_ALL}")

    if http.get("status_code"):
        print(f"  {'HTTP:':<22} {http['status_code']} ({http.get('content_format', 'N/A')})")
    if http.get("response_time_s") is not None:
        print(f"  {'Tiempo respuesta:':<22} {http['response_time_s']}s")

    confirmed = tech.get("js_frameworks_confirmed", [])
    probable = tech.get("js_frameworks_probable", [])
    render = tech.get("render_type", "no_identificado")
    print(f"\n  {'Renderizado:':<22} {render}")
    if confirmed:
        print(f"  {'Frameworks (conf.):':<22} {', '.join(confirmed)}")
    if probable:
        print(f"  {'Frameworks (prob.):':<22} {', '.join(probable)}")
    if tech.get("cms_probable"):
        print(f"  {'CMS (prob.):':<22} {tech['cms_probable']}")

    print(f"\n  {Fore.YELLOW}Protecciones:{Style.RESET_ALL}")
    if protection.get("ssrf_blocked"):
        print(f"  {Fore.RED}  ⚠ Bloqueo anti-SSRF: {protection.get('block_reason')}{Style.RESET_ALL}")
    if protection.get("captcha_detected"):
        print(f"  {Fore.RED}  ⚠ CAPTCHA ({protection.get('captcha_confidence')}): {protection.get('captcha_type')}{Style.RESET_ALL}")
    if protection.get("access_blocked") and not protection.get("ssrf_blocked"):
        print(f"  {Fore.RED}  ⚠ Acceso bloqueado: {protection.get('block_reason')}{Style.RESET_ALL}")
    if protection.get("auth_required"):
        print(f"  {Fore.YELLOW}  ⚠ Autenticación requerida: {protection.get('auth_type')}{Style.RESET_ALL}")
    if protection.get("waf_detected"):
        print(f"  {Fore.YELLOW}  ⚠ WAF/CDN: {protection.get('waf_type')}{Style.RESET_ALL}")
    if protection.get("rate_limiting"):
        print(f"  {Fore.YELLOW}  ⚠ Rate limiting detectado{Style.RESET_ALL}")
    if not any([
        protection.get("ssrf_blocked"),
        protection.get("captcha_detected"),
        protection.get("access_blocked"),
        protection.get("auth_required"),
    ]):
        print(f"  {Fore.GREEN}  ✓ Sin protecciones críticas bloqueantes detectadas{Style.RESET_ALL}")

    via = viability.get("technical_feasibility", "no_evaluado")
    score = viability.get("feasibility_score", 0)
    via_color = {
        "viable": Fore.GREEN,
        "parcialmente_viable": Fore.YELLOW,
        "limitada": Fore.YELLOW,
        "no_viable": Fore.RED,
    }.get(via, Fore.WHITE)

    print(f"\n  {'Viabilidad técnica:':<22} {via_color}{via.upper()} (score: {score}/100){Style.RESET_ALL}")
    strategies = viability.get("recommended_strategies", [])
    if strategies:
        print("  Estrategias técnicas recomendadas:")
        for s in strategies:
            print(f"    · {s}")

    if errors:
        print(f"\n  {Fore.RED}Incidencias / errores registrados:{Style.RESET_ALL}")
        for err in errors:
            print(f"    ⚠ {err}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Herramienta de Extracción Inteligente — Diagnóstico Web (Avance 1)"
    )
    parser.add_argument("--url", help="URL pública a analizar (http/https).")
    parser.add_argument("--label", default="", help="Etiqueta opcional del sitio.")
    parser.add_argument(
        "--no-selenium",
        action="store_true",
        help="Omitir análisis DOM dinámico con Selenium.",
    )
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="Listar perfiles almacenados y salir.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Función principal — orquesta el flujo de diagnóstico."""
    args = _parse_args(argv)
    _print_banner()

    config = get_config()
    log_file = (
        config.paths.logs_dir
        / f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    )
    setup_logging(level=config.log_level, log_file=log_file)
    logger = get_logger("main")

    profile_manager = ProfileManager(config.paths.profiles_dir)
    reporter = DiagnosticReporter(config.paths.reports_dir)

    if args.list_profiles:
        profiles = profile_manager.list_profiles()
        if not profiles:
            print("  No hay perfiles guardados todavía.\n")
        else:
            print(f"  Perfiles registrados ({len(profiles)}):")
            for p in profiles:
                print(
                    f"    · {p['domain']} ({p['url']}) — "
                    f"{p['analysis_count']} análisis (último: {p['last_analysis']})"
                )
            print()
        return

    if args.url:
        url = normalize_url(args.url)
        valid, msg = validate_url(url)
        if not valid:
            print(f"{Fore.RED}  ✗ URL no válida: {msg}{Style.RESET_ALL}")
            sys.exit(1)
        existing_profile = profile_manager.load(url)
        reusable = profile_manager.get_reusable_config(url) or {}
        label = args.label or str(reusable.get("label", ""))
        use_selenium = not args.no_selenium
    else:
        url = _prompt_url()
        existing_profile = profile_manager.load(url)
        reusable = profile_manager.get_reusable_config(url) or {}
        options = _prompt_options(default_label=str(reusable.get("label", "")))
        label = str(options.get("label", ""))
        use_selenium = bool(options.get("use_selenium", True))

    if existing_profile:
        count = existing_profile.get("analysis_count", 0)
        last = existing_profile.get("last_analysis", "desconocido")
        print(
            f"{Fore.CYAN}  ℹ Perfil previo recuperado "
            f"({count} análisis, último: {last}){Style.RESET_ALL}\n"
        )

    try:
        target = register_target(
            url=url,
            label=label,
            respect_robots_txt=config.respect_robots_txt,
        )
    except ValueError as exc:
        print(f"{Fore.RED}  ✗ Error al registrar URL: {exc}{Style.RESET_ALL}")
        sys.exit(1)

    logger.info("Objetivo registrado: %s", sanitize_url(target.url))
    print(f"{Fore.CYAN}  ⟳ Iniciando diagnóstico de {target.safe_url}...{Style.RESET_ALL}\n")

    inspector = WebInspector(config)
    try:
        diagnosis = inspector.inspect(
            target,
            use_selenium=use_selenium,
            previous_profile=existing_profile,
        )
    except Exception as exc:
        logger.exception("Error crítico durante el diagnóstico")
        print(f"{Fore.RED}  ✗ Error crítico: {exc}{Style.RESET_ALL}")
        sys.exit(1)

    profile_path = profile_manager.save(diagnosis)
    print(f"\n{Fore.GREEN}  ✓ Perfil guardado: {profile_path.name}{Style.RESET_ALL}")

    report_paths = reporter.generate(diagnosis)
    print(f"{Fore.GREEN}  ✓ Reporte JSON: {report_paths['json'].name}{Style.RESET_ALL}")
    print(f"{Fore.GREEN}  ✓ Reporte TXT:  {report_paths['txt'].name}{Style.RESET_ALL}")

    _print_summary(diagnosis)

    print(f"\n{Fore.CYAN}  Archivos generados en:{Style.RESET_ALL}")
    print(f"    Perfiles:  {config.paths.profiles_dir}")
    print(f"    Reportes:  {config.paths.reports_dir}")
    print(f"    Logs:      {config.paths.logs_dir}")
    print(f"\n{Fore.CYAN}{'=' * 65}{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
