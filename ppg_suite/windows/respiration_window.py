from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from ..paths import RAW_DIR
from respiration import RespirationConfig, RespirationMetrics, analyze_respiration
from respiration.batch import load_raw_csv
from respiration.plotting import build_diagnostic_layout
from respiration.preprocessing import prepare_respiration_signal
from respiration.riav import compute_riav, detect_cardiac_beats
from respiration.rifv import compute_rifv
from respiration.riiv import compute_riiv
from respiration.windows import analyze_windows


def _duration_band(duration_s: float) -> str:
    if not np.isfinite(duration_s):
        return "desconocida"
    if duration_s >= 120.0:
        return "ideal"
    if duration_s >= 60.0:
        return "recomendado"
    if duration_s >= 45.0:
        return "aceptable"
    if duration_s >= 30.0:
        return "baja confianza"
    return "insuficiente"


@dataclass
class RespirationRawInfo:
    path: Path
    duration_s: float
    rows: int
    animal: str = ""
    date: str = ""


class RespirationWindow(QtWidgets.QMainWindow):
    back_to_menu = QtCore.pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PPG Suite | Respiración")
        self.resize(1360, 880)
        self.cfg = RespirationConfig()
        self.raw_files: list[RespirationRawInfo] = []
        self.current_path: Path | None = None
        self._build_ui()
        self.reload_raws()

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
        title = QtWidgets.QLabel("Respiración (RR) desde registros ya realizados")
        title.setStyleSheet("font-size: 14pt; font-weight: bold;")
        top.addWidget(title)
        top.addStretch(1)
        self.btn_reload = QtWidgets.QPushButton("Recargar registros")
        self.btn_reload.clicked.connect(self.reload_raws)
        top.addWidget(self.btn_reload)

        controls = QtWidgets.QGroupBox("Selección de registro (raw)")
        cl = QtWidgets.QGridLayout(controls)
        self.text_filter = QtWidgets.QLineEdit()
        self.text_filter.setPlaceholderText("Filtrar por archivo, animal o fecha")
        self.duration_threshold = QtWidgets.QDoubleSpinBox()
        self.duration_threshold.setRange(0.0, 600.0)
        self.duration_threshold.setValue(30.0)
        self.duration_threshold.setSuffix(" s")
        self.duration_threshold.setToolTip(
            "Umbral mínimo recomendado: 30 s (mínimo operativo). 45-59 s aceptable, "
            "60-119 s recomendado, >=120 s ideal para validación."
        )
        self.chk_show_below_threshold = QtWidgets.QCheckBox("Mostrar también por debajo del umbral")
        self.btn_analyze = QtWidgets.QPushButton("Analizar respiración")
        self.btn_analyze.setMinimumHeight(36)
        self.btn_analyze.setStyleSheet("font-weight: bold;")
        self.btn_analyze.setEnabled(False)
        cl.addWidget(QtWidgets.QLabel("Texto"), 0, 0)
        cl.addWidget(self.text_filter, 0, 1)
        cl.addWidget(QtWidgets.QLabel("Duración mínima"), 0, 2)
        cl.addWidget(self.duration_threshold, 0, 3)
        cl.addWidget(self.chk_show_below_threshold, 0, 4)
        cl.addWidget(self.btn_analyze, 0, 5)
        root.addWidget(controls)
        self.text_filter.textChanged.connect(self.apply_filters)
        self.duration_threshold.valueChanged.connect(self.apply_filters)
        self.chk_show_below_threshold.toggled.connect(self.apply_filters)
        self.btn_analyze.clicked.connect(self.analyze_current)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        root.addWidget(splitter, stretch=1)

        self.raw_table = QtWidgets.QTableWidget(0, 5)
        self.raw_table.setHorizontalHeaderLabels(["Animal", "Fecha", "Duración (s)", "Banda", "Archivo"])
        self.raw_table.verticalHeader().setVisible(False)
        self.raw_table.setAlternatingRowColors(True)
        self.raw_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.raw_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.raw_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.raw_table.itemSelectionChanged.connect(self._on_selection_changed)
        self.raw_table.doubleClicked.connect(self.analyze_current)
        splitter.addWidget(self.raw_table)

        bottom_split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(bottom_split)
        splitter.setSizes([320, 560])

        self.details = QtWidgets.QTextEdit()
        self.details.setReadOnly(True)
        bottom_split.addWidget(self.details)

        self.plot_container = QtWidgets.QVBoxLayout()
        plot_widget = QtWidgets.QWidget()
        plot_widget.setLayout(self.plot_container)
        bottom_split.addWidget(plot_widget)
        bottom_split.setSizes([420, 760])
        self._plot_layout = None

    def reload_raws(self):
        infos: list[RespirationRawInfo] = []
        if RAW_DIR.exists():
            paths = sorted(RAW_DIR.glob("raw_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in paths:
                t, red, ir, meta = load_raw_csv(path)
                if t.size < 2:
                    continue
                duration_s = float(t[-1] - t[0])
                infos.append(
                    RespirationRawInfo(
                        path=path,
                        duration_s=duration_s,
                        rows=int(t.size),
                        animal=meta.get("id", ""),
                        date=path.stem,
                    )
                )
        infos.sort(key=lambda info: info.duration_s, reverse=True)
        self.raw_files = infos
        self.apply_filters()

    def apply_filters(self):
        text = self.text_filter.text().strip().lower()
        threshold = self.duration_threshold.value()
        show_below = self.chk_show_below_threshold.isChecked()
        self.raw_table.setRowCount(0)
        for info in self.raw_files:
            if not show_below and info.duration_s < threshold:
                continue
            haystack = f"{info.path.name} {info.animal} {info.date}".lower()
            if text and text not in haystack:
                continue
            row = self.raw_table.rowCount()
            self.raw_table.insertRow(row)
            band = _duration_band(info.duration_s)
            values = [info.animal, info.date, f"{info.duration_s:.1f}", band, info.path.name]
            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, str(info.path))
                if band == "insuficiente":
                    item.setForeground(QtGui.QColor("#a33"))
                elif band == "baja confianza":
                    item.setForeground(QtGui.QColor("#a67c00"))
                elif band in ("recomendado", "ideal"):
                    item.setForeground(QtGui.QColor("#1f4f35"))
                self.raw_table.setItem(row, col, item)
        self.raw_table.resizeColumnsToContents()

    def _on_selection_changed(self):
        self.btn_analyze.setEnabled(self.raw_table.currentRow() >= 0)

    def _selected_path(self) -> Path | None:
        row = self.raw_table.currentRow()
        if row < 0:
            return None
        item = self.raw_table.item(row, 0)
        if item is None:
            return None
        return Path(item.data(QtCore.Qt.ItemDataRole.UserRole))

    def analyze_current(self):
        path = self._selected_path()
        if path is None:
            return
        self.current_path = path
        t, red, ir, meta = load_raw_csv(path)
        metrics = analyze_respiration(t, red, ir, self.cfg)
        self._show_details(path, metrics)
        self._show_diagnostics(t, red, ir)

    def _show_details(self, path: Path, m: RespirationMetrics):
        def fmt(value: float, decimals: int = 1) -> str:
            return f"{value:.{decimals}f}" if np.isfinite(value) else "-"

        lines = [
            f"<h3>{path.name}</h3>",
            f"<b>RR:</b> {fmt(m.rr)} rpm &nbsp; <b>Confianza:</b> {fmt(m.confidence)} % &nbsp; "
            f"<b>Válido:</b> {'sí' if m.valid else 'no'}",
            f"<b>Duración usable:</b> {fmt(m.usable_duration_s)} s &nbsp; "
            f"<b>Ciclos estimados:</b> {fmt(m.respiratory_cycles_estimated)}",
            "<hr>",
            "<b>Estimadores</b>",
            f"RIIV IR: {fmt(m.rr_riiv_ir)} &nbsp; RIIV RED: {fmt(m.rr_riiv_red)}",
            f"RIAV IR: {fmt(m.rr_riav_ir)} &nbsp; RIAV RED: {fmt(m.rr_riav_red)}",
            f"RIFV: {fmt(m.rr_rifv)}",
            f"FFT: {fmt(m.rr_fft)} &nbsp; Autocorrelación: {fmt(m.rr_autocorr)}",
            "<hr>",
            "<b>Concordancia y estabilidad</b>",
            f"FFT vs autocorr: {fmt(m.agreement_methods)} % &nbsp; RED vs IR: {fmt(m.agreement_red_ir)} % &nbsp; "
            f"Entre estimadores: {fmt(m.agreement_estimators)} %",
            f"Estabilidad temporal: {fmt(m.stability)} %",
            f"Artefactos IR: {fmt(m.artifact_ir_pct)} % &nbsp; Artefactos RED: {fmt(m.artifact_red_pct)} %",
            "<hr>",
            f"<b>Fuente final:</b> {m.final_source or '-'}",
            f"<b>Motivo:</b> {m.reason or '-'}",
        ]
        self.details.setHtml("<br>".join(lines))

    def _show_diagnostics(self, t: np.ndarray, red: np.ndarray, ir: np.ndarray):
        while self.plot_container.count():
            item = self.plot_container.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        prepared = prepare_respiration_signal(t, red, ir, self.cfg)
        if not prepared.valid:
            placeholder = QtWidgets.QLabel(f"Sin diagnóstico gráfico: {prepared.reason}")
            self.plot_container.addWidget(placeholder)
            return

        riiv_ir = compute_riiv(prepared.resp_ir, prepared.resp_hz, self.cfg)
        riiv_red = compute_riiv(prepared.resp_red, prepared.resp_hz, self.cfg) if prepared.has_red else np.asarray([])
        pt, pv, tv = detect_cardiac_beats(prepared.fine_t, prepared.fine_ir, prepared.fine_hz, self.cfg)
        riav_ir = compute_riav(pt, pv, tv, prepared.resp_t, prepared.resp_hz, self.cfg)
        rifv = compute_rifv(pt, prepared.resp_t, prepared.resp_hz, self.cfg)
        riav_red = np.asarray([])
        if prepared.has_red:
            pt_r, pv_r, tv_r = detect_cardiac_beats(prepared.fine_t, prepared.fine_red, prepared.fine_hz, self.cfg)
            riav_red = compute_riav(pt_r, pv_r, tv_r, prepared.resp_t, prepared.resp_hz, self.cfg)
        windowed = analyze_windows(prepared, self.cfg)

        layout = build_diagnostic_layout(
            title=self.current_path.name if self.current_path else "",
            fine_t=prepared.fine_t,
            fine_ir=prepared.fine_ir,
            fine_red=prepared.fine_red,
            riiv_ir=riiv_ir,
            riiv_red=riiv_red,
            riav_ir=riav_ir,
            riav_red=riav_red,
            rifv=rifv,
            resp_t=prepared.resp_t,
            resp_hz=prepared.resp_hz,
            windows=windowed.windows,
            respiratory_low_hz=self.cfg.respiratory_low_hz,
            respiratory_high_hz=self.cfg.respiratory_high_hz,
        )
        self._plot_layout = layout
        self.plot_container.addWidget(layout)
