from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from .models import AnalysisConfig, SensorConfig


class SensorConfigWidget(QtWidgets.QGroupBox):
    config_changed = QtCore.pyqtSignal()

    def __init__(self, title: str = "Configuración MAX3010x"):
        super().__init__(title)
        layout = QtWidgets.QGridLayout(self)
        self.red = QtWidgets.QSpinBox(); self.red.setRange(0, 255); self.red.setValue(31)
        self.ir = QtWidgets.QSpinBox(); self.ir.setRange(0, 255); self.ir.setValue(31)
        self.avg = QtWidgets.QComboBox(); self.avg.addItems(["1", "2", "4", "8", "16", "32"])
        self.rate = QtWidgets.QComboBox(); self.rate.addItems(["50", "100", "200", "400", "800", "1000", "1600", "3200"]); self.rate.setCurrentText("100")
        self.width = QtWidgets.QComboBox(); self.width.addItems(["69", "118", "215", "411"]); self.width.setCurrentText("411")
        self.adc = QtWidgets.QComboBox(); self.adc.addItems(["2048", "4096", "8192", "16384"]); self.adc.setCurrentText("16384")
        self.skip = QtWidgets.QSpinBox(); self.skip.setRange(0, 200); self.skip.setValue(10)
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
        self.bpm_min = QtWidgets.QSpinBox(); self.bpm_min.setRange(20, 180); self.bpm_min.setValue(45)
        self.bpm_max = QtWidgets.QSpinBox(); self.bpm_max.setRange(60, 260); self.bpm_max.setValue(180)
        self.thr = QtWidgets.QDoubleSpinBox(); self.thr.setRange(0.1, 3.0); self.thr.setDecimals(2); self.thr.setSingleStep(0.05); self.thr.setValue(0.55)
        self.detrend = QtWidgets.QDoubleSpinBox(); self.detrend.setRange(0.3, 8.0); self.detrend.setDecimals(2); self.detrend.setSingleStep(0.1); self.detrend.setValue(2.0)
        self.smooth = QtWidgets.QDoubleSpinBox(); self.smooth.setRange(0.01, 0.5); self.smooth.setDecimals(2); self.smooth.setSingleStep(0.01); self.smooth.setValue(0.07)
        self.ignore = QtWidgets.QDoubleSpinBox(); self.ignore.setRange(0, 10); self.ignore.setDecimals(1); self.ignore.setSingleStep(0.5); self.ignore.setValue(1.0)
        self.min_quality = QtWidgets.QDoubleSpinBox(); self.min_quality.setRange(0, 100); self.min_quality.setDecimals(0); self.min_quality.setValue(45)
        self.spo2_formula = QtWidgets.QComboBox(); self.spo2_formula.addItems(["quad", "linear_104_17", "linear_110_25", "custom"])
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


