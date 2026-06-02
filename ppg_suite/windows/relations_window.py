from __future__ import annotations

import csv
import html
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from ..paths import FIGURES_DIR, PROCESSED_DIR, RAW_DIR, REPORT_DIR, RESULTS_DIR, SCREENSHOT_DIR, SESSION_DIR
from ..utils import fmt


MODE_LABELS = {
    "real": "Medicion de campo",
    "normal": "Medicion de campo",
    "test": "Test de campo",
    "temp": "Solo temperatura",
    "temperature": "Solo temperatura",
    "temp_ajuste": "Solo temperatura",
    "reajustes": "Reajustes",
    "long": "Reajustes",
    "configurations": "Configuraciones",
    "scheduled": "Configuraciones",
}


def _mode_label(mode: str) -> str:
    return MODE_LABELS.get((mode or "").strip(), mode or "")


def _mode_from_label(label: str) -> str:
    for raw, translated in MODE_LABELS.items():
        if translated == label:
            return raw
    return label


def _read_csv(path: Path, limit: int | None = None) -> list[dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    rows: list[dict[str, str]] = []
    for row in csv.DictReader(text.splitlines(), dialect=dialect):
        rows.append({str(k or "").strip(): str(v or "").strip() for k, v in row.items()})
        if limit is not None and len(rows) >= limit:
            break
    return rows


def _as_float(value: str) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return math.nan


def _strip_prefix(name: str, prefixes: Iterable[str]) -> str:
    stem = Path(name).stem
    for prefix in prefixes:
        if stem.startswith(prefix):
            return stem[len(prefix):]
    return stem


def _base_from_row(row: dict[str, str]) -> str:
    if row.get("base_name"):
        return row["base_name"]
    for key, prefixes in (
        ("raw", ("raw_",)),
        ("processed", ("proc_",)),
        ("summary", ("summary_",)),
        ("blocks_10s_file", ("bpm_blocks_10s_",)),
        ("plot", ("plot_",)),
        ("screenshot", ("screen_",)),
    ):
        value = row.get(key, "")
        if value:
            return _strip_prefix(Path(value).name, prefixes)
    return ""


def _cap_first(cap: "CaptureRecord", *keys: str) -> str:
    for key in keys:
        value = cap.value(key)
        if value:
            return value
    return ""


@dataclass
class CaptureRecord:
    session_key: str
    capture_id: str
    base_name: str
    row: dict[str, str] = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)

    def value(self, key: str) -> str:
        return self.row.get(key, "")


@dataclass
class SessionGroup:
    key: str
    path: Path | None
    captures: list[CaptureRecord] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name if self.path else self.key


