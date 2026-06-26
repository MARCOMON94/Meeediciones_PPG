from __future__ import annotations

import csv
import html
import json
import math
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from ..animal_config import animal_label, display_mapping, normalize_animal_type
from ..models import AnalysisConfig, SensorConfig
from ..paths import CONFIG_DIR, FIGURES_DIR, PROCESSED_DIR, RAW_DIR, REPORT_DIR, RESULTS_DIR, SCREENSHOT_DIR, SESSION_DIR
from ..processing import score_and_merge_metrics
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
    "marco": "Experimento 3M",
    "experimento_3m": "Experimento 3M",
}

HEADER_TOOLTIPS = {
    "Sesion": "Archivo o grupo de tomas al que pertenece la medicion.",
    "Fecha": "Fecha registrada para la sesion o toma.",
    "Inicio": "Hora inicial registrada para la sesion.",
    "Modos": "Tipos de medicion incluidos en la sesion.",
    "Tomas": "Numero de capturas o raws visibles en esta sesion.",
    "Animales": "Numero de identificadores de animal distintos dentro de la sesion.",
    "Calidad media": "Media de la calidad guardada en las tomas visibles. Es orientativa y depende del cribado aplicado.",
    "Hora": "Hora registrada para la toma.",
    "Animal": "Identificador introducido para el animal o sujeto de la toma.",
    "Especie": "Tipo de animal seleccionado: oveja, cabra o vaca.",
    "Modo": "Modo de recogida usado: campo, reajustes, configuraciones, experimento 3M, etc.",
    "Configuracion": "Etiqueta de configuracion usada para el sensor en esa toma.",
    "Sensor": "Sensor indicado para la toma: derecha o izquierda.",
    "Termometros": "Asignacion de termometros analogicos A0/A1 a derecha/izquierda usada al capturar.",
    "Canal temp": "Canal analogico usado como temperatura primaria de la toma.",
    "Medicion": "Indica si la toma se hizo con vacio o sin vacio. Es metadato, no entra en calculos.",
    "Estado": "Lectura rapida de calidad: Buena, Aceptable o Dudosa segun la calidad calculada.",
    "Pulso ref.": "BPM de referencia introducidos a mano: media de pulso previo, pulsioximetro final y fonendo final, ignorando ceros y vacios.",
    "Dif. BPM-ref": "Diferencia absoluta entre el BPM calculado por el sistema y el BPM de referencia manual.",
    "BPM medio": "Estimacion final de frecuencia cardiaca tras combinar estimadores validos y aplicar cribado.",
    "BPM picos": "BPM estimado detectando picos locales en la senal PPG procesada.",
    "BPM FFT": "BPM estimado con transformada de Fourier sobre la senal IR procesada.",
    "BPM autocorr": "BPM estimado con autocorrelacion: repeticion temporal del patron de pulso.",
    "Oxigeno medio": "SpO2 estimada experimentalmente desde RED/IR. No esta calibrada clinicamente.",
    "Ratio R": "Ratio AC/DC de RED dividido por AC/DC de IR usado para estimar SpO2.",
    "Temp final": "Maximo del primer golpe de calor tras 1 s de estabilizacion y dentro de los 5 s siguientes.",
    "Temp ult.": "Ultima temperatura registrada en la toma.",
    "Resp/min (experimental)": "Respiraciones por minuto estimadas desde modulaciones lentas de PPG. Requiere validacion externa.",
    "Calidad resp.": "Confianza interna de la respiracion experimental, de 0 a 100.",
    "Temp raw": "Valor bruto del ADC de temperatura.",
    "Temp RT final": "Maximo independiente de la ubre derecha en la ventana final 1-6 s.",
    "Temp RT ult.": "Ultima temperatura calculada para la ubre derecha.",
    "Temp RT raw": "Raw asociado a la ubre derecha.",
    "Temp LT final": "Maximo independiente de la ubre izquierda en la ventana final 1-6 s.",
    "Temp LT ult.": "Ultima temperatura calculada para la ubre izquierda.",
    "Temp LT raw": "Raw asociado a la ubre izquierda.",
    "Temp FLT final": "Maximo de temperatura en sensor delantero izquierdo de vaca.",
    "Temp FRT final": "Maximo de temperatura en sensor delantero derecho de vaca.",
    "Temp RLT final": "Maximo de temperatura en sensor trasero izquierdo de vaca.",
    "Temp RRT final": "Maximo de temperatura en sensor trasero derecho de vaca.",
    "Temp A0 final": "Maximo calculado desde la fuente analogica A0 en la ventana final 1-6 s.",
    "Temp A0 ult.": "Ultima temperatura calculada desde la fuente analogica A0.",
    "Temp A0 raw": "Ultimo valor bruto ADC de la fuente analogica A0.",
    "Temp A1 final": "Maximo calculado desde la fuente analogica A1 en la ventana final 1-6 s.",
    "Temp A1 ult.": "Ultima temperatura calculada desde la fuente analogica A1.",
    "Temp A1 raw": "Ultimo valor bruto ADC de la fuente analogica A1.",
    "Raw": "Archivo raw asociado a la toma.",
    "Calidad": "Puntuacion global interna de la toma tras BPM, PI, artefactos, saturacion y cribado.",
    "Contacto": "Etiqueta de contacto/perfusion derivada del nivel DC e indice de perfusion IR.",
    "PI IR %": "Indice de perfusion IR: componente pulsatile AC respecto al nivel DC. Cuanto mayor, mas visible es el pulso.",
    "PI RED %": "Indice de perfusion RED: componente pulsatile AC respecto al nivel DC.",
    "Artef. IR %": "Porcentaje de muestras IR marcadas como artefacto o descartadas por cribado robusto.",
    "Artef. RED %": "Porcentaje de muestras RED marcadas como artefacto por cribado robusto.",
    "Sat. %": "Porcentaje de muestras cerca del techo digital del ADC. Si sube, hay riesgo de perder informacion.",
    "RED": "Amplitud LED roja configurada en el MAX3010x, valor de registro 0-255.",
    "IR": "Amplitud LED infrarroja configurada en el MAX3010x, valor de registro 0-255.",
    "AVG": "Promedio FIFO configurado en el sensor. Valores mayores suavizan y retrasan mas.",
    "RATE": "Frecuencia de muestreo configurada en el sensor.",
    "WIDTH": "Ancho de pulso LED configurado; influye en resolucion y energia de cada muestra.",
    "ADC": "Rango ADC configurado para el sensor.",
    "Duracion": "Duracion real analizada tras descartes iniciales o de gaps.",
    "Hz": "Frecuencia real estimada a partir de los tiempos guardados.",
    "Muestras": "Numero de muestras disponibles o analizadas.",
    "Pulso previo": "BPM manual anotado antes de la toma.",
    "Temp manual inicio": "Temperatura manual anotada al inicio; es solo referencia y no afecta a ningun calculo.",
    "Temp manual RT": "Temperatura manual inicial de la teta derecha.",
    "Temp manual LT": "Temperatura manual inicial de la teta izquierda.",
    "Temp manual FLT": "Temperatura manual inicial de la teta delantera izquierda.",
    "Temp manual FRT": "Temperatura manual inicial de la teta delantera derecha.",
    "Temp manual RLT": "Temperatura manual inicial de la teta trasera izquierda.",
    "Temp manual RRT": "Temperatura manual inicial de la teta trasera derecha.",
    "Pulso final pulsio": "BPM manual anotado al final con pulsioximetro.",
    "Pulso final fonendo": "BPM manual anotado al final con fonendo.",
    "tipo": "Tipo de archivo asociado a la toma: raw, processed, summary, plot, etc.",
    "archivo": "Nombre del archivo asociado.",
    "filas": "Numero de filas si el archivo asociado es CSV.",
    "ruta": "Ruta completa del archivo asociado.",
    "Tramo": "Intervalo temporal relativo a la toma seleccionada. Se fuerza el primer dato disponible como segundo 0.",
    "Inicio s": "Inicio del tramo usando tiempo relativo: el primer dato disponible se considera 0 s.",
    "Fin s": "Final real del tramo. En el ultimo tramo puede cortar antes de 10 s si la toma termina antes.",
    "BPM 10s": "BPM medio guardado para ese bloque de 10 s.",
    "BPM tramo": "BPM recalculado para el tramo desde raw RED/IR; si existe BPM rolling se usa como apoyo.",
    "SpO2 tramo": "SpO2 recalculada para el tramo desde raw RED/IR cuando no exista rolling; experimental y no calibrada.",
    "Calidad tramo": "Calidad recalculada para el tramo desde raw RED/IR cuando no exista rolling.",
    "Temp max tramo": "Temperatura maxima primaria disponible dentro del tramo.",
    "Temp RT max tramo": "Temperatura maxima de la ubre derecha dentro del tramo.",
    "Temp LT max tramo": "Temperatura maxima de la ubre izquierda dentro del tramo.",
    "Temp FLT max tramo": "Temperatura maxima del sensor delantero izquierdo dentro del tramo.",
    "Temp FRT max tramo": "Temperatura maxima del sensor delantero derecho dentro del tramo.",
    "Temp RLT max tramo": "Temperatura maxima del sensor trasero izquierdo dentro del tramo.",
    "Temp RRT max tramo": "Temperatura maxima del sensor trasero derecho dentro del tramo.",
    "Muestras tramo": "Numero de muestras dentro del tramo temporal.",
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


def _as_ref_pulse(value: object) -> float:
    bpm = _as_float(str(value if value is not None else ""))
    if np.isfinite(bpm) and bpm > 0:
        return bpm
    return math.nan


def _mean_ref_pulse(*values: object) -> tuple[float, int]:
    valid = [_as_ref_pulse(value) for value in values]
    valid = [value for value in valid if np.isfinite(value)]
    if not valid:
        return math.nan, 0
    return float(np.mean(valid)), len(valid)


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


def _cap_temp_final(cap: "CaptureRecord", final_key: str, legacy_key: str, *fallback_keys: str) -> str:
    return _cap_first(cap, final_key, *fallback_keys, legacy_key)


def _select_first_row(table: QtWidgets.QTableView):
    model = table.model()
    if model is not None and model.rowCount() > 0:
        table.selectRow(0)


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
        self.check_changed_callback = None

    def rowCount(self, parent=QtCore.QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QtCore.QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index: QtCore.QModelIndex, role=QtCore.Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        key = self.headers[index.column()]
        if key == "Correo" and role == QtCore.Qt.ItemDataRole.CheckStateRole:
            return QtCore.Qt.CheckState.Checked if row.get("_mail_checked") == "1" else QtCore.Qt.CheckState.Unchecked
        if key == "Correo" and role == QtCore.Qt.ItemDataRole.ToolTipRole:
            return row.get("_mail_tooltip", "Marcar archivo para preparar correo")
        if key == "Correo" and role == QtCore.Qt.ItemDataRole.DisplayRole:
            return ""
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
        if role == QtCore.Qt.ItemDataRole.ForegroundRole and key == "Estado":
            state = row.get(key, "")
            if state in {"Buena", "Aceptable", "Dudosa"}:
                return QtGui.QBrush(QtGui.QColor("#17202a"))
        return None

    def flags(self, index: QtCore.QModelIndex):
        base = super().flags(index)
        if not index.isValid():
            return base
        key = self.headers[index.column()]
        if key == "Correo":
            row = self.rows[index.row()] if 0 <= index.row() < len(self.rows) else {}
            if not row.get("_mail_key"):
                return base | QtCore.Qt.ItemFlag.ItemIsSelectable
            return base | QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
        return base

    def setData(self, index: QtCore.QModelIndex, value, role=QtCore.Qt.ItemDataRole.EditRole):
        if not index.isValid() or not (0 <= index.row() < len(self.rows)):
            return False
        key = self.headers[index.column()]
        if key != "Correo" or role != QtCore.Qt.ItemDataRole.CheckStateRole:
            return False
        checked_value = getattr(QtCore.Qt.CheckState.Checked, "value", 2)
        checked = value == QtCore.Qt.CheckState.Checked or value == checked_value
        row = self.rows[index.row()]
        if not row.get("_mail_key"):
            return False
        row["_mail_checked"] = "1" if checked else "0"
        if self.check_changed_callback:
            self.check_changed_callback(row, checked)
        self.dataChanged.emit(index, index, [QtCore.Qt.ItemDataRole.CheckStateRole])
        return True

    def headerData(self, section: int, orientation: QtCore.Qt.Orientation, role=QtCore.Qt.ItemDataRole.DisplayRole):
        if orientation == QtCore.Qt.Orientation.Horizontal:
            header = self.headers[section]
            if role == QtCore.Qt.ItemDataRole.DisplayRole:
                return header
            if role == QtCore.Qt.ItemDataRole.ToolTipRole:
                return HEADER_TOOLTIPS.get(header, header)
            return None
        if role != QtCore.Qt.ItemDataRole.DisplayRole:
            return None
        return str(section + 1)

    def set_rows(self, headers: list[str], rows: list[dict[str, str]]):
        self.beginResetModel()
        self.headers = headers
        self.rows = rows
        self.endResetModel()

    def sort(self, column: int, order=QtCore.Qt.SortOrder.AscendingOrder):
        if not (0 <= column < len(self.headers)):
            return
        key = self.headers[column]

        def sort_key(row: dict[str, str]):
            text = row.get(key, "")
            number = _as_float(text)
            if np.isfinite(number):
                return (0, number)
            return (1, text.lower())

        self.layoutAboutToBeChanged.emit()
        self.rows.sort(key=sort_key, reverse=order == QtCore.Qt.SortOrder.DescendingOrder)
        self.layoutChanged.emit()


class RelationExplorerWindow(QtWidgets.QMainWindow):
    back_to_menu = QtCore.pyqtSignal()

    session_headers = ["Correo", "Sesion", "Fecha", "Inicio", "Modos", "Tomas", "Animales", "Calidad media"]
    capture_two_temp_headers = ["Temp RT final", "Temp LT final"]
    capture_cow_temp_headers = ["Temp FLT final", "Temp FRT final", "Temp RLT final", "Temp RRT final"]
    capture_two_manual_temp_headers = ["Temp manual RT", "Temp manual LT"]
    capture_cow_manual_temp_headers = ["Temp manual FLT", "Temp manual FRT", "Temp manual RLT", "Temp manual RRT"]
    capture_headers = [
        "Correo", "Hora", "Animal", "Especie", "Modo", "Sensor", "Termometros", "Medicion", "Configuracion", "Estado",
        "Pulso ref.", "Temp manual inicio", "Temp manual RT", "Temp manual LT", "Temp manual FLT", "Temp manual FRT", "Temp manual RLT", "Temp manual RRT",
        "Dif. BPM-ref", "BPM medio", "Oxigeno medio", "Calidad", "Contacto",
        "Temp final", "Temp RT final", "Temp LT final", "Temp FLT final", "Temp FRT final", "Temp RLT final", "Temp RRT final",
        "Duracion", "Hz", "Muestras", "Raw",
    ]
    files_headers = ["Correo", "tipo", "archivo", "filas", "ruta"]
    temporal_two_temp_headers = ["Temp RT max tramo", "Temp LT max tramo"]
    temporal_cow_temp_headers = ["Temp FLT max tramo", "Temp FRT max tramo", "Temp RLT max tramo", "Temp RRT max tramo"]
    temporal_headers = ["Tramo", "Inicio s", "Fin s", "BPM 10s", "BPM tramo", "SpO2 tramo", "Calidad tramo", "Temp max tramo", "Temp RT max tramo", "Temp LT max tramo", "Temp FLT max tramo", "Temp FRT max tramo", "Temp RLT max tramo", "Temp RRT max tramo", "Muestras tramo"]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PPG Suite v8 | Estadisticas")
        self.resize(1380, 860)
        self.search_roots: list[Path] = [RESULTS_DIR]
        self.sessions: list[SessionGroup] = []
        self.filtered_sessions: list[SessionGroup] = []
        self.current_session: SessionGroup | None = None
        self.current_capture: CaptureRecord | None = None
        self.temporal_source_rows: list[dict[str, str]] = []
        self.temporal_rel_t = np.asarray([], dtype=float)
        self.mail_paths: dict[str, Path] = {}
        self._build_ui()
        self.update_mail_status()
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
        self.mail_status = QtWidgets.QLabel("0 archivos seleccionados")
        self.btn_prepare_mail = QtWidgets.QPushButton("Preparar correo")
        self.btn_clear_mail = QtWidgets.QPushButton("Limpiar seleccion")
        self.btn_prepare_mail.setMinimumHeight(42)
        self.btn_clear_mail.setMinimumHeight(42)
        top.addStretch(1)
        top.addWidget(self.mail_status)
        top.addWidget(self.btn_prepare_mail)
        top.addWidget(self.btn_clear_mail)
        self.btn_back.clicked.connect(self.back_to_menu.emit)
        self.btn_prepare_mail.clicked.connect(self.prepare_mail_zip)
        self.btn_clear_mail.clicked.connect(self.clear_mail_selection)

        filters = QtWidgets.QGroupBox("Buscar en sesiones")
        fl = QtWidgets.QGridLayout(filters)
        self.text_filter = QtWidgets.QLineEdit()
        self.text_filter.setPlaceholderText("Animal, modo, configuracion, contacto...")
        self.mode_filter = QtWidgets.QComboBox()
        self.mode_filter.addItem("Todos")
        self.udder_filter = QtWidgets.QComboBox()
        self.udder_filter.addItem("Todos")
        self.vacuum_filter = QtWidgets.QComboBox()
        self.vacuum_filter.addItem("Todos")
        self.quality_min = QtWidgets.QDoubleSpinBox()
        self.quality_min.setRange(0, 100)
        self.quality_min.setValue(0)
        self.btn_clear = QtWidgets.QPushButton("Limpiar")
        self.btn_import = QtWidgets.QPushButton("Leer otra carpeta")
        fl.addWidget(QtWidgets.QLabel("Texto"), 0, 0)
        fl.addWidget(self.text_filter, 0, 1, 1, 4)
        fl.addWidget(QtWidgets.QLabel("Modo"), 0, 5)
        fl.addWidget(self.mode_filter, 0, 6)
        fl.addWidget(QtWidgets.QLabel("Sensor"), 0, 7)
        fl.addWidget(self.udder_filter, 0, 8)
        fl.addWidget(QtWidgets.QLabel("Medicion"), 0, 9)
        fl.addWidget(self.vacuum_filter, 0, 10)
        fl.addWidget(QtWidgets.QLabel("Calidad min."), 1, 0)
        fl.addWidget(self.quality_min, 1, 1)
        fl.addWidget(self.btn_clear, 1, 2)
        fl.addWidget(self.btn_import, 1, 3)
        root.addWidget(filters)
        self.text_filter.textChanged.connect(self.apply_filters)
        self.mode_filter.currentTextChanged.connect(self.apply_filters)
        self.udder_filter.currentTextChanged.connect(self.apply_filters)
        self.vacuum_filter.currentTextChanged.connect(self.apply_filters)
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
        self.sessions_model.check_changed_callback = self.on_mail_checked
        self.sessions_table = QtWidgets.QTableView()
        self.sessions_table.setModel(self.sessions_model)
        self.sessions_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.sessions_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.sessions_table.setAlternatingRowColors(True)
        self.sessions_table.verticalHeader().setVisible(False)
        self.sessions_table.setSortingEnabled(True)
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
        self.captures_model.check_changed_callback = self.on_mail_checked
        self.captures_table = QtWidgets.QTableView()
        self.captures_table.setModel(self.captures_model)
        self.captures_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.captures_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.captures_table.setAlternatingRowColors(True)
        self.captures_table.verticalHeader().setVisible(False)
        self.captures_table.setSortingEnabled(True)
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

        temporal_page = QtWidgets.QWidget()
        temporal_layout = QtWidgets.QHBoxLayout(temporal_page)
        self.temporal_model = DictTableModel(self.temporal_headers)
        self.temporal_table = QtWidgets.QTableView()
        self.temporal_table.setModel(self.temporal_model)
        self.temporal_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.temporal_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.temporal_table.setAlternatingRowColors(True)
        self.temporal_table.verticalHeader().setVisible(False)
        self.temporal_table.setSortingEnabled(True)
        self.temporal_table.setMinimumWidth(520)
        self.temporal_table.selectionModel().selectionChanged.connect(self.update_selected_temporal_plot)
        temporal_layout.addWidget(self.temporal_table, stretch=0)
        temporal_right = QtWidgets.QVBoxLayout()
        temporal_layout.addLayout(temporal_right, stretch=1)
        temporal_controls = QtWidgets.QHBoxLayout()
        temporal_right.addLayout(temporal_controls)
        self.chk_temporal_signal = QtWidgets.QCheckBox("IR/RED")
        self.chk_temporal_signal.setChecked(True)
        self.chk_temporal_bpm = QtWidgets.QCheckBox("BPM")
        self.chk_temporal_bpm.setChecked(True)
        self.chk_temporal_spo2 = QtWidgets.QCheckBox("Oxigeno")
        self.chk_temporal_spo2.setChecked(True)
        self.chk_temporal_temp = QtWidgets.QCheckBox("Temperatura")
        self.chk_temporal_temp.setChecked(True)
        self.chk_temporal_blocks = QtWidgets.QCheckBox("Bloques BPM")
        for chk in [self.chk_temporal_signal, self.chk_temporal_bpm, self.chk_temporal_spo2, self.chk_temporal_temp, self.chk_temporal_blocks]:
            chk.toggled.connect(self.update_selected_temporal_plot)
            temporal_controls.addWidget(chk)
        temporal_controls.addStretch(1)
        self.plot_temporal_signal = pg.PlotWidget(title="Senal del tramo seleccionado")
        self.plot_temporal_signal.setBackground("w")
        self.plot_temporal_signal.showGrid(x=True, y=True, alpha=0.25)
        self.plot_temporal_signal.setLabel("bottom", "Tiempo relativo", units="s")
        temporal_right.addWidget(self.plot_temporal_signal, stretch=1)
        self.detail_tabs.addTab(temporal_page, "Temporalizacion")

        self.params = QtWidgets.QTextEdit()
        self.params.setReadOnly(True)
        self.detail_tabs.addTab(self.params, "Parametros dispositivo")

        self.files_model = DictTableModel(self.files_headers)
        self.files_model.check_changed_callback = self.on_mail_checked
        files_page = QtWidgets.QWidget()
        files_layout = QtWidgets.QVBoxLayout(files_page)
        files_buttons = QtWidgets.QHBoxLayout()
        self.btn_open_selected_files = QtWidgets.QPushButton("Abrir seleccion")
        self.btn_copy_file_paths = QtWidgets.QPushButton("Copiar rutas")
        files_buttons.addWidget(self.btn_open_selected_files)
        files_buttons.addWidget(self.btn_copy_file_paths)
        files_buttons.addStretch(1)
        files_layout.addLayout(files_buttons)
        self.files_table = QtWidgets.QTableView()
        self.files_table.setModel(self.files_model)
        self.files_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.files_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.files_table.setAlternatingRowColors(True)
        self.files_table.verticalHeader().setVisible(False)
        self.files_table.setSortingEnabled(True)
        self.files_table.doubleClicked.connect(self.open_file_from_files_table)
        files_layout.addWidget(self.files_table)
        self.btn_open_selected_files.clicked.connect(self.open_selected_files)
        self.btn_copy_file_paths.clicked.connect(self.copy_selected_file_paths)
        self.detail_tabs.addTab(files_page, "Archivos")

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
        self.udder_filter.setCurrentIndex(0)
        self.vacuum_filter.setCurrentIndex(0)
        self.quality_min.setValue(0)
        self.apply_filters()

    def reload_data(self):
        self.sessions = self._discover_sessions()
        modes = sorted({_mode_label(cap.value("modo")) for session in self.sessions for cap in session.captures if cap.value("modo")})
        udders = sorted({cap.value("ubre") for session in self.sessions for cap in session.captures if cap.value("ubre")})
        vacuums = sorted({cap.value("medicion_vacio") for session in self.sessions for cap in session.captures if cap.value("medicion_vacio")})
        current = self.mode_filter.currentText()
        current_udder = self.udder_filter.currentText()
        current_vacuum = self.vacuum_filter.currentText()
        self.mode_filter.blockSignals(True)
        self.udder_filter.blockSignals(True)
        self.vacuum_filter.blockSignals(True)
        self.mode_filter.clear()
        self.mode_filter.addItem("Todos")
        self.mode_filter.addItems(modes)
        self.mode_filter.setCurrentText(current if current in ["Todos", *modes] else "Todos")
        self.udder_filter.clear()
        self.udder_filter.addItem("Todos")
        self.udder_filter.addItems(udders)
        self.udder_filter.setCurrentText(current_udder if current_udder in ["Todos", *udders] else "Todos")
        self.vacuum_filter.clear()
        self.vacuum_filter.addItem("Todos")
        self.vacuum_filter.addItems(vacuums)
        self.vacuum_filter.setCurrentText(current_vacuum if current_vacuum in ["Todos", *vacuums] else "Todos")
        self.mode_filter.blockSignals(False)
        self.udder_filter.blockSignals(False)
        self.vacuum_filter.blockSignals(False)
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
        cap.row.setdefault("animal_type", str(data.get("animal_type") or ""))
        cap.row.setdefault("condiciones_medida", str(data.get("measurement_condition") or ""))
        cap.row.setdefault("ubre", str(data.get("udder_side") or data.get("ubre") or ""))
        cap.row.setdefault("temp_mapping", str(data.get("temp_mapping") or ""))
        cap.row.setdefault("temp_primary_channel", str(data.get("temp_primary_channel") or ""))
        cap.row.setdefault("medicion_vacio", str(data.get("vacuum_condition") or data.get("medicion_vacio") or ""))
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
        annotations = data.get("annotations") or {}
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
            "temp_c_final_max_5s": temp.get("temp_c_final_max_5s"),
            "temp_c_final_time_s": temp.get("temp_c_final_time_s"),
            "temp_c_final_raw_at_max": temp.get("temp_c_final_raw_at_max"),
            "temp_c_media": temp.get("temp_c_mean"),
            "temp_c_ultima": temp.get("temp_c_last"),
            "temp_c_min": temp.get("temp_c_min"),
            "temp_c_max": temp.get("temp_c_max"),
            "temp_raw_ultima": temp.get("temp_raw_last"),
            "temp_rt_c_final_max_5s": temp.get("temp_rt_c_final_max_5s"),
            "temp_rt_c_final_time_s": temp.get("temp_rt_c_final_time_s"),
            "temp_rt_c_final_raw_at_max": temp.get("temp_rt_c_final_raw_at_max"),
            "temp_rt_c_media": temp.get("temp_rt_c_mean"),
            "temp_rt_c_ultima": temp.get("temp_rt_c_last"),
            "temp_rt_raw_ultima": temp.get("temp_rt_raw_last"),
            "temp_lt_c_final_max_5s": temp.get("temp_lt_c_final_max_5s"),
            "temp_lt_c_final_time_s": temp.get("temp_lt_c_final_time_s"),
            "temp_lt_c_final_raw_at_max": temp.get("temp_lt_c_final_raw_at_max"),
            "temp_lt_c_media": temp.get("temp_lt_c_mean"),
            "temp_lt_c_ultima": temp.get("temp_lt_c_last"),
            "temp_lt_raw_ultima": temp.get("temp_lt_raw_last"),
            "temp_a0_c_final_max_5s": temp.get("temp_a0_c_final_max_5s"),
            "temp_a0_c_final_time_s": temp.get("temp_a0_c_final_time_s"),
            "temp_a0_c_final_raw_at_max": temp.get("temp_a0_c_final_raw_at_max"),
            "temp_a0_c_media": temp.get("temp_a0_c_mean"),
            "temp_a0_c_ultima": temp.get("temp_a0_c_last"),
            "temp_a0_raw_ultima": temp.get("temp_a0_raw_last"),
            "temp_a1_c_final_max_5s": temp.get("temp_a1_c_final_max_5s"),
            "temp_a1_c_final_time_s": temp.get("temp_a1_c_final_time_s"),
            "temp_a1_c_final_raw_at_max": temp.get("temp_a1_c_final_raw_at_max"),
            "temp_a1_c_media": temp.get("temp_a1_c_mean"),
            "temp_a1_c_ultima": temp.get("temp_a1_c_last"),
            "temp_a1_raw_ultima": temp.get("temp_a1_raw_last"),
            "temp_a2_c_final_max_5s": temp.get("temp_a2_c_final_max_5s"),
            "temp_a2_c_final_time_s": temp.get("temp_a2_c_final_time_s"),
            "temp_a2_c_final_raw_at_max": temp.get("temp_a2_c_final_raw_at_max"),
            "temp_a2_c_media": temp.get("temp_a2_c_mean"),
            "temp_a2_c_ultima": temp.get("temp_a2_c_last"),
            "temp_a2_raw_ultima": temp.get("temp_a2_raw_last"),
            "temp_a3_c_final_max_5s": temp.get("temp_a3_c_final_max_5s"),
            "temp_a3_c_final_time_s": temp.get("temp_a3_c_final_time_s"),
            "temp_a3_c_final_raw_at_max": temp.get("temp_a3_c_final_raw_at_max"),
            "temp_a3_c_media": temp.get("temp_a3_c_mean"),
            "temp_a3_c_ultima": temp.get("temp_a3_c_last"),
            "temp_a3_raw_ultima": temp.get("temp_a3_raw_last"),
            "temp_flt_c_final_max_5s": temp.get("temp_flt_c_final_max_5s"),
            "temp_flt_c_media": temp.get("temp_flt_c_mean"),
            "temp_flt_c_ultima": temp.get("temp_flt_c_last"),
            "temp_flt_raw_ultima": temp.get("temp_flt_raw_last"),
            "temp_frt_c_final_max_5s": temp.get("temp_frt_c_final_max_5s"),
            "temp_frt_c_media": temp.get("temp_frt_c_mean"),
            "temp_frt_c_ultima": temp.get("temp_frt_c_last"),
            "temp_frt_raw_ultima": temp.get("temp_frt_raw_last"),
            "temp_rlt_c_final_max_5s": temp.get("temp_rlt_c_final_max_5s"),
            "temp_rlt_c_media": temp.get("temp_rlt_c_mean"),
            "temp_rlt_c_ultima": temp.get("temp_rlt_c_last"),
            "temp_rlt_raw_ultima": temp.get("temp_rlt_raw_last"),
            "temp_rrt_c_final_max_5s": temp.get("temp_rrt_c_final_max_5s"),
            "temp_rrt_c_media": temp.get("temp_rrt_c_mean"),
            "temp_rrt_c_ultima": temp.get("temp_rrt_c_last"),
            "temp_rrt_raw_ultima": temp.get("temp_rrt_raw_last"),
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
            "temperatura_manual_inicio_c": manual.get("temperatura_manual_inicio_c"),
            "temperatura_manual_inicio_rt_c": manual.get("temperatura_manual_inicio_rt_c"),
            "temperatura_manual_inicio_lt_c": manual.get("temperatura_manual_inicio_lt_c"),
            "temperatura_manual_inicio_frt_c": manual.get("temperatura_manual_inicio_frt_c"),
            "temperatura_manual_inicio_flt_c": manual.get("temperatura_manual_inicio_flt_c"),
            "temperatura_manual_inicio_rrt_c": manual.get("temperatura_manual_inicio_rrt_c"),
            "temperatura_manual_inicio_rlt_c": manual.get("temperatura_manual_inicio_rlt_c"),
            "pulso_final_pulsio": manual.get("pulso_final_pulsio"),
            "pulso_final_fonendo": manual.get("pulso_final_fonendo"),
            "anotaciones_finales": annotations.get("final"),
        }
        for key, value in values.items():
            if value is not None and not cap.row.get(key):
                cap.row[key] = str(value)
        for kind, file_path in (data.get("files") or {}).items():
            path_obj = Path(str(file_path))
            if path_obj.exists():
                normalized_kind = "blocks" if str(kind) == "bpm_blocks_10s" else str(kind)
                cap.files.setdefault(normalized_kind, path_obj)

    def _resolve_file_from_row(self, row: dict[str, str], key: str, default_dir: Path) -> Path | None:
        value = row.get(key, "")
        if not value:
            return None
        direct = Path(value)
        candidates = [direct]
        if not direct.is_absolute():
            candidates.append(default_dir / direct.name)
            for root in self.search_roots:
                candidates.append(root / direct.name)
        for candidate in candidates:
            if candidate.exists():
                return candidate
        for root in self.search_roots:
            for candidate in root.rglob(direct.name):
                if candidate.exists():
                    return candidate
        return None

    def _attach_files_from_row(self, cap: CaptureRecord):
        row_files = {
            "raw": ("raw", RAW_DIR),
            "processed": ("processed", PROCESSED_DIR),
            "plot": ("plot", FIGURES_DIR),
            "screenshot": ("screenshot", SCREENSHOT_DIR),
            "summary": ("summary", REPORT_DIR),
            "config": ("config", CONFIG_DIR),
            "blocks": ("blocks_10s_file", REPORT_DIR),
        }
        for kind, (row_key, default_dir) in row_files.items():
            if kind in cap.files:
                continue
            path = self._resolve_file_from_row(cap.row, row_key, default_dir)
            if path:
                cap.files[kind] = path

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
                    self._attach_files_from_row(cap)
                    self._enrich_capture_from_summary(cap)
                    group.captures.append(cap)
                if group.captures:
                    groups.append(group)
        orphan = SessionGroup(key="historico_sin_session", path=None)
        for base, files in files_by_base.items():
            if base in attached_bases:
                continue
            if "summary" not in files and "processed" not in files and "raw" not in files:
                continue
            raw_row = {}
            if "raw" in files:
                raw_rows = _read_csv(files["raw"], limit=1)
                raw_row = raw_rows[0] if raw_rows else {}
            cap = CaptureRecord(
                session_key=orphan.key,
                capture_id=base,
                base_name=base,
                row={"session_id": base, "base_name": base, **raw_row},
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
        udder = self.udder_filter.currentText()
        vacuum = self.vacuum_filter.currentText()
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
                if udder != "Todos" and cap.value("ubre") != udder:
                    continue
                if vacuum != "Todos" and cap.value("medicion_vacio") != vacuum:
                    continue
                quality = _as_float(cap.value("calidad"))
                if np.isfinite(quality) and quality < quality_min:
                    continue
                captures.append(cap)
            if captures or (not session.captures and not text and mode == "Todos" and udder == "Todos" and vacuum == "Todos"):
                filtered.append(SessionGroup(key=session.key, path=session.path, captures=captures))
        self.filtered_sessions = filtered
        session_rows = []
        for idx, session in enumerate(filtered):
            row = self._session_row(session)
            row["_session_index"] = str(idx)
            session_rows.append(row)
        self.sessions_model.set_rows(self.session_headers, session_rows)
        self.sessions_table.resizeColumnsToContents()
        self.sessions_label.setText(f"{len(filtered)} sesiones | {sum(len(s.captures) for s in filtered)} tomas visibles")
        if filtered:
            _select_first_row(self.sessions_table)
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
        mail_key = self.mail_key(session.path)
        return {
            "Correo": "",
            "_mail_key": mail_key,
            "_mail_path": str(session.path) if session.path else "",
            "_mail_checked": "1" if mail_key and mail_key in self.mail_paths else "0",
            "_mail_tooltip": "Marcar CSV de sesion para incluirlo en el ZIP" if session.path else "Esta sesion no tiene CSV localizado",
            "Sesion": session.name,
            "Fecha": min(dates) if dates else "",
            "Inicio": min(hours) if hours else "",
            "Modos": ", ".join(modes),
            "Tomas": str(len(caps)),
            "Animales": str(len(animals)),
            "Calidad media": fmt(float(np.mean(qualities)) if qualities else math.nan, 0, ""),
        }

    def capture_raw_path(self, cap: CaptureRecord) -> Path | None:
        path = cap.files.get("raw")
        if path and path.exists():
            return path
        path = self._resolve_file_from_row(cap.row, "raw", RAW_DIR)
        return path if path and path.exists() else None

    def mail_key(self, path: Path | None) -> str:
        if path is None:
            return ""
        try:
            return str(path.resolve())
        except OSError:
            return str(path)

    def on_mail_checked(self, row: dict[str, str], checked: bool):
        key = row.get("_mail_key", "")
        path_text = row.get("_mail_path", "")
        if not key or not path_text:
            return
        path = Path(path_text)
        if checked:
            self.mail_paths[key] = path
        else:
            self.mail_paths.pop(key, None)
        self.update_mail_status()

    def update_mail_status(self):
        count = len(self.mail_paths)
        self.mail_status.setText(f"{count} archivo{'s' if count != 1 else ''} seleccionado{'s' if count != 1 else ''}")

    def clear_mail_selection(self):
        if not self.mail_paths:
            return
        self.mail_paths.clear()
        self.update_mail_status()
        self.apply_filters()
        if self.current_session is not None:
            self.set_session(self.current_session)

    def desktop_dir(self) -> Path:
        desktop = Path.home() / "Desktop"
        if not desktop.exists():
            desktop = Path.home() / "Escritorio"
        return desktop if desktop.exists() else Path.home()

    def prepare_mail_zip(self):
        paths = [path for path in self.mail_paths.values() if path.exists()]
        if not paths:
            QtWidgets.QMessageBox.information(self, "Preparar correo", "Marca primero uno o varios archivos, raws o sesiones.")
            return
        desktop = self.desktop_dir()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_path = desktop / f"mtestv2_archivos_para_correo_{stamp}.zip"
        used_names: dict[str, int] = {}
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in paths:
                name = path.name
                if name in used_names:
                    used_names[name] += 1
                    stem = path.stem
                    suffix = path.suffix
                    name = f"{stem}_{used_names[path.name]}{suffix}"
                else:
                    used_names[name] = 1
                zf.write(path, arcname=name)
        QtWidgets.QApplication.clipboard().setText(str(zip_path))
        QtWidgets.QMessageBox.information(
            self,
            "Preparar correo",
            f"Se ha creado un ZIP en el Escritorio con {len(paths)} archivo(s):\n\n{zip_path}\n\nLa ruta queda copiada al portapapeles.",
        )

    def select_session(self):
        indexes = self.sessions_table.selectionModel().selectedRows()
        if not indexes:
            self.set_session(None)
            return
        row = indexes[0].row()
        model_row = self.sessions_model.rows[row] if 0 <= row < len(self.sessions_model.rows) else {}
        source_row = int(model_row.get("_session_index", row) or row)
        self.set_session(self.filtered_sessions[source_row] if 0 <= source_row < len(self.filtered_sessions) else None)

    def set_session(self, session: SessionGroup | None):
        self.current_session = session
        self.current_capture = None
        if session is None:
            self.captures_label.setText("Raws / tomas de la sesion seleccionada")
            self.captures_model.set_rows(self.capture_headers, [])
            self.set_capture(None)
            return
        self.captures_label.setText(f"Raws / tomas dentro de {session.name}")
        capture_rows = []
        for idx, cap in enumerate(session.captures):
            row = self._capture_row(cap)
            row["_capture_index"] = str(idx)
            capture_rows.append(row)
        capture_headers = self._headers_for_temperature_rows(
            self.capture_headers,
            capture_rows,
            self.capture_two_temp_headers + self.capture_two_manual_temp_headers,
            self.capture_cow_temp_headers + self.capture_cow_manual_temp_headers,
        )
        self.captures_model.set_rows(capture_headers, capture_rows)
        self.captures_table.resizeColumnsToContents()
        if session.captures:
            _select_first_row(self.captures_table)
        else:
            self.set_capture(None)

    def _capture_row(self, cap: CaptureRecord) -> dict[str, str]:
        quality = _as_float(cap.value("calidad"))
        bpm = _as_float(cap.value("bpm"))
        raw_path = self.capture_raw_path(cap)
        raw_key = self.mail_key(raw_path)
        ref_avg, _ref_count = _mean_ref_pulse(
            cap.value("pulso_previo"),
            cap.value("pulso_final_pulsio"),
            cap.value("pulso_final_fonendo"),
        )
        diff_ref = abs(bpm - ref_avg) if np.isfinite(bpm) and np.isfinite(ref_avg) else math.nan
        if np.isfinite(quality) and quality >= 70:
            state = "Buena"
        elif np.isfinite(quality) and quality >= 45:
            state = "Aceptable"
        else:
            state = "Dudosa" if cap.value("bpm") else ""
        return {
            "Correo": "",
            "_mail_key": raw_key,
            "_mail_path": str(raw_path) if raw_path else "",
            "_mail_checked": "1" if raw_key and raw_key in self.mail_paths else "0",
            "_mail_tooltip": "Marcar raw para incluirlo en el ZIP de correo" if raw_path else "Esta toma no tiene raw localizado",
            "Hora": cap.value("hora"),
            "Animal": cap.value("id"),
            "Especie": animal_label(cap.value("animal_type")) if cap.value("animal_type") else "",
            "Modo": _mode_label(cap.value("modo")),
            "Sensor": cap.value("ubre"),
            "Termometros": self._display_temp_mapping(cap.value("temp_mapping"), cap.value("animal_type")),
            "Canal temp": cap.value("temp_primary_channel"),
            "Medicion": cap.value("medicion_vacio"),
            "Configuracion": cap.value("config_label"),
            "Estado": state,
            "Pulso ref.": fmt(ref_avg, 1, ""),
            "Dif. BPM-ref": fmt(diff_ref, 1, ""),
            "BPM medio": fmt(bpm, 0, ""),
            "BPM picos": fmt(_as_float(cap.value("bpm_peak")), 0, ""),
            "BPM FFT": fmt(_as_float(cap.value("bpm_fft")), 0, ""),
            "BPM autocorr": fmt(_as_float(cap.value("bpm_autocorr")), 0, ""),
            "Oxigeno medio": fmt(_as_float(cap.value("spo2_pct")), 1, ""),
            "Ratio R": fmt(_as_float(cap.value("ratio_r")), 4, ""),
            "Resp/min (experimental)": fmt(_as_float(_cap_first(cap, "resp_rate_rpm", "resp_min_exp")), 1, ""),
            "Calidad resp.": fmt(_as_float(_cap_first(cap, "resp_quality", "resp_calidad_exp")), 0, ""),
            "Temp final": fmt(_as_float(_cap_temp_final(cap, "temp_c_final_max_5s", "temp_c_media")), 1, ""),
            "Temp ult.": fmt(_as_float(cap.value("temp_c_ultima")), 1, ""),
            "Temp RT final": fmt(_as_float(_cap_temp_final(cap, "temp_rt_c_final_max_5s", "temp_rt_c_media", "temp_a0_c_final_max_5s", "temp_a0_c_media", "temp_c_final_max_5s")), 1, ""),
            "Temp RT ult.": fmt(_as_float(_cap_first(cap, "temp_rt_c_ultima", "temp_a0_c_ultima", "temp_c_ultima")), 1, ""),
            "Temp RT raw": fmt(_as_float(_cap_first(cap, "temp_rt_raw_ultima", "temp_a0_raw_ultima", "temp_raw_ultima")), 0, ""),
            "Temp LT final": fmt(_as_float(_cap_temp_final(cap, "temp_lt_c_final_max_5s", "temp_lt_c_media", "temp_a1_c_final_max_5s", "temp_a1_c_media")), 1, ""),
            "Temp LT ult.": fmt(_as_float(_cap_first(cap, "temp_lt_c_ultima", "temp_a1_c_ultima")), 1, ""),
            "Temp LT raw": fmt(_as_float(_cap_first(cap, "temp_lt_raw_ultima", "temp_a1_raw_ultima")), 0, ""),
            "Temp FLT final": fmt(_as_float(_cap_temp_final(cap, "temp_flt_c_final_max_5s", "temp_flt_c_media")), 1, ""),
            "Temp FRT final": fmt(_as_float(_cap_temp_final(cap, "temp_frt_c_final_max_5s", "temp_frt_c_media")), 1, ""),
            "Temp RLT final": fmt(_as_float(_cap_temp_final(cap, "temp_rlt_c_final_max_5s", "temp_rlt_c_media")), 1, ""),
            "Temp RRT final": fmt(_as_float(_cap_temp_final(cap, "temp_rrt_c_final_max_5s", "temp_rrt_c_media")), 1, ""),
            "Temp A0 final": fmt(_as_float(_cap_temp_final(cap, "temp_a0_c_final_max_5s", "temp_a0_c_media", "temp_c_final_max_5s", "temp_c_media")), 1, ""),
            "Temp A0 ult.": fmt(_as_float(_cap_first(cap, "temp_a0_c_ultima", "temp_c_ultima")), 1, ""),
            "Temp A0 raw": fmt(_as_float(_cap_first(cap, "temp_a0_raw_ultima", "temp_raw_ultima")), 0, ""),
            "Temp A1 final": fmt(_as_float(_cap_temp_final(cap, "temp_a1_c_final_max_5s", "temp_a1_c_media")), 1, ""),
            "Temp A1 ult.": fmt(_as_float(cap.value("temp_a1_c_ultima")), 1, ""),
            "Temp A1 raw": fmt(_as_float(cap.value("temp_a1_raw_ultima")), 0, ""),
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
            "Temp manual inicio": cap.value("temperatura_manual_inicio_c"),
            "Temp manual RT": cap.value("temperatura_manual_inicio_rt_c"),
            "Temp manual LT": cap.value("temperatura_manual_inicio_lt_c"),
            "Temp manual FLT": cap.value("temperatura_manual_inicio_flt_c"),
            "Temp manual FRT": cap.value("temperatura_manual_inicio_frt_c"),
            "Temp manual RLT": cap.value("temperatura_manual_inicio_rlt_c"),
            "Temp manual RRT": cap.value("temperatura_manual_inicio_rrt_c"),
            "Pulso final pulsio": cap.value("pulso_final_pulsio"),
            "Pulso final fonendo": cap.value("pulso_final_fonendo"),
            "Raw": raw_path.name if raw_path else "",
        }

    def _display_temp_mapping(self, value: str, animal_type: str = "") -> str:
        mapping = (value or "").strip()
        if mapping:
            return display_mapping(mapping, animal_type)
        return ""

    def _row_is_cow(self, row: dict[str, str]) -> bool:
        species = (row.get("Especie") or row.get("animal_type") or "").strip()
        if species and normalize_animal_type(species) == "vaca":
            return True
        return any(str(row.get(header, "")).strip() for header in self.capture_cow_temp_headers)

    def _row_is_two_sensor(self, row: dict[str, str]) -> bool:
        species = (row.get("Especie") or row.get("animal_type") or "").strip()
        if species and normalize_animal_type(species) != "vaca":
            return True
        return any(str(row.get(header, "")).strip() for header in self.capture_two_temp_headers)

    def _headers_for_temperature_rows(
        self,
        headers: list[str],
        rows: list[dict[str, str]],
        two_headers: list[str],
        cow_headers: list[str],
        *,
        show_two: bool | None = None,
        show_cow: bool | None = None,
    ) -> list[str]:
        if show_two is None:
            show_two = any(
                self._row_is_two_sensor(row) or any(str(row.get(header, "")).strip() for header in two_headers)
                for row in rows
            )
        if show_cow is None:
            show_cow = any(
                self._row_is_cow(row) or any(str(row.get(header, "")).strip() for header in cow_headers)
                for row in rows
            )
        if rows and not show_two and not show_cow:
            show_two = True
        visible: list[str] = []
        for header in headers:
            if header in two_headers and not show_two:
                continue
            if header in cow_headers and not show_cow:
                continue
            visible.append(header)
        return visible

    def _capture_is_cow(self, cap: CaptureRecord) -> bool:
        animal_type = cap.value("animal_type").strip()
        if animal_type and normalize_animal_type(animal_type) == "vaca":
            return True
        position = cap.value("ubre").strip().upper()
        if position in {"FLT", "FRT", "RLT", "RRT"}:
            return True
        return any(cap.value(key) for key in ("temp_flt_c_final_max_5s", "temp_frt_c_final_max_5s", "temp_rlt_c_final_max_5s", "temp_rrt_c_final_max_5s"))

    def _capture_is_two_sensor(self, cap: CaptureRecord) -> bool:
        animal_type = cap.value("animal_type").strip()
        if animal_type and normalize_animal_type(animal_type) != "vaca":
            return True
        position = cap.value("ubre").strip().upper()
        if position in {"RT", "LT"}:
            return True
        return any(cap.value(key) for key in ("temp_rt_c_final_max_5s", "temp_lt_c_final_max_5s"))

    def select_capture(self):
        if self.current_session is None:
            self.set_capture(None)
            return
        indexes = self.captures_table.selectionModel().selectedRows()
        if not indexes:
            self.set_capture(None)
            return
        row = indexes[0].row()
        model_row = self.captures_model.rows[row] if 0 <= row < len(self.captures_model.rows) else {}
        source_row = int(model_row.get("_capture_index", row) or row)
        self.set_capture(self.current_session.captures[source_row] if 0 <= source_row < len(self.current_session.captures) else None)

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
            self.temporal_model.set_rows(self.temporal_headers, [])
            self.temporal_source_rows = []
            self.temporal_rel_t = np.asarray([], dtype=float)
            self.plot_temporal_signal.clear()
            return
        self.summary.setHtml(self._summary_html(cap))
        self.params.setHtml(self._params_html(cap))
        file_rows = []
        for kind, path in sorted(cap.files.items()):
            mail_key = self.mail_key(path)
            file_rows.append({
                "Correo": "",
                "_mail_key": mail_key,
                "_mail_path": str(path),
                "_mail_checked": "1" if mail_key and mail_key in self.mail_paths else "0",
                "_mail_tooltip": "Marcar archivo para incluirlo en el ZIP de correo",
                "tipo": kind,
                "archivo": path.name,
                "filas": str(len(_read_csv(path))) if path.suffix.lower() == ".csv" else "",
                "ruta": str(path),
            })
        self.files_model.set_rows(self.files_headers, file_rows)
        raw_rows = _read_csv(cap.files["raw"], limit=5000) if "raw" in cap.files else []
        proc_rows = _read_csv(cap.files["processed"], limit=5000) if "processed" in cap.files else []
        raw_rows_full = _read_csv(cap.files["raw"]) if "raw" in cap.files else []
        proc_rows_full = _read_csv(cap.files["processed"]) if "processed" in cap.files else []
        block_rows = _read_csv(cap.files["blocks"], limit=5000) if "blocks" in cap.files else []
        self.files_table.resizeColumnsToContents()
        self.update_capture_plot(raw_rows, proc_rows, block_rows)
        self.update_temporalization(cap, raw_rows_full, proc_rows_full, block_rows)

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
        model_row = self.sessions_model.rows[row] if 0 <= row < len(self.sessions_model.rows) else {}
        source_row = int(model_row.get("_session_index", row) or row)
        if 0 <= source_row < len(self.filtered_sessions):
            self.open_path(self.filtered_sessions[source_row].path)

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

    def selected_file_paths(self) -> list[Path]:
        indexes = self.files_table.selectionModel().selectedRows() if self.files_table.selectionModel() else []
        paths: list[Path] = []
        for index in indexes:
            if 0 <= index.row() < len(self.files_model.rows):
                path_text = self.files_model.rows[index.row()].get("ruta", "")
                if path_text:
                    path = Path(path_text)
                    if path.exists() and path not in paths:
                        paths.append(path)
        if not paths and self.current_capture:
            raw = self.current_capture.files.get("raw")
            if raw and raw.exists():
                paths.append(raw)
        return paths

    def open_selected_files(self):
        for path in self.selected_file_paths():
            self.open_path(path)

    def copy_selected_file_paths(self):
        paths = self.selected_file_paths()
        if not paths:
            QtWidgets.QMessageBox.information(self, "Archivos", "No hay archivos seleccionados.")
            return
        QtWidgets.QApplication.clipboard().setText("\n".join(str(path) for path in paths))
        QtWidgets.QMessageBox.information(self, "Archivos", f"Copiadas {len(paths)} ruta(s) al portapapeles.")

    def _summary_html(self, cap: CaptureRecord) -> str:
        quality = _as_float(cap.value("calidad"))
        bpm = _as_float(cap.value("bpm"))
        spo2 = _as_float(cap.value("spo2_pct"))
        resp = _as_float(_cap_first(cap, "resp_rate_rpm", "resp_min_exp"))
        resp_quality = _as_float(_cap_first(cap, "resp_quality", "resp_calidad_exp"))
        pi_ir = _as_float(cap.value("pi_ir_pct"))
        artifacts = _as_float(cap.value("artefactos_ir_pct"))
        saturation = _as_float(cap.value("saturation_pct"))
        ref_avg, ref_count = _mean_ref_pulse(
            cap.value("pulso_previo"),
            cap.value("pulso_final_pulsio"),
            cap.value("pulso_final_fonendo"),
        )
        diff_ref = abs(bpm - ref_avg) if np.isfinite(bpm) and np.isfinite(ref_avg) else math.nan
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
        if np.isfinite(diff_ref) and diff_ref > 12:
            warnings.append(f"La BPM media queda a {diff_ref:.1f} BPM de la referencia manual; revisar contacto/configuracion o la anotacion manual.")
        reason_text = cap.value("metrics_reason") or ""
        if "cribado robusto" in reason_text.lower():
            warnings.append("El calculo ya aplica cribado robusto: el raw se conserva completo, pero las muestras inestables no pesan en la estimacion.")

        if not warnings:
            warnings.append("Sin avisos tecnicos destacados en las metricas guardadas.")

        def row(name: str, value: str) -> str:
            return f"<tr><td><b>{html.escape(name)}</b></td><td>{html.escape(value)}</td></tr>"

        fields = [
            ("Animal", cap.value("id") or "-"),
            ("Especie", animal_label(cap.value("animal_type")) if cap.value("animal_type") else "-"),
            ("Modo de recogida", _mode_label(cap.value("modo")) or "-"),
            ("Fecha y hora", f"{cap.value('fecha')} {cap.value('hora')}".strip() or "-"),
            ("Configuracion", cap.value("config_label") or "-"),
            ("Descripcion configuracion", cap.value("config_description") or "-"),
            ("Sensor", cap.value("ubre") or "-"),
            ("Termometros", self._display_temp_mapping(cap.value("temp_mapping"), cap.value("animal_type")) or "-"),
            ("Canal temp primario", cap.value("temp_primary_channel") or "-"),
            ("Medicion", cap.value("medicion_vacio") or "-"),
            ("Condiciones", cap.value("condiciones_medida") or "-"),
            ("Anotaciones finales", cap.value("anotaciones_finales") or "-"),
            ("Pulso ref. medio", f"{fmt(ref_avg, 1, '-')} BPM ({ref_count} lectura(s) validas; 0/vacio se ignora)"),
            ("Pulso previo / temp inicio / pulsio final / fonendo final", f"{cap.value('pulso_previo') or '-'} / {cap.value('temperatura_manual_inicio_c') or '-'} / {cap.value('pulso_final_pulsio') or '-'} / {cap.value('pulso_final_fonendo') or '-'}"),
            ("Temp manual RT / LT", f"{cap.value('temperatura_manual_inicio_rt_c') or '-'} / {cap.value('temperatura_manual_inicio_lt_c') or '-'}"),
            ("Temp manual FLT / FRT / RLT / RRT", f"{cap.value('temperatura_manual_inicio_flt_c') or '-'} / {cap.value('temperatura_manual_inicio_frt_c') or '-'} / {cap.value('temperatura_manual_inicio_rlt_c') or '-'} / {cap.value('temperatura_manual_inicio_rrt_c') or '-'}"),
            ("Diferencia BPM medio - ref.", f"{fmt(diff_ref, 1, '-')} BPM"),
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
            ("Temperatura primaria final / ultima", f"{fmt(_as_float(_cap_temp_final(cap, 'temp_c_final_max_5s', 'temp_c_media')), 2, '-')} / {fmt(_as_float(cap.value('temp_c_ultima')), 2, '-')} C"),
            ("Temperatura RT final / ultima / raw", f"{fmt(_as_float(_cap_temp_final(cap, 'temp_rt_c_final_max_5s', 'temp_rt_c_media', 'temp_a0_c_final_max_5s', 'temp_a0_c_media', 'temp_c_final_max_5s')), 2, '-')} / {fmt(_as_float(_cap_first(cap, 'temp_rt_c_ultima', 'temp_a0_c_ultima', 'temp_c_ultima')), 2, '-')} C / {fmt(_as_float(_cap_first(cap, 'temp_rt_raw_ultima', 'temp_a0_raw_ultima', 'temp_raw_ultima')), 0, '-')}"),
            ("Temperatura LT final / ultima / raw", f"{fmt(_as_float(_cap_temp_final(cap, 'temp_lt_c_final_max_5s', 'temp_lt_c_media', 'temp_a1_c_final_max_5s', 'temp_a1_c_media')), 2, '-')} / {fmt(_as_float(_cap_first(cap, 'temp_lt_c_ultima', 'temp_a1_c_ultima')), 2, '-')} C / {fmt(_as_float(_cap_first(cap, 'temp_lt_raw_ultima', 'temp_a1_raw_ultima')), 0, '-')}"),
            ("Temperatura A0 final / ultima / raw", f"{fmt(_as_float(_cap_temp_final(cap, 'temp_a0_c_final_max_5s', 'temp_a0_c_media', 'temp_c_final_max_5s', 'temp_c_media')), 2, '-')} / {fmt(_as_float(_cap_first(cap, 'temp_a0_c_ultima', 'temp_c_ultima')), 2, '-')} C / {fmt(_as_float(_cap_first(cap, 'temp_a0_raw_ultima', 'temp_raw_ultima')), 0, '-')}"),
            ("Temperatura A1 final / ultima / raw", f"{fmt(_as_float(_cap_temp_final(cap, 'temp_a1_c_final_max_5s', 'temp_a1_c_media')), 2, '-')} / {fmt(_as_float(cap.value('temp_a1_c_ultima')), 2, '-')} C / {fmt(_as_float(cap.value('temp_a1_raw_ultima')), 0, '-')}"),
            ("Temperatura A2 final / ultima / raw", f"{fmt(_as_float(_cap_temp_final(cap, 'temp_a2_c_final_max_5s', 'temp_a2_c_media')), 2, '-')} / {fmt(_as_float(cap.value('temp_a2_c_ultima')), 2, '-')} C / {fmt(_as_float(cap.value('temp_a2_raw_ultima')), 0, '-')}"),
            ("Temperatura A3 final / ultima / raw", f"{fmt(_as_float(_cap_temp_final(cap, 'temp_a3_c_final_max_5s', 'temp_a3_c_media')), 2, '-')} / {fmt(_as_float(cap.value('temp_a3_c_ultima')), 2, '-')} C / {fmt(_as_float(cap.value('temp_a3_raw_ultima')), 0, '-')}"),
            ("Vaca FLT / FRT final", f"{fmt(_as_float(_cap_temp_final(cap, 'temp_flt_c_final_max_5s', 'temp_flt_c_media')), 2, '-')} / {fmt(_as_float(_cap_temp_final(cap, 'temp_frt_c_final_max_5s', 'temp_frt_c_media')), 2, '-')} C"),
            ("Vaca RLT / RRT final", f"{fmt(_as_float(_cap_temp_final(cap, 'temp_rlt_c_final_max_5s', 'temp_rlt_c_media')), 2, '-')} / {fmt(_as_float(_cap_temp_final(cap, 'temp_rrt_c_final_max_5s', 'temp_rrt_c_media')), 2, '-')} C"),
            ("Duracion real / Hz real / muestras", f"{fmt(_as_float(cap.value('duracion_real_s')), 2, '-')} s / {fmt(_as_float(cap.value('hz_real')), 2, '-')} Hz / {cap.value('muestras') or '-'}"),
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

    def update_temporalization(self, cap: CaptureRecord, raw_rows: list[dict[str, str]], proc_rows: list[dict[str, str]], block_rows: list[dict[str, str]]):
        rows = proc_rows or raw_rows
        self.plot_temporal_signal.clear()
        self.temporal_source_rows = rows
        self.temporal_rel_t = np.asarray([], dtype=float)
        if not rows and not block_rows:
            self.temporal_model.set_rows(self.temporal_headers, [])
            return

        source_rows = rows
        rel_t = np.asarray([], dtype=float)
        if source_rows:
            t = np.asarray([_as_float(r.get("tiempo_s", "")) for r in source_rows], dtype=float)
            finite_t = t[np.isfinite(t)]
            if finite_t.size:
                rel_t = t - float(finite_t[0])
        self.temporal_rel_t = rel_t
        duration = self._temporal_duration(cap, rel_t, block_rows)
        if not np.isfinite(duration) or duration <= 0:
            self.temporal_model.set_rows(self.temporal_headers, [])
            return

        block_bpm = [_as_float(row.get("bpm_medio_10s", "")) for row in block_rows]
        interval_count = max(int(math.ceil(duration / 10.0)), len(block_bpm), 1)
        sensor_cfg = self._sensor_config_from_capture(cap)
        analysis_cfg = self._analysis_config_from_capture(cap)
        red_values = self._temporal_series(source_rows, "red_raw")
        ir_values = self._temporal_series(source_rows, "ir_raw")
        bpm_rolling = self._temporal_series(source_rows, "bpm_rolling_5s")
        spo2_rolling = self._temporal_series(source_rows, "spo2_rolling_5s")
        quality_rolling = self._temporal_series(source_rows, "quality_rolling_5s")
        temp_values = self._temporal_series(source_rows, "temp_c")
        temp_rt_values = self._temporal_series_with_fallback(source_rows, "temp_rt_c", "temp_a0_c", "temp_c")
        temp_lt_values = self._temporal_series_with_fallback(source_rows, "temp_lt_c", "temp_a1_c")
        temp_flt_values = self._temporal_series(source_rows, "temp_flt_c")
        temp_frt_values = self._temporal_series(source_rows, "temp_frt_c")
        temp_rlt_values = self._temporal_series(source_rows, "temp_rlt_c")
        temp_rrt_values = self._temporal_series(source_rows, "temp_rrt_c")

        table_rows: list[dict[str, str]] = []
        centers: list[float] = []
        bpm_block_values: list[float] = []
        bpm_tramo_values: list[float] = []
        spo2_values: list[float] = []
        quality_values: list[float] = []
        temp_plot_values: list[float] = []
        temp_rt_plot_values: list[float] = []
        temp_lt_plot_values: list[float] = []
        sample_values: list[float] = []

        for idx in range(interval_count):
            start = idx * 10.0
            end = min(duration, start + 10.0)
            if end <= start:
                continue
            if rel_t.size:
                if idx == interval_count - 1:
                    mask = np.isfinite(rel_t) & (rel_t >= start) & (rel_t <= end)
                else:
                    mask = np.isfinite(rel_t) & (rel_t >= start) & (rel_t < end)
                samples = int(np.sum(mask))
                bpm_roll = self._masked_mean(bpm_rolling, mask)
                spo2 = self._masked_mean(spo2_rolling, mask)
                quality = self._masked_mean(quality_rolling, mask)
                temp = self._masked_max(temp_values, mask)
                temp_rt = self._masked_max(temp_rt_values, mask)
                temp_lt = self._masked_max(temp_lt_values, mask)
                temp_flt = self._masked_max(temp_flt_values, mask)
                temp_frt = self._masked_max(temp_frt_values, mask)
                temp_rlt = self._masked_max(temp_rlt_values, mask)
                temp_rrt = self._masked_max(temp_rrt_values, mask)
                metrics = self._metrics_for_temporal_mask(rel_t, red_values, ir_values, mask, sensor_cfg, analysis_cfg)
                bpm_tramo = metrics.bpm if np.isfinite(metrics.bpm) else bpm_roll
                spo2 = metrics.spo2 if np.isfinite(metrics.spo2) else spo2
                quality = metrics.quality if np.isfinite(metrics.quality) and metrics.n else quality
            else:
                samples = 0
                bpm_tramo = math.nan
                spo2 = math.nan
                quality = math.nan
                temp = math.nan
                temp_rt = math.nan
                temp_lt = math.nan
                temp_flt = math.nan
                temp_frt = math.nan
                temp_rlt = math.nan
                temp_rrt = math.nan
            bpm_10s = block_bpm[idx] if idx < len(block_bpm) else math.nan
            if not np.isfinite(bpm_10s) and np.isfinite(bpm_tramo):
                bpm_10s = bpm_tramo
            center = (start + end) / 2.0
            centers.append(center)
            bpm_block_values.append(bpm_10s)
            bpm_tramo_values.append(bpm_tramo)
            spo2_values.append(spo2)
            quality_values.append(quality)
            temp_plot_values.append(temp)
            temp_rt_plot_values.append(temp_rt)
            temp_lt_plot_values.append(temp_lt)
            sample_values.append(float(samples))
            table_rows.append({
                "Tramo": f"{idx + 1}",
                "Inicio s": fmt(start, 1, ""),
                "Fin s": fmt(end, 1, ""),
                "BPM 10s": fmt(bpm_10s, 1, ""),
                "BPM tramo": fmt(bpm_tramo, 1, ""),
                "SpO2 tramo": fmt(spo2, 1, ""),
                "Calidad tramo": fmt(quality, 1, ""),
                "Temp max tramo": fmt(temp, 2, ""),
                "Temp RT max tramo": fmt(temp_rt, 2, ""),
                "Temp LT max tramo": fmt(temp_lt, 2, ""),
                "Temp FLT max tramo": fmt(temp_flt, 2, ""),
                "Temp FRT max tramo": fmt(temp_frt, 2, ""),
                "Temp RLT max tramo": fmt(temp_rlt, 2, ""),
                "Temp RRT max tramo": fmt(temp_rrt, 2, ""),
                "Muestras tramo": str(samples),
            })

        temporal_headers = self._headers_for_temperature_rows(
            self.temporal_headers,
            table_rows,
            self.temporal_two_temp_headers,
            self.temporal_cow_temp_headers,
            show_two=self._capture_is_two_sensor(cap) or any(
                str(row.get(header, "")).strip()
                for row in table_rows
                for header in self.temporal_two_temp_headers
            ),
            show_cow=self._capture_is_cow(cap) or any(
                str(row.get(header, "")).strip()
                for row in table_rows
                for header in self.temporal_cow_temp_headers
            ),
        )
        self.temporal_model.set_rows(temporal_headers, table_rows)
        self.temporal_table.resizeColumnsToContents()
        if table_rows:
            self.temporal_table.selectRow(0)
        else:
            self.plot_temporal_signal.clear()

    def update_selected_temporal_plot(self):
        indexes = self.temporal_table.selectionModel().selectedRows()
        if not indexes:
            self.plot_temporal_signal.clear()
            return
        row_index = indexes[0].row()
        if not (0 <= row_index < len(self.temporal_model.rows)):
            self.plot_temporal_signal.clear()
            return
        row = self.temporal_model.rows[row_index]
        start = _as_float(row.get("Inicio s", ""))
        end = _as_float(row.get("Fin s", ""))
        self.plot_temporal_signal.clear()
        rows = self.temporal_source_rows
        rel_t = self.temporal_rel_t
        if not rows or not rel_t.size or not np.isfinite(start) or not np.isfinite(end):
            return
        mask = np.isfinite(rel_t) & (rel_t >= start) & (rel_t <= end)
        if not np.any(mask):
            return
        tt = rel_t[mask]
        self.plot_temporal_signal.setTitle(f"Tramo {row.get('Tramo', '')}: {fmt(start, 1, '')}-{fmt(end, 1, '')} s")
        if self.chk_temporal_signal.isChecked():
            ir = np.asarray([_as_float(r.get("ir_proc_norm") or r.get("ir_raw", "")) for r in rows], dtype=float)
            red = np.asarray([_as_float(r.get("red_proc_norm") or r.get("red_raw", "")) for r in rows], dtype=float)
            self._plot_temporal_segment_series(tt, ir[mask], (0, 80, 220), "IR")
            self._plot_temporal_segment_series(tt, red[mask], (220, 40, 35), "RED")
        if self.chk_temporal_bpm.isChecked():
            bpm = np.asarray([_as_float(r.get("bpm_rolling_5s", "")) for r in rows], dtype=float)
            self._plot_temporal_segment_series(tt, bpm[mask], (40, 140, 50), "BPM")
        if self.chk_temporal_spo2.isChecked():
            spo2 = np.asarray([_as_float(r.get("spo2_rolling_5s", "")) for r in rows], dtype=float)
            self._plot_temporal_segment_series(tt, spo2[mask], (150, 70, 160), "SpO2")
        if self.chk_temporal_temp.isChecked():
            temp = np.asarray([_as_float(r.get("temp_c", "")) for r in rows], dtype=float)
            self._plot_temporal_segment_series(tt, temp[mask], (220, 120, 30), "Temp")
        if self.chk_temporal_blocks.isChecked():
            bpm_10s = _as_float(row.get("BPM 10s", ""))
            if np.isfinite(bpm_10s):
                self.plot_temporal_signal.plot(
                    [float(start), float(end)],
                    [bpm_10s, bpm_10s],
                    pen=pg.mkPen((20, 120, 110), width=2, style=QtCore.Qt.PenStyle.DashLine),
                    name="BPM 10s",
                )
        self.plot_temporal_signal.setXRange(float(start), max(float(end), float(start) + 1.0), padding=0.01)
        self.plot_temporal_signal.setLabel("bottom", "Tiempo relativo", units="s")

    def _temporal_duration(self, cap: CaptureRecord, rel_t: np.ndarray, block_rows: list[dict[str, str]]) -> float:
        if rel_t.size:
            finite_t = rel_t[np.isfinite(rel_t)]
            if finite_t.size:
                return float(np.max(finite_t))
        duration = _as_float(cap.value("duracion_real_s"))
        if np.isfinite(duration) and duration > 0:
            return duration
        ends = [_as_float(row.get("fin_s", "")) for row in block_rows]
        ends = [value for value in ends if np.isfinite(value)]
        return max(ends) if ends else math.nan

    def _temporal_series(self, rows: list[dict[str, str]], key: str) -> np.ndarray:
        if not rows:
            return np.asarray([], dtype=float)
        return np.asarray([_as_float(row.get(key, "")) for row in rows], dtype=float)

    def _temporal_series_with_fallback(self, rows: list[dict[str, str]], *keys: str) -> np.ndarray:
        if not rows:
            return np.asarray([], dtype=float)
        values = []
        for row in rows:
            value = math.nan
            for key in keys:
                value = _as_float(row.get(key, ""))
                if np.isfinite(value):
                    break
            values.append(value)
        return np.asarray(values, dtype=float)

    def _masked_mean(self, values: np.ndarray, mask: np.ndarray) -> float:
        if values.size != mask.size:
            return math.nan
        selected = values[mask]
        selected = selected[np.isfinite(selected)]
        if not selected.size:
            return math.nan
        return float(np.mean(selected))

    def _masked_max(self, values: np.ndarray, mask: np.ndarray) -> float:
        if values.size != mask.size:
            return math.nan
        selected = values[mask]
        selected = selected[np.isfinite(selected)]
        if not selected.size:
            return math.nan
        return float(np.max(selected))

    def _metrics_for_temporal_mask(
        self,
        rel_t: np.ndarray,
        red_values: np.ndarray,
        ir_values: np.ndarray,
        mask: np.ndarray,
        sensor_cfg: SensorConfig,
        analysis_cfg: AnalysisConfig,
    ):
        if rel_t.size != mask.size or red_values.size != mask.size or ir_values.size != mask.size:
            return self._empty_temporal_metrics()
        valid = mask & np.isfinite(rel_t) & np.isfinite(red_values) & np.isfinite(ir_values)
        if int(np.sum(valid)) < 80:
            return self._empty_temporal_metrics()
        t = rel_t[valid]
        return score_and_merge_metrics(t - float(t[0]), red_values[valid], ir_values[valid], sensor_cfg, analysis_cfg)

    def _empty_temporal_metrics(self):
        from ..models import Metrics

        return Metrics()

    def _sensor_config_from_capture(self, cap: CaptureRecord) -> SensorConfig:
        return SensorConfig(
            red=self._int_cap(cap, "cfg_red", 63),
            ir=self._int_cap(cap, "cfg_ir", 63),
            avg=self._int_cap(cap, "cfg_avg", 4),
            rate=self._int_cap(cap, "cfg_rate", 800),
            width=self._int_cap(cap, "cfg_width", 411),
            adc=self._int_cap(cap, "cfg_adc", 16384),
            skip=self._int_cap(cap, "cfg_skip", 50),
            debug=str(cap.value("cfg_debug")).strip().lower() in {"1", "true", "si", "yes"},
        ).clean()

    def _analysis_config_from_capture(self, cap: CaptureRecord) -> AnalysisConfig:
        cfg = AnalysisConfig()
        cfg.bpm_min = self._int_cap(cap, "analysis_bpm_min", cfg.bpm_min)
        cfg.bpm_max = self._int_cap(cap, "analysis_bpm_max", cfg.bpm_max)
        cfg.detrend_seconds = self._float_cap(cap, "analysis_detrend_seconds", cfg.detrend_seconds)
        cfg.smooth_seconds = self._float_cap(cap, "analysis_smooth_seconds", cfg.smooth_seconds)
        cfg.ignore_initial_seconds = self._float_cap(cap, "analysis_ignore_initial_seconds", cfg.ignore_initial_seconds)
        formula = cap.value("analysis_spo2_formula")
        if formula:
            cfg.spo2_formula = formula
        return cfg

    def _int_cap(self, cap: CaptureRecord, key: str, default: int) -> int:
        value = _as_float(cap.value(key))
        return int(value) if np.isfinite(value) else int(default)

    def _float_cap(self, cap: CaptureRecord, key: str, default: float) -> float:
        value = _as_float(cap.value(key))
        return float(value) if np.isfinite(value) else float(default)

    def _plot_temporal_segment_series(self, x: np.ndarray, y: np.ndarray, color: tuple[int, int, int], name: str):
        mask = np.isfinite(x) & np.isfinite(y)
        if np.any(mask):
            self.plot_temporal_signal.plot(x[mask], y[mask], pen=pg.mkPen(color, width=1 if name in {"IR", "RED"} else 2), name=name)

    def closeEvent(self, event: QtGui.QCloseEvent):
        event.accept()
