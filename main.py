"""
main.py — Punto de entrada de la Herramienta de Extracción
Inteligente y Automatizada de Información Web.

AVANCE 1: Sistema de inspección, diagnóstico y perfilamiento web.
AVANCE 2: Extracción multitecnología, selección, transformación y almacenamiento.

Uso interactivo:
    python main.py

Uso por línea de comandos:
    python main.py --url https://ejemplo.com --label "Sitio" --extract
    python main.py --url https://ejemplo.com --extract --strategy selenium \
        --max-pages 2 --max-records 25 --format json,csv --schema schema.json
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

# Evita fallos de codificación en consolas Windows con cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.config.settings import (
    get_config,
    normalize_url,
    register_target,
    validate_url,
)
from src.extractor.extraction_service import ExtractionService
from src.inspector.web_inspector import WebInspector
from src.monitor.logger import get_logger, setup_logging
from src.selector.builder import load_schema_file
from src.monitor.reporter import DiagnosticReporter
from src.utils.profiles import ProfileManager
from src.utils.security import sanitize_url


def _print_banner() -> None:
    """Imprime el banner de bienvenida."""
    print(f"\n{Fore.CYAN}{'=' * 65}")
    print(f"  {'HERRAMIENTA DE EXTRACCIÓN INTELIGENTE Y AUTOMATIZADA':^61}")
    print(f"  {'Avance 1+2 — Diagnóstico, Extracción y Calidad':^61}")
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


def _parse_formats(value: str) -> list[str]:
    """Parsea una lista de formatos separada por comas."""
    formats = [item.strip().lower() for item in value.split(",") if item.strip()]
    allowed = {"json", "csv", "excel", "sqlite"}
    invalid = [item for item in formats if item not in allowed]
    if not formats or invalid:
        raise argparse.ArgumentTypeError(
            f"Formatos válidos: {', '.join(sorted(allowed))}. Recibido: {value}"
        )
    return formats


def _prompt_extraction_options(config: Any) -> dict[str, Any]:
    """Solicita parámetros de extracción en modo interactivo."""
    print(f"{Fore.CYAN}  Opciones de extracción (Avance 2):{Style.RESET_ALL}")
    strategy = input(
        "  Estrategia [auto/requests/selenium/api] (Enter = auto): "
    ).strip().lower() or "auto"
    max_pages_raw = input(
        f"  Máximo de páginas (Enter = {config.extraction.max_pages}): "
    ).strip()
    max_records_raw = input(
        f"  Máximo de registros (Enter = {config.extraction.max_records}): "
    ).strip()
    formats_raw = input(
        f"  Formatos json,csv,excel,sqlite (Enter = {config.extraction.default_format}): "
    ).strip()
    schema_path = input(
        "  Ruta de esquema JSON opcional (Enter = automático): "
    ).strip()
    return {
        "strategy": strategy,
        "max_pages": int(max_pages_raw) if max_pages_raw.isdigit() else None,
        "max_records": int(max_records_raw) if max_records_raw.isdigit() else None,
        "formats": _parse_formats(formats_raw) if formats_raw else None,
        "schema_path": schema_path,
    }


def _print_extraction_summary(summary: dict[str, Any]) -> None:
    """Imprime el resultado de la extracción en consola."""
    status = summary.get("status", "desconocido")
    color = {
        "ok": Fore.GREEN,
        "blocked": Fore.YELLOW,
        "error": Fore.RED,
    }.get(status, Fore.WHITE)

    print(f"\n{Fore.CYAN}{'=' * 65}")
    print("  RESUMEN DE EXTRACCIÓN")
    print(f"{'=' * 65}{Style.RESET_ALL}")
    print(f"  {'Estado:':<22} {color}{status.upper()}{Style.RESET_ALL}")
    print(f"  {'ID ejecución:':<22} {summary.get('execution_id', '-')}")
    print(f"  {'Estrategia:':<22} {(summary.get('extraction_config') or {}).get('strategy', '-')}")
    schema = summary.get("schema") or {}
    print(f"  {'Esquema:':<22} {schema.get('name', '-')} ({len(schema.get('fields', []))} campos)")
    print(f"  {'Páginas:':<22} {len(summary.get('pages', []))}")
    print(f"  {'Registros crudos:':<22} {summary.get('raw_records', 0)}")
    print(f"  {'Registros procesados:':<22} {summary.get('processed_records', 0)}")

    quality = summary.get("quality") or {}
    if quality:
        print(
            f"  {'Calidad:':<22} {quality.get('valid_records', 0)} válidos / "
            f"{quality.get('invalid_records', 0)} inválidos / "
            f"{quality.get('duplicates_removed', 0)} duplicados"
        )

    coverage = summary.get("lineage_coverage") or {}
    if coverage:
        print(
            f"  {'Linaje:':<22} {coverage.get('records_with_lineage', 0)}/"
            f"{coverage.get('records_total', 0)} registros auditables"
        )

    for error in summary.get("errors", []):
        print(f"  {Fore.YELLOW}⚠ {error}{Style.RESET_ALL}")

    outputs = summary.get("outputs") or {}
    if outputs:
        print(f"\n  {Fore.GREEN}Datasets generados:{Style.RESET_ALL}")
        for fmt, paths in outputs.items():
            print(f"    [{fmt}] raw:      {paths.get('raw', '-')}")
            print(f"    [{fmt}] processed:{paths.get('processed', '-')}")

    reports = summary.get("report_files") or {}
    if reports:
        print(f"\n  Reporte de extracción: {reports.get('json', '-')}")
    print(f"{Fore.CYAN}{'=' * 65}{Style.RESET_ALL}\n")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Herramienta de Extracción Inteligente — Diagnóstico y extracción "
            "multitecnología (Avances 1 y 2)"
        )
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

    # ---- Avance 2: extracción ----
    parser.add_argument(
        "--extract",
        action="store_true",
        help="Ejecutar extracción completa tras el diagnóstico.",
    )
    parser.add_argument(
        "--schema",
        help="Ruta a un esquema JSON de selección (opcional; por defecto automático).",
    )
    parser.add_argument(
        "--strategy",
        choices=("auto", "requests", "selenium", "api"),
        default="auto",
        help="Motor de adquisición (auto elige según el diagnóstico).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Máximo de páginas a adquirir (por defecto, configuración).",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Máximo de registros a procesar (por defecto, configuración).",
    )
    parser.add_argument(
        "--format",
        dest="formats",
        type=_parse_formats,
        default=None,
        help="Formatos de salida separados por comas: json,csv,excel,sqlite.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directorio para datasets raw/processed (por defecto: data/).",
    )
    parser.add_argument(
        "--reuse-profile",
        action="store_true",
        help="Reutilizar el diagnóstico guardado en el perfil (si existe).",
    )
    parser.add_argument(
        "--allow-private-network",
        action="store_true",
        help="Solo para pruebas locales controladas: permite 127.0.0.1/localhost.",
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
        valid, msg = validate_url(
            url, allow_private_network=args.allow_private_network
        )
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
            allow_private_network=args.allow_private_network,
        )
    except ValueError as exc:
        print(f"{Fore.RED}  ✗ Error al registrar URL: {exc}{Style.RESET_ALL}")
        sys.exit(1)

    logger.info("Objetivo registrado: %s", sanitize_url(target.url))

    reused_diagnosis = bool(
        args.reuse_profile
        and existing_profile
        and existing_profile.get("current_diagnosis")
    )
    if reused_diagnosis:
        diagnosis = existing_profile["current_diagnosis"]
        print(
            f"{Fore.CYAN}  ℹ Reutilizando diagnóstico del perfil "
            f"({diagnosis.get('diagnosis_timestamp', 'fecha desconocida')})"
            f"{Style.RESET_ALL}\n"
        )
    else:
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

    # ---- Avance 2: extracción ----
    extract = args.extract
    extraction_options: dict[str, Any] = {
        "strategy": args.strategy,
        "max_pages": args.max_pages,
        "max_records": args.max_records,
        "formats": args.formats,
        "schema_path": args.schema,
    }
    if not extract and not args.url:
        answer = input("\n  ¿Desea ejecutar la extracción completa? [s/N]: ").strip().lower()
        extract = answer in ("s", "si", "sí")
        if extract:
            extraction_options = _prompt_extraction_options(config)

    if extract:
        schema_config: dict[str, Any] | None = None
        if extraction_options.get("schema_path"):
            try:
                schema_config = load_schema_file(extraction_options["schema_path"])
            except Exception as exc:
                print(f"{Fore.RED}  ✗ No se pudo cargar el esquema: {exc}{Style.RESET_ALL}")
                sys.exit(1)

        print(f"\n{Fore.CYAN}  ⟳ Ejecutando extracción con las reglas del Avance 2...{Style.RESET_ALL}")
        service = ExtractionService(config)
        try:
            summary = service.run(
                target,
                diagnosis=diagnosis,
                profile=existing_profile,
                schema_config=schema_config,
                strategy=str(extraction_options.get("strategy") or "auto"),
                max_pages=extraction_options.get("max_pages"),
                max_records=extraction_options.get("max_records"),
                formats=extraction_options.get("formats"),
                output_dir=Path(args.output_dir) if args.output_dir else None,
            )
        except Exception as exc:
            logger.exception("Error crítico durante la extracción")
            print(f"{Fore.RED}  ✗ Error crítico en extracción: {exc}{Style.RESET_ALL}")
            sys.exit(1)
        _print_extraction_summary(summary)

    print(f"\n{Fore.CYAN}  Archivos generados en:{Style.RESET_ALL}")
    print(f"    Perfiles:  {config.paths.profiles_dir}")
    print(f"    Reportes:  {config.paths.reports_dir}")
    print(f"    Logs:      {config.paths.logs_dir}")
    print(f"\n{Fore.CYAN}{'=' * 65}{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
