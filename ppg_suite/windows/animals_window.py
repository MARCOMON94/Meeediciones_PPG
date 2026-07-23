from __future__ import annotations

import csv
import json
import math
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from ..animal_config import (
    ANIMAL_OPTIONS,
    POSITION_SUMMARY_PREFIXES,
    TEMP_CHANNELS,
    animal_label,
    normalize_animal_type,
    normalize_position,
    parse_temp_mapping,
    positions_for_animal,
)
from ..io_utils import atomic_csv_dict_writer, atomic_write_json
from ..models import AnalysisConfig, SensorConfig
from ..paths import (
    ANIMAL_PHOTO_DIR,
    ANIMALS_DIR,
    CONFIG_DIR,
    FIGURES_DIR,
    PROCESSED_DIR,
    RAW_DIR,
    REPORT_DIR,
    SCREENSHOT_DIR,
    SESSION_DIR,
)
from ..processing import stable_bpm_segment
from ..trash import TrashBatch
from ..utils import fmt, sanitize_id
from .relations_window import (
    HEADER_TOOLTIPS,
    SELECTION_HEADER,
    _as_float,
    _base_from_row,
    _mean_ref_pulse,
    _mode_label,
    _read_csv,
    _strip_prefix,
)


UNASSIGNED_IDS = {"", "SIN_CROTAL", "-", "NONE", "NULL"}


@dataclass
class AnimalMeasurement:
    animal_key: str
    row: dict[str, str] = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)


@dataclass
class AnimalSelectionRecord:
    kind: str
    key: str
    path: Path | None = None
    animal_key: str = ""
    capture_key: str = ""


ANIMAL_HEADER_TOOLTIPS = {
    **HEADER_TOOLTIPS,
    "Nombre": "Alias o nombre visible guardado en la ficha del animal.",
    "Tomas": "Numero de tomas/raw asociados al animal.",
    "Última": "Fecha y hora de la ultima toma asociada al animal.",
    "Ultima": "Fecha y hora de la ultima toma asociada al animal.",
    "BPM estable medio": "Media historica del BPM estable de 5 segundos cuando existe una ventana fiable.",
    "Aviso recomendado": "Resumen del umbral recomendado a partir del historial del animal.",
}


def animal_key(animal_type: str, animal_id: str) -> str:
    clean_id = sanitize_id(str(animal_id or "").strip())
    if clean_id.upper() in UNASSIGNED_IDS:
        return ""
    return f"{normalize_animal_type(animal_type)}:{clean_id}"


def display_key_label(key: str) -> str:
    if ":" not in key:
        return key
    species, animal_id = key.split(":", 1)
    return f"{animal_label(species)} {animal_id}"


def safe_file_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "animal"


def _load_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a project CSV keeping the header order, so it can be rewritten in place."""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return [], []
    if not text.strip():
        return [], []
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    rows = [{str(k or "").strip(): str(v or "").strip() for k, v in row.items()} for row in reader]
    return list(reader.fieldnames or []), rows


def load_oriented_pixmap(path: Path) -> QtGui.QPixmap:
    """Load an image applying its EXIF orientation, so it matches how the
    file looks in Windows Explorer/Photos instead of the raw sensor pixels."""
    reader = QtGui.QImageReader(str(path))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        return QtGui.QPixmap()
    return QtGui.QPixmap.fromImage(image)


class AnimalPhotoCell(QtWidgets.QFrame):
    """Drag-and-droppable image slot used by BulkPhotoDialog's table."""

    def __init__(self, dialog: "BulkPhotoDialog", row_index: int):
        super().__init__()
        self.dialog = dialog
        self.row_index = row_index
        self.photo_path: Path | None = None
        self._drag_start: QtCore.QPoint | None = None
        self.setFrameShape(QtWidgets.QFrame.Shape.Box)
        self.setFixedSize(112, 92)
        self.setAcceptDrops(True)
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        self.thumb = QtWidgets.QLabel("Sin foto")
        self.thumb.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.thumb.setFixedSize(82, 60)
        self.thumb.setStyleSheet("background:#f0f2f4; color:#8a97a3; border:1px dashed #ccd3da;")
        self.name_label = QtWidgets.QLabel("")
        self.name_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.name_label.setStyleSheet("font-size: 8pt; color: #45505a;")
        self.name_label.setWordWrap(True)
        layout.addWidget(self.thumb)
        layout.addWidget(self.name_label)

    def set_photo(self, path: Path | None):
        self.photo_path = path
        if path and path.exists():
            pix = load_oriented_pixmap(path)
            if not pix.isNull():
                self.thumb.setPixmap(
                    pix.scaled(82, 60, QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation)
                )
                self.thumb.setText("")
            else:
                self.thumb.setPixmap(QtGui.QPixmap())
                self.thumb.setText("Invalida")
            self.name_label.setText(path.name)
        else:
            self.thumb.setPixmap(QtGui.QPixmap())
            self.thumb.setText("Sin foto")
            self.name_label.setText("")

    def mousePressEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self.photo_path:
            self._drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        if (
            self._drag_start is not None
            and self.photo_path
            and (event.position().toPoint() - self._drag_start).manhattanLength() >= QtWidgets.QApplication.startDragDistance()
        ):
            drag = QtGui.QDrag(self)
            mime = QtCore.QMimeData()
            mime.setText(str(self.row_index))
            drag.setMimeData(mime)
            if self.thumb.pixmap() is not None and not self.thumb.pixmap().isNull():
                drag.setPixmap(self.thumb.pixmap())
            drag.exec(QtCore.Qt.DropAction.MoveAction)
            self._drag_start = None
        super().mouseMoveEvent(event)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent):
        if event.mimeData().hasText() or event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QtGui.QDropEvent):
        mime = event.mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    self.dialog.assign_photo(self.row_index, Path(url.toLocalFile()))
                    break
            event.acceptProposedAction()
            return
        if mime.hasText():
            try:
                source_row = int(mime.text())
            except ValueError:
                return
            if source_row != self.row_index:
                self.dialog.swap_photos(source_row, self.row_index)
            event.acceptProposedAction()


