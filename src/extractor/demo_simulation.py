"""Ejemplo local claramente simulado para la demostración reCAPTCHA."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from src.config.settings import ProjectConfig
from src.storage.adapters import StorageFactory

DEMO_URL = "https://www.google.com/recaptcha/api2/demo"


def is_demo_url(url: str) -> bool:
    """Acepta exclusivamente la URL pública exacta de la demostración."""
    try:
        parsed = urlsplit(url.strip())
        return (parsed.scheme.lower() == "https" and parsed.netloc.lower() == "www.google.com"
                and parsed.path == "/recaptcha/api2/demo" and not parsed.query
                and not parsed.fragment and "?" not in url and "#" not in url)
    except ValueError:
        return False


def generate_demo(url: str, config: ProjectConfig, formats: list[str]) -> dict:
    """Genera datos ficticios sin consultar la página ni verificar un CAPTCHA."""
    if not is_demo_url(url):
        raise ValueError("La simulación solo está disponible para la URL exacta de la demostración.")
    if not formats:
        raise ValueError("Seleccione al menos un formato de salida.")
    execution_id = uuid.uuid4().hex
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    records = [
        {"origen": "SIMULADO", "metodo": "simulacion_local", "ejemplo": "Ejemplo ficticio 1",
         "resultado": "Solo demostración; no se verificó el CAPTCHA"},
        {"origen": "SIMULADO", "metodo": "simulacion_local", "ejemplo": "Ejemplo ficticio 2",
         "resultado": "Datos generados localmente; no extraídos de Google"},
    ]
    data_dir = Path(config.paths.data_dir)
    outputs: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    for fmt in formats:
        try:
            storage = StorageFactory.create(fmt, data_dir)
            for stage in ("raw", "processed"):
                name = f"demo_recaptcha_simulado_{stage}_{stamp}_{execution_id[:8]}"
                output = storage.save(records, name, table=name, database="demo_recaptcha_simulado")
                outputs.setdefault(fmt, {})[stage] = str(output)
        except Exception as exc:
            errors.append(f"No se pudo guardar {fmt} ({type(exc).__name__}). Revise su configuración.")
    summary = {
        "tipo": "SIMULACION_LOCAL",
        "estado": "simulado_parcial" if outputs and errors else "simulado" if outputs else "error_simulacion",
        "aviso": "Datos ficticios generados localmente. No son extracción real y no se verificó el CAPTCHA.",
        "url_demostracion": DEMO_URL,
        "execution_id": execution_id,
        "registros": len(records),
        "outputs": outputs,
        "errors": errors,
    }
    reports_dir = Path(config.paths.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    base = reports_dir / f"demo_recaptcha_simulado_{stamp}_{execution_id[:8]}"
    json_path = base.with_suffix(".json")
    txt_path = base.with_suffix(".txt")
    summary["report_files"] = {"json": str(json_path), "txt": str(txt_path)}
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text("SIMULACIÓN LOCAL — NO ES EXTRACCIÓN REAL\n\n" + json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary
