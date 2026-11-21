"""Visores de reportes y registros para la interfaz de escritorio."""
from __future__ import annotations

import csv
import json
import queue
import sqlite3
import threading
from itertools import islice
import re
import tkinter as tk
from pathlib import Path
from tkinter import font, scrolledtext, ttk
from typing import Any

from src.utils.security import sanitize_for_persistence
from src.gui.database_viewer import list_datasets, read_database_dataset

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
        if kind == "reports":
            self.report_text = scrolledtext.ScrolledText(tree_frame, wrap="word", state="disabled")
            self.report_text.grid(row=0, column=0, sticky="nsew")
            self.report_text.grid_remove()
        if kind == "logs":
            ttk.Label(container, text="Detalle del evento seleccionado").pack(anchor="w", pady=(8, 0))
            self.detail = tk.Text(container, height=5, wrap="word", state="disabled")
            self.detail.pack(fill="x")
        self.refresh()

    def refresh(self) -> None:
        selected = self.file_choice.get()
        try:
            self.files = sorted((p for p in self.directory.iterdir() if p.is_file() and p.suffix.lower() in ({".json", ".txt"} if self.kind == "reports" else {".log"})),
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
            if self.kind == "reports":
                self.report_text.grid_remove()
                self.tree.grid()
            if self.kind == "logs":
                self._set_detail("")

    def _selected(self, _event: Any = None) -> None:
        self.tree.delete(*self.tree.get_children())
        if self.kind == "reports":
            self.report_text.grid_remove()
            self.tree.grid()
        if self.kind == "logs":
            self._set_detail("")
        index = self.file_choice.current()
        if index < 0 or index >= len(self.files):
            return
        try:
            if self.kind == "reports":
                path = self.files[index]
                self._report_rows = 0
                if path.suffix.lower() == ".txt":
                    text = read_report_text(path)
                    self.tree.grid_remove()
                    self.report_text.configure(state="normal")
                    self.report_text.delete("1.0", "end")
                    self.report_text.insert("1.0", text)
                    self.report_text.configure(state="disabled")
                    self.report_text.grid()
                else:
                    data = read_report(path)
                    for key, value in data.items():
                        self._add_report("", label(str(key)), value, True)
                suffix = " Vista limitada a 3000 campos." if self._report_rows >= _MAX_ROWS else ""
                self.notice.set(("Reporte de texto." if path.suffix.lower() == ".txt" else "Seleccione o despliegue una sección para consultar sus datos.") + suffix)
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


def read_report_text(path: Path) -> str:
    """Lee un reporte de texto, aplicando el mismo límite del visor JSON."""
    if path.stat().st_size > _MAX_BYTES:
        raise ValueError("El reporte supera el límite de 5 MB para el visor.")
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ValueError("El reporte está vacío.")
    return text


def sqlite_tables(path: Path) -> list[str]:
    """Enumera tablas sin permitir escrituras en la base."""
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        return [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]


def read_dataset(path: Path, table: str | None = None) -> tuple[list[str], list[list[Any]], bool]:
    """Lee una muestra tabular de los datos exportados."""
    suffix = path.suffix.lower()
    if suffix in (".json", ".csv") and path.stat().st_size > _MAX_BYTES:
        raise ValueError("El archivo supera el límite de 5 MB para el visor.")
    if suffix == ".sqlite":
        tables = sqlite_tables(path)
        if table not in tables:
            raise ValueError("Seleccione una tabla válida.")
        safe_table = table.replace('"', '""')
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            cursor = connection.execute(f'SELECT * FROM "{safe_table}" LIMIT ?', (_MAX_ROWS + 1,))
            columns = [str(item[0]) for item in cursor.description]
            rows = [list(row) for row in cursor.fetchall()]
    elif suffix == ".xlsx":
        from openpyxl import load_workbook
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = workbook.active
            values = list(islice(sheet.iter_rows(values_only=True), _MAX_ROWS + 2))
        finally:
            workbook.close()
        columns = [str(value) if value is not None else f"Columna {i}" for i, value in enumerate(values[0], 1)] if values else []
        rows = [list(row) for row in values[1:]]
    elif suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream)
            columns = next(reader, [])
            rows = [list(row) for row in islice(reader, _MAX_ROWS + 1)]
    elif suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise ValueError("El dataset JSON debe ser una lista de registros.")
        sample = data[:_MAX_ROWS + 1]
        columns = list(dict.fromkeys(key for row in sample for key in row))
        rows = [[row.get(key) for key in columns] for row in sample]
    else:
        raise ValueError("Formato de datos no compatible.")
    return columns, rows[:_MAX_ROWS], len(rows) > _MAX_ROWS


def dataset_column_widths(columns: list[str], rows: list[list[Any]], measure: Any) -> list[int]:
    """Calcula anchos acotados usando encabezados y una muestra de registros."""
    widths = [max(100, min(420, measure(str(column)[:100]) + 24)) for column in columns]
    for row in rows[:100]:
        for index, value in enumerate(row[:len(columns)]):
            text = str(value)[:100] if value is not None else ""
            widths[index] = max(widths[index], min(420, measure(text) + 24))
    return widths


class DatasetViewer(tk.Toplevel):
    """Consulta los datasets locales exportados en una tabla."""

    def __init__(self, parent: tk.Misc, directory: Path) -> None:
        super().__init__(parent)
        self.directory = Path(directory)
        self.files: list[Path] = []
        self._database_results: queue.Queue = queue.Queue()
        self._request_id = 0
        self.title("Datos exportados")
        self.geometry("1100x650")
        container = ttk.Frame(self, padding=12)
        container.pack(fill="both", expand=True)
        toolbar = ttk.Frame(container)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="Fuente:").pack(side="left")
        self.source = ttk.Combobox(toolbar, values=("Archivos locales", "PostgreSQL", "MongoDB"), state="readonly", width=15)
        self.source.current(0)
        self.source.pack(side="left", padx=(5, 8))
        self.source.bind("<<ComboboxSelected>>", lambda event: self.refresh())
        ttk.Label(toolbar, text="Archivo:").pack(side="left")
        self.file_choice = ttk.Combobox(toolbar, state="readonly")
        self.file_choice.pack(side="left", fill="x", expand=True, padx=8)
        self.file_choice.bind("<<ComboboxSelected>>", self._select_file)
        ttk.Button(toolbar, text="Actualizar", command=self.refresh).pack(side="left")
        ttk.Label(toolbar, text="Tabla SQLite:").pack(side="left", padx=(8, 0))
        self.table_choice = ttk.Combobox(toolbar, state="disabled", width=25)
        self.table_choice.pack(side="left", padx=8)
        self.table_choice.bind("<<ComboboxSelected>>", self._show_data)
        self.notice = tk.StringVar()
        ttk.Label(container, textvariable=self.notice, wraplength=1000).pack(fill="x", pady=8)
        frame = ttk.Frame(container)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, show="headings")
        self.tree.bind("<ButtonRelease-1>", self._show_cell)
        self.tree.bind("<Shift-MouseWheel>", self._horizontal_wheel)
        self.tree.bind("<Shift-Button-4>", lambda event: self._horizontal_step(-1))
        self.tree.bind("<Shift-Button-5>", lambda event: self._horizontal_step(1))
        self.tree.bind("<Shift-Left>", lambda event: self._horizontal_step(-1))
        self.tree.bind("<Shift-Right>", lambda event: self._horizontal_step(1))
        vertical = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self._horizontal_scroll)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        ttk.Label(container, text="Valor completo de la celda seleccionada").pack(anchor="w", pady=(8, 0))
        self.detail = scrolledtext.ScrolledText(container, height=4, wrap="word", state="disabled")
        self.detail.pack(fill="x")
        self.refresh()
        self.after(100, self._poll_database)

    def _horizontal_scroll(self, *args: str) -> None:
        """Aumenta el paso de las flechas sin alterar el arrastre ni las páginas."""
        if len(args) == 3 and args[0] == "scroll" and args[2] == "units":
            self.tree.xview_scroll(int(args[1]) * 80, "units")
        else:
            self.tree.xview(*args)

    def _horizontal_step(self, direction: int) -> str:
        self.tree.xview_scroll(direction * 80, "units")
        return "break"

    def _horizontal_wheel(self, event: Any) -> str:
        if event.delta:
            steps = max(1, abs(event.delta) // 120)
            return self._horizontal_step(-steps if event.delta > 0 else steps)
        return "break"

    def refresh(self) -> None:
        self._request_id += 1
        self.tree.delete(*self.tree.get_children())
        self._set_cell_detail("")
        self.table_choice.set("")
        self.table_choice.configure(state="disabled", values=())
        if self.source.get() != "Archivos locales":
            self.file_choice.set("")
            self.file_choice["values"] = []
            self._run_database("list", self.source.get())
            return
        selected = self.file_choice.get()
        try:
            self.files = sorted((p for p in self.directory.iterdir() if p.is_file() and p.suffix.lower() in {".json", ".csv", ".xlsx", ".sqlite"}), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError as exc:
            self.files = []
            self.notice.set(f"No se pudo leer la carpeta: {exc}")
            return
        names = [p.name for p in self.files]
        self.file_choice["values"] = names
        if names:
            self.file_choice.current(names.index(selected) if selected in names else 0)
            self._select_file()
        else:
            self.file_choice.set("")
            self.tree.delete(*self.tree.get_children())
            self.notice.set("Aún no hay datasets locales. Ejecute una extracción con JSON, CSV, Excel o SQLite.")

    def _select_file(self, _event: Any = None) -> None:
        if self.source.get() != "Archivos locales":
            name = self.file_choice.get()
            if name:
                self._request_id += 1
                self.tree.delete(*self.tree.get_children())
                self._set_cell_detail("")
                self._run_database("read", self.source.get(), name)
            return
        self.table_choice.set("")
        self.table_choice.configure(state="disabled", values=())
        index = self.file_choice.current()
        if index < 0 or index >= len(self.files):
            return
        path = self.files[index]
        try:
            if path.suffix.lower() == ".sqlite":
                tables = sqlite_tables(path)
                if not tables:
                    raise ValueError("La base de datos no contiene tablas.")
                self.table_choice.configure(state="readonly", values=tables)
                self.table_choice.current(0)
            self._show_data()
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.tree.delete(*self.tree.get_children())
            self.notice.set(f"No se pudo abrir el archivo: {exc}")

    def _show_data(self, _event: Any = None) -> None:
        index = self.file_choice.current()
        if index < 0 or index >= len(self.files):
            return
        self.tree.delete(*self.tree.get_children())
        self._set_cell_detail("")
        try:
            columns, rows, truncated = read_dataset(self.files[index], self.table_choice.get() or None)
            self._display_data(columns, rows, truncated)
        except Exception as exc:
            self.notice.set(f"No se pudo abrir el archivo: {exc}")

    def _display_data(self, columns: list[str], rows: list[list[Any]], truncated: bool) -> None:
        self.tree.delete(*self.tree.get_children())
        ids = [f"column_{i}" for i in range(len(columns))]
        self.tree["columns"] = ids
        widths = dataset_column_widths(columns, rows, font.nametofont("TkDefaultFont").measure)
        for column_id, heading, width in zip(ids, columns, widths):
            self.tree.heading(column_id, text=str(heading))
            self.tree.column(column_id, width=width, minwidth=80, stretch=False)
        for row in rows:
            values = [json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else "" if v is None else str(v) for v in row]
            self.tree.insert("", "end", values=values)
        self.notice.set(f"{len(rows)} registros mostrados." + (" Vista limitada a 3000 registros." if truncated else "") if columns else "El dataset no contiene datos tabulares.")

    def _run_database(self, action: str, source: str, name: str = "") -> None:
        request_id = self._request_id
        self.notice.set("Consultando la base de datos…")
        def task() -> None:
            try:
                result = list_datasets(source) if action == "list" else read_database_dataset(source, name)
                self._database_results.put((request_id, action, result, None))
            except Exception as exc:
                # Solo los errores controlados se muestran; nunca una URI del controlador.
                message = str(exc) if isinstance(exc, ValueError) else "No se pudo consultar la base de datos."
                self._database_results.put((request_id, action, None, message))
        threading.Thread(target=task, daemon=True).start()

    def _poll_database(self) -> None:
        try:
            while True:
                request_id, action, result, error = self._database_results.get_nowait()
                if request_id != self._request_id:
                    continue
                if error:
                    self.notice.set(error)
                elif action == "list":
                    self.file_choice["values"] = result
                    if result:
                        self.file_choice.current(0)
                        self._select_file()
                    else:
                        self.notice.set("No hay datasets guardados en esta base de datos.")
                else:
                    self._display_data(*result)
        except queue.Empty:
            pass
        self.after(100, self._poll_database)

    def _set_cell_detail(self, text: str) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def _show_cell(self, event: Any) -> None:
        row_id = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        if not row_id or not column_id or column_id == "#0":
            return
        index = int(column_id[1:]) - 1
        values = self.tree.item(row_id, "values")
        if 0 <= index < len(values):
            self._set_cell_detail(str(values[index]))
