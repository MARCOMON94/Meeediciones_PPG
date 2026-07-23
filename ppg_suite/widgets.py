from __future__ import annotations

import json

from PyQt6 import QtCore, QtWidgets

from .animal_config import animal_label
from .models import AnalysisConfig, SensorConfig
from .paths import ANIMALS_DIR
from .utils import sanitize_id


class NoWheelSpinBox(QtWidgets.QSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class NoWheelDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class NoWheelComboBox(QtWidgets.QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class SensorConfigWidget(QtWidgets.QGroupBox):
    config_changed = QtCore.pyqtSignal()

    def __init__(self, title: str = "Configuración MAX3010x"):
        super().__init__(title)
        layout = QtWidgets.QGridLayout(self)
        self.red = NoWheelSpinBox(); self.red.setRange(0, 255); self.red.setValue(63)
        self.ir = NoWheelSpinBox(); self.ir.setRange(0, 255); self.ir.setValue(63)
        self.avg = NoWheelComboBox(); self.avg.addItems(["1", "2", "4", "8", "16", "32"]); self.avg.setCurrentText("4")
        self.rate = NoWheelComboBox(); self.rate.addItems(["50", "100", "200", "400", "800", "1000", "1600", "3200"]); self.rate.setCurrentText("800")
        self.width = NoWheelComboBox(); self.width.addItems(["69", "118", "215", "411"]); self.width.setCurrentText("411")
        self.adc = NoWheelComboBox(); self.adc.addItems(["2048", "4096", "8192", "16384"]); self.adc.setCurrentText("16384")
        self.skip = NoWheelSpinBox(); self.skip.setRange(0, 200); self.skip.setValue(50)
        self.debug = QtWidgets.QCheckBox("")
        labels = ["LED RED", "LED IR", "sampleAverage", "sampleRate", "pulseWidth", "adcRange", "Skip inicio"]
        widgets = [self.red, self.ir, self.avg, self.rate, self.width, self.adc, self.skip]
        for row, (lab, wid) in enumerate(zip(labels, widgets)):
            layout.addWidget(QtWidgets.QLabel(lab), row, 0)
            layout.addWidget(wid, row, 1)
        self.debug.setVisible(False)
        for w in widgets:
            if hasattr(w, "valueChanged"):
                w.valueChanged.connect(self.config_changed.emit)
            if hasattr(w, "currentTextChanged"):
                w.currentTextChanged.connect(self.config_changed.emit)
        self.debug.stateChanged.connect(self.config_changed.emit)

    def get_config(self) -> SensorConfig:
        return SensorConfig(
            red=self.red.value(), ir=self.ir.value(), avg=int(self.avg.currentText()), rate=int(self.rate.currentText()),
            width=int(self.width.currentText()), adc=int(self.adc.currentText()), skip=self.skip.value(), debug=self.debug.isChecked()
        ).clean()

    def set_config(self, cfg: SensorConfig):
        self.red.setValue(cfg.red); self.ir.setValue(cfg.ir); self.avg.setCurrentText(str(cfg.avg)); self.rate.setCurrentText(str(cfg.rate))
        self.width.setCurrentText(str(cfg.width)); self.adc.setCurrentText(str(cfg.adc)); self.skip.setValue(cfg.skip); self.debug.setChecked(cfg.debug)

class AnalysisConfigWidget(QtWidgets.QGroupBox):
    config_changed = QtCore.pyqtSignal()

    def __init__(self, title: str = "Análisis"):
        super().__init__(title)
        layout = QtWidgets.QGridLayout(self)
        self.bpm_min = NoWheelSpinBox(); self.bpm_min.setRange(20, 180); self.bpm_min.setValue(45)
        self.bpm_max = NoWheelSpinBox(); self.bpm_max.setRange(60, 260); self.bpm_max.setValue(180)
        self.thr = NoWheelDoubleSpinBox(); self.thr.setRange(0.1, 3.0); self.thr.setDecimals(2); self.thr.setSingleStep(0.05); self.thr.setValue(0.55)
        self.detrend = NoWheelDoubleSpinBox(); self.detrend.setRange(0.3, 8.0); self.detrend.setDecimals(2); self.detrend.setSingleStep(0.1); self.detrend.setValue(2.0)
        self.smooth = NoWheelDoubleSpinBox(); self.smooth.setRange(0.01, 0.5); self.smooth.setDecimals(2); self.smooth.setSingleStep(0.01); self.smooth.setValue(0.07)
        self.ignore = NoWheelDoubleSpinBox(); self.ignore.setRange(0, 10); self.ignore.setDecimals(1); self.ignore.setSingleStep(0.5); self.ignore.setValue(1.0)
        self.min_quality = NoWheelDoubleSpinBox(); self.min_quality.setRange(0, 100); self.min_quality.setDecimals(0); self.min_quality.setValue(45)
        self.spo2_formula = NoWheelComboBox(); self.spo2_formula.addItems(["quad", "linear_104_17", "linear_110_25", "custom"])
        rows = [
            ("BPM mínimo", self.bpm_min), ("BPM máximo", self.bpm_max), ("Umbral picos SD", self.thr),
            ("Detrend s", self.detrend), ("Suavizado s", self.smooth), ("Ignorar inicio s", self.ignore),
            ("Calidad mínima", self.min_quality), ("Fórmula SpO2", self.spo2_formula),
        ]
        for r, (lab, w) in enumerate(rows):
            layout.addWidget(QtWidgets.QLabel(lab), r, 0)
            layout.addWidget(w, r, 1)
            if hasattr(w, "valueChanged"):
                w.valueChanged.connect(self.config_changed.emit)
            if hasattr(w, "currentTextChanged"):
                w.currentTextChanged.connect(self.config_changed.emit)

    def get_config(self) -> AnalysisConfig:
        return AnalysisConfig(
            bpm_min=self.bpm_min.value(), bpm_max=self.bpm_max.value(), peak_threshold_sd=self.thr.value(),
            detrend_seconds=self.detrend.value(), smooth_seconds=self.smooth.value(), ignore_initial_seconds=self.ignore.value(),
            min_quality_to_accept=self.min_quality.value(), spo2_formula=self.spo2_formula.currentText()
        )


_UNASSIGNED_CROTAL_IDS = {"", "SIN_CROTAL", "-", "NONE", "NULL"}


def load_known_animals() -> list[dict[str, str]]:
    """Read saved animal profiles (id/type/display_name) from Animales for pickers.

    Returns an empty list if animals.json is missing/unreadable so callers
    just degrade to a plain free-text field.
    """
    path = ANIMALS_DIR / "animals.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    animals: list[dict[str, str]] = []
    for profile in (data.get("animals") or {}).values():
        animal_id = str(profile.get("id") or "").strip()
        if not animal_id:
            continue
        animals.append({
            "id": animal_id,
            "animal_type": str(profile.get("animal_type") or ""),
            "display_name": str(profile.get("display_name") or ""),
        })
    animals.sort(key=lambda a: a["id"].lower())
    return animals


class AnimalCrotalPicker(QtWidgets.QWidget):
    """Crotal/ID entry for measurement screens.

    Drop-in replacement for a plain QLineEdit (exposes .text()/.setText()/.clear()):
    an editable combo box pre-filled with animals already registered in
    'Animales', with autocomplete, plus a live hint saying whether the typed
    crotal matches an existing animal (the new capture will be added to that
    animal's history) or will create a new one. This exists to avoid
    accidental near-duplicate animals from typos (e.g. "0776" vs "776", which
    sanitize_id does not catch) without blocking the normal, frequent case of
    re-measuring the same animal.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.combo = NoWheelComboBox()
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self._known = load_known_animals()
        self.combo.addItem("SIN_CROTAL")
        for animal in self._known:
            self.combo.addItem(self._item_label(animal), animal["id"])
        self.combo.setCurrentText("SIN_CROTAL")
        completer = QtWidgets.QCompleter([animal["id"] for animal in self._known], self.combo)
        completer.setCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(QtCore.Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QtWidgets.QCompleter.CompletionMode.PopupCompletion)
        self.combo.setCompleter(completer)
        layout.addWidget(self.combo)
        self.hint = QtWidgets.QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #555555; font-size: 11px;")
        layout.addWidget(self.hint)
        self.combo.editTextChanged.connect(self._update_hint)
        self.combo.activated.connect(self._on_item_activated)
        self._update_hint(self.combo.currentText())

    @staticmethod
    def _item_label(animal: dict[str, str]) -> str:
        species = animal_label(animal["animal_type"]) if animal["animal_type"] else ""
        extra = " / ".join(part for part in (species, animal["display_name"]) if part)
        return f"{animal['id']} ({extra})" if extra else animal["id"]

    def _on_item_activated(self, index: int):
        # Selecting a dropdown item shows its decorated label; put the plain
        # id back in the edit field so .text() keeps returning just the crotal.
        animal_id = self.combo.itemData(index)
        if animal_id:
            self.combo.setEditText(str(animal_id))

    def _match_for_text(self, text: str) -> dict[str, str] | None:
        clean = sanitize_id(text)
        if not clean or clean.upper() in _UNASSIGNED_CROTAL_IDS:
            return None
        for animal in self._known:
            if sanitize_id(animal["id"]) == clean:
                return animal
        return None

    def _update_hint(self, text: str):
        match = self._match_for_text(text)
        if match:
            species = animal_label(match["animal_type"]) if match["animal_type"] else ""
            extra = " - ".join(part for part in (species, match["display_name"]) if part)
            suffix = f" ({extra})" if extra else ""
            self.hint.setText(f"Animal ya existente{suffix}: esta toma se añadirá a su historial.")
            self.hint.setStyleSheet("color: #1a6b1a; font-size: 11px;")
            return
        clean = sanitize_id(text)
        if clean and clean.upper() not in _UNASSIGNED_CROTAL_IDS:
            self.hint.setText("Crotal nuevo: se creará una ficha nueva en Animales al guardar.")
            self.hint.setStyleSheet("color: #555555; font-size: 11px;")
        else:
            self.hint.setText("")

    def text(self) -> str:
        return self.combo.currentText()

    def setText(self, value: str):
        self.combo.setCurrentText(value)

    def clear(self):
        self.combo.setCurrentText("SIN_CROTAL")