class BulkPhotoDialog(QtWidgets.QDialog):
    """Additional window to assign one photo per checked animal, oldest-first."""

    def __init__(self, parent: QtWidgets.QWidget, rows: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("Fotos de animales seleccionados")
        self.resize(720, 420)
        self.rows = rows
        self.photo_cells: list[AnimalPhotoCell] = []
        self.assignments: dict[str, Path] = {}

        layout = QtWidgets.QVBoxLayout(self)
        info = QtWidgets.QLabel(
            f"{len(rows)} animal(es) seleccionado(s), ordenados de mas antiguo a mas nuevo (fecha de alta). "
            "Sube hasta el mismo numero de fotos: se emparejan por fecha (foto mas antigua con animal mas antiguo). "
            "Tambien puedes arrastrar una casilla de imagen sobre otra para intercambiarlas."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        controls = QtWidgets.QHBoxLayout()
        self.btn_upload = QtWidgets.QPushButton(f"Subir fotos (max {len(rows)})")
        self.btn_upload.clicked.connect(self.pick_photos)
        controls.addWidget(self.btn_upload)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.table = QtWidgets.QTableWidget(len(rows), 4)
        self.table.setHorizontalHeaderLabels(["Animal", "Crotal", "Hora y fecha de creación", "Imagen"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for row_index, row in enumerate(rows):
            self.table.setItem(row_index, 0, self._read_only_item(row["label"]))
            self.table.setItem(row_index, 1, self._read_only_item(row["crotal"]))
            self.table.setItem(row_index, 2, self._read_only_item(row["created_label"]))
            cell = AnimalPhotoCell(self, row_index)
            self.photo_cells.append(cell)
            self.table.setCellWidget(row_index, 3, cell)
            self.table.setRowHeight(row_index, 96)
        self.table.setColumnWidth(3, 120)
        layout.addWidget(self.table, stretch=1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _read_only_item(value: str) -> QtWidgets.QTableWidgetItem:
        item = QtWidgets.QTableWidgetItem(value)
        item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable)
        return item

    def pick_photos(self):
        limit = len(self.rows)
        paths_text, _filter = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Seleccionar fotos",
            str(Path.home()),
            "Imagenes (*.png *.jpg *.jpeg *.bmp *.webp);;Todos los archivos (*.*)",
        )
        if not paths_text:
            return
        candidates = [Path(p) for p in paths_text]
        if len(candidates) > limit:
            QtWidgets.QMessageBox.warning(
                self,
                "Fotos",
                f"Has seleccionado {len(candidates)} fotos pero solo hay {limit} animal(es) seleccionados.\n"
                f"Se usaran las {limit} mas antiguas segun su fecha de creación.",
            )
        candidates.sort(key=lambda p: p.stat().st_ctime if p.exists() else 0.0)
        for cell, path in zip(self.photo_cells, candidates[:limit]):
            cell.set_photo(path)

    def assign_photo(self, row_index: int, path: Path):
        self.photo_cells[row_index].set_photo(path)

    def swap_photos(self, row_a: int, row_b: int):
        cell_a = self.photo_cells[row_a]
        cell_b = self.photo_cells[row_b]
        path_a, path_b = cell_a.photo_path, cell_b.photo_path
        cell_a.set_photo(path_b)
        cell_b.set_photo(path_a)

    def on_save(self):
        self.assignments = {
            row["animal_key"]: cell.photo_path
            for row, cell in zip(self.rows, self.photo_cells)
            if cell.photo_path is not None
        }
        if not self.assignments:
            QtWidgets.QMessageBox.information(self, "Fotos", "No has asignado ninguna foto.")
            return
        self.accept()


class AnimalsWindow(QtWidgets.QMainWindow):
    back_to_menu = QtCore.pyqtSignal()

    animal_headers = [SELECTION_HEADER, "Animal", "Nombre", "Especie", "Tomas", "Última", "BPM estable medio", "Aviso recomendado"]
    history_headers = [
        SELECTION_HEADER, "Animal", "Especie",
        "Temp RT final", "Temp LT final", "Temp FLT final", "Temp FRT final", "Temp RLT final", "Temp RRT final",
        "Pulso ref.", "BPM medio", "BPM estable", "Tramo estable", "Pulso final pulsio", "Pulso final fonendo",
        "Modo", "Sensor", "Termómetros",
        "Oxígeno medio", "Calidad", "Contacto", "Estado",
        "Dif. BPM-ref", "Medición", "Configuración",
        "Temp manual RT", "Temp manual LT", "Temp manual FLT", "Temp manual FRT", "Temp manual RLT", "Temp manual RRT",
        "Fecha", "Hora", "Duración", "Hz", "Muestras", "Raw",
    ]
    history_two_temp_headers = ["Temp RT final", "Temp LT final", "Temp manual RT", "Temp manual LT"]
    history_cow_temp_headers = [
        "Temp FLT final", "Temp FRT final", "Temp RLT final", "Temp RRT final",
        "Temp manual FLT", "Temp manual FRT", "Temp manual RLT", "Temp manual RRT",
    ]
    history_column_groups = [
        ("animal", "Animal", ["Animal", "Especie"]),
        ("sample", "Muestra", ["Modo", "Sensor", "Termómetros", "Medición", "Configuración", "Fecha", "Hora"]),
        ("quality", "Calidad", ["Calidad", "Contacto", "Estado"]),
        ("pulse", "Pulsaciones", ["Pulso ref.", "BPM medio", "BPM estable", "Tramo estable", "Pulso final pulsio", "Pulso final fonendo", "Dif. BPM-ref"]),
        ("temperature", "Temperatura", [
            "Temp RT final", "Temp LT final", "Temp FLT final", "Temp FRT final", "Temp RLT final", "Temp RRT final",
            "Temp manual RT", "Temp manual LT", "Temp manual FLT", "Temp manual FRT", "Temp manual RLT", "Temp manual RRT",
        ]),
        ("spo2", "SpO2", ["Oxígeno medio"]),
    ]
    file_headers = [SELECTION_HEADER, "Fecha", "tipo", "archivo", "ruta"]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PPG Suite v8 | Animales")
        self.resize(1380, 860)
        self.profiles: dict[str, dict] = {}
        self.measurements_by_animal: dict[str, list[AnimalMeasurement]] = {}
        self.current_key = ""
        self.pending_photo_source: Path | None = None
        self.selected_items: dict[str, AnimalSelectionRecord] = {}
        self.history_column_group_state = {
            key: key in {"animal", "pulse"} for key, _label, _headers in self.history_column_groups
        }
        self.history_column_buttons: dict[str, QtWidgets.QToolButton] = {}
        self.stable_bpm_cache: dict[str, dict[str, float | int | str]] = {}
        self._loading_form = False
        self._updating_tables = False
        self._build_ui()
        self.update_selection_status()
        self.reload_data()

    @property
    def data_file(self) -> Path:
        return ANIMALS_DIR / "animals.json"

    def configure_table(self, table: QtWidgets.QTableWidget, headers: list[str], *, sortable: bool = True):
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        for col, header in enumerate(headers):
            item = table.horizontalHeaderItem(col) or QtWidgets.QTableWidgetItem(header)
            if header == SELECTION_HEADER:
                item.setText("")
            item.setToolTip(ANIMAL_HEADER_TOOLTIPS.get(header, header))
            table.setHorizontalHeaderItem(col, item)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSortingEnabled(sortable)
        header = table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Interactive)
        if headers and headers[0] == SELECTION_HEADER:
            header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(0, 34)

    def make_selection_item(
        self,
        *,
        kind: str,
        key: str,
        tooltip: str,
        path: Path | None = None,
        animal_key_value: str = "",
        capture_key: str = "",
    ) -> QtWidgets.QTableWidgetItem:
        item = QtWidgets.QTableWidgetItem("")
        item.setFlags(
            QtCore.Qt.ItemFlag.ItemIsEnabled
            | QtCore.Qt.ItemFlag.ItemIsSelectable
            | QtCore.Qt.ItemFlag.ItemIsUserCheckable
        )
        item.setCheckState(QtCore.Qt.CheckState.Checked if key in self.selected_items else QtCore.Qt.CheckState.Unchecked)
        item.setToolTip(tooltip)
        item.setData(QtCore.Qt.ItemDataRole.UserRole, key)
        item.setData(QtCore.Qt.ItemDataRole.UserRole.value + 1, kind)
        item.setData(QtCore.Qt.ItemDataRole.UserRole.value + 2, str(path) if path else "")
        item.setData(QtCore.Qt.ItemDataRole.UserRole.value + 3, animal_key_value)
        item.setData(QtCore.Qt.ItemDataRole.UserRole.value + 4, capture_key)
        return item

    def table_item(self, value: object, tooltip: str | None = None) -> QtWidgets.QTableWidgetItem:
        text = str(value if value is not None else "")
        item = QtWidgets.QTableWidgetItem(text)
        item.setToolTip(tooltip if tooltip is not None else text)
        return item

    def on_selection_item_changed(self, item: QtWidgets.QTableWidgetItem):
        if self._updating_tables or item.column() != 0:
            return
        key = str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "")
        kind = str(item.data(QtCore.Qt.ItemDataRole.UserRole.value + 1) or "")
        if not key or not kind:
            return
        path_text = str(item.data(QtCore.Qt.ItemDataRole.UserRole.value + 2) or "")
        record = AnimalSelectionRecord(
            kind=kind,
            key=key,
            path=Path(path_text) if path_text else None,
            animal_key=str(item.data(QtCore.Qt.ItemDataRole.UserRole.value + 3) or ""),
            capture_key=str(item.data(QtCore.Qt.ItemDataRole.UserRole.value + 4) or ""),
        )
        if item.checkState() == QtCore.Qt.CheckState.Checked:
            self.selected_items[key] = record
        else:
            self.selected_items.pop(key, None)
        self.update_selection_status()

    def update_selection_status(self):
        count = len(self.selected_items)
        status = self.__dict__.get("selection_status")
        if status is not None:
            status.setText(f"{count} elemento{'s' if count != 1 else ''} seleccionado{'s' if count != 1 else ''}")

    def clear_selection(self):
        if not self.selected_items:
            return
        self.selected_items.clear()
        self.update_selection_status()
        self.populate_animal_list()
        self.populate_history()
        self.populate_files()

    def set_history_column_group(self, group_key: str, checked: bool):
        if group_key not in self.history_column_group_state:
            return
        self.history_column_group_state[group_key] = checked
        button = self.history_column_buttons.get(group_key)
        if button is not None:
            button.setArrowType(QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow)
        self.apply_history_column_visibility()

    def visible_history_headers(self) -> list[str]:
        visible = [SELECTION_HEADER]
        rows = [measurement.row for measurement in self.current_measurements()]
        for key, _label, headers in self.history_column_groups:
            if not self.history_column_group_state.get(key, False):
                continue
            group_headers = list(headers)
            if key == "temperature":
                group_headers = self.history_temperature_headers(group_headers, rows)
            for header in group_headers:
                if header in self.history_headers and header not in visible:
                    visible.append(header)
        return visible

    def history_temperature_headers(self, headers: list[str], rows: list[dict[str, str]]) -> list[str]:
        if not rows:
            return [header for header in headers if header in self.history_two_temp_headers]
        has_cow = any(normalize_animal_type(row.get("animal_type", "")) == "vaca" for row in rows)
        has_two = any(normalize_animal_type(row.get("animal_type", "")) != "vaca" for row in rows)
        wanted: list[str] = []
        if has_two:
            wanted.extend(self.history_two_temp_headers)
        if has_cow:
            wanted.extend(self.history_cow_temp_headers)
        return [header for header in headers if header in wanted]

    def apply_history_column_visibility(self):
        if not hasattr(self, "history_table"):
            return
        visible = set(self.visible_history_headers())
        for col, header in enumerate(self.history_headers):
            self.history_table.setColumnHidden(col, header not in visible)
        self.history_table.resizeColumnsToContents()
        self.history_table.setColumnWidth(0, 34)

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)

        top = QtWidgets.QHBoxLayout()
        root.addLayout(top)
        self.btn_back = QtWidgets.QPushButton("Volver al menu inicial")
        self.btn_back.setMinimumHeight(42)
        self.btn_back.clicked.connect(self.back_to_menu.emit)
        top.addWidget(self.btn_back)
        title = QtWidgets.QLabel("Animales")
        title.setStyleSheet("font-size: 14pt; font-weight: bold;")
        top.addWidget(title)
        top.addStretch(1)
        self.selection_status = QtWidgets.QLabel("0 elementos seleccionados")
        self.btn_prepare_mail = QtWidgets.QPushButton("Preparar correo")
        self.btn_delete = QtWidgets.QPushButton("Eliminar")
        self.btn_clear_selection = QtWidgets.QPushButton("Limpiar selección")
        self.btn_reload = QtWidgets.QPushButton("Recargar datos")
        for button in (self.btn_prepare_mail, self.btn_delete, self.btn_clear_selection, self.btn_reload):
            button.setMinimumHeight(42)
        top.addWidget(self.selection_status)
        top.addWidget(self.btn_prepare_mail)
        top.addWidget(self.btn_delete)
        top.addWidget(self.btn_clear_selection)
        self.btn_reload.clicked.connect(self.reload_data)
        self.btn_prepare_mail.clicked.connect(self.prepare_mail_zip)
        self.btn_delete.clicked.connect(self.delete_checked_items)
        self.btn_clear_selection.clicked.connect(self.clear_selection)
        top.addWidget(self.btn_reload)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        root.addWidget(splitter, stretch=1)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Buscar por crotal, nombre o especie")
        self.search_edit.textChanged.connect(self.populate_animal_list)
        left_layout.addWidget(self.search_edit)
        buttons = QtWidgets.QHBoxLayout()
        self.btn_new = QtWidgets.QPushButton("Nuevo")
        self.btn_save = QtWidgets.QPushButton("Guardar ficha")
        self.btn_photos = QtWidgets.QPushButton("Fotos")
        self.btn_photos.setToolTip(
            "Asigna una foto a cada animal marcado en la tabla (checkbox), de mas antiguo a mas nuevo."
        )
        buttons.addWidget(self.btn_new)
        buttons.addWidget(self.btn_save)
        buttons.addWidget(self.btn_photos)
        left_layout.addLayout(buttons)
        self.btn_new.clicked.connect(self.new_animal)
        self.btn_save.clicked.connect(self.save_current_profile)
        self.btn_photos.clicked.connect(self.open_bulk_photo_dialog)

        self.animals_table = QtWidgets.QTableWidget(0, len(self.animal_headers))
        self.configure_table(self.animals_table, self.animal_headers)
        self.animals_table.currentCellChanged.connect(self.select_animal_from_table)
        self.animals_table.itemChanged.connect(self.on_selection_item_changed)
        left_layout.addWidget(self.animals_table, stretch=1)
        splitter.addWidget(left)

        self.tabs = QtWidgets.QTabWidget()
        splitter.addWidget(self.tabs)
        splitter.setSizes([360, 1020])

        self._build_profile_tab()
        self._build_notes_tab()
        self._build_history_tab()
        self._build_graph_tab()
        self._build_files_tab()

    def _build_profile_tab(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(page)

        form_panel = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(form_panel)
        self.id_edit = QtWidgets.QLineEdit()
        self.species_combo = QtWidgets.QComboBox()
        for label, value in ANIMAL_OPTIONS:
            self.species_combo.addItem(label, value)
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("Alias o nombre visible")
        self.baseline_temp_delta = QtWidgets.QDoubleSpinBox()
        self.baseline_temp_delta.setRange(0.1, 10.0)
        self.baseline_temp_delta.setDecimals(1)
        self.baseline_temp_delta.setValue(1.0)
        self.baseline_temp_delta.setSuffix(" C")
        self.baseline_bpm_delta = QtWidgets.QDoubleSpinBox()
        self.baseline_bpm_delta.setRange(1.0, 80.0)
        self.baseline_bpm_delta.setDecimals(0)
        self.baseline_bpm_delta.setValue(15.0)
        self.baseline_bpm_delta.setSuffix(" BPM")
        self.baseline_min_records = QtWidgets.QSpinBox()
        self.baseline_min_records.setRange(1, 100)
        self.baseline_min_records.setValue(5)
        self.baseline_enabled = QtWidgets.QCheckBox("Activar avisos para este animal")
        self.baseline_enabled.toggled.connect(self.update_baseline_controls_enabled)
        self.btn_save_alerts = QtWidgets.QPushButton("Guardar avisos")
        self.btn_save_alerts.clicked.connect(self.save_alert_settings)
        form.addRow("Crotal / ID:", self.id_edit)
        form.addRow("Especie:", self.species_combo)
        form.addRow("Nombre:", self.name_edit)
        form.addRow("Avisos:", self.baseline_enabled)
        form.addRow("Aviso temp futura:", self.baseline_temp_delta)
        form.addRow("Aviso BPM futuro:", self.baseline_bpm_delta)
        form.addRow("Min. tomas basal:", self.baseline_min_records)
        form.addRow("", self.btn_save_alerts)
        self.btn_apply_recommended_alerts = QtWidgets.QPushButton("Aplicar recomendación")
        self.btn_apply_recommended_alerts.clicked.connect(self.apply_recommended_alerts)
        form.addRow("", self.btn_apply_recommended_alerts)
        self.summary_text = QtWidgets.QTextEdit()
        self.summary_text.setReadOnly(True)
        form.addRow("Resumen:", self.summary_text)
        self.recommended_alerts_text = QtWidgets.QTextEdit()
        self.recommended_alerts_text.setReadOnly(True)
        self.recommended_alerts_text.setMinimumHeight(120)
        form.addRow("Aviso recomendado:", self.recommended_alerts_text)
        self.update_baseline_controls_enabled(False)
        layout.addWidget(form_panel, stretch=2)

        photo_panel = QtWidgets.QWidget()
        photo_layout = QtWidgets.QVBoxLayout(photo_panel)
        self.photo_label = QtWidgets.QLabel("Sin foto")
        self.photo_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.photo_label.setMinimumSize(280, 260)
        self.photo_label.setStyleSheet("border: 1px solid #ccd3da; background: #f7f9fb; color: #586673;")
        self.btn_pick_photo = QtWidgets.QPushButton("Seleccionar foto")
        self.btn_pick_photo.clicked.connect(self.pick_photo)
        self.btn_save_photo = QtWidgets.QPushButton("Guardar foto")
        self.btn_save_photo.clicked.connect(self.save_photo)
        self.btn_save_photo.setEnabled(False)
        photo_layout.addWidget(self.photo_label, stretch=1)
        photo_layout.addWidget(self.btn_pick_photo)
        photo_layout.addWidget(self.btn_save_photo)
        layout.addWidget(photo_panel, stretch=1)
        self.tabs.addTab(page, "Ficha")

    def _build_notes_tab(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        controls = QtWidgets.QHBoxLayout()
        self.note_date = QtWidgets.QDateTimeEdit(QtCore.QDateTime.currentDateTime())
        self.note_date.setCalendarPopup(True)
        self.note_date.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.btn_add_note = QtWidgets.QPushButton("Anadir nota")
        self.btn_delete_note = QtWidgets.QPushButton("Eliminar nota")
        controls.addWidget(self.note_date)
        controls.addWidget(self.btn_add_note)
        controls.addWidget(self.btn_delete_note)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.note_text = QtWidgets.QPlainTextEdit()
        self.note_text.setPlaceholderText("Ej.: varios abortos, tratamiento, cambio de lote, observacion de campo...")
        layout.addWidget(self.note_text, stretch=1)
        self.notes_table = QtWidgets.QTableWidget(0, 2)
        self.configure_table(self.notes_table, ["Fecha", "Nota"], sortable=False)
        layout.addWidget(self.notes_table, stretch=2)
        self.btn_add_note.clicked.connect(self.add_note)
        self.btn_delete_note.clicked.connect(self.delete_selected_note)
        self.tabs.addTab(page, "Anotaciones")

    def _build_history_tab(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        column_controls = QtWidgets.QHBoxLayout()
        column_controls.addWidget(QtWidgets.QLabel("Columnas"))
        for key, label, _headers in self.history_column_groups:
            button = QtWidgets.QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setChecked(self.history_column_group_state.get(key, False))
            button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setArrowType(
                QtCore.Qt.ArrowType.DownArrow
                if self.history_column_group_state.get(key, False)
                else QtCore.Qt.ArrowType.RightArrow
            )
            button.toggled.connect(lambda checked, group_key=key: self.set_history_column_group(group_key, checked))
            self.history_column_buttons[key] = button
            column_controls.addWidget(button)
        column_controls.addStretch(1)
        layout.addLayout(column_controls)
        self.history_table = QtWidgets.QTableWidget(0, len(self.history_headers))
        self.configure_table(self.history_table, self.history_headers)
        self.history_table.doubleClicked.connect(self.open_history_raw)
        self.history_table.itemChanged.connect(self.on_selection_item_changed)
        layout.addWidget(self.history_table)
        self.tabs.addTab(page, "Historial")

    def _build_graph_tab(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        controls = QtWidgets.QHBoxLayout()
        self.chk_bpm = QtWidgets.QCheckBox("BPM")
        self.chk_bpm.setChecked(True)
        self.chk_temp = QtWidgets.QCheckBox("Temperatura")
        self.chk_temp.setChecked(True)
        self.chk_spo2 = QtWidgets.QCheckBox("SpO2")
        self.chk_quality = QtWidgets.QCheckBox("Calidad")
        for chk in (self.chk_bpm, self.chk_temp, self.chk_spo2, self.chk_quality):
            chk.toggled.connect(self.update_graph)
            controls.addWidget(chk)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.plot = pg.PlotWidget(title="Evolucion del animal")
        self.plot.setBackground("w")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "Toma")
        layout.addWidget(self.plot)
        self.tabs.addTab(page, "Graficas")

    def _build_files_tab(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        self.files_table = QtWidgets.QTableWidget(0, len(self.file_headers))
        self.configure_table(self.files_table, self.file_headers)
        self.files_table.doubleClicked.connect(self.open_selected_file)
        self.files_table.itemChanged.connect(self.on_selection_item_changed)
        layout.addWidget(self.files_table)
        self.tabs.addTab(page, "Archivos")

    def reload_data(self):
        self.profiles = self.load_profiles()
        self.measurements_by_animal = self.discover_measurements()
        self.populate_animal_list()
        if self.current_key and self.current_key in self.all_animal_keys():
            self.select_animal(self.current_key)
        elif self.animals_table.rowCount() > 0:
            self.animals_table.selectRow(0)
        else:
            self.new_animal()

    def load_profiles(self) -> dict[str, dict]:
        try:
            data = json.loads(self.data_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if isinstance(data, dict) and isinstance(data.get("animals"), dict):
            data = data["animals"]
        return data if isinstance(data, dict) else {}

    def save_profiles(self):
        ANIMALS_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"animals": self.profiles, "updated": datetime.now().isoformat()}
        atomic_write_json(self.data_file, payload)

    def all_animal_keys(self) -> list[str]:
        keys = set(self.profiles) | set(self.measurements_by_animal)
        return sorted(keys, key=lambda key: (self.last_measurement_stamp(key), display_key_label(key)), reverse=True)

    def populate_animal_list(self):
        text = self.search_edit.text().strip().lower()
        current = self.current_key
        self._updating_tables = True
        self.animals_table.setSortingEnabled(False)
        self.animals_table.setRowCount(0)
        try:
            for key in self.all_animal_keys():
                profile = self.profiles.get(key, {})
                label = display_key_label(key)
                name = str(profile.get("display_name") or "")
                haystack = f"{key} {label} {name}".lower()
                if text and text not in haystack:
                    continue
                measurements = self.measurements_by_animal.get(key, [])
                recommendation = self.recommended_alerts_for_measurements(key, measurements)
                row = self.animals_table.rowCount()
                self.animals_table.insertRow(row)
                selection_key = self.selection_key("animal", key)
                self.animals_table.setItem(
                    row,
                    0,
                    self.make_selection_item(
                        kind="animal",
                        key=selection_key,
                        tooltip="Seleccionar animal completo para correo o eliminación trazable",
                        animal_key_value=key,
                    ),
                )
                values = [
                    label,
                    name,
                    animal_label(profile.get("animal_type") or key.split(":", 1)[0]),
                    str(len(measurements)),
                    self.last_measurement_stamp(key),
                    fmt(recommendation["bpm_mean"], 1, ""),
                    str(recommendation["summary"]),
                ]
                for offset, value in enumerate(values, start=1):
                    item = self.table_item(value)
                    item.setData(QtCore.Qt.ItemDataRole.UserRole, key)
                    self.animals_table.setItem(row, offset, item)
                if key == current:
                    self.animals_table.selectRow(row)
        finally:
            self._updating_tables = False
            self.animals_table.setSortingEnabled(True)
        self.animals_table.resizeColumnsToContents()
        self.animals_table.setColumnWidth(0, 34)

    def select_animal_from_table(self, current_row: int, _current_col: int, _prev_row: int, _prev_col: int):
        if self._loading_form or self._updating_tables or current_row < 0:
            return
        item = self.animals_table.item(current_row, 0)
        key = item.data(QtCore.Qt.ItemDataRole.UserRole.value + 3) if item else ""
        if not key:
            label_item = self.animals_table.item(current_row, 1)
            key = label_item.data(QtCore.Qt.ItemDataRole.UserRole) if label_item else ""
        if key:
            self.select_animal(str(key))

    def select_animal(self, key: str):
        self.current_key = key
        self.pending_photo_source = None
        profile = self.profile_for_key(key)
        species, animal_id = key.split(":", 1) if ":" in key else ("oveja", key)
        self._loading_form = True
        try:
            self.id_edit.setText(str(profile.get("id") or animal_id))
            self.set_species_combo(str(profile.get("animal_type") or species))
            self.name_edit.setText(str(profile.get("display_name") or ""))
            baseline = profile.get("baseline_settings") or {}
            self.baseline_enabled.setChecked(bool(baseline.get("enabled", False)))
            self.baseline_temp_delta.setValue(float(baseline.get("temp_delta_c", 1.0) or 1.0))
            self.baseline_bpm_delta.setValue(float(baseline.get("bpm_delta", 15.0) or 15.0))
            self.baseline_min_records.setValue(int(baseline.get("min_records", 5) or 5))
            self.update_baseline_controls_enabled(self.baseline_enabled.isChecked())
        finally:
            self._loading_form = False
        self.update_photo(profile)
        self.update_summary()
        self.update_recommended_alerts()
        self.populate_notes()
        self.populate_history()
        self.populate_files()
        self.update_graph()

    def profile_for_key(self, key: str) -> dict:
        profile = dict(self.profiles.get(key, {}))
        if ":" in key:
            species, animal_id = key.split(":", 1)
            profile.setdefault("animal_key", key)
            profile.setdefault("animal_type", species)
            profile.setdefault("id", animal_id)
        profile.setdefault("notes", [])
        profile.setdefault("baseline_settings", {"enabled": False, "temp_delta_c": 1.0, "bpm_delta": 15.0, "min_records": 5})
        return profile

    def set_species_combo(self, animal_type: str):
        wanted = normalize_animal_type(animal_type)
        for i in range(self.species_combo.count()):
            if self.species_combo.itemData(i) == wanted:
                self.species_combo.setCurrentIndex(i)
                return

    def new_animal(self):
        self.current_key = ""
        self.pending_photo_source = None
        self._loading_form = True
        try:
            self.id_edit.clear()
            self.set_species_combo("oveja")
            self.name_edit.clear()
            self.baseline_enabled.setChecked(False)
            self.baseline_temp_delta.setValue(1.0)
            self.baseline_bpm_delta.setValue(15.0)
            self.baseline_min_records.setValue(5)
            self.update_baseline_controls_enabled(False)
            self.note_text.clear()
        finally:
            self._loading_form = False
        self.photo_label.setText("Sin foto")
        self.photo_label.setPixmap(QtGui.QPixmap())
        self.btn_save_photo.setEnabled(False)
        self.summary_text.clear()
        self.recommended_alerts_text.clear()
        self.notes_table.setRowCount(0)
        self.history_table.setRowCount(0)
        self.files_table.setRowCount(0)
        self.plot.clear()

    def current_form_key(self) -> str:
        return animal_key(str(self.species_combo.currentData() or ""), self.id_edit.text())

    def update_baseline_controls_enabled(self, enabled: bool):
        for widget in (self.baseline_temp_delta, self.baseline_bpm_delta, self.baseline_min_records):
            widget.setEnabled(bool(enabled))

    def _rewrite_capture_file_ids(self, path: Path, base: str, new_id: str, new_type: str, *, match_base: bool) -> bool:
        """Rewrite the id/animal_type columns of a capture CSV so it re-associates with the new crotal.

        `match_base` is True for session_*.csv, which can hold rows for several
        animals in one file: only the row matching this capture's base_name is
        touched. Per-capture files (raw/processed/blocks) hold one animal per
        file, so every row is updated.
        """
        fieldnames, rows = _load_csv_rows(path)
        if not fieldnames or not rows:
            return False
        changed = False
        for row in rows:
            if match_base and _base_from_row(row) != base:
                continue
            if "id" in row and row.get("id") != new_id:
                row["id"] = new_id
                changed = True
            if new_type and "animal_type" in row and row.get("animal_type") != new_type:
                row["animal_type"] = new_type
                changed = True
        if not changed:
            return False
        with atomic_csv_dict_writer(path, fieldnames) as writer:
            writer.writeheader()
            writer.writerows(rows)
        return True

    def reassign_measurements(self, old_key: str, new_type: str, new_id: str) -> int:
        """Re-point every raw/session/summary file for `old_key` to the new crotal.

        Without this, editing the crotal in the animal form only moved the
        profile card (photo/notes/alerts); the historical raw/session/summary
        files still carried the old id, so the animal's measurement history
        silently detached from it on the next reload.
        """
        moved = 0
        for measurement in self.measurements_by_animal.get(old_key, []):
            base = _base_from_row(measurement.row)
            files = measurement.files
            summary_path = files.get("summary")
            if summary_path and summary_path.exists():
                try:
                    data = json.loads(summary_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    data = None
                if isinstance(data, dict):
                    data_changed = False
                    if data.get("id") != new_id:
                        data["id"] = new_id
                        data_changed = True
                    if new_type and data.get("animal_type") != new_type:
                        data["animal_type"] = new_type
                        data_changed = True
                    if data_changed:
                        atomic_write_json(summary_path, data)
            for kind in ("raw", "processed", "blocks"):
                path = files.get(kind)
                if path and path.exists():
                    self._rewrite_capture_file_ids(path, base, new_id, new_type, match_base=False)
            session_path = files.get("session")
            if session_path and session_path.exists():
                self._rewrite_capture_file_ids(session_path, base, new_id, new_type, match_base=True)
            moved += 1
        return moved

    def _merge_profile_data(self, primary: dict, secondary: dict) -> dict:
        """Combine two profile dicts when an animal is renamed onto an existing crotal.

        `primary` wins field-by-field conflicts (it's the more relevant/recent
        record); `secondary` only fills gaps so photos/notes/alerts from the
        old record are never silently dropped. Notes from both are unioned.
        """
        merged = dict(primary)
        notes = list(primary.get("notes") or []) + list(secondary.get("notes") or [])
        seen: set[tuple[str, str]] = set()
        deduped: list[dict] = []
        for note in notes:
            marker = (str(note.get("date", "")), str(note.get("text", "")))
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(note)
        deduped.sort(key=lambda row: row.get("date", ""), reverse=True)
        merged["notes"] = deduped
        if not merged.get("photo_path") and secondary.get("photo_path"):
            merged["photo_path"] = secondary["photo_path"]
        primary_baseline = primary.get("baseline_settings") or {}
        secondary_baseline = secondary.get("baseline_settings") or {}
        if not primary_baseline.get("enabled") and secondary_baseline.get("enabled"):
            merged["baseline_settings"] = secondary_baseline
        created_values = [v for v in (primary.get("created"), secondary.get("created")) if v]
        if created_values:
            merged["created"] = min(created_values)
        return merged

    def save_current_profile(self) -> str:
        key = self.current_form_key()
        if not key:
            QtWidgets.QMessageBox.warning(self, "Animales", "Introduce un crotal/ID real. SIN_CROTAL se mantiene como grupo no asignado.")
            return ""
        now = datetime.now().isoformat()
        old_key = self.current_key if self.current_key in self.profiles else ""
        renaming = bool(old_key and old_key != key)
        duplicate_merge = renaming and key in self.profiles

        if duplicate_merge:
            pending_count = len(self.measurements_by_animal.get(old_key, []))
            reply = QtWidgets.QMessageBox.question(
                self,
                "Animales",
                f"El crotal '{display_key_label(key)}' ya existe como otro animal.\n\n"
                f"Se combinarán las {pending_count} toma(s), fotos, notas y avisos de ambos registros "
                "bajo este crotal. Esta acción no se puede deshacer. ¿Continuar?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if reply != QtWidgets.QMessageBox.StandardButton.Yes:
                return ""

        existing = self.profile_for_key(key)
        base = self._merge_profile_data(existing, self.profile_for_key(old_key)) if renaming else existing
        profile = {
            **base,
            "animal_key": key,
            "id": sanitize_id(self.id_edit.text()),
            "animal_type": normalize_animal_type(str(self.species_combo.currentData() or "")),
            "display_name": self.name_edit.text().strip(),
            "baseline_settings": base.get("baseline_settings") or {"enabled": False, "temp_delta_c": 1.0, "bpm_delta": 15.0, "min_records": 5},
            "created": base.get("created") or now,
            "updated": now,
        }
        if self.pending_photo_source:
            profile["photo_path"] = str(self.copy_photo(self.pending_photo_source, key))
            self.pending_photo_source = None
            self.btn_save_photo.setEnabled(False)
        if old_key and old_key != key:
            self.profiles.pop(old_key, None)
        self.profiles[key] = profile
        self.current_key = key
        self.save_profiles()

        moved = 0
        if renaming:
            moved = self.reassign_measurements(old_key, profile["animal_type"], profile["id"])

        self.reload_data()
        self.select_animal(key)

        if renaming and (moved or duplicate_merge):
            message = f"Se han movido {moved} toma(s) de '{display_key_label(old_key)}' a '{display_key_label(key)}'."
            if duplicate_merge:
                message += "\nLos datos de ambos animales se han combinado bajo este crotal."
            QtWidgets.QMessageBox.information(self, "Animales", message)
        return key

    def save_alert_settings(self):
        enabled = bool(self.baseline_enabled.isChecked())
        key = self.current_form_key()
        if not key:
            QtWidgets.QMessageBox.warning(self, "Animales", "Guarda primero un crotal/ID real para activar avisos.")
            return
        now = datetime.now().isoformat()
        existing = self.profile_for_key(key)
        recommendation = self.recommended_alerts_for_measurements(
            key,
            self.measurements_by_animal.get(key, []),
            recompute_stable=True,
        )
        profile = {
            **existing,
            "animal_key": key,
            "id": sanitize_id(self.id_edit.text()),
            "animal_type": normalize_animal_type(str(self.species_combo.currentData() or "")),
            "display_name": self.name_edit.text().strip(),
            "baseline_settings": {
                "enabled": enabled,
                "temp_delta_c": float(self.baseline_temp_delta.value()),
                "bpm_delta": float(self.baseline_bpm_delta.value()),
                "min_records": int(self.baseline_min_records.value()),
                "bpm_baseline_5s": recommendation.get("bpm_mean"),
                "bpm_baseline_count": recommendation.get("bpm_count"),
                "bpm_recommended_limit": recommendation.get("bpm_limit"),
                "temp_baselines_c": recommendation.get("temp_by_position"),
                "temp_recommended_delta_c": recommendation.get("temp_delta"),
                "recommendation_summary": recommendation.get("summary"),
                "updated": now,
            },
            "created": existing.get("created") or now,
            "updated": now,
        }
        self.profiles[key] = profile
        self.current_key = key
        self.save_profiles()
        self.reload_data()
        self.select_animal(key)

    def pick_photo(self):
        path_text, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Seleccionar foto del animal",
            str(Path.home()),
            "Imagenes (*.png *.jpg *.jpeg *.bmp *.webp);;Todos los archivos (*.*)",
        )
        if not path_text:
            return
        self.pending_photo_source = Path(path_text)
        self.update_photo({"photo_path": str(self.pending_photo_source)})
        self.btn_save_photo.setEnabled(True)

    def save_photo(self):
        if not self.pending_photo_source:
            QtWidgets.QMessageBox.information(self, "Foto", "Selecciona primero una foto.")
            return
        key = self.current_form_key()
        if not key:
            QtWidgets.QMessageBox.warning(self, "Foto", "Guarda primero un crotal/ID real para asociar la foto.")
            return
        existing = self.profile_for_key(key)
        profile = {
            **existing,
            "animal_key": key,
            "id": sanitize_id(self.id_edit.text()),
            "animal_type": normalize_animal_type(str(self.species_combo.currentData() or "")),
            "display_name": self.name_edit.text().strip(),
            "photo_path": str(self.copy_photo(self.pending_photo_source, key)),
            "created": existing.get("created") or datetime.now().isoformat(),
            "updated": datetime.now().isoformat(),
        }
        self.profiles[key] = profile
        self.current_key = key
        self.pending_photo_source = None
        self.btn_save_photo.setEnabled(False)
        self.save_profiles()
        self.reload_data()
        self.select_animal(key)
        QtWidgets.QMessageBox.information(self, "Foto", "Foto guardada en la ficha del animal.")

    def copy_photo(self, source: Path, key: str) -> Path:
        ANIMAL_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
        suffix = source.suffix.lower() if source.suffix else ".jpg"
        target = ANIMAL_PHOTO_DIR / f"{safe_file_part(key)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}"
        shutil.copy2(source, target)
        return target

    @staticmethod
    def format_created_stamp(value: object) -> str:
        text = str(value or "")
        if not text:
            return ""
        try:
            return datetime.fromisoformat(text).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return text

    def open_bulk_photo_dialog(self):
        seen: dict[str, None] = {}
        for record in self.selected_items.values():
            if record.kind == "animal" and record.animal_key:
                seen.setdefault(record.animal_key, None)
        if not seen:
            QtWidgets.QMessageBox.information(
                self, "Fotos", "Marca la casilla de al menos un animal en la tabla para asignarle una foto."
            )
            return
        ordered_keys = sorted(seen, key=lambda key: self.registration_stamp(key) or "￿")
        rows = []
        for key in ordered_keys:
            profile = self.profile_for_key(key)
            name = str(profile.get("display_name") or "")
            label = display_key_label(key)
            rows.append(
                {
                    "animal_key": key,
                    "label": f"{label} ({name})" if name else label,
                    "crotal": str(profile.get("id") or key.split(":", 1)[-1]),
                    "created_label": self.format_created_stamp(self.registration_stamp(key)),
                }
            )
        dialog = BulkPhotoDialog(self, rows)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        now = datetime.now().isoformat()
        for key, source_path in dialog.assignments.items():
            existing = self.profile_for_key(key)
            profile = {
                **existing,
                "animal_key": key,
                "photo_path": str(self.copy_photo(source_path, key)),
                "created": existing.get("created") or now,
                "updated": now,
            }
            self.profiles[key] = profile
        self.save_profiles()
        self.reload_data()
        QtWidgets.QMessageBox.information(self, "Fotos", f"Se han guardado {len(dialog.assignments)} foto(s).")

    def update_photo(self, profile: dict):
        self.photo_label.setPixmap(QtGui.QPixmap())
        path_text = str(profile.get("photo_path") or "")
        path = Path(path_text) if path_text else None
        if not path or not path.exists():
            self.photo_label.setText("Sin foto")
            return
        pix = load_oriented_pixmap(path)
        if pix.isNull():
            self.photo_label.setText("No se pudo cargar la foto")
            return
        self.photo_label.setText("")
        self.photo_label.setPixmap(pix.scaled(self.photo_label.size(), QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event: QtGui.QResizeEvent):
        super().resizeEvent(event)
        if self.current_key:
            self.update_photo(self.profile_for_key(self.current_key))

    def current_measurements(self) -> list[AnimalMeasurement]:
        return self.measurements_by_animal.get(self.current_key, [])

    def add_note(self):
        key = self.current_key or self.save_current_profile()
        if not key:
            return
        text = self.note_text.toPlainText().strip()
        if not text:
            return
        profile = self.profile_for_key(key)
        notes = list(profile.get("notes") or [])
        notes.append({
            "date": self.note_date.dateTime().toString("yyyy-MM-dd HH:mm"),
            "text": text,
        })
        notes.sort(key=lambda row: row.get("date", ""), reverse=True)
        profile["notes"] = notes
        profile["updated"] = datetime.now().isoformat()
        self.profiles[key] = profile
        self.save_profiles()
        self.note_text.clear()
        self.populate_notes()

    def delete_selected_note(self):
        key = self.current_key
        if not key:
            return
        rows = sorted({idx.row() for idx in self.notes_table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        profile = self.profile_for_key(key)
        notes = list(profile.get("notes") or [])
        for row in rows:
            if 0 <= row < len(notes):
                notes.pop(row)
        profile["notes"] = notes
        profile["updated"] = datetime.now().isoformat()
        self.profiles[key] = profile
        self.save_profiles()
        self.populate_notes()

    def populate_notes(self):
        notes = list(self.profile_for_key(self.current_key).get("notes") or []) if self.current_key else []
        self.notes_table.setRowCount(0)
        for note in notes:
            row = self.notes_table.rowCount()
            self.notes_table.insertRow(row)
            self.notes_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(note.get("date", ""))))
            self.notes_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(note.get("text", ""))))
        self.notes_table.resizeColumnsToContents()

    def mail_key(self, path: Path | None) -> str:
        if path is None:
            return ""
        try:
            return str(path.resolve()).lower()
        except OSError:
            return str(path).lower()

    def selection_key(self, kind: str, value: object) -> str:
        if value in (None, ""):
            return ""
        text = str(value)
        if kind == "file":
            text = self.mail_key(Path(text))
        return f"{kind}:{text}"

    def capture_delete_key(self, measurement: AnimalMeasurement) -> str:
        row = measurement.row
        session_path = measurement.files.get("session")
        session_key = session_path.stem if session_path else row.get("session_id", "")
        base = _base_from_row(row)
        capture_id = row.get("session_id", "") or base
        return "|".join([session_key, capture_id, base])

    def capture_label(self, measurement: AnimalMeasurement) -> str:
        row = measurement.row
        raw = measurement.files.get("raw")
        pieces = [
            row.get("id") or _base_from_row(row),
            f"{row.get('fecha', '')} {row.get('hora', '')}".strip(),
            _mode_label(row.get("modo", "")),
            row.get("config_label", ""),
            raw.name if raw else _base_from_row(row),
        ]
        return " | ".join(piece for piece in pieces if piece)

    def capture_raw_path(self, measurement: AnimalMeasurement | None) -> Path | None:
        if measurement is None:
            return None
        raw = measurement.files.get("raw")
        if raw and raw.exists():
            return raw
        value = measurement.row.get("raw", "")
        if value:
            candidate = Path(value)
            if candidate.exists():
                return candidate
            candidate = RAW_DIR / candidate.name
            if candidate.exists():
                return candidate
        return None

    def measurement_by_capture_key(self, capture_key: str) -> AnimalMeasurement | None:
        for measurements in self.measurements_by_animal.values():
            for measurement in measurements:
                if self.capture_delete_key(measurement) == capture_key:
                    return measurement
        return None

    def measurement_for_path(self, path: Path | None) -> AnimalMeasurement | None:
        wanted = self.mail_key(path)
        if not wanted:
            return None
        for measurements in self.measurements_by_animal.values():
            for measurement in measurements:
                raw = self.capture_raw_path(measurement)
                if raw and self.mail_key(raw) == wanted:
                    return measurement
                for related in measurement.files.values():
                    if self.mail_key(related) == wanted:
                        return measurement
        return None

    def int_from_row(self, row: dict[str, str], key: str, default: int) -> int:
        value = _as_float(row.get(key, ""))
        return int(value) if np.isfinite(value) else default

    def sensor_config_from_row(self, row: dict[str, str]) -> SensorConfig:
        return SensorConfig(
            red=self.int_from_row(row, "cfg_red", 63),
            ir=self.int_from_row(row, "cfg_ir", 63),
            avg=self.int_from_row(row, "cfg_avg", 4),
            rate=self.int_from_row(row, "cfg_rate", 800),
            width=self.int_from_row(row, "cfg_width", 411),
            adc=self.int_from_row(row, "cfg_adc", 16384),
            skip=self.int_from_row(row, "cfg_skip", 50),
        ).clean()

    def stable_values_for_measurement(self, measurement: AnimalMeasurement, *, recompute: bool = False) -> dict[str, float | int | str]:
        stored = _as_float(measurement.row.get("bpm_estable_5s", ""))
        if np.isfinite(stored):
            return {
                "bpm": stored,
                "start": _as_float(measurement.row.get("bpm_estable_inicio_s", "")),
                "end": _as_float(measurement.row.get("bpm_estable_fin_s", "")),
                "reason": measurement.row.get("bpm_estable_motivo", ""),
            }
        if not recompute:
            return {"bpm": math.nan, "start": math.nan, "end": math.nan, "reason": ""}
        raw = measurement.files.get("raw")
        if not raw or not raw.exists():
            return {"bpm": math.nan, "start": math.nan, "end": math.nan, "reason": ""}
        cache_key = self.capture_delete_key(measurement)
        cached = self.stable_bpm_cache.get(cache_key)
        if cached is not None:
            return cached
        rows = _read_csv(raw)
        if not rows:
            self.stable_bpm_cache[cache_key] = {"bpm": math.nan, "start": math.nan, "end": math.nan, "reason": ""}
            return self.stable_bpm_cache[cache_key]
        t = np.asarray([_as_float(row.get("tiempo_s", "")) for row in rows], dtype=float)
        red = np.asarray([_as_float(row.get("red_raw", "")) for row in rows], dtype=float)
        ir = np.asarray([_as_float(row.get("ir_raw", "")) for row in rows], dtype=float)
        ref_avg, ref_count = _mean_ref_pulse(
            measurement.row.get("pulso_previo"),
            measurement.row.get("pulso_final_pulsio"),
            measurement.row.get("pulso_final_fonendo"),
        )
        stable = stable_bpm_segment(
            t,
            red,
            ir,
            self.sensor_config_from_row(measurement.row),
            AnalysisConfig(),
            window_s=5.0,
            reference_bpm=ref_avg if ref_count else None,
        )
        value = stable.bpm_estable_5s if np.isfinite(stable.bpm_estable_5s) else math.nan
        self.stable_bpm_cache[cache_key] = {
            "bpm": value,
            "start": stable.bpm_estable_inicio_s,
            "end": stable.bpm_estable_fin_s,
            "reason": stable.bpm_estable_motivo,
        }
        return self.stable_bpm_cache[cache_key]

    def stable_bpm_for_measurement(self, measurement: AnimalMeasurement, *, recompute: bool = False) -> float:
        return float(self.stable_values_for_measurement(measurement, recompute=recompute).get("bpm", math.nan))

    def measurement_position_temp(self, row: dict[str, str], position: str) -> float:
        animal_type = row.get("animal_type", "")
        normalized = normalize_position(position, animal_type)
        assignments = parse_temp_mapping(row.get("temp_mapping", ""), animal_type)
        for channel in TEMP_CHANNELS:
            if assignments.get(channel) != normalized:
                continue
            value = _as_float(row.get(f"temp_{channel.lower()}_c_final_max_5s", ""))
            if np.isfinite(value):
                return value
        prefix = POSITION_SUMMARY_PREFIXES.get(normalized, "")
        if prefix:
            value = _as_float(row.get(f"{prefix}_c_final_max_5s", ""))
            if np.isfinite(value):
                return value
        measured_position = normalize_position(row.get("ubre", ""), animal_type)
        if measured_position == normalized:
            value = _as_float(row.get("temp_c_final_max_5s", ""))
            if np.isfinite(value):
                return value
        if normalized == "RT":
            return _as_float(row.get("temp_a0_c_final_max_5s", "") or row.get("temp_c_final_max_5s", ""))
        if normalized == "LT":
            return _as_float(row.get("temp_a1_c_final_max_5s", ""))
        return math.nan

    def recommended_alerts_for_measurements(
        self,
        key: str,
        measurements: list[AnimalMeasurement],
        *,
        recompute_stable: bool = False,
    ) -> dict[str, object]:
        profile = self.profile_for_key(key) if key else {}
        species = normalize_animal_type(profile.get("animal_type") or (key.split(":", 1)[0] if ":" in key else ""))
        bpm_values = [self.stable_bpm_for_measurement(measurement, recompute=recompute_stable) for measurement in measurements]
        bpm_values = [value for value in bpm_values if np.isfinite(value)]
        bpm_excluded = max(0, len(measurements) - len(bpm_values))
        bpm_mean = float(np.mean(bpm_values)) if bpm_values else math.nan
        bpm_std = float(np.std(bpm_values)) if len(bpm_values) >= 2 else math.nan
        bpm_delta = max(8.0, min(25.0, round((bpm_std * 2.0) if np.isfinite(bpm_std) and bpm_std > 0 else 12.0)))
        temp_by_position: dict[str, dict[str, float | int]] = {}
        temp_std_values: list[float] = []
        for position in positions_for_animal(species):
            values = [self.measurement_position_temp(measurement.row, position) for measurement in measurements]
            values = [value for value in values if np.isfinite(value)]
            mean = float(np.mean(values)) if values else math.nan
            std = float(np.std(values)) if len(values) >= 2 else math.nan
            if np.isfinite(std):
                temp_std_values.append(std)
            temp_by_position[position] = {"mean": mean, "count": len(values), "std": std}
        max_temp_std = max(temp_std_values) if temp_std_values else math.nan
        temp_delta = max(0.6, min(3.0, round(((max_temp_std * 2.0) if np.isfinite(max_temp_std) and max_temp_std > 0 else 1.0), 1)))
        pieces = []
        if np.isfinite(bpm_mean):
            pieces.append(f"BPM>{fmt(bpm_mean + bpm_delta, 0, '-')}")
        temp_limits = []
        for position, stats in temp_by_position.items():
            mean = float(stats["mean"])
            if np.isfinite(mean):
                temp_limits.append(f"{position}>{fmt(mean + temp_delta, 1, '-')}")
        if temp_limits:
            pieces.append("Temp " + ", ".join(temp_limits))
        return {
            "species": species,
            "bpm_mean": bpm_mean,
            "bpm_count": len(bpm_values),
            "bpm_excluded": bpm_excluded,
            "bpm_delta": bpm_delta,
            "bpm_limit": bpm_mean + bpm_delta if np.isfinite(bpm_mean) else math.nan,
            "temp_delta": temp_delta,
            "temp_by_position": temp_by_position,
            "summary": " | ".join(pieces) if pieces else "Sin historial suficiente",
        }

    def recommended_alert_lines(self, recommendation: dict[str, object]) -> list[str]:
        lines: list[str] = []
        bpm_mean = float(recommendation.get("bpm_mean", math.nan))
        bpm_limit = float(recommendation.get("bpm_limit", math.nan))
        bpm_count = int(recommendation.get("bpm_count", 0) or 0)
        bpm_excluded = int(recommendation.get("bpm_excluded", 0) or 0)
        if np.isfinite(bpm_mean):
            lines.append(
                f"BPM estable 5 s: media {fmt(bpm_mean, 1, '-')} BPM con {bpm_count} toma(s); "
                f"aviso recomendado por encima de {fmt(bpm_limit, 0, '-')} BPM."
            )
            if bpm_excluded:
                lines.append(f"BPM estable 5 s: {bpm_excluded} toma(s) excluida(s) por no tener tramo estable fiable.")
        else:
            lines.append("BPM estable 5 s: sin suficientes ventanas fiables anteriores; las tomas sin tramo estable no entran en la media.")
        temp_delta = float(recommendation.get("temp_delta", math.nan))
        temp_by_position = recommendation.get("temp_by_position", {})
        if isinstance(temp_by_position, dict):
            for position, stats_obj in temp_by_position.items():
                stats = stats_obj if isinstance(stats_obj, dict) else {}
                mean = float(stats.get("mean", math.nan))
                count = int(stats.get("count", 0) or 0)
                if np.isfinite(mean):
                    lines.append(
                        f"Temperatura {position}: media final {fmt(mean, 1, '-')} C con {count} toma(s); "
                        f"aviso recomendado por encima de {fmt(mean + temp_delta, 1, '-')} C."
                    )
                else:
                    lines.append(f"Temperatura {position}: sin datos finales suficientes.")
        return lines

    def update_recommended_alerts(self):
        if not self.current_key:
            self.recommended_alerts_text.clear()
            return
        recommendation = self.recommended_alerts_for_measurements(
            self.current_key,
            self.current_measurements(),
            recompute_stable=True,
        )
        self.recommended_alerts_text.setPlainText("\n".join(self.recommended_alert_lines(recommendation)))

    def apply_recommended_alerts(self):
        if not self.current_key:
            QtWidgets.QMessageBox.information(self, "Avisos", "Selecciona o guarda primero un animal.")
            return
        recommendation = self.recommended_alerts_for_measurements(
            self.current_key,
            self.current_measurements(),
            recompute_stable=True,
        )
        has_bpm = np.isfinite(float(recommendation.get("bpm_mean", math.nan)))
        temp_by_position = recommendation.get("temp_by_position", {})
        has_temp = isinstance(temp_by_position, dict) and any(
            np.isfinite(float(stats.get("mean", math.nan)))
            for stats in temp_by_position.values()
            if isinstance(stats, dict)
        )
        if not has_bpm and not has_temp:
            QtWidgets.QMessageBox.information(self, "Avisos", "No hay historial suficiente para recomendar avisos.")
            return
        self.baseline_enabled.setChecked(True)
        self.baseline_bpm_delta.setValue(float(recommendation.get("bpm_delta", 12.0)))
        self.baseline_temp_delta.setValue(float(recommendation.get("temp_delta", 1.0)))
        self.baseline_min_records.setValue(max(1, min(5, len(self.current_measurements()))))
        self.update_recommended_alerts()

    def update_summary(self):
        measurements = self.current_measurements()
        recommendation = self.recommended_alerts_for_measurements(self.current_key, measurements, recompute_stable=True)
        values = {
            "BPM medio": self.mean_value(measurements, "bpm"),
            "BPM estable medio": recommendation["bpm_mean"],
            "SpO2 medio": self.mean_value(measurements, "spo2_pct"),
            "Temperatura media": self.mean_temp(measurements),
            "Calidad media": self.mean_value(measurements, "calidad"),
        }
        last = self.last_measurement_stamp(self.current_key) if self.current_key else ""
        lines = [
            f"Tomas asociadas: {len(measurements)}",
            f"Ultima toma: {last or '-'}",
        ]
        for label, value in values.items():
            lines.append(f"{label}: {fmt(value, 1, '-')}")
        lines.append("")
        baseline = self.profile_for_key(self.current_key).get("baseline_settings") or {}
        if baseline.get("enabled"):
            lines.append(
                "Avisos basales guardados: "
                f"temp +{fmt(_as_float(baseline.get('temp_delta_c')), 1, '-')} C, "
                f"BPM +{fmt(_as_float(baseline.get('bpm_delta')), 0, '-')} "
                f"con minimo {baseline.get('min_records', 5)} tomas."
            )
        else:
            lines.append("Avisos basales desactivados. No se usaran hasta pulsar Guardar avisos con la casilla activada.")
        lines.append(f"Aviso recomendado: {recommendation['summary']}")
        self.summary_text.setPlainText("\n".join(lines))

    def populate_history(self):
        measurements = self.current_measurements()
        self._updating_tables = True
        self.history_table.setSortingEnabled(False)
        self.history_table.setRowCount(0)
        try:
            for measurement in measurements:
                row_idx = self.history_table.rowCount()
                self.history_table.insertRow(row_idx)
                data = measurement.row
                bpm = _as_float(data.get("bpm", ""))
                ref_avg, _count = _mean_ref_pulse(
                    data.get("pulso_previo"),
                    data.get("pulso_final_pulsio"),
                    data.get("pulso_final_fonendo"),
                )
                diff_ref = abs(bpm - ref_avg) if np.isfinite(bpm) and np.isfinite(ref_avg) else math.nan
                quality = _as_float(data.get("calidad", ""))
                if np.isfinite(quality) and quality >= 70:
                    state = "Buena"
                elif np.isfinite(quality) and quality >= 45:
                    state = "Aceptable"
                elif np.isfinite(quality):
                    state = "Dudosa"
                else:
                    state = ""
                stable = self.stable_values_for_measurement(measurement, recompute=True)
                stable_bpm = float(stable.get("bpm", math.nan))
                stable_start = float(stable.get("start", math.nan))
                stable_end = float(stable.get("end", math.nan))
                stable_segment = (
                    f"{fmt(stable_start, 2, '')}-{fmt(stable_end, 2, '')} s"
                    if np.isfinite(stable_start) and np.isfinite(stable_end)
                    else ""
                )
                raw = self.capture_raw_path(measurement)
                capture_key = self.capture_delete_key(measurement)
                values = {
                    "Animal": data.get("id", ""),
                    "Especie": animal_label(data.get("animal_type", "")),
                    "Temp RT final": fmt(self.measurement_position_temp(data, "RT"), 1, ""),
                    "Temp LT final": fmt(self.measurement_position_temp(data, "LT"), 1, ""),
                    "Temp FLT final": fmt(self.measurement_position_temp(data, "FLT"), 1, ""),
                    "Temp FRT final": fmt(self.measurement_position_temp(data, "FRT"), 1, ""),
                    "Temp RLT final": fmt(self.measurement_position_temp(data, "RLT"), 1, ""),
                    "Temp RRT final": fmt(self.measurement_position_temp(data, "RRT"), 1, ""),
                    "Pulso ref.": fmt(ref_avg, 1, ""),
                    "BPM medio": fmt(bpm, 1, ""),
                    "BPM estable": fmt(stable_bpm, 1, ""),
                    "Tramo estable": stable_segment,
                    "Pulso final pulsio": data.get("pulso_final_pulsio", ""),
                    "Pulso final fonendo": data.get("pulso_final_fonendo", ""),
                    "Modo": _mode_label(data.get("modo", "")),
                    "Sensor": data.get("ubre", ""),
                    "Termómetros": data.get("temp_mapping", ""),
                    "Oxígeno medio": fmt(_as_float(data.get("spo2_pct", "")), 1, ""),
                    "Calidad": fmt(quality, 0, ""),
                    "Contacto": data.get("contacto", ""),
                    "Estado": state,
                    "Dif. BPM-ref": fmt(diff_ref, 1, ""),
                    "Medición": data.get("medicion_vacio", ""),
                    "Configuración": data.get("config_label", ""),
                    "Temp manual RT": data.get("temperatura_manual_inicio_rt_c", ""),
                    "Temp manual LT": data.get("temperatura_manual_inicio_lt_c", ""),
                    "Temp manual FLT": data.get("temperatura_manual_inicio_flt_c", ""),
                    "Temp manual FRT": data.get("temperatura_manual_inicio_frt_c", ""),
                    "Temp manual RLT": data.get("temperatura_manual_inicio_rlt_c", ""),
                    "Temp manual RRT": data.get("temperatura_manual_inicio_rrt_c", ""),
                    "Fecha": data.get("fecha", ""),
                    "Hora": data.get("hora", ""),
                    "Duración": fmt(_as_float(data.get("duracion_real_s", "")), 2, ""),
                    "Hz": fmt(_as_float(data.get("hz_real", "")), 2, ""),
                    "Muestras": data.get("muestras", ""),
                    "Raw": raw.name if raw else data.get("raw", ""),
                }
                self.history_table.setItem(
                    row_idx,
                    0,
                    self.make_selection_item(
                        kind="capture",
                        key=self.selection_key("capture", capture_key),
                        tooltip="Seleccionar esta toma/raw para correo o eliminación trazable",
                        path=raw,
                        animal_key_value=measurement.animal_key,
                        capture_key=capture_key,
                    ),
                )
                for col, header in enumerate(self.history_headers[1:], start=1):
                    item = self.table_item(values.get(header, ""))
                    if raw:
                        item.setData(QtCore.Qt.ItemDataRole.UserRole, str(raw))
                    if header == "Estado" and state:
                        if state == "Buena":
                            item.setBackground(QtGui.QColor("#d8f3dc"))
                        elif state == "Aceptable":
                            item.setBackground(QtGui.QColor("#fff3bf"))
                        elif state == "Dudosa":
                            item.setBackground(QtGui.QColor("#ffd6d6"))
                    self.history_table.setItem(row_idx, col, item)
        finally:
            self._updating_tables = False
            self.history_table.setSortingEnabled(True)
        self.apply_history_column_visibility()

    def populate_files(self):
        self._updating_tables = True
        self.files_table.setSortingEnabled(False)
        self.files_table.setRowCount(0)
        try:
            for measurement in self.current_measurements():
                stamp = f"{measurement.row.get('fecha', '')} {measurement.row.get('hora', '')}".strip()
                capture_key = self.capture_delete_key(measurement)
                for kind, path in sorted(measurement.files.items()):
                    if not path.exists():
                        continue
                    row = self.files_table.rowCount()
                    self.files_table.insertRow(row)
                    self.files_table.setItem(
                        row,
                        0,
                        self.make_selection_item(
                            kind="file",
                            key=self.selection_key("file", path),
                            tooltip="Seleccionar archivo para correo o eliminación trazable",
                            path=path,
                            animal_key_value=measurement.animal_key,
                            capture_key=capture_key,
                        ),
                    )
                    values = [stamp, kind, path.name, str(path)]
                    for col, value in enumerate(values, start=1):
                        item = self.table_item(value)
                        item.setData(QtCore.Qt.ItemDataRole.UserRole, str(path))
                        self.files_table.setItem(row, col, item)
        finally:
            self._updating_tables = False
            self.files_table.setSortingEnabled(True)
        self.files_table.resizeColumnsToContents()
        self.files_table.setColumnWidth(0, 34)

    def update_graph(self):
        self.plot.clear()
        measurements = list(reversed(self.current_measurements()))
        if not measurements:
            return
        x = np.arange(1, len(measurements) + 1, dtype=float)

        def series(key: str) -> np.ndarray:
            if key == "temp":
                return np.asarray([self.measurement_temp(m.row) for m in measurements], dtype=float)
            return np.asarray([_as_float(m.row.get(key, "")) for m in measurements], dtype=float)

        if self.chk_bpm.isChecked():
            self.plot_series(x, series("bpm"), (40, 120, 210), "BPM")
        if self.chk_temp.isChecked():
            self.plot_series(x, series("temp"), (220, 120, 30), "Temp")
        if self.chk_spo2.isChecked():
            self.plot_series(x, series("spo2_pct"), (150, 70, 160), "SpO2")
        if self.chk_quality.isChecked():
            self.plot_series(x, series("calidad"), (40, 150, 70), "Calidad")

    def plot_series(self, x: np.ndarray, y: np.ndarray, color: tuple[int, int, int], name: str):
        mask = np.isfinite(x) & np.isfinite(y)
        if np.any(mask):
            self.plot.plot(x[mask], y[mask], pen=pg.mkPen(color, width=2), symbol="o", symbolSize=7, name=name)

    def open_history_raw(self, index: QtCore.QModelIndex):
        if not index.isValid():
            return
        raw_col = self.history_headers.index("Raw") if "Raw" in self.history_headers else -1
        item = self.history_table.item(index.row(), raw_col) if raw_col >= 0 else None
        path_text = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else ""
        self.open_path(Path(str(path_text)) if path_text else None)

    def open_selected_file(self, index: QtCore.QModelIndex):
        if not index.isValid():
            return
        path_col = self.file_headers.index("ruta") if "ruta" in self.file_headers else -1
        item = self.files_table.item(index.row(), path_col) if path_col >= 0 else None
        path_text = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else ""
        self.open_path(Path(str(path_text)) if path_text else None)

    def open_path(self, path: Path | None):
        if not path or not path.exists():
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def desktop_dir(self) -> Path:
        desktop = Path.home() / "Desktop"
        if not desktop.exists():
            desktop = Path.home() / "Escritorio"
        return desktop if desktop.exists() else Path.home()

    def selected_paths_for_mail(self) -> list[Path]:
        paths: list[Path] = []
        for record in self.selected_items.values():
            candidates: list[Path] = []
            if record.kind == "animal":
                for measurement in self.measurements_by_animal.get(record.animal_key, []):
                    raw = self.capture_raw_path(measurement)
                    if raw:
                        candidates.append(raw)
            elif record.kind == "capture":
                measurement = self.measurement_by_capture_key(record.capture_key)
                raw = self.capture_raw_path(measurement) if measurement else record.path
                if raw:
                    candidates.append(raw)
            elif record.kind == "file" and record.path:
                candidates.append(record.path)
            for path in candidates:
                if path.exists() and path not in paths:
                    paths.append(path)
        return paths

    def prepare_mail_zip(self):
        paths = self.selected_paths_for_mail()
        if not paths:
            QtWidgets.QMessageBox.information(self, "Preparar correo", "Selecciona primero un animal, raw o archivo.")
            return
        desktop = self.desktop_dir()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_path = desktop / f"mtestv2_animales_para_correo_{stamp}.zip"
        used_names: dict[str, int] = {}
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in paths:
                name = path.name
                if name in used_names:
                    used_names[name] += 1
                    name = f"{path.stem}_{used_names[path.name]}{path.suffix}"
                else:
                    used_names[name] = 1
                zf.write(path, arcname=name)
        QtWidgets.QApplication.clipboard().setText(str(zip_path))
        QtWidgets.QMessageBox.information(
            self,
            "Preparar correo",
            f"Se ha creado un ZIP en el Escritorio con {len(paths)} archivo(s):\n\n{zip_path}\n\nLa ruta queda copiada al portapapeles.",
        )

    def delete_checked_items(self):
        capture_choices, standalone_paths, animal_keys = self.checked_delete_choices()
        if not capture_choices and not standalone_paths and not animal_keys:
            QtWidgets.QMessageBox.information(self, "Eliminar", "Selecciona primero un animal, raw o archivo.")
            return
        selected_measurements, selected_paths = self.pick_delete_targets(capture_choices, standalone_paths)
        selected_capture_keys = {self.capture_delete_key(measurement) for measurement in selected_measurements}
        delete_profile_keys = []
        for key in animal_keys:
            if key not in self.profiles:
                continue
            animal_capture_keys = {
                self.capture_delete_key(measurement)
                for measurement in self.measurements_by_animal.get(key, [])
            }
            if not animal_capture_keys or animal_capture_keys.issubset(selected_capture_keys):
                delete_profile_keys.append(key)
        if not selected_measurements and not selected_paths and not delete_profile_keys:
            return
        for key in delete_profile_keys:
            photo = self.profile_photo_path(key)
            if photo and photo.exists() and photo not in selected_paths:
                selected_paths.append(photo)
        paths_by_capture = {
            self.capture_delete_key(measurement): self.delete_paths_for_measurement(measurement)
            for measurement in selected_measurements
        }
        paths = self.delete_paths_for_targets(selected_measurements, selected_paths)
        if not paths and delete_profile_keys:
            if self.confirm_profile_delete_only(delete_profile_keys):
                self.remove_profiles(delete_profile_keys)
                self.reload_data()
            return
        if not paths:
            QtWidgets.QMessageBox.information(self, "Eliminar", "No hay archivos existentes asociados a la selección.")
            return
        detail_lines = [str(path) for path in paths]
        session_notes = self.session_update_notes(selected_measurements)
        profile_notes = [f"Eliminar ficha del animal: {display_key_label(key)}" for key in delete_profile_keys]
        preview_lines = detail_lines[:25]
        if len(detail_lines) > len(preview_lines):
            preview_lines.append(f"... y {len(detail_lines) - len(preview_lines)} archivo(s) más")
        msg = QtWidgets.QMessageBox(self)
        msg.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        msg.setWindowTitle("Confirmar eliminación")
        msg.setText(f"Se van a mover {len(paths)} archivo(s) a la papelera interna.")
        msg.setInformativeText("\n".join(preview_lines + session_notes + profile_notes) + "\n\nLos archivos se conservarán en resultados/.trash.")
        msg.setDetailedText("\n".join(detail_lines + session_notes + profile_notes))
        delete_btn = msg.addButton("Mover a papelera", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton("Cancelar", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if msg.clickedButton() != delete_btn:
            return
        moved = 0
        failed_paths: dict[Path, str] = {}
        trash_batch = TrashBatch(source="animals_window")
        for path in paths:
            ok, error = trash_batch.move(path)
            if ok:
                moved += 1
            else:
                failed_paths[path] = error
        manifest_ok, manifest_error = trash_batch.write_manifest()
        manifest_errors = [] if manifest_ok else [f"{trash_batch.batch_dir / 'manifest.json'}: {manifest_error}"]
        successful_measurements = [
            measurement for measurement in selected_measurements
            if paths_by_capture.get(self.capture_delete_key(measurement))
            and all(path not in failed_paths for path in paths_by_capture[self.capture_delete_key(measurement)])
        ]
        session_errors, failed_sessions = self.remove_capture_rows_from_sessions(successful_measurements)
        errors = [f"{path}: {reason}" for path, reason in failed_paths.items()]
        errors.extend(session_errors)
        errors.extend(manifest_errors)
        if not errors:
            self.remove_profiles(delete_profile_keys)
        self.keep_failed_delete_selection(selected_measurements, selected_paths, failed_paths, failed_sessions)
        self.reload_data()
        if errors:
            QtWidgets.QMessageBox.warning(
                self,
                "Eliminar",
                f"Movidos {moved} archivo(s) a papelera, pero hubo {len(errors)} error(es).\n\n" + "\n".join(errors[:10]),
            )
        else:
            QtWidgets.QMessageBox.information(
                self,
                "Eliminar",
                f"Movidos {moved} archivo(s) a papelera interna:\n\n{trash_batch.batch_dir}",
            )

    def checked_delete_choices(self) -> tuple[dict[str, tuple[AnimalMeasurement, bool]], list[Path], list[str]]:
        captures: dict[str, tuple[AnimalMeasurement, bool]] = {}
        standalone_paths: list[Path] = []
        animal_keys: list[str] = []
        for record in self.selected_items.values():
            if record.kind == "animal":
                if record.animal_key and record.animal_key not in animal_keys:
                    animal_keys.append(record.animal_key)
                for measurement in self.measurements_by_animal.get(record.animal_key, []):
                    captures[self.capture_delete_key(measurement)] = (measurement, True)
            elif record.kind == "capture":
                measurement = self.measurement_by_capture_key(record.capture_key)
                if measurement is not None:
                    captures[self.capture_delete_key(measurement)] = (measurement, True)
                elif record.path and record.path.exists() and record.path not in standalone_paths:
                    standalone_paths.append(record.path)
            elif record.kind == "file":
                measurement = self.measurement_for_path(record.path)
                if measurement is not None:
                    captures[self.capture_delete_key(measurement)] = (measurement, True)
                elif record.path and record.path.exists() and record.path not in standalone_paths:
                    standalone_paths.append(record.path)
        return captures, standalone_paths, animal_keys

    def pick_delete_targets(
        self,
        capture_choices: dict[str, tuple[AnimalMeasurement, bool]],
        standalone_paths: list[Path],
    ) -> tuple[list[AnimalMeasurement], list[Path]]:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Seleccionar qué mover a papelera")
        screen = QtWidgets.QApplication.primaryScreen()
        available_height = screen.availableGeometry().height() if screen else 900
        max_height = max(420, available_height - 120)
        dialog.resize(780, min(520, max_height))
        dialog.setMaximumHeight(max_height)
        layout = QtWidgets.QVBoxLayout(dialog)
        info = QtWidgets.QLabel(
            "Revisa la selección antes de moverla a papelera. Cada raw muestra sus archivos relacionados."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        tree = QtWidgets.QTreeWidget()
        tree.setHeaderLabels(["Mover", "Toma o archivo", "Relacionados"])
        tree.setRootIsDecorated(True)
        capture_by_key = {key: measurement for key, (measurement, _checked) in capture_choices.items()}
        path_by_key = {str(path): path for path in standalone_paths}
        for key, (measurement, checked) in capture_choices.items():
            paths = self.delete_paths_for_measurement(measurement)
            item = QtWidgets.QTreeWidgetItem(["", self.capture_label(measurement), f"{len(paths)} archivo(s)"])
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, QtCore.Qt.CheckState.Checked if checked else QtCore.Qt.CheckState.Unchecked)
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, f"capture:{key}")
            for path in paths:
                item.addChild(QtWidgets.QTreeWidgetItem(["", path.name, str(path.parent)]))
            tree.addTopLevelItem(item)
        for path in standalone_paths:
            item = QtWidgets.QTreeWidgetItem(["", path.name, str(path.parent)])
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, QtCore.Qt.CheckState.Checked)
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, f"path:{path}")
            tree.addTopLevelItem(item)
        tree.expandAll()
        tree.resizeColumnToContents(0)
        tree.resizeColumnToContents(1)
        layout.addWidget(tree, stretch=1)
        buttons = QtWidgets.QHBoxLayout()
        btn_all = QtWidgets.QPushButton("Marcar todos")
        btn_none = QtWidgets.QPushButton("Desmarcar")
        btn_delete = QtWidgets.QPushButton("Mover a papelera")
        btn_cancel = QtWidgets.QPushButton("Cancelar")
        buttons.addWidget(btn_all)
        buttons.addWidget(btn_none)
        buttons.addStretch(1)
        buttons.addWidget(btn_delete)
        buttons.addWidget(btn_cancel)
        layout.addLayout(buttons)

        def set_all(state: QtCore.Qt.CheckState):
            for row in range(tree.topLevelItemCount()):
                tree.topLevelItem(row).setCheckState(0, state)

        btn_all.clicked.connect(lambda: set_all(QtCore.Qt.CheckState.Checked))
        btn_none.clicked.connect(lambda: set_all(QtCore.Qt.CheckState.Unchecked))
        btn_cancel.clicked.connect(dialog.reject)
        btn_delete.clicked.connect(dialog.accept)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return [], []
        measurements: list[AnimalMeasurement] = []
        paths: list[Path] = []
        for row in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(row)
            if item.checkState(0) != QtCore.Qt.CheckState.Checked:
                continue
            marker = str(item.data(0, QtCore.Qt.ItemDataRole.UserRole) or "")
            if marker.startswith("capture:"):
                measurement = capture_by_key.get(marker.removeprefix("capture:"))
                if measurement is not None:
                    measurements.append(measurement)
            elif marker.startswith("path:"):
                path = path_by_key.get(marker.removeprefix("path:"))
                if path is not None:
                    paths.append(path)
        return measurements, paths

    def delete_paths_for_measurement(self, measurement: AnimalMeasurement) -> list[Path]:
        kinds = ("raw", "processed", "summary", "blocks", "plot", "screenshot", "config")
        paths: list[Path] = []
        for kind in kinds:
            path = measurement.files.get(kind)
            if path and path.exists() and path not in paths:
                paths.append(path)
        return paths

    def delete_paths_for_targets(self, measurements: list[AnimalMeasurement], standalone_paths: list[Path]) -> list[Path]:
        paths: list[Path] = []
        for measurement in measurements:
            for path in self.delete_paths_for_measurement(measurement):
                if path not in paths:
                    paths.append(path)
        for path in standalone_paths:
            if path.exists() and path not in paths:
                paths.append(path)
        return paths

    def session_update_notes(self, measurements: list[AnimalMeasurement]) -> list[str]:
        sessions = sorted({measurement.files["session"] for measurement in measurements if measurement.files.get("session")})
        return [f"Actualizar CSV de sesión sin mover el archivo: {path}" for path in sessions]

    def remove_capture_rows_from_sessions(self, measurements: list[AnimalMeasurement]) -> tuple[list[str], set[Path]]:
        by_session: dict[Path, set[str]] = {}
        errors: list[str] = []
        failed_sessions: set[Path] = set()
        for measurement in measurements:
            session_path = measurement.files.get("session")
            if not session_path or not session_path.exists():
                continue
            keys = by_session.setdefault(session_path, set())
            base = _base_from_row(measurement.row)
            keys.add(base)
            keys.add(measurement.row.get("base_name", ""))
            keys.add(measurement.row.get("session_id", ""))
            keys.discard("")
        for session_path, keys in by_session.items():
            rows = _read_csv(session_path)
            if not rows:
                continue
            fieldnames = list(rows[0].keys())
            kept = []
            for row in rows:
                row_keys = {_base_from_row(row), row.get("base_name", ""), row.get("session_id", "")}
                row_keys.discard("")
                if row_keys & keys:
                    continue
                kept.append(row)
            try:
                with atomic_csv_dict_writer(session_path, fieldnames, delimiter=";") as writer:
                    writer.writeheader()
                    writer.writerows(kept)
            except OSError as exc:
                failed_sessions.add(session_path)
                errors.append(f"{session_path}: no se pudo actualizar la sesión ({exc})")
        return errors, failed_sessions

    def keep_failed_delete_selection(
        self,
        measurements: list[AnimalMeasurement],
        standalone_paths: list[Path],
        failed_paths: dict[Path, str],
        failed_sessions: set[Path],
    ):
        self.selected_items.clear()
        for measurement in measurements:
            paths = self.delete_paths_for_measurement(measurement)
            session_path = measurement.files.get("session")
            if any(path in failed_paths for path in paths) or (session_path and session_path in failed_sessions):
                capture_key = self.capture_delete_key(measurement)
                key = self.selection_key("capture", capture_key)
                self.selected_items[key] = AnimalSelectionRecord(
                    kind="capture",
                    key=key,
                    path=self.capture_raw_path(measurement),
                    animal_key=measurement.animal_key,
                    capture_key=capture_key,
                )
        for path in standalone_paths:
            if path in failed_paths:
                key = self.selection_key("file", path)
                self.selected_items[key] = AnimalSelectionRecord(kind="file", key=key, path=path)
        self.update_selection_status()

    def profile_photo_path(self, key: str) -> Path | None:
        path_text = str(self.profile_for_key(key).get("photo_path") or "")
        if not path_text:
            return None
        path = Path(path_text)
        return path if path.exists() else None

    def confirm_profile_delete_only(self, keys: list[str]) -> bool:
        msg = QtWidgets.QMessageBox(self)
        msg.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        msg.setWindowTitle("Eliminar animal")
        msg.setText("No hay archivos asociados. Se eliminará solo la ficha del animal.")
        msg.setInformativeText("\n".join(display_key_label(key) for key in keys))
        delete_btn = msg.addButton("Eliminar ficha", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton("Cancelar", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        return msg.clickedButton() == delete_btn

    def remove_profiles(self, keys: list[str]):
        changed = False
        for key in keys:
            if key in self.profiles:
                self.profiles.pop(key, None)
                changed = True
        if changed:
            self.save_profiles()

    def mean_value(self, measurements: list[AnimalMeasurement], key: str) -> float:
        values = [_as_float(m.row.get(key, "")) for m in measurements]
        values = [value for value in values if np.isfinite(value)]
        return float(np.mean(values)) if values else math.nan

    def mean_temp(self, measurements: list[AnimalMeasurement]) -> float:
        values = [self.measurement_temp(m.row) for m in measurements]
        values = [value for value in values if np.isfinite(value)]
        return float(np.mean(values)) if values else math.nan

    def measurement_temp(self, row: dict[str, str]) -> float:
        for key in (
            "temp_c_final_max_5s",
            "temp_rt_c_final_max_5s",
            "temp_lt_c_final_max_5s",
            "temp_flt_c_final_max_5s",
            "temp_frt_c_final_max_5s",
            "temp_rlt_c_final_max_5s",
            "temp_rrt_c_final_max_5s",
            "temp_c_media",
        ):
            value = _as_float(row.get(key, ""))
            if np.isfinite(value):
                return value
        return math.nan

    def last_measurement_stamp(self, key: str) -> str:
        measurements = self.measurements_by_animal.get(key, [])
        if not measurements:
            return ""
        return max((self.measurement_stamp(m.row) for m in measurements), default="")

    def first_measurement_stamp(self, key: str) -> str:
        measurements = self.measurements_by_animal.get(key, [])
        if not measurements:
            return ""
        return min((self.measurement_stamp(m.row) for m in measurements), default="")

    def registration_stamp(self, key: str) -> str:
        """Best-effort 'age' of an animal: explicit profile creation date,
        falling back to its earliest known measurement when the profile was
        never saved manually (animal only exists via discovered measurements)."""
        created = str(self.profile_for_key(key).get("created") or "")
        return created or self.first_measurement_stamp(key)

    def measurement_stamp(self, row: dict[str, str]) -> str:
        return f"{row.get('fecha', '')} {row.get('hora', '')}".strip() or row.get("created", "")

    def discover_measurements(self) -> dict[str, list[AnimalMeasurement]]:
        files_by_base = self.find_files()
        measurements: list[AnimalMeasurement] = []
        attached_bases: set[str] = set()

        for session_file in SESSION_DIR.rglob("session_*.csv"):
            for row in _read_csv(session_file):
                base = _base_from_row(row)
                files = {"session": session_file}
                if base and base in files_by_base:
                    files.update(files_by_base[base])
                    attached_bases.add(base)
                self.attach_files_from_row(row, files)
                self.enrich_from_summary(row, files.get("summary"), files)
                key = animal_key(row.get("animal_type", ""), row.get("id", ""))
                if key:
                    measurements.append(AnimalMeasurement(key, row, files))

        for base, files in files_by_base.items():
            if base in attached_bases:
                continue
            row: dict[str, str] = {"base_name": base}
            if "summary" in files:
                self.enrich_from_summary(row, files["summary"], files)
            if "raw" in files:
                raw_rows = _read_csv(files["raw"], limit=1)
                if raw_rows:
                    row = {**raw_rows[0], **row}
            key = animal_key(row.get("animal_type", ""), row.get("id", ""))
            if key:
                measurements.append(AnimalMeasurement(key, row, dict(files)))

        grouped: dict[str, list[AnimalMeasurement]] = {}
        for measurement in measurements:
            grouped.setdefault(measurement.animal_key, []).append(measurement)
        for rows in grouped.values():
            rows.sort(key=lambda measurement: self.measurement_stamp(measurement.row), reverse=True)
        return grouped

    def find_files(self) -> dict[str, dict[str, Path]]:
        index: dict[str, dict[str, Path]] = {}
        patterns = {
            "raw": ("raw_*.csv", RAW_DIR, ("raw_",)),
            "processed": ("proc_*.csv", PROCESSED_DIR, ("proc_",)),
            "blocks": ("bpm_blocks_10s_*.csv", REPORT_DIR, ("bpm_blocks_10s_",)),
            "summary": ("summary_*.json", REPORT_DIR, ("summary_",)),
            "plot": ("plot_*.png", FIGURES_DIR, ("plot_",)),
            "screenshot": ("screen_*.png", SCREENSHOT_DIR, ("screen_",)),
            "config": ("config_*.json", CONFIG_DIR, ("config_",)),
        }
        for kind, (pattern, folder, prefixes) in patterns.items():
            if not folder.exists():
                continue
            for path in folder.rglob(pattern):
                base = _strip_prefix(path.name, prefixes)
                index.setdefault(base, {})[kind] = path
        return index

    def resolve_file(self, value: str, default_dir: Path) -> Path | None:
        if not value:
            return None
        path = Path(value)
        candidates = [path]
        if not path.is_absolute():
            candidates.append(default_dir / path.name)
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def attach_files_from_row(self, row: dict[str, str], files: dict[str, Path]):
        mapping = {
            "raw": ("raw", RAW_DIR),
            "processed": ("processed", PROCESSED_DIR),
            "plot": ("plot", FIGURES_DIR),
            "screenshot": ("screenshot", SCREENSHOT_DIR),
            "summary": ("summary", REPORT_DIR),
            "config": ("config", CONFIG_DIR),
            "blocks": ("blocks_10s_file", REPORT_DIR),
        }
        for kind, (row_key, folder) in mapping.items():
            if kind in files:
                continue
            path = self.resolve_file(row.get(row_key, ""), folder)
            if path:
                files[kind] = path

    def enrich_from_summary(self, row: dict[str, str], summary_path: Path | None, files: dict[str, Path] | None = None):
        if not summary_path or not summary_path.exists():
            return
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        created = str(data.get("created") or "")
        if created and not row.get("fecha"):
            row["fecha"] = created[:10]
            row["hora"] = created[11:19] if len(created) >= 19 else ""
        for key, value in (
            ("created", created),
            ("session_id", data.get("session_id")),
            ("id", data.get("id")),
            ("base_name", data.get("base_name")),
            ("modo", data.get("mode")),
            ("animal_type", data.get("animal_type")),
            ("config_label", data.get("config_label")),
            ("condiciones_medida", data.get("measurement_condition")),
        ):
            if value is not None and not row.get(key):
                row[key] = str(value)
        metrics = data.get("metrics") or {}
        temp = data.get("temperature") or {}
        manual = data.get("manual_reference") or {}
        sensor = data.get("sensor_config") or {}
        values = {
            "bpm": metrics.get("bpm"),
            "bpm_peak": metrics.get("bpm_peak"),
            "bpm_fft": metrics.get("bpm_fft"),
            "bpm_autocorr": metrics.get("bpm_autocorr"),
            "bpm_estable_5s": metrics.get("bpm_estable_5s"),
            "bpm_estable_inicio_s": metrics.get("bpm_estable_inicio_s"),
            "bpm_estable_fin_s": metrics.get("bpm_estable_fin_s"),
            "bpm_estable_calidad": metrics.get("bpm_estable_calidad"),
            "bpm_estable_muestras": metrics.get("bpm_estable_muestras"),
            "bpm_estable_motivo": metrics.get("bpm_estable_motivo"),
            "spo2_pct": metrics.get("spo2"),
            "calidad": metrics.get("quality"),
            "calidad_label": metrics.get("quality_label"),
            "contacto": metrics.get("contact_label"),
            "pi_ir_pct": metrics.get("pi_ir_pct"),
            "pi_red_pct": metrics.get("pi_red_pct"),
            "artefactos_ir_pct": metrics.get("artifact_ir_pct"),
            "artefactos_red_pct": metrics.get("artifact_red_pct"),
            "temp_c_final_max_5s": temp.get("temp_c_final_max_5s"),
            "temp_c_media": temp.get("temp_c_mean"),
            "temp_rt_c_final_max_5s": temp.get("temp_rt_c_final_max_5s"),
            "temp_lt_c_final_max_5s": temp.get("temp_lt_c_final_max_5s"),
            "temp_flt_c_final_max_5s": temp.get("temp_flt_c_final_max_5s"),
            "temp_frt_c_final_max_5s": temp.get("temp_frt_c_final_max_5s"),
            "temp_rlt_c_final_max_5s": temp.get("temp_rlt_c_final_max_5s"),
            "temp_rrt_c_final_max_5s": temp.get("temp_rrt_c_final_max_5s"),
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
            "cfg_red": sensor.get("red"),
            "cfg_ir": sensor.get("ir"),
            "cfg_avg": sensor.get("avg"),
            "cfg_rate": sensor.get("rate"),
            "cfg_width": sensor.get("width"),
            "cfg_adc": sensor.get("adc"),
            "cfg_skip": sensor.get("skip"),
        }
        for key, value in values.items():
            if value is not None and not row.get(key):
                row[key] = str(value)
        if files is not None:
            for kind, path_text in (data.get("files") or {}).items():
                normalized = "blocks" if str(kind) == "bpm_blocks_10s" else str(kind)
                path = Path(str(path_text))
                if path.exists():
                    files.setdefault(normalized, path)
