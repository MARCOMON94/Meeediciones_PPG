from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from ..animal_config import ANIMAL_OPTIONS, animal_label, normalize_animal_type
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
from ..utils import fmt, sanitize_id
from .relations_window import _as_float, _base_from_row, _mean_ref_pulse, _mode_label, _read_csv, _strip_prefix


UNASSIGNED_IDS = {"", "SIN_CROTAL", "-", "NONE", "NULL"}


@dataclass
class AnimalMeasurement:
    animal_key: str
    row: dict[str, str] = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)


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


class AnimalsWindow(QtWidgets.QMainWindow):
    back_to_menu = QtCore.pyqtSignal()

    history_headers = [
        "Fecha", "Hora", "Animal", "Especie", "Modo", "Configuracion",
        "BPM", "Pulso ref.", "SpO2", "Temp final", "Calidad", "Raw",
    ]
    file_headers = ["Fecha", "tipo", "archivo", "ruta"]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PPG Suite v8 | Animales")
        self.resize(1380, 860)
        self.profiles: dict[str, dict] = {}
        self.measurements_by_animal: dict[str, list[AnimalMeasurement]] = {}
        self.current_key = ""
        self.pending_photo_source: Path | None = None
        self._loading_form = False
        self._build_ui()
        self.reload_data()

    @property
    def data_file(self) -> Path:
        return ANIMALS_DIR / "animals.json"

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
        self.btn_reload = QtWidgets.QPushButton("Recargar datos")
        self.btn_reload.clicked.connect(self.reload_data)
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
        buttons.addWidget(self.btn_new)
        buttons.addWidget(self.btn_save)
        left_layout.addLayout(buttons)
        self.btn_new.clicked.connect(self.new_animal)
        self.btn_save.clicked.connect(self.save_current_profile)

        self.animals_table = QtWidgets.QTableWidget(0, 5)
        self.animals_table.setHorizontalHeaderLabels(["Animal", "Nombre", "Especie", "Tomas", "Ultima"])
        self.animals_table.verticalHeader().setVisible(False)
        self.animals_table.setAlternatingRowColors(True)
        self.animals_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.animals_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.animals_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.animals_table.currentCellChanged.connect(self.select_animal_from_table)
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
        self.summary_text = QtWidgets.QTextEdit()
        self.summary_text.setReadOnly(True)
        form.addRow("Resumen:", self.summary_text)
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
        photo_layout.addWidget(self.photo_label, stretch=1)
        photo_layout.addWidget(self.btn_pick_photo)
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
        self.notes_table.setHorizontalHeaderLabels(["Fecha", "Nota"])
        self.notes_table.verticalHeader().setVisible(False)
        self.notes_table.setAlternatingRowColors(True)
        self.notes_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.notes_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.notes_table, stretch=2)
        self.btn_add_note.clicked.connect(self.add_note)
        self.btn_delete_note.clicked.connect(self.delete_selected_note)
        self.tabs.addTab(page, "Anotaciones")

    def _build_history_tab(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        self.history_table = QtWidgets.QTableWidget(0, len(self.history_headers))
        self.history_table.setHorizontalHeaderLabels(self.history_headers)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.doubleClicked.connect(self.open_history_raw)
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
        self.files_table.setHorizontalHeaderLabels(self.file_headers)
        self.files_table.verticalHeader().setVisible(False)
        self.files_table.setAlternatingRowColors(True)
        self.files_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.files_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.files_table.doubleClicked.connect(self.open_selected_file)
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
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    def all_animal_keys(self) -> list[str]:
        keys = set(self.profiles) | set(self.measurements_by_animal)
        return sorted(keys, key=lambda key: (self.last_measurement_stamp(key), display_key_label(key)), reverse=True)

    def populate_animal_list(self):
        text = self.search_edit.text().strip().lower()
        current = self.current_key
        self.animals_table.setRowCount(0)
        for key in self.all_animal_keys():
            profile = self.profiles.get(key, {})
            label = display_key_label(key)
            name = str(profile.get("display_name") or "")
            haystack = f"{key} {label} {name}".lower()
            if text and text not in haystack:
                continue
            measurements = self.measurements_by_animal.get(key, [])
            row = self.animals_table.rowCount()
            self.animals_table.insertRow(row)
            values = [
                label,
                name,
                animal_label(profile.get("animal_type") or key.split(":", 1)[0]),
                str(len(measurements)),
                self.last_measurement_stamp(key),
            ]
            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, key)
                self.animals_table.setItem(row, col, item)
            if key == current:
                self.animals_table.selectRow(row)
        self.animals_table.resizeColumnsToContents()

    def select_animal_from_table(self, current_row: int, _current_col: int, _prev_row: int, _prev_col: int):
        if self._loading_form or current_row < 0:
            return
        item = self.animals_table.item(current_row, 0)
        key = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else ""
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
        self.summary_text.clear()
        self.notes_table.setRowCount(0)
        self.history_table.setRowCount(0)
        self.files_table.setRowCount(0)
        self.plot.clear()

    def current_form_key(self) -> str:
        return animal_key(str(self.species_combo.currentData() or ""), self.id_edit.text())

    def update_baseline_controls_enabled(self, enabled: bool):
        for widget in (self.baseline_temp_delta, self.baseline_bpm_delta, self.baseline_min_records):
            widget.setEnabled(bool(enabled))

    def save_current_profile(self) -> str:
        key = self.current_form_key()
        if not key:
            QtWidgets.QMessageBox.warning(self, "Animales", "Introduce un crotal/ID real. SIN_CROTAL se mantiene como grupo no asignado.")
            return ""
        now = datetime.now().isoformat()
        old_key = self.current_key if self.current_key in self.profiles else ""
        existing = self.profile_for_key(key)
        profile = {
            **existing,
            "animal_key": key,
            "id": sanitize_id(self.id_edit.text()),
            "animal_type": normalize_animal_type(str(self.species_combo.currentData() or "")),
            "display_name": self.name_edit.text().strip(),
            "baseline_settings": existing.get("baseline_settings") or {"enabled": False, "temp_delta_c": 1.0, "bpm_delta": 15.0, "min_records": 5},
            "created": existing.get("created") or now,
            "updated": now,
        }
        if self.pending_photo_source:
            profile["photo_path"] = str(self.copy_photo(self.pending_photo_source, key))
            self.pending_photo_source = None
        if old_key and old_key != key:
            self.profiles.pop(old_key, None)
        self.profiles[key] = profile
        self.current_key = key
        self.save_profiles()
        self.reload_data()
        self.select_animal(key)
        return key

    def save_alert_settings(self):
        enabled = bool(self.baseline_enabled.isChecked())
        key = self.current_form_key()
        if not key:
            QtWidgets.QMessageBox.warning(self, "Animales", "Guarda primero un crotal/ID real para activar avisos.")
            return
        now = datetime.now().isoformat()
        existing = self.profile_for_key(key)
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

    def copy_photo(self, source: Path, key: str) -> Path:
        ANIMAL_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
        suffix = source.suffix.lower() if source.suffix else ".jpg"
        target = ANIMAL_PHOTO_DIR / f"{safe_file_part(key)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}"
        shutil.copy2(source, target)
        return target

    def update_photo(self, profile: dict):
        self.photo_label.setPixmap(QtGui.QPixmap())
        path_text = str(profile.get("photo_path") or "")
        path = Path(path_text) if path_text else None
        if not path or not path.exists():
            self.photo_label.setText("Sin foto")
            return
        pix = QtGui.QPixmap(str(path))
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

    def update_summary(self):
        measurements = self.current_measurements()
        values = {
            "BPM medio": self.mean_value(measurements, "bpm"),
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
        self.summary_text.setPlainText("\n".join(lines))

    def populate_history(self):
        measurements = self.current_measurements()
        self.history_table.setRowCount(0)
        for measurement in measurements:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            data = measurement.row
            bpm = _as_float(data.get("bpm", ""))
            ref_avg, _count = _mean_ref_pulse(data.get("pulso_previo"), data.get("pulso_final_pulsio"), data.get("pulso_final_fonendo"))
            values = [
                data.get("fecha", ""),
                data.get("hora", ""),
                data.get("id", ""),
                animal_label(data.get("animal_type", "")),
                _mode_label(data.get("modo", "")),
                data.get("config_label", ""),
                fmt(bpm, 1, ""),
                fmt(ref_avg, 1, ""),
                fmt(_as_float(data.get("spo2_pct", "")), 1, ""),
                fmt(self.measurement_temp(data), 1, ""),
                fmt(_as_float(data.get("calidad", "")), 0, ""),
                measurement.files.get("raw", Path(data.get("raw", ""))).name if (measurement.files.get("raw") or data.get("raw")) else "",
            ]
            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(str(value))
                raw = measurement.files.get("raw")
                if raw:
                    item.setData(QtCore.Qt.ItemDataRole.UserRole, str(raw))
                self.history_table.setItem(row, col, item)
        self.history_table.resizeColumnsToContents()

    def populate_files(self):
        self.files_table.setRowCount(0)
        for measurement in self.current_measurements():
            stamp = f"{measurement.row.get('fecha', '')} {measurement.row.get('hora', '')}".strip()
            for kind, path in sorted(measurement.files.items()):
                if not path.exists():
                    continue
                row = self.files_table.rowCount()
                self.files_table.insertRow(row)
                values = [stamp, kind, path.name, str(path)]
                for col, value in enumerate(values):
                    item = QtWidgets.QTableWidgetItem(value)
                    item.setData(QtCore.Qt.ItemDataRole.UserRole, str(path))
                    self.files_table.setItem(row, col, item)
        self.files_table.resizeColumnsToContents()

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
        item = self.history_table.item(index.row(), 0)
        path_text = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else ""
        self.open_path(Path(path_text) if path_text else None)

    def open_selected_file(self, index: QtCore.QModelIndex):
        if not index.isValid():
            return
        item = self.files_table.item(index.row(), 0)
        path_text = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else ""
        self.open_path(Path(path_text) if path_text else None)

    def open_path(self, path: Path | None):
        if not path or not path.exists():
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

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
        values = {
            "bpm": metrics.get("bpm"),
            "spo2_pct": metrics.get("spo2"),
            "calidad": metrics.get("quality"),
            "calidad_label": metrics.get("quality_label"),
            "temp_c_final_max_5s": temp.get("temp_c_final_max_5s"),
            "temp_c_media": temp.get("temp_c_mean"),
            "temp_rt_c_final_max_5s": temp.get("temp_rt_c_final_max_5s"),
            "temp_lt_c_final_max_5s": temp.get("temp_lt_c_final_max_5s"),
            "temp_flt_c_final_max_5s": temp.get("temp_flt_c_final_max_5s"),
            "temp_frt_c_final_max_5s": temp.get("temp_frt_c_final_max_5s"),
            "temp_rlt_c_final_max_5s": temp.get("temp_rlt_c_final_max_5s"),
            "temp_rrt_c_final_max_5s": temp.get("temp_rrt_c_final_max_5s"),
            "pulso_previo": manual.get("pulso_previo"),
            "pulso_final_pulsio": manual.get("pulso_final_pulsio"),
            "pulso_final_fonendo": manual.get("pulso_final_fonendo"),
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
