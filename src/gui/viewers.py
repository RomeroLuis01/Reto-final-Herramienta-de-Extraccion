"""Visores de reportes y registros para la interfaz de escritorio."""
from __future__ import annotations

import json
import re
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any

from src.utils.security import sanitize_for_persistence

_MAX_BYTES = 5 * 1024 * 1024
_MAX_ROWS = 3000
_LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+\[([^]]+)\]\s*(.*)$")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_LABELS = {
    "url": "URL", "domain": "Dominio", "label": "Etiqueta", "status": "Estado",
    "access_status": "Estado de acceso", "diagnosis_timestamp": "Fecha del diagnóstico",
    "started_at": "Inicio", "finished_at": "Fin", "execution_id": "ID de ejecución",
    "site_identification": "Identificación del sitio", "http": "Acceso HTTP",
    "html_analysis": "Estructura HTML y DOM", "technologies": "Tecnologías detectadas",
    "acquisition_mechanisms": "Mecanismos de adquisición", "network": "Red y API",
    "pagination": "Paginación y carga", "interactions": "Interacciones",
    "protection": "Protecciones y restricciones", "viability": "Viabilidad",
    "confidence_summary": "Certeza de hallazgos", "traceability": "Trazabilidad",
    "extraction_config": "Configuración de extracción", "schema": "Esquema",
    "pages": "Páginas adquiridas", "raw_records": "Registros originales",
    "processed_records": "Registros procesados", "quality": "Calidad de datos",
    "lineage_coverage": "Cobertura de linaje", "changes": "Cambios detectados",
    "outputs": "Archivos generados", "errors": "Errores e incidencias",
}


def label(key: str) -> str:
    return _LABELS.get(key, key.replace("_", " ").capitalize())


def read_report(path: Path) -> dict[str, Any]:
    """Carga y sanitiza un reporte JSON estructurado de tamaño acotado."""
    if path.stat().st_size > _MAX_BYTES:
        raise ValueError("El reporte supera el límite de 5 MB para el visor.")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not data:
        raise ValueError("El reporte no contiene un objeto con datos.")
    return sanitize_for_persistence(data)


def read_log(path: Path) -> tuple[list[dict[str, str]], bool]:
    """Lee el final del log y agrupa las líneas de continuación de cada evento."""
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        truncated = size > _MAX_BYTES
        stream.seek(max(0, size - _MAX_BYTES))
        raw = stream.read().decode("utf-8", errors="replace")
    if truncated:
        raw = raw.partition("\n")[2]
    entries: list[dict[str, str]] = []
    for line in raw.splitlines():
        line = _ANSI.sub("", line)
        match = _LOG_LINE.match(line)
        if match:
            entries.append(dict(zip(("date", "level", "component", "message"), match.groups())))
        elif entries:
            entries[-1]["message"] += "\n" + line
        elif line.strip():
            entries.append({"date": "", "level": "", "component": "", "message": line})
    return entries, truncated