class DictTableModel(QtCore.QAbstractTableModel):
    def __init__(self, headers: list[str], rows: list[dict[str, str]] | None = None):
        super().__init__()
        self.headers = headers
        self.rows = rows or []

    def rowCount(self, parent=QtCore.QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QtCore.QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index: QtCore.QModelIndex, role=QtCore.Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        key = self.headers[index.column()]
        if role in (QtCore.Qt.ItemDataRole.DisplayRole, QtCore.Qt.ItemDataRole.ToolTipRole):
            return row.get(key, "")
        if role == QtCore.Qt.ItemDataRole.BackgroundRole and key == "Estado":
            state = row.get(key, "")
            if state == "Buena":
                return QtGui.QBrush(QtGui.QColor("#d8f3dc"))
            if state == "Aceptable":
                return QtGui.QBrush(QtGui.QColor("#fff3bf"))
            if state == "Dudosa":
                return QtGui.QBrush(QtGui.QColor("#ffd6d6"))
        return None

    def headerData(self, section: int, orientation: QtCore.Qt.Orientation, role=QtCore.Qt.ItemDataRole.DisplayRole):
        if role != QtCore.Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == QtCore.Qt.Orientation.Horizontal:
            return self.headers[section]
        return str(section + 1)

    def set_rows(self, headers: list[str], rows: list[dict[str, str]]):
        self.beginResetModel()
        self.headers = headers
        self.rows = rows
        self.endResetModel()


class RelationExplorerWindow(QtWidgets.QMainWindow):
    back_to_menu = QtCore.pyqtSignal()

    session_headers = ["Sesion", "Fecha", "Inicio", "Modos", "Tomas", "Animales", "Calidad media"]
    capture_headers = [
        "Hora", "Animal", "Modo", "Configuracion", "Estado", "BPM medio", "BPM picos",
        "BPM FFT", "BPM autocorr", "Oxigeno medio", "Ratio R", "Temp media", "Temp ult.",
        "Resp/min (experimental)", "Calidad resp.", "Temp raw", "Calidad", "Contacto", "PI IR %", "PI RED %", "Artef. IR %",
        "Artef. RED %", "Sat. %", "RED", "IR", "AVG", "RATE", "WIDTH", "ADC",
        "Duracion", "Hz", "Muestras", "Pulso previo", "Pulso final",
    ]
    files_headers = ["tipo", "archivo", "filas", "ruta"]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PPG Suite v8 | Estadisticas")
        self.resize(1380, 860)
        self.search_roots: list[Path] = [RESULTS_DIR]
        self.sessions: list[SessionGroup] = []
        self.filtered_sessions: list[SessionGroup] = []
        self.current_session: SessionGroup | None = None
        self.current_capture: CaptureRecord | None = None
        self._build_ui()
        self.reload_data()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)

        top = QtWidgets.QHBoxLayout()
        root.addLayout(top)
        self.btn_back = QtWidgets.QPushButton("Volver al menu inicial")
        self.btn_back.setMinimumHeight(42)
        top.addWidget(self.btn_back)
        top.addStretch(1)
        self.btn_back.clicked.connect(self.back_to_menu.emit)

        filters = QtWidgets.QGroupBox("Buscar en sesiones")
        fl = QtWidgets.QGridLayout(filters)
        self.text_filter = QtWidgets.QLineEdit()
        self.text_filter.setPlaceholderText("Animal, modo, configuracion, contacto...")
        self.mode_filter = QtWidgets.QComboBox()
        self.mode_filter.addItem("Todos")
        self.quality_min = QtWidgets.QDoubleSpinBox()
        self.quality_min.setRange(0, 100)
        self.quality_min.setValue(0)
        self.btn_clear = QtWidgets.QPushButton("Limpiar")
        self.btn_import = QtWidgets.QPushButton("Leer otra carpeta")
        fl.addWidget(QtWidgets.QLabel("Texto"), 0, 0)
        fl.addWidget(self.text_filter, 0, 1, 1, 4)
        fl.addWidget(QtWidgets.QLabel("Modo"), 0, 5)
        fl.addWidget(self.mode_filter, 0, 6)
        fl.addWidget(QtWidgets.QLabel("Calidad min."), 0, 7)
        fl.addWidget(self.quality_min, 0, 8)
        fl.addWidget(self.btn_clear, 0, 9)
        fl.addWidget(self.btn_import, 0, 10)
        root.addWidget(filters)
        self.text_filter.textChanged.connect(self.apply_filters)
        self.mode_filter.currentTextChanged.connect(self.apply_filters)
        self.quality_min.valueChanged.connect(self.apply_filters)
        self.btn_clear.clicked.connect(self.clear_filters)
        self.btn_import.clicked.connect(self.pick_folder)

        main_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        root.addWidget(main_splitter, stretch=1)

        sessions_panel = QtWidgets.QWidget()
        sessions_layout = QtWidgets.QVBoxLayout(sessions_panel)
        self.sessions_label = QtWidgets.QLabel()
        self.sessions_label.setStyleSheet("font-size: 11pt; font-weight: bold;")
        sessions_layout.addWidget(self.sessions_label)
        self.sessions_model = DictTableModel(self.session_headers)
        self.sessions_table = QtWidgets.QTableView()
        self.sessions_table.setModel(self.sessions_model)
        self.sessions_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.sessions_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.sessions_table.setAlternatingRowColors(True)
        self.sessions_table.verticalHeader().setVisible(False)
        self.sessions_table.selectionModel().selectionChanged.connect(self.select_session)
        self.sessions_table.doubleClicked.connect(self.open_selected_session_file)
        sessions_layout.addWidget(self.sessions_table)
        main_splitter.addWidget(sessions_panel)

        captures_panel = QtWidgets.QWidget()
        captures_layout = QtWidgets.QVBoxLayout(captures_panel)
        self.captures_label = QtWidgets.QLabel("Raws / tomas de la sesion seleccionada")
        self.captures_label.setStyleSheet("font-size: 11pt; font-weight: bold;")
        captures_layout.addWidget(self.captures_label)
        self.captures_model = DictTableModel(self.capture_headers)
        self.captures_table = QtWidgets.QTableView()
        self.captures_table.setModel(self.captures_model)
        self.captures_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.captures_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.captures_table.setAlternatingRowColors(True)
        self.captures_table.verticalHeader().setVisible(False)
        self.captures_table.selectionModel().selectionChanged.connect(self.select_capture)
        self.captures_table.doubleClicked.connect(self.open_selected_capture_file)
        captures_layout.addWidget(self.captures_table)
        main_splitter.addWidget(captures_panel)

        self.detail_tabs = QtWidgets.QTabWidget()
        main_splitter.addWidget(self.detail_tabs)
        main_splitter.setSizes([230, 260, 360])

        self.summary = QtWidgets.QTextEdit()
        self.summary.setReadOnly(True)
        self.detail_tabs.addTab(self.summary, "Resumen")

        graph_page = QtWidgets.QWidget()
        graph_layout = QtWidgets.QVBoxLayout(graph_page)
        graph_controls = QtWidgets.QHBoxLayout()
        graph_layout.addLayout(graph_controls)
        self.chk_signal = QtWidgets.QCheckBox("IR/RED")
        self.chk_signal.setChecked(True)
        self.chk_bpm = QtWidgets.QCheckBox("BPM")
        self.chk_bpm.setChecked(True)
        self.chk_spo2 = QtWidgets.QCheckBox("Oxigeno")
        self.chk_temp = QtWidgets.QCheckBox("Temperatura")
        self.chk_blocks = QtWidgets.QCheckBox("Bloques BPM")
        for chk in [self.chk_signal, self.chk_bpm, self.chk_spo2, self.chk_temp, self.chk_blocks]:
            chk.toggled.connect(self.refresh_capture_detail)
            graph_controls.addWidget(chk)
        graph_controls.addStretch(1)
        self.plot_capture = pg.PlotWidget(title="Graficas de la toma seleccionada")
        self.plot_capture.setBackground("w")
        self.plot_capture.showGrid(x=True, y=True, alpha=0.25)
        graph_layout.addWidget(self.plot_capture, stretch=1)
        self.detail_tabs.addTab(graph_page, "Graficas")

        self.params = QtWidgets.QTextEdit()
        self.params.setReadOnly(True)
        self.detail_tabs.addTab(self.params, "Parametros dispositivo")

        self.files_model = DictTableModel(self.files_headers)
        self.files_table = QtWidgets.QTableView()
        self.files_table.setModel(self.files_model)
        self.files_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.files_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.files_table.setAlternatingRowColors(True)
        self.files_table.verticalHeader().setVisible(False)
        self.files_table.doubleClicked.connect(self.open_file_from_files_table)
        self.detail_tabs.addTab(self.files_table, "Archivos")

    def pick_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Seleccionar carpeta con CSV", str(RESULTS_DIR))
        if not folder:
            return
        path = Path(folder)
        if path not in self.search_roots:
            self.search_roots.append(path)
        self.reload_data()

    def clear_filters(self):
        self.text_filter.clear()
        self.mode_filter.setCurrentIndex(0)
        self.quality_min.setValue(0)
        self.apply_filters()

    def reload_data(self):
        self.sessions = self._discover_sessions()
        modes = sorted({_mode_label(cap.value("modo")) for session in self.sessions for cap in session.captures if cap.value("modo")})
        current = self.mode_filter.currentText()
        self.mode_filter.blockSignals(True)
        self.mode_filter.clear()
        self.mode_filter.addItem("Todos")
        self.mode_filter.addItems(modes)
        self.mode_filter.setCurrentText(current if current in ["Todos", *modes] else "Todos")
        self.mode_filter.blockSignals(False)
        self.apply_filters()

    def _find_files(self) -> dict[str, dict[str, Path]]:
        index: dict[str, dict[str, Path]] = {}
        patterns = {
            "raw": ("raw_*.csv", RAW_DIR, ("raw_",)),
            "processed": ("proc_*.csv", PROCESSED_DIR, ("proc_",)),
            "blocks": ("bpm_blocks_10s_*.csv", REPORT_DIR, ("bpm_blocks_10s_",)),
            "summary": ("summary_*.json", REPORT_DIR, ("summary_",)),
            "plot": ("plot_*.png", FIGURES_DIR, ("plot_",)),
            "screenshot": ("screen_*.png", SCREENSHOT_DIR, ("screen_",)),
        }
        for root in self.search_roots:
            for kind, (pattern, default_folder, prefixes) in patterns.items():
                search_base = default_folder if root == RESULTS_DIR else root
                for path in search_base.rglob(pattern):
                    base = _strip_prefix(path.name, prefixes)
                    index.setdefault(base, {})[kind] = path
        return index

    def _enrich_capture_from_summary(self, cap: CaptureRecord):
        path = cap.files.get("summary")
        if not path:
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        cap.row.setdefault("session_id", str(data.get("session_id") or cap.capture_id))
        cap.row.setdefault("base_name", str(data.get("base_name") or cap.base_name))
        cap.row.setdefault("id", str(data.get("id") or ""))
        cap.row.setdefault("modo", str(data.get("mode") or ""))
        cap.row.setdefault("condiciones_medida", str(data.get("measurement_condition") or ""))
        cap.row.setdefault("config_label", str(data.get("config_label") or ""))
        cap.row.setdefault("config_description", str(data.get("config_description") or ""))
        cap.row.setdefault("motivo_fin", str(data.get("reason") or ""))
        cap.row.setdefault("duracion_solicitada_s", str(data.get("requested_duration_s") or ""))
        cap.row.setdefault("muestras", str(data.get("samples") or ""))
        metrics = data.get("metrics") or {}
        temp = data.get("temperature") or {}
        sensor = data.get("sensor_config") or {}
        analysis = data.get("analysis_config") or {}
        manual = data.get("manual_reference") or {}
        values = {
            "duracion_real_s": metrics.get("duration_s"),
            "hz_real": metrics.get("hz"),
            "bpm": metrics.get("bpm"),
            "bpm_peak": metrics.get("bpm_peak"),
            "bpm_fft": metrics.get("bpm_fft"),
            "bpm_autocorr": metrics.get("bpm_autocorr"),
            "calidad": metrics.get("quality"),
            "calidad_label": metrics.get("quality_label"),
            "spo2_pct": metrics.get("spo2"),
            "ratio_r": metrics.get("ratio_r"),
            "resp_rate_rpm": metrics.get("resp_rate_rpm"),
            "resp_quality": metrics.get("resp_quality"),
            "resp_reason": metrics.get("resp_reason"),
            "temp_c_media": temp.get("temp_c_mean"),
            "temp_c_ultima": temp.get("temp_c_last"),
            "temp_c_min": temp.get("temp_c_min"),
            "temp_c_max": temp.get("temp_c_max"),
            "temp_raw_ultima": temp.get("temp_raw_last"),
            "artefactos_ir_pct": metrics.get("artifact_ir_pct"),
            "artefactos_red_pct": metrics.get("artifact_red_pct"),
            "pi_ir_pct": metrics.get("pi_ir_pct"),
            "pi_red_pct": metrics.get("pi_red_pct"),
            "ac_ir": metrics.get("ac_ir"),
            "dc_ir": metrics.get("dc_ir"),
            "ac_red": metrics.get("ac_red"),
            "dc_red": metrics.get("dc_red"),
            "saturation_pct": metrics.get("saturation_pct"),
            "metrics_reason": metrics.get("reason"),
            "peaks_count": metrics.get("peaks_count"),
            "contacto": metrics.get("contact_label"),
            "cfg_red": sensor.get("red"),
            "cfg_ir": sensor.get("ir"),
            "cfg_avg": sensor.get("avg"),
            "cfg_rate": sensor.get("rate"),
            "cfg_width": sensor.get("width"),
            "cfg_adc": sensor.get("adc"),
            "cfg_skip": sensor.get("skip"),
            "cfg_debug": sensor.get("debug"),
            "analysis_bpm_min": analysis.get("bpm_min"),
            "analysis_bpm_max": analysis.get("bpm_max"),
            "analysis_detrend_seconds": analysis.get("detrend_seconds"),
            "analysis_smooth_seconds": analysis.get("smooth_seconds"),
            "analysis_ignore_initial_seconds": analysis.get("ignore_initial_seconds"),
            "analysis_spo2_formula": analysis.get("spo2_formula"),
            "pulso_previo": manual.get("pulso_previo"),
            "pulso_final_pulsio": manual.get("pulso_final_pulsio"),
            "pulso_final_fonendo": manual.get("pulso_final_fonendo"),
        }
        for key, value in values.items():
            if value is not None and not cap.row.get(key):
                cap.row[key] = str(value)
        for kind, file_path in (data.get("files") or {}).items():
            path_obj = Path(str(file_path))
            if path_obj.exists():
                normalized_kind = "blocks" if str(kind) == "bpm_blocks_10s" else str(kind)
                cap.files.setdefault(normalized_kind, path_obj)

    def _discover_sessions(self) -> list[SessionGroup]:
        files_by_base = self._find_files()
        groups: list[SessionGroup] = []
        attached_bases: set[str] = set()
        for root in self.search_roots:
            session_base = SESSION_DIR if root == RESULTS_DIR else root
            for session_file in session_base.rglob("session_*.csv"):
                rows = _read_csv(session_file)
                group = SessionGroup(key=session_file.stem, path=session_file)
                for idx, row in enumerate(rows, start=1):
                    base = _base_from_row(row)
                    capture_id = row.get("session_id") or base or f"{session_file.stem}_{idx}"
                    cap = CaptureRecord(session_key=group.key, capture_id=capture_id, base_name=base or capture_id, row=row.copy())
                    cap.files["session"] = session_file
                    if base and base in files_by_base:
                        cap.files.update(files_by_base[base])
                        attached_bases.add(base)
                    self._enrich_capture_from_summary(cap)
                    group.captures.append(cap)
                if group.captures:
                    groups.append(group)
        orphan = SessionGroup(key="historico_sin_session", path=None)
        for base, files in files_by_base.items():
            if base in attached_bases:
                continue
            if "summary" not in files and "processed" not in files:
                continue
            cap = CaptureRecord(
                session_key=orphan.key,
                capture_id=base,
                base_name=base,
                row={"session_id": base, "base_name": base},
                files=files.copy(),
            )
            self._enrich_capture_from_summary(cap)
            orphan.captures.append(cap)
        if orphan.captures:
            groups.append(orphan)
        groups.sort(key=lambda s: (self._session_date(s), s.name), reverse=True)
        return groups

    def _session_date(self, session: SessionGroup) -> str:
        dates = [cap.value("fecha") + " " + cap.value("hora") for cap in session.captures if cap.value("fecha")]
        if dates:
            return max(dates)
        if session.path:
            return session.path.stem.replace("session_", "")
        return ""

    def apply_filters(self):
        text = self.text_filter.text().strip().lower()
        mode = self.mode_filter.currentText()
        quality_min = self.quality_min.value()
        filtered: list[SessionGroup] = []
        for session in self.sessions:
            captures = []
            for cap in session.captures:
                haystack = " ".join([session.name, cap.capture_id, cap.base_name, _mode_label(cap.value("modo")), " ".join(cap.row.values())]).lower()
                if text and text not in haystack:
                    continue
                if mode != "Todos" and _mode_label(cap.value("modo")) != mode:
                    continue
                quality = _as_float(cap.value("calidad"))
                if np.isfinite(quality) and quality < quality_min:
                    continue
                captures.append(cap)
            if captures or (not session.captures and not text and mode == "Todos"):
                filtered.append(SessionGroup(key=session.key, path=session.path, captures=captures))
        self.filtered_sessions = filtered
        self.sessions_model.set_rows(self.session_headers, [self._session_row(session) for session in filtered])
        self.sessions_table.resizeColumnsToContents()
        self.sessions_label.setText(f"{len(filtered)} sesiones | {sum(len(s.captures) for s in filtered)} tomas visibles")
        if filtered:
            self.sessions_table.selectRow(0)
        else:
            self.set_session(None)

    def _session_row(self, session: SessionGroup) -> dict[str, str]:
        caps = session.captures
        modes = sorted({_mode_label(cap.value("modo")) for cap in caps if cap.value("modo")})
        dates = [cap.value("fecha") for cap in caps if cap.value("fecha")]
        hours = [cap.value("hora") for cap in caps if cap.value("hora")]
        qualities = [_as_float(cap.value("calidad")) for cap in caps]
        qualities = [q for q in qualities if np.isfinite(q)]
        animals = {cap.value("id").strip() for cap in caps if cap.value("id").strip()}
        return {
            "Sesion": session.name,
            "Fecha": min(dates) if dates else "",
            "Inicio": min(hours) if hours else "",
            "Modos": ", ".join(modes),
            "Tomas": str(len(caps)),
            "Animales": str(len(animals)),
            "Calidad media": fmt(float(np.mean(qualities)) if qualities else math.nan, 0, ""),
        }

    def select_session(self):
        indexes = self.sessions_table.selectionModel().selectedRows()
        if not indexes:
            self.set_session(None)
            return
        row = indexes[0].row()
        self.set_session(self.filtered_sessions[row] if 0 <= row < len(self.filtered_sessions) else None)

    def set_session(self, session: SessionGroup | None):
        self.current_session = session
        self.current_capture = None
        if session is None:
            self.captures_label.setText("Raws / tomas de la sesion seleccionada")
            self.captures_model.set_rows(self.capture_headers, [])
            self.set_capture(None)
            return
        self.captures_label.setText(f"Raws / tomas dentro de {session.name}")
        self.captures_model.set_rows(self.capture_headers, [self._capture_row(cap) for cap in session.captures])
        self.captures_table.resizeColumnsToContents()
        if session.captures:
            self.captures_table.selectRow(0)
        else:
            self.set_capture(None)

    def _capture_row(self, cap: CaptureRecord) -> dict[str, str]:
        quality = _as_float(cap.value("calidad"))
        if np.isfinite(quality) and quality >= 70:
            state = "Buena"
        elif np.isfinite(quality) and quality >= 45:
            state = "Aceptable"
        else:
            state = "Dudosa" if cap.value("bpm") else ""
        return {
            "Hora": cap.value("hora"),
            "Animal": cap.value("id"),
            "Modo": _mode_label(cap.value("modo")),
            "Configuracion": cap.value("config_label"),
            "Estado": state,
            "BPM medio": fmt(_as_float(cap.value("bpm")), 0, ""),
            "BPM picos": fmt(_as_float(cap.value("bpm_peak")), 0, ""),
            "BPM FFT": fmt(_as_float(cap.value("bpm_fft")), 0, ""),
            "BPM autocorr": fmt(_as_float(cap.value("bpm_autocorr")), 0, ""),
            "Oxigeno medio": fmt(_as_float(cap.value("spo2_pct")), 1, ""),
            "Ratio R": fmt(_as_float(cap.value("ratio_r")), 4, ""),
            "Resp/min (experimental)": fmt(_as_float(_cap_first(cap, "resp_rate_rpm", "resp_min_exp")), 1, ""),
            "Calidad resp.": fmt(_as_float(_cap_first(cap, "resp_quality", "resp_calidad_exp")), 0, ""),
            "Temp media": fmt(_as_float(cap.value("temp_c_media")), 1, ""),
            "Temp ult.": fmt(_as_float(cap.value("temp_c_ultima")), 1, ""),
            "Temp raw": fmt(_as_float(cap.value("temp_raw_ultima")), 0, ""),
            "Calidad": fmt(quality, 0, ""),
            "Contacto": cap.value("contacto"),
            "PI IR %": fmt(_as_float(cap.value("pi_ir_pct")), 3, ""),
            "PI RED %": fmt(_as_float(cap.value("pi_red_pct")), 3, ""),
            "Artef. IR %": fmt(_as_float(cap.value("artefactos_ir_pct")), 1, ""),
            "Artef. RED %": fmt(_as_float(cap.value("artefactos_red_pct")), 1, ""),
            "Sat. %": fmt(_as_float(cap.value("saturation_pct")), 1, ""),
            "RED": cap.value("cfg_red"),
            "IR": cap.value("cfg_ir"),
            "AVG": cap.value("cfg_avg"),
            "RATE": cap.value("cfg_rate"),
            "WIDTH": cap.value("cfg_width"),
            "ADC": cap.value("cfg_adc"),
            "Duracion": fmt(_as_float(cap.value("duracion_real_s")), 1, ""),
            "Hz": fmt(_as_float(cap.value("hz_real")), 1, ""),
            "Muestras": cap.value("muestras"),
            "Pulso previo": cap.value("pulso_previo"),
            "Pulso final": cap.value("pulso_final_pulsio"),
        }

    def select_capture(self):
        if self.current_session is None:
            self.set_capture(None)
            return
        indexes = self.captures_table.selectionModel().selectedRows()
        if not indexes:
            self.set_capture(None)
            return
        row = indexes[0].row()
        self.set_capture(self.current_session.captures[row] if 0 <= row < len(self.current_session.captures) else None)

    def set_capture(self, cap: CaptureRecord | None):
        self.current_capture = cap
        self.refresh_capture_detail()

    def refresh_capture_detail(self):
        cap = self.current_capture
        if cap is None:
            self.summary.clear()
            self.params.clear()
            self.files_model.set_rows(self.files_headers, [])
            self.plot_capture.clear()
            return
        self.summary.setHtml(self._summary_html(cap))
        self.params.setHtml(self._params_html(cap))
        file_rows = []
        for kind, path in sorted(cap.files.items()):
            file_rows.append({
                "tipo": kind,
                "archivo": path.name,
                "filas": str(len(_read_csv(path))) if path.suffix.lower() == ".csv" else "",
                "ruta": str(path),
            })
        self.files_model.set_rows(self.files_headers, file_rows)
        raw_rows = _read_csv(cap.files["raw"], limit=5000) if "raw" in cap.files else []
        proc_rows = _read_csv(cap.files["processed"], limit=5000) if "processed" in cap.files else []
        block_rows = _read_csv(cap.files["blocks"], limit=5000) if "blocks" in cap.files else []
        self.files_table.resizeColumnsToContents()
        self.update_capture_plot(raw_rows, proc_rows, block_rows)

    def open_path(self, path: Path | None):
        if path is None:
            return
        if not path.exists():
            QtWidgets.QMessageBox.warning(self, "Abrir archivo", f"No se encontro el archivo:\n{path}")
            return
        ok = QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))
        if not ok:
            QtWidgets.QMessageBox.warning(self, "Abrir archivo", f"No se pudo abrir:\n{path}")

    def open_selected_session_file(self, *_args):
        indexes = self.sessions_table.selectionModel().selectedRows()
        if not indexes:
            return
        row = indexes[0].row()
        if 0 <= row < len(self.filtered_sessions):
            self.open_path(self.filtered_sessions[row].path)

    def open_selected_capture_file(self, *_args):
        cap = self.current_capture
        if cap is None:
            return
        for key in ("raw", "processed", "summary", "plot", "screenshot", "blocks", "session"):
            if key in cap.files:
                self.open_path(cap.files[key])
                return

    def open_file_from_files_table(self, index: QtCore.QModelIndex):
        if not index.isValid() or not (0 <= index.row() < len(self.files_model.rows)):
            return
        path_text = self.files_model.rows[index.row()].get("ruta", "")
        self.open_path(Path(path_text) if path_text else None)

    def _summary_html(self, cap: CaptureRecord) -> str:
        quality = _as_float(cap.value("calidad"))
        bpm = _as_float(cap.value("bpm"))
        spo2 = _as_float(cap.value("spo2_pct"))
        resp = _as_float(_cap_first(cap, "resp_rate_rpm", "resp_min_exp"))
        resp_quality = _as_float(_cap_first(cap, "resp_quality", "resp_calidad_exp"))
        pi_ir = _as_float(cap.value("pi_ir_pct"))
        artifacts = _as_float(cap.value("artefactos_ir_pct"))
        saturation = _as_float(cap.value("saturation_pct"))
        warnings: list[str] = []
        if not np.isfinite(bpm):
            warnings.append("BPM no fiable o no estimable en esta toma.")
        if np.isfinite(quality) and quality < 45:
            warnings.append("Calidad global baja: interpretar con cautela.")
        if np.isfinite(pi_ir) and pi_ir < 0.15:
            warnings.append("Perfusion/PI IR bajo: el pulso tiene poca amplitud relativa.")
        if np.isfinite(artifacts) and artifacts > 8:
            warnings.append("Artefactos IR elevados: posible movimiento, contacto irregular o ruido.")
        if np.isfinite(saturation) and saturation > 0:
            warnings.append("Hay muestras saturadas: revisar potencia LED/rango ADC.")
        if np.isfinite(spo2):
            warnings.append("Oxigeno calculado de forma no calibrada; usar como orientacion tecnica, no como valor clinico.")
        if np.isfinite(resp):
            warnings.append("Respiraciones calculadas de forma experimental desde modulaciones lentas de PPG; validar con referencia externa.")

        if not warnings:
            warnings.append("Sin avisos tecnicos destacados en las metricas guardadas.")

        def row(name: str, value: str) -> str:
            return f"<tr><td><b>{html.escape(name)}</b></td><td>{html.escape(value)}</td></tr>"

        fields = [
            ("Animal", cap.value("id") or "-"),
            ("Modo de recogida", _mode_label(cap.value("modo")) or "-"),
            ("Fecha y hora", f"{cap.value('fecha')} {cap.value('hora')}".strip() or "-"),
            ("Configuracion", cap.value("config_label") or "-"),
            ("Descripcion configuracion", cap.value("config_description") or "-"),
            ("Condiciones", cap.value("condiciones_medida") or "-"),
            ("BPM medio", fmt(bpm, 1, "-")),
            ("BPM por picos / FFT / autocorr", f"{fmt(_as_float(cap.value('bpm_peak')), 1, '-')} / {fmt(_as_float(cap.value('bpm_fft')), 1, '-')} / {fmt(_as_float(cap.value('bpm_autocorr')), 1, '-')}"),
            ("Oxigeno medio", f"{fmt(spo2, 1, '-')} %"),
            ("Ratio R", fmt(_as_float(cap.value("ratio_r")), 5, "-")),
            ("Respiraciones (experimental)", f"{fmt(resp, 1, '-')} resp/min | calidad {fmt(resp_quality, 0, '-')}"),
            ("Calidad", f"{fmt(quality, 1, '-')} | {cap.value('calidad_label') or '-'}"),
            ("Contacto", cap.value("contacto") or "-"),
            ("PI IR / PI RED", f"{fmt(pi_ir, 4, '-')} % / {fmt(_as_float(cap.value('pi_red_pct')), 4, '-')} %"),
            ("Artefactos IR / RED", f"{fmt(artifacts, 1, '-')} % / {fmt(_as_float(cap.value('artefactos_red_pct')), 1, '-')} %"),
            ("Saturacion", f"{fmt(saturation, 1, '-')} %"),
            ("Temperatura media / ultima", f"{fmt(_as_float(cap.value('temp_c_media')), 2, '-')} / {fmt(_as_float(cap.value('temp_c_ultima')), 2, '-')} C"),
            ("Duracion real / Hz real / muestras", f"{fmt(_as_float(cap.value('duracion_real_s')), 2, '-')} s / {fmt(_as_float(cap.value('hz_real')), 2, '-')} Hz / {cap.value('muestras') or '-'}"),
            ("Pulso manual previo / final", f"{cap.value('pulso_previo') or '-'} / {cap.value('pulso_final_pulsio') or '-'}"),
            ("Motivo fin", cap.value("motivo_fin") or "-"),
            ("Nexo interno", cap.capture_id),
        ]
        rows = "".join(row(name, value) for name, value in fields)
        warning_items = "".join(f"<li>{html.escape(item)}</li>" for item in warnings)
        reason = cap.value("metrics_reason") or "-"
        resp_reason = _cap_first(cap, "resp_reason", "resp_razon_exp") or "-"
        return f"""
        <h2>Toma seleccionada</h2>
        <p><b>Lectura rapida:</b> esta vista resume como fue la toma, que estimadores coincidieron, si hubo contacto util y que limitaciones tecnicas debe tener presentes quien revise los datos.</p>
        <table cellspacing='8'>{rows}</table>
        <h3>Avisos de interpretacion</h3>
        <ul>{warning_items}</ul>
        <p><b>Razon interna del calculo:</b> {html.escape(reason)}</p>
        <p><b>Razon respiracion (experimental):</b> {html.escape(resp_reason)}</p>
        """

    def _params_html(self, cap: CaptureRecord) -> str:
        def row(name: str, value: str) -> str:
            return f"<tr><td><b>{html.escape(name)}</b></td><td>{html.escape(value or '-')}</td></tr>"

        sensor_fields = [
            ("Configuracion", cap.value("config_label")),
            ("Descripcion", cap.value("config_description")),
            ("RED", cap.value("cfg_red")),
            ("IR", cap.value("cfg_ir")),
            ("AVG", cap.value("cfg_avg")),
            ("RATE", cap.value("cfg_rate")),
            ("WIDTH", cap.value("cfg_width")),
            ("ADC", cap.value("cfg_adc")),
            ("SKIP", cap.value("cfg_skip")),
            ("DEBUG", cap.value("cfg_debug")),
            ("Confirmacion Arduino", cap.value("cfg_confirmacion")),
        ]
        analysis_fields = [
            ("BPM minimo", cap.value("analysis_bpm_min")),
            ("BPM maximo", cap.value("analysis_bpm_max")),
            ("Detrend", f"{cap.value('analysis_detrend_seconds')} s" if cap.value("analysis_detrend_seconds") else ""),
            ("Suavizado", f"{cap.value('analysis_smooth_seconds')} s" if cap.value("analysis_smooth_seconds") else ""),
            ("Ignorar inicio", f"{cap.value('analysis_ignore_initial_seconds')} s" if cap.value("analysis_ignore_initial_seconds") else ""),
            ("Formula SpO2", cap.value("analysis_spo2_formula")),
        ]
        sensor_rows = "".join(row(name, value) for name, value in sensor_fields)
        analysis_rows = "".join(row(name, value) for name, value in analysis_fields)
        return f"""
        <h2>Parametros dispositivo</h2>
        <p>Estos son los parametros de sensor y analisis asociados al raw seleccionado. Sirven para saber exactamente con que configuracion se genero la toma.</p>
        <h3>Sensor MAX3010x</h3>
        <table cellspacing='8'>{sensor_rows}</table>
        <h3>Analisis usado al guardar resumen</h3>
        <table cellspacing='8'>{analysis_rows}</table>
        """

    def update_capture_plot(self, raw_rows: list[dict[str, str]], proc_rows: list[dict[str, str]], block_rows: list[dict[str, str]]):
        self.plot_capture.clear()
        rows = proc_rows or raw_rows
        if rows:
            t = np.asarray([_as_float(r.get("tiempo_s", "")) for r in rows], dtype=float)
            mask_t = np.isfinite(t)
            if self.chk_signal.isChecked() and np.any(mask_t):
                ir = np.asarray([_as_float(r.get("ir_proc_norm") or r.get("ir_raw", "")) for r in rows], dtype=float)
                red = np.asarray([_as_float(r.get("red_proc_norm") or r.get("red_raw", "")) for r in rows], dtype=float)
                self.plot_capture.plot(t[mask_t], ir[mask_t], pen=pg.mkPen((0, 80, 220), width=1), name="IR")
                self.plot_capture.plot(t[mask_t], red[mask_t], pen=pg.mkPen((220, 40, 35), width=1), name="RED")
            if self.chk_bpm.isChecked():
                bpm = np.asarray([_as_float(r.get("bpm_rolling_5s", "")) for r in rows], dtype=float)
                mask = mask_t & np.isfinite(bpm)
                if np.any(mask):
                    self.plot_capture.plot(t[mask], bpm[mask], pen=pg.mkPen((40, 140, 50), width=2), name="BPM")
            if self.chk_spo2.isChecked():
                spo2 = np.asarray([_as_float(r.get("spo2_rolling_5s", "")) for r in rows], dtype=float)
                mask = mask_t & np.isfinite(spo2)
                if np.any(mask):
                    self.plot_capture.plot(t[mask], spo2[mask], pen=pg.mkPen((150, 70, 160), width=2), name="Oxigeno")
            if self.chk_temp.isChecked():
                temp = np.asarray([_as_float(r.get("temp_c", "")) for r in rows], dtype=float)
                mask = mask_t & np.isfinite(temp)
                if np.any(mask):
                    self.plot_capture.plot(t[mask], temp[mask], pen=pg.mkPen((220, 120, 30), width=2), name="Temp")
        if self.chk_blocks.isChecked() and block_rows:
            x = np.asarray([_as_float(r.get("inicio_s", "")) for r in block_rows], dtype=float)
            y = np.asarray([_as_float(r.get("bpm_medio_10s", "")) for r in block_rows], dtype=float)
            mask = np.isfinite(x) & np.isfinite(y)
            if np.any(mask):
                self.plot_capture.plot(x[mask] + 5, y[mask], pen=pg.mkPen((20, 120, 110), width=2), symbol="o", name="Bloques BPM")
        self.plot_capture.setLabel("bottom", "Tiempo", units="s")

    def closeEvent(self, event: QtGui.QCloseEvent):
        event.accept()