class FileViewer(tk.Toplevel):
    """Explora archivos generados con vistas estructuradas por tipo."""

    def __init__(self, parent: tk.Misc, directory: Path, kind: str) -> None:
        super().__init__(parent)
        self.directory = Path(directory)
        self.kind = kind
        self.files: list[Path] = []
        self.title("Reportes generados" if kind == "reports" else "Registro de actividad")
        self.geometry("1100x650")
        self.minsize(700, 400)
        container = ttk.Frame(self, padding=12)
        container.pack(fill="both", expand=True)
        toolbar = ttk.Frame(container)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Label(toolbar, text="Archivo:").pack(side="left")
        self.file_choice = ttk.Combobox(toolbar, state="readonly")
        self.file_choice.pack(side="left", fill="x", expand=True, padx=8)
        self.file_choice.bind("<<ComboboxSelected>>", self._selected)
        ttk.Button(toolbar, text="Actualizar", command=self.refresh).pack(side="left")
        self.notice = tk.StringVar()
        ttk.Label(container, textvariable=self.notice, wraplength=1000).pack(fill="x", pady=(0, 5))
        tree_frame = ttk.Frame(container)
        tree_frame.pack(fill="both", expand=True)
        if kind == "reports":
            self.tree = ttk.Treeview(tree_frame, columns=("value",), show="tree headings")
            self.tree.heading("#0", text="Sección / campo")
            self.tree.heading("value", text="Valor")
            self.tree.column("#0", width=300, stretch=True)
            self.tree.column("value", width=650, stretch=True)
        else:
            self.tree = ttk.Treeview(tree_frame, columns=("date", "level", "component", "message"), show="headings")
            for column, heading, width in (("date", "Fecha y hora", 145), ("level", "Nivel", 80),
                                           ("component", "Módulo", 180), ("message", "Mensaje", 540)):
                self.tree.heading(column, text=heading)
                self.tree.column(column, width=width, stretch=(column == "message"))
            self.tree.tag_configure("ERROR", foreground="#b42318")
            self.tree.tag_configure("CRITICAL", foreground="#b42318")
            self.tree.tag_configure("WARNING", foreground="#9a6700")
            self.tree.bind("<<TreeviewSelect>>", self._show_detail)
        vertical = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        if kind == "logs":
            ttk.Label(container, text="Detalle del evento seleccionado").pack(anchor="w", pady=(8, 0))
            self.detail = tk.Text(container, height=5, wrap="word", state="disabled")
            self.detail.pack(fill="x")
        self.refresh()

    def refresh(self) -> None:
        selected = self.file_choice.get()
        try:
            self.files = sorted(self.directory.glob("*.json" if self.kind == "reports" else "*.log"),
                                key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError as exc:
            self.files = []
            self.notice.set(f"No se pudo leer la carpeta: {exc}")
        self.file_choice["values"] = [p.name for p in self.files]
        if self.files:
            names = [p.name for p in self.files]
            self.file_choice.current(names.index(selected) if selected in names else 0)
            self._selected()
        else:
            self.file_choice.set("")
            self.tree.delete(*self.tree.get_children())
            self.notice.set("Aún no hay archivos generados en la carpeta configurada.")
            if self.kind == "logs":
                self._set_detail("")

    def _selected(self, _event: Any = None) -> None:
        self.tree.delete(*self.tree.get_children())
        if self.kind == "logs":
            self._set_detail("")
        index = self.file_choice.current()
        if index < 0 or index >= len(self.files):
            return
        try:
            if self.kind == "reports":
                data = read_report(self.files[index])
                self._report_rows = 0
                for key, value in data.items():
                    self._add_report("", label(str(key)), value, True)
                suffix = " Vista limitada a 3000 campos." if self._report_rows >= _MAX_ROWS else ""
                self.notice.set("Seleccione o despliegue una sección para consultar sus datos." + suffix)
            else:
                entries, truncated = read_log(self.files[index])
                for entry in entries[-_MAX_ROWS:]:
                    self.tree.insert("", "end", values=(entry["date"], entry["level"], entry["component"], entry["message"]), tags=(entry["level"],))
                suffix = " Se muestran los últimos 5 MB." if truncated else ""
                if len(entries) > _MAX_ROWS:
                    suffix += " Se muestran los últimos 3000 eventos."
                self.notice.set(f"{len(entries)} eventos. Seleccione uno para ver su detalle.{suffix}" if entries else "El archivo de log está vacío.")
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            self.tree.delete(*self.tree.get_children())
            self.notice.set(f"No se pudo abrir el archivo: {exc}")

    def _add_report(self, parent: str, name: str, value: Any, root: bool = False) -> None:
        if self._report_rows >= _MAX_ROWS:
            return
        self._report_rows += 1
        if isinstance(value, dict):
            node = self.tree.insert(parent, "end", text=name, values=("" if value else "Sin datos",), open=root)
            for key, item in value.items():
                if self._report_rows >= _MAX_ROWS:
                    break
                self._add_report(node, label(str(key)), item)
        elif isinstance(value, list):
            node = self.tree.insert(parent, "end", text=name, values=(f"{len(value)} elementos" if value else "Sin elementos",), open=root)
            for i, item in enumerate(value, 1):
                if self._report_rows >= _MAX_ROWS:
                    break
                self._add_report(node, str(i), item)
        else:
            display = "Sí" if value is True else "No" if value is False else "No disponible" if value is None else str(value)
            self.tree.insert(parent, "end", text=name, values=(display,))

    def _set_detail(self, text: str) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def _show_detail(self, _event: Any = None) -> None:
        selection = self.tree.selection()
        values = self.tree.item(selection[0], "values") if selection else ()
        self._set_detail(str(values[3]) if len(values) > 3 else "")
