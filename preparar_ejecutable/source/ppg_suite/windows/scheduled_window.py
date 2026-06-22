from __future__ import annotations

import math
import time
import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets

from ..models import SensorConfig
from ..processing import block_bpm, detect_artifacts, estimate_bpm_peaks, estimate_hz, processed_for_plot, score_and_merge_metrics, spo2_support_message
from ..utils import fmt, safe_float_text, sanitize_id, now_stamp
from ..widgets import AnalysisConfigWidget, NoWheelDoubleSpinBox, NoWheelSpinBox, SensorConfigWidget
from ..paths import DOCUMENTS_DIR, FIGURES_DIR, PROCESSED_DIR, RAW_DIR, REPORT_DIR, RESULTS_DIR
from ..utils import open_folder
from .measurement_window import PPGSuite, TEMP_MAPPING_INVERTED, temperature_channel_summary


@dataclass(frozen=True)
class ScheduledStep:
    label: str
    description: str
    config: SensorConfig


@dataclass
class ScheduledSegment:
    step: ScheduledStep
    index: int
    start_sample: int
    end_sample: int | None = None
    pulse_prev: str = ""
    pulse_final_pulsio: str = ""
    pulse_final_fonendo: str = ""


def build_64_config_steps() -> list[ScheduledStep]:
    steps: list[ScheduledStep] = []
    idx = 1
    for adc in (8192, 16384):
        for avg in (1, 4):
            for ir in (31, 63, 95, 127):
                for red in (31, 63, 95, 127):
                    label = f"CONFIG {idx:02d} - RED{red} IR{ir} AVG{avg} ADC{adc}"
                    desc = f"Barrido 64 configuraciones: RED={red}, IR={ir}, AVG={avg}, ADC={adc}"
                    steps.append(ScheduledStep(label, desc, SensorConfig(red=red, ir=ir, avg=avg, rate=800, width=411, adc=adc, skip=50)))
                    idx += 1
    return steps


def build_12_config_steps() -> list[ScheduledStep]:
    specs = [
        ("B1F_AVG1_ADC8192", 31, 1, 8192, "Brillo bajo, sin promediado, rango ADC 8192"),
        ("B1F_AVG1_ADC16384", 31, 1, 16384, "Brillo bajo, sin promediado, rango ADC 16384"),
        ("B1F_AVG4_ADC8192", 31, 4, 8192, "Brillo bajo, promediado x4, rango ADC 8192"),
        ("B1F_AVG4_ADC16384", 31, 4, 16384, "Brillo bajo, promediado x4, rango ADC 16384"),
        ("B3F_AVG1_ADC8192", 63, 1, 8192, "Brillo medio, sin promediado, rango ADC 8192"),
        ("B3F_AVG1_ADC16384", 63, 1, 16384, "Brillo medio, sin promediado, rango ADC 16384"),
        ("B3F_AVG4_ADC8192", 63, 4, 8192, "Brillo medio, promediado x4, rango ADC 8192"),
        ("B3F_AVG4_ADC16384", 63, 4, 16384, "Brillo medio, promediado x4, rango ADC 16384"),
        ("B7F_AVG1_ADC8192", 127, 1, 8192, "Brillo alto, sin promediado, rango ADC 8192"),
        ("B7F_AVG1_ADC16384", 127, 1, 16384, "Brillo alto, sin promediado, rango ADC 16384"),
        ("B7F_AVG4_ADC8192", 127, 4, 8192, "Brillo alto, promediado x4, rango ADC 8192"),
        ("B7F_AVG4_ADC16384", 127, 4, 16384, "Brillo alto, promediado x4, rango ADC 16384"),
    ]
    steps: list[ScheduledStep] = []
    for idx, (name, brightness, avg, adc, desc) in enumerate(specs, start=1):
        label = f"CONFIG {idx:02d} - {name}"
        cfg = SensorConfig(red=brightness, ir=brightness, avg=avg, rate=800, width=411, adc=adc, skip=50)
        steps.append(ScheduledStep(label, desc, cfg))
    return steps


def build_3m_search_space() -> list[SensorConfig]:
    configs: list[SensorConfig] = []
    seen: set[tuple[int, int, int, int]] = set()
    for adc in (16384, 8192):
        for avg in (1, 2, 4, 8):
            for ir in (24, 31, 47, 63, 95, 127, 159):
                red_values = [ir, int(round(ir * 0.75)), int(round(ir * 1.25))]
                for red in red_values:
                    cfg = SensorConfig(red=red, ir=ir, avg=avg, rate=800, width=411, adc=adc, skip=50).clean()
                    key = (cfg.red, cfg.ir, cfg.avg, cfg.adc)
                    if key in seen:
                        continue
                    seen.add(key)
                    configs.append(cfg)
    return configs


def make_3m_step(index: int, cfg: SensorConfig, reason: str) -> ScheduledStep:
    label = f"3M {index:02d} - RED{cfg.red} IR{cfg.ir} AVG{cfg.avg} ADC{cfg.adc}"
    desc = f"Experimento 3M: {reason}"
    return ScheduledStep(label, desc, cfg)


def _ref_pulse(value: object) -> float:
    try:
        bpm = float(str(value if value is not None else "").replace(",", "."))
    except (TypeError, ValueError):
        return math.nan
    return bpm if math.isfinite(bpm) and bpm > 0 else math.nan


def _ref_average(*values: object) -> tuple[float, int]:
    valid = [_ref_pulse(value) for value in values]
    valid = [value for value in valid if math.isfinite(value)]
    if not valid:
        return math.nan, 0
    return float(np.mean(valid)), len(valid)


class ScheduledConfigWindow(PPGSuite):
    def __init__(self, title: str, steps: list[ScheduledStep], total_duration_s: float, condition: str):
        self.scheduled_title = title
        self.scheduled_steps = steps
        self.scheduled_total_duration_s = float(total_duration_s)
        self.scheduled_condition = condition
        self.scheduled_step_index = 0
        self.scheduled_step_start_wall = 0.0
        self.scheduled_step_duration_s = self.scheduled_total_duration_s / max(1, len(self.scheduled_steps))
        self.scheduled_segments: list[ScheduledSegment] = []
        super().__init__("test")
        self.setWindowTitle(f"PPG Suite v8 | {title}")
        self.resize(1120, 740)

    def capture_mode_name(self) -> str:
        return "configurations"

    def build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)

        left = QtWidgets.QVBoxLayout()
        root.addLayout(left, stretch=0)

        serial_group = QtWidgets.QGroupBox("Puerto")
        serial_layout = QtWidgets.QGridLayout(serial_group)
        self.port_combo = QtWidgets.QComboBox()
        self.btn_refresh_ports = QtWidgets.QPushButton("Refrescar")
        self.btn_connect = QtWidgets.QPushButton("Conectar")
        serial_layout.addWidget(self.port_combo, 0, 0, 1, 2)
        serial_layout.addWidget(self.btn_refresh_ports, 1, 0)
        serial_layout.addWidget(self.btn_connect, 1, 1)
        left.addWidget(serial_group)
        self.btn_refresh_ports.clicked.connect(self.refresh_ports)
        self.btn_connect.clicked.connect(self.connect_selected_port)

        self.sensor_widget = SensorConfigWidget()
        self.sensor_widget.setVisible(False)
        self.analysis_widget = AnalysisConfigWidget()
        self.analysis_widget.setVisible(False)

        capture_group = QtWidgets.QGroupBox(self.scheduled_title)
        form = QtWidgets.QFormLayout(capture_group)
        self.crotal_edit = QtWidgets.QLineEdit("SIN_CROTAL")
        self.prev_pulse_edit = QtWidgets.QLineEdit()
        self.udder_combo = QtWidgets.QComboBox()
        self.configure_udder_combo(self.udder_combo)
        self.temp_mapping_combo = QtWidgets.QComboBox()
        self.configure_temp_mapping_combo(self.temp_mapping_combo)
        self.vacuum_combo = QtWidgets.QComboBox()
        self.vacuum_combo.addItems(["", "con vacio", "sin vacio"])
        self.condition_edit = QtWidgets.QLineEdit(self.scheduled_condition)
        self.duration_spin = NoWheelDoubleSpinBox()
        self.duration_spin.setRange(1, 120)
        self.duration_spin.setDecimals(1)
        self.duration_spin.setValue(self.scheduled_total_duration_s / 60.0)
        self.duration_spin.setSuffix(" min")
        form.addRow("Crotal:", self.crotal_edit)
        form.addRow("Pulso previo ref.:", self.prev_pulse_edit)
        form.addRow("Ubre:", self.udder_combo)
        form.addRow("Asignacion termometros:", self.temp_mapping_combo)
        form.addRow("Medicion:", self.vacuum_combo)
        form.addRow("Condiciones:", self.condition_edit)
        form.addRow("Duración total:", self.duration_spin)
        self.duration_warning = QtWidgets.QLabel(
            "Aviso: para comparar configuraciones, usa al menos 10-15 s por fila. "
            "Con menos tiempo puede no calcular BPM o calidad fiable."
        )
        self.duration_warning.setWordWrap(True)
        self.duration_warning.setStyleSheet("color: #8a5a00; font-weight: bold;")
        form.addRow("", self.duration_warning)
        left.addWidget(capture_group)

        self.btn_start = QtWidgets.QPushButton("Iniciar bloque")
        self.btn_stop = QtWidgets.QPushButton("Parar")
        self.btn_open_base = QtWidgets.QPushButton("Abrir resultados")
        self.btn_back_menu = QtWidgets.QPushButton("Volver al menú inicial")
        for b in [self.btn_start, self.btn_stop, self.btn_open_base, self.btn_back_menu]:
            b.setMinimumHeight(42)
            left.addWidget(b)
        self.btn_start.clicked.connect(self.start_scheduled_capture)
        self.btn_stop.clicked.connect(lambda: self.stop_capture("STOP_BLOQUE_MANUAL"))
        self.btn_open_base.clicked.connect(lambda: open_folder(RESULTS_DIR))
        self.btn_back_menu.clicked.connect(self.return_to_menu)

        self.info = QtWidgets.QLabel()
        self.info.setFont(QtGui.QFont("Consolas", 9))
        self.info.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.info.setWordWrap(True)
        self.info.setMinimumWidth(390)
        left.addWidget(self.info, stretch=1)

        self.tabs = QtWidgets.QTabWidget()
        root.addWidget(self.tabs, stretch=1)
        self.build_plot_tabs()

    def build_plot_tabs(self):
        import pyqtgraph as pg

        self.plot_main = pg.PlotWidget(title="IR y RED normalizadas/procesadas")
        self.plot_main.setBackground("w")
        self.plot_main.showGrid(x=True, y=True, alpha=0.25)
        self.plot_main.setLabel("bottom", "Tiempo", units="s")
        self.ir_curve = self.plot_main.plot([], [], pen=pg.mkPen((0, 80, 220), width=2), name="IR")
        self.red_curve = self.plot_main.plot([], [], pen=pg.mkPen((220, 30, 30), width=1), name="RED")
        self.tabs.addTab(self.plot_main, "Señal")

        self.plot_trend = pg.PlotWidget(title="Rolling vivo | BPM / SpO2")
        self.plot_trend.setBackground("w")
        self.plot_trend.showGrid(x=True, y=True, alpha=0.25)
        self.trend_bpm_curve = self.plot_trend.plot([], [], pen=pg.mkPen((30, 140, 40), width=2))
        self.trend_spo2_curve = self.plot_trend.plot([], [], pen=pg.mkPen((160, 60, 160), width=2))
        self.tabs.addTab(self.plot_trend, "Rolling")

    def start_scheduled_capture(self):
        if not self.serial_port or not self.serial_port.is_open:
            QtWidgets.QMessageBox.warning(self, "Serial", "No hay puerto serie abierto.")
            return
        if self.state.capturing:
            return
        self.reset_capture_state(keep_identity=False)
        st = self.state
        st.mode = self.capture_mode_name()
        st.requested_duration_s = float(self.duration_spin.value()) * 60.0
        self.scheduled_total_duration_s = st.requested_duration_s
        self.scheduled_step_duration_s = self.scheduled_total_duration_s / max(1, len(self.scheduled_steps))
        self.scheduled_step_index = 0
        self.scheduled_segments = []
        st.crotal_id = sanitize_id(self.crotal_edit.text())
        pulse_prev = self.ensure_initial_pulse_or_confirm()
        if pulse_prev is None:
            return
        st.pulse_prev = pulse_prev
        st.measurement_condition = self.current_condition_text() or self.scheduled_condition
        st.udder_side = self.current_udder_text()
        st.temp_mapping = self.current_temp_mapping()
        st.temp_primary_channel = self.current_temp_primary_channel()
        st.vacuum_condition = self.current_vacuum_text()
        st.base_name = f"BLOQUE_{len(self.scheduled_steps)}CFG_{st.crotal_id}_{now_stamp()}"
        st.session_id = st.base_name
        st.capture_start_wall = time.time()
        st.capturing = False
        try:
            self.serial_port.reset_input_buffer()
            self.serial_port.reset_output_buffer()
        except Exception:
            pass
        first_step = self.scheduled_steps[0]
        self.scheduled_step_index = 0
        self.state.config_label = first_step.label
        self.sensor_widget.set_config(first_step.config)
        if not self.apply_config_and_wait(first_step.config, show_warning=True):
            st.capturing = False
            return
        try:
            self.serial_port.reset_input_buffer()
        except Exception:
            pass
        self.open_raw_file()
        self.scheduled_segments.append(ScheduledSegment(first_step, 0, 0, pulse_prev=st.pulse_prev))
        self.save_current_config_json(prefix=f"config_{st.base_name}")
        self.send_command("START_CONTINUOUS")
        self.scheduled_step_start_wall = time.time()
        st.capturing = True

    def ask_transition_reference(self, previous_step: ScheduledStep, next_step: ScheduledStep | None) -> tuple[str, str]:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Pulso entre configuraciones")
        form = QtWidgets.QFormLayout(dialog)
        next_label = next_step.label if next_step is not None else "el programa elegira la siguiente configuracion"
        info = QtWidgets.QLabel(
            f"Terminada:\n{previous_step.label}\n\n"
            f"Siguiente:\n{next_label}\n\n"
            "Introduce las lecturas manuales. Los valores 0 o vacios se ignoraran en la media de referencia."
        )
        info.setWordWrap(True)
        pulsio = QtWidgets.QLineEdit()
        pulsio.setPlaceholderText("Ej.: 72")
        fonendo = QtWidgets.QLineEdit()
        fonendo.setPlaceholderText("Opcional. Ej.: 74")
        form.addRow(info)
        form.addRow("Pulsioximetro:", pulsio)
        form.addRow("Fonendo:", fonendo)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            return safe_float_text(pulsio.text()), safe_float_text(fonendo.text())
        return "", ""

    def ask_last_segment_reference(self) -> tuple[str, str]:
        if not self.scheduled_segments:
            return "", ""
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Pulso final de la ultima configuracion")
        form = QtWidgets.QFormLayout(dialog)
        step = self.scheduled_segments[-1].step
        info = QtWidgets.QLabel(
            f"Terminada:\n{step.label}\n\n"
            "Introduce la lectura final del pulsioximetro para esta ultima configuracion."
        )
        info.setWordWrap(True)
        pulsio = QtWidgets.QLineEdit(self.scheduled_segments[-1].pulse_final_pulsio)
        pulsio.setPlaceholderText("Ej.: 72")
        fonendo = QtWidgets.QLineEdit(self.scheduled_segments[-1].pulse_final_fonendo)
        fonendo.setPlaceholderText("Opcional. Ej.: 74")
        form.addRow(info)
        form.addRow("Pulsioximetro final:", pulsio)
        form.addRow("Fonendo final:", fonendo)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            return safe_float_text(pulsio.text()), safe_float_text(fonendo.text())
        return "", ""

    def apply_scheduled_step(self, index: int):
        if self.scheduled_segments and self.scheduled_segments[-1].end_sample is None:
            self.scheduled_segments[-1].end_sample = len(self.state.t)
        step = self.scheduled_steps[index]
        previous = self.scheduled_segments[-1].step if self.scheduled_segments else None
        if previous is not None:
            self.state.capturing = False
            self.send_command("STOP")
            if self.state.raw_handle:
                self.state.raw_handle.flush()
            QtWidgets.QApplication.processEvents()
            pulse_between, fonendo_between = self.ask_transition_reference(previous, step)
            self.scheduled_segments[-1].pulse_final_pulsio = pulse_between
            self.scheduled_segments[-1].pulse_final_fonendo = fonendo_between
            self.state.pulse_prev = pulse_between or fonendo_between
            self.prev_pulse_edit.setText(self.state.pulse_prev)
        self.scheduled_step_index = index
        self.state.config_label = step.label
        self.sensor_widget.set_config(step.config)
        self.apply_sensor_config(step.config)
        try:
            self.serial_port.reset_input_buffer()
        except Exception:
            pass
        self.scheduled_segments.append(ScheduledSegment(step, index, len(self.state.t), pulse_prev=self.state.pulse_prev))
        self.send_command("START_CONTINUOUS")
        self.scheduled_step_start_wall = time.time()
        self.state.capturing = True

    def check_auto_stop(self):
        st = self.state
        if not st.capturing:
            return
        step_elapsed = time.time() - self.scheduled_step_start_wall
        if step_elapsed < self.scheduled_step_duration_s:
            return
        if self.scheduled_step_index >= len(self.scheduled_steps) - 1:
            self.stop_capture("BLOQUE_COMPLETADO")
            return
        self.apply_scheduled_step(self.scheduled_step_index + 1)

    def finalize_capture(self, reason: str):
        if self.scheduled_segments and self.scheduled_segments[-1].end_sample is None:
            self.scheduled_segments[-1].end_sample = len(self.state.t)
        if not self.scheduled_segments:
            super().finalize_capture(reason)
            return
        if (
            not math.isfinite(_ref_pulse(self.scheduled_segments[-1].pulse_final_pulsio))
            and not math.isfinite(_ref_pulse(self.scheduled_segments[-1].pulse_final_fonendo))
        ):
            pulse_final, fonendo_final = self.ask_last_segment_reference()
            self.scheduled_segments[-1].pulse_final_pulsio = pulse_final
            self.scheduled_segments[-1].pulse_final_fonendo = fonendo_final
        written = 0
        for segment in self.scheduled_segments:
            if self.save_segment_capture(segment, reason):
                written += 1
        self.session_handle.flush()
        t_all = np.asarray(self.state.t, dtype=float)
        red_all = np.asarray(self.state.red, dtype=float)
        ir_all = np.asarray(self.state.ir, dtype=float)
        if t_all.size > 1:
            self.save_signal_plot(
                FIGURES_DIR / f"plot_{self.state.base_name}_COMPLETO.png",
                t_all - float(t_all[0]),
                red_all,
                ir_all,
                self.analysis_widget.get_config(),
                f"{self.scheduled_title} - bloque completo",
            )
        self.info.setText(self.info.text() + f"\nGuardadas {written} tomas independientes en la sesion.\n")

    def segment_arrays(self, segment: ScheduledSegment) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        st = self.state
        start = max(0, segment.start_sample)
        end = min(segment.end_sample if segment.end_sample is not None else len(st.t), len(st.t))
        t = np.asarray(st.t[start:end], dtype=float)
        red = np.asarray(st.red[start:end], dtype=float)
        ir = np.asarray(st.ir[start:end], dtype=float)
        temp_c = np.asarray(st.temp_c[start:end], dtype=float)
        temp_raw = np.asarray(st.temp_raw[start:end], dtype=float)
        temp_a0_c = np.asarray(st.temp_a0_c[start:end], dtype=float)
        temp_a0_raw = np.asarray(st.temp_a0_raw[start:end], dtype=float)
        temp_a1_c = np.asarray(st.temp_a1_c[start:end], dtype=float)
        temp_a1_raw = np.asarray(st.temp_a1_raw[start:end], dtype=float)
        if t.size:
            t = t - t[0]
        return t, red, ir, temp_c, temp_raw, temp_a0_c, temp_a0_raw, temp_a1_c, temp_a1_raw

    def temp_summary_for_arrays(self, t: np.ndarray, temp_c: np.ndarray, temp_raw: np.ndarray, temp_a0_c: np.ndarray | None = None, temp_a0_raw: np.ndarray | None = None, temp_a1_c: np.ndarray | None = None, temp_a1_raw: np.ndarray | None = None) -> dict[str, float | int]:
        temp_a0_c = temp_c if temp_a0_c is None else temp_a0_c
        temp_a0_raw = temp_raw if temp_a0_raw is None else temp_a0_raw
        temp_a1_c = np.asarray([], dtype=float) if temp_a1_c is None else temp_a1_c
        temp_a1_raw = np.asarray([], dtype=float) if temp_a1_raw is None else temp_a1_raw
        primary = temperature_channel_summary(t, temp_c, temp_raw)
        a0 = temperature_channel_summary(t, temp_a0_c, temp_a0_raw)
        a1 = temperature_channel_summary(t, temp_a1_c, temp_a1_raw)
        if self.state.temp_mapping == TEMP_MAPPING_INVERTED:
            rt, lt = a1, a0
        else:
            rt, lt = a0, a1
        return {
            "temp_samples": primary["samples"],
            "temp_raw_samples": primary["raw_samples"],
            "temp_a0_samples": a0["samples"],
            "temp_a0_raw_samples": a0["raw_samples"],
            "temp_a1_samples": a1["samples"],
            "temp_a1_raw_samples": a1["raw_samples"],
            "temp_c_last": primary["last"],
            "temp_c_mean": primary["mean"],
            "temp_c_min": primary["min"],
            "temp_c_max": primary["max"],
            "temp_c_final_max_5s": primary["final_max_5s"],
            "temp_c_final_time_s": primary["final_time_s"],
            "temp_c_final_raw_at_max": primary["final_raw_at_max"],
            "temp_c_final_samples": primary["final_samples"],
            "temp_final_window_start_s": primary["final_window_start_s"],
            "temp_final_window_end_s": primary["final_window_end_s"],
            "temp_final_window_used": primary["final_window_used"],
            "temp_raw_last": primary["raw_last"],
            "temp_a0_c_last": a0["last"],
            "temp_a0_c_mean": a0["mean"],
            "temp_a0_c_min": a0["min"],
            "temp_a0_c_max": a0["max"],
            "temp_a0_c_final_max_5s": a0["final_max_5s"],
            "temp_a0_c_final_time_s": a0["final_time_s"],
            "temp_a0_c_final_raw_at_max": a0["final_raw_at_max"],
            "temp_a0_c_final_samples": a0["final_samples"],
            "temp_a0_raw_last": a0["raw_last"],
            "temp_a1_c_last": a1["last"],
            "temp_a1_c_mean": a1["mean"],
            "temp_a1_c_min": a1["min"],
            "temp_a1_c_max": a1["max"],
            "temp_a1_c_final_max_5s": a1["final_max_5s"],
            "temp_a1_c_final_time_s": a1["final_time_s"],
            "temp_a1_c_final_raw_at_max": a1["final_raw_at_max"],
            "temp_a1_c_final_samples": a1["final_samples"],
            "temp_a1_raw_last": a1["raw_last"],
            "temp_rt_c_last": rt["last"],
            "temp_rt_c_mean": rt["mean"],
            "temp_rt_c_min": rt["min"],
            "temp_rt_c_max": rt["max"],
            "temp_rt_c_final_max_5s": rt["final_max_5s"],
            "temp_rt_c_final_time_s": rt["final_time_s"],
            "temp_rt_c_final_raw_at_max": rt["final_raw_at_max"],
            "temp_rt_c_final_samples": rt["final_samples"],
            "temp_rt_raw_last": rt["raw_last"],
            "temp_lt_c_last": lt["last"],
            "temp_lt_c_mean": lt["mean"],
            "temp_lt_c_min": lt["min"],
            "temp_lt_c_max": lt["max"],
            "temp_lt_c_final_max_5s": lt["final_max_5s"],
            "temp_lt_c_final_time_s": lt["final_time_s"],
            "temp_lt_c_final_raw_at_max": lt["final_raw_at_max"],
            "temp_lt_c_final_samples": lt["final_samples"],
            "temp_lt_raw_last": lt["raw_last"],
        }

    def save_signal_plot(self, path, t: np.ndarray, red: np.ndarray, ir: np.ndarray, analysis_cfg, title: str):
        width, height = 1100, 560
        image = QtGui.QImage(width, height, QtGui.QImage.Format.Format_RGB32)
        image.fill(QtGui.QColor("#ffffff"))
        painter = QtGui.QPainter(image)
        try:
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            margin_l, margin_r, margin_t, margin_b = 72, 28, 56, 54
            left, right = margin_l, width - margin_r
            top, bottom = margin_t, height - margin_b

            painter.setPen(QtGui.QPen(QtGui.QColor("#17202a"), 1))
            painter.setFont(QtGui.QFont("Arial", 13, QtGui.QFont.Weight.Bold))
            painter.drawText(QtCore.QRectF(left, 16, right - left, 26), title)
            painter.setFont(QtGui.QFont("Arial", 9))
            painter.setPen(QtGui.QColor("#586673"))
            painter.drawText(QtCore.QRectF(left, 38, right - left, 18), "IR azul y RED rojo, senales procesadas/normalizadas")

            painter.setPen(QtGui.QPen(QtGui.QColor("#d5dde5"), 1))
            painter.drawRect(QtCore.QRectF(left, top, right - left, bottom - top))
            for i in range(1, 5):
                y = top + i * (bottom - top) / 5.0
                painter.drawLine(int(left), int(y), int(right), int(y))
            for i in range(1, 6):
                x = left + i * (right - left) / 6.0
                painter.drawLine(int(x), int(top), int(x), int(bottom))

            n = min(t.size, red.size, ir.size)
            tt_src = t[:n]
            red_src = red[:n]
            ir_src = ir[:n]
            mask = np.isfinite(tt_src) & np.isfinite(red_src) & np.isfinite(ir_src)
            tt = tt_src[mask]
            rr = red_src[mask]
            ii = ir_src[mask]
            if tt.size < 2:
                painter.setPen(QtGui.QColor("#586673"))
                painter.drawText(QtCore.QRectF(left, top, right - left, bottom - top), QtCore.Qt.AlignmentFlag.AlignCenter, "Sin muestras suficientes para graficar.")
                image.save(str(path), "PNG")
                return
            tt = tt - float(tt[0])
            hz = estimate_hz(tt)
            red_y = processed_for_plot(rr, hz, analysis_cfg)
            ir_y = processed_for_plot(ii, hz, analysis_cfg)
            duration = max(float(tt[-1] - tt[0]), 1e-6)

            def make_poly(values: np.ndarray) -> QtGui.QPolygonF:
                finite = np.isfinite(values)
                xvals = tt[finite]
                yvals = values[finite]
                poly = QtGui.QPolygonF()
                if xvals.size < 2:
                    return poly
                step = max(1, int(math.ceil(xvals.size / 900)))
                for xv, yv in zip(xvals[::step], yvals[::step]):
                    px = left + (float(xv) - float(tt[0])) / duration * (right - left)
                    py = top + (1.0 - float(np.clip((yv + 1.05) / 2.10, 0.0, 1.0))) * (bottom - top)
                    poly.append(QtCore.QPointF(px, py))
                return poly

            painter.setPen(QtGui.QPen(QtGui.QColor("#d7263d"), 1))
            painter.drawPolyline(make_poly(red_y))
            painter.setPen(QtGui.QPen(QtGui.QColor("#0b63ce"), 2))
            painter.drawPolyline(make_poly(ir_y))

            painter.setFont(QtGui.QFont("Arial", 8))
            painter.setPen(QtGui.QColor("#586673"))
            painter.drawText(QtCore.QRectF(left, bottom + 10, right - left, 18), QtCore.Qt.AlignmentFlag.AlignCenter, "Tiempo (s)")
            painter.drawText(QtCore.QRectF(8, top, 58, bottom - top), QtCore.Qt.AlignmentFlag.AlignCenter, "Norm.")
            for tick in np.linspace(0, duration, 7):
                x = left + float(tick) / duration * (right - left)
                painter.drawText(QtCore.QRectF(x - 22, bottom + 28, 44, 14), QtCore.Qt.AlignmentFlag.AlignCenter, f"{tick:.0f}")
        finally:
            painter.end()
        image.save(str(path), "PNG")

    def save_segment_capture(self, segment: ScheduledSegment, reason: str) -> bool:
        st = self.state
        t, red, ir, temp_c, temp_raw, temp_a0_c, temp_a0_raw, temp_a1_c, temp_a1_raw = self.segment_arrays(segment)
        if t.size < 2:
            return False
        step = segment.step
        label_id = sanitize_id(step.label)[:42]
        base_name = f"{st.base_name}_CFG{segment.index + 1:03d}_{label_id}"
        session_id = base_name
        analysis_cfg = self.analysis_widget.get_config()
        metrics = score_and_merge_metrics(t, red, ir, step.config, analysis_cfg)
        blocks = block_bpm(t, ir, step.config, analysis_cfg, block_s=10)
        temp = self.temp_summary_for_arrays(t, temp_c, temp_raw, temp_a0_c, temp_a0_raw, temp_a1_c, temp_a1_raw)

        raw_file = RAW_DIR / f"raw_{base_name}.csv"
        with open(raw_file, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([
                "session_id", "id", "base_name", "modo", "condiciones_medida", "ubre", "temp_mapping", "temp_primary_channel", "medicion_vacio", "config_label", "sample_index", "tiempo_s",
                "red_raw", "ir_raw", "temp_c", "temp_raw", "temp_a0_c", "temp_a0_raw", "temp_a1_c", "temp_a1_raw",
                "temp_rt_c", "temp_rt_raw", "temp_lt_c", "temp_lt_raw",
                "cfg_red", "cfg_ir", "cfg_avg", "cfg_rate", "cfg_width", "cfg_adc", "cfg_skip", "cfg_debug",
                "pulso_previo", "pulso_final_pulsio", "pulso_final_fonendo",
                "cfg_confirmacion", "system_time",
            ])
            for i in range(t.size):
                tc = temp_c[i] if i < temp_c.size else math.nan
                tr = temp_raw[i] if i < temp_raw.size else math.nan
                ta0c = temp_a0_c[i] if i < temp_a0_c.size else math.nan
                ta0r = temp_a0_raw[i] if i < temp_a0_raw.size else math.nan
                ta1c = temp_a1_c[i] if i < temp_a1_c.size else math.nan
                ta1r = temp_a1_raw[i] if i < temp_a1_raw.size else math.nan
                if st.temp_mapping == TEMP_MAPPING_INVERTED:
                    trtc, trtr, tltc, tltr = ta1c, ta1r, ta0c, ta0r
                else:
                    trtc, trtr, tltc, tltr = ta0c, ta0r, ta1c, ta1r
                w.writerow([
                    session_id, st.crotal_id, base_name, self.capture_mode_name(), st.measurement_condition, st.udder_side, st.temp_mapping, st.temp_primary_channel, st.vacuum_condition, step.label, i + 1, f"{t[i]:.6f}",
                    f"{red[i]:.0f}", f"{ir[i]:.0f}", fmt(tc, 2, ""), fmt(tr, 0, ""),
                    fmt(ta0c, 2, ""), fmt(ta0r, 0, ""), fmt(ta1c, 2, ""), fmt(ta1r, 0, ""),
                    fmt(trtc, 2, ""), fmt(trtr, 0, ""), fmt(tltc, 2, ""), fmt(tltr, 0, ""),
                    step.config.red, step.config.ir, step.config.avg, step.config.rate, step.config.width, step.config.adc,
                    step.config.skip, 1 if step.config.debug else 0,
                    segment.pulse_prev, segment.pulse_final_pulsio, segment.pulse_final_fonendo,
                    self.last_config_ack, datetime.now().isoformat(timespec="milliseconds"),
                ])

        processed_file = PROCESSED_DIR / f"proc_{base_name}.csv"
        hz = estimate_hz(t)
        red_proc = processed_for_plot(red, hz, analysis_cfg)
        ir_proc = processed_for_plot(ir, hz, analysis_cfg)
        art_red = detect_artifacts(red, strict=True)
        art_ir = detect_artifacts(ir, strict=True)
        peak_flags = np.zeros(t.size, dtype=int)
        _, _, _, _, peaks, peak_t = estimate_bpm_peaks(t, ir, analysis_cfg)
        if peaks.size and peak_t.size:
            nearest = np.searchsorted(t, peak_t[peaks])
            nearest = nearest[(nearest >= 0) & (nearest < t.size)]
            peak_flags[nearest] = 1
        with open(processed_file, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([
                "session_id", "id", "base_name", "modo", "condiciones_medida", "ubre", "temp_mapping", "temp_primary_channel", "medicion_vacio", "config_label", "sample_index", "tiempo_s",
                "red_raw", "ir_raw", "temp_c", "temp_raw", "temp_a0_c", "temp_a0_raw", "temp_a1_c", "temp_a1_raw",
                "temp_rt_c", "temp_rt_raw", "temp_lt_c", "temp_lt_raw",
                "red_proc_norm", "ir_proc_norm", "artifact_red", "artifact_ir", "peak_ir",
                "bpm_rolling_5s", "spo2_rolling_5s", "ratio_r_rolling_5s", "quality_rolling_5s",
            ])
            for i in range(t.size):
                tc = temp_c[i] if i < temp_c.size else math.nan
                tr = temp_raw[i] if i < temp_raw.size else math.nan
                ta0c = temp_a0_c[i] if i < temp_a0_c.size else math.nan
                ta0r = temp_a0_raw[i] if i < temp_a0_raw.size else math.nan
                ta1c = temp_a1_c[i] if i < temp_a1_c.size else math.nan
                ta1r = temp_a1_raw[i] if i < temp_a1_raw.size else math.nan
                if st.temp_mapping == TEMP_MAPPING_INVERTED:
                    trtc, trtr, tltc, tltr = ta1c, ta1r, ta0c, ta0r
                else:
                    trtc, trtr, tltc, tltr = ta0c, ta0r, ta1c, ta1r
                w.writerow([
                    session_id, st.crotal_id, base_name, self.capture_mode_name(), st.measurement_condition, st.udder_side, st.temp_mapping, st.temp_primary_channel, st.vacuum_condition, step.label, i + 1, f"{t[i]:.6f}",
                    f"{red[i]:.0f}", f"{ir[i]:.0f}", fmt(tc, 2, ""), fmt(tr, 0, ""),
                    fmt(ta0c, 2, ""), fmt(ta0r, 0, ""), fmt(ta1c, 2, ""), fmt(ta1r, 0, ""),
                    fmt(trtc, 2, ""), fmt(trtr, 0, ""), fmt(tltc, 2, ""), fmt(tltr, 0, ""),
                    f"{red_proc[i]:.5f}", f"{ir_proc[i]:.5f}", int(art_red[i]), int(art_ir[i]), int(peak_flags[i]),
                    "", "", "", "",
                ])

        blocks_file = REPORT_DIR / f"bpm_blocks_10s_{base_name}.csv"
        with open(blocks_file, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["session_id", "id", "base_name", "modo", "bloque", "inicio_s", "fin_s", "bpm_medio_10s"])
            for i, bpm in enumerate(blocks):
                w.writerow([session_id, st.crotal_id, base_name, self.capture_mode_name(), i + 1, i * 10, i * 10 + 10, fmt(bpm, 2, "")])

        plot_file = FIGURES_DIR / f"plot_{base_name}.png"
        self.save_signal_plot(plot_file, t, red, ir, analysis_cfg, step.label)
        summary_file = REPORT_DIR / f"summary_{base_name}.json"
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump({
                "session_id": session_id,
                "id": st.crotal_id,
                "base_name": base_name,
                "mode": self.capture_mode_name(),
                "measurement_condition": st.measurement_condition,
                "udder_side": st.udder_side,
                "temp_mapping": st.temp_mapping,
                "temp_primary_channel": st.temp_primary_channel,
                "vacuum_condition": st.vacuum_condition,
                "config_label": step.label,
                "config_description": step.description,
                "reason": reason,
                "requested_duration_s": self.scheduled_step_duration_s,
                "samples": int(t.size),
                "metrics": asdict(metrics),
                "temperature": temp,
                "bpm_blocks_10s_mean": blocks,
                "sensor_config": asdict(step.config),
                "analysis_config": asdict(analysis_cfg),
                "files": {
                    "raw": str(raw_file),
                    "processed": str(processed_file),
                    "plot": str(plot_file),
                    "bpm_blocks_10s": str(blocks_file),
                },
                "manual_reference": {
                    "pulso_previo": segment.pulse_prev,
                    "pulso_final_pulsio": segment.pulse_final_pulsio,
                    "pulso_final_fonendo": segment.pulse_final_fonendo,
                },
                "created": datetime.now().isoformat(),
            }, f, indent=2, ensure_ascii=False)

        now = datetime.now()
        self.session_writer.writerow([
            session_id, st.crotal_id, base_name, now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"),
            self.capture_mode_name(), st.measurement_condition, st.udder_side, st.temp_mapping, st.temp_primary_channel, st.vacuum_condition, step.label, reason, fmt(self.scheduled_step_duration_s, 1, ""),
            int(t.size), fmt(metrics.duration_s, 3, ""), fmt(metrics.hz, 2, ""), fmt(metrics.bpm, 1, ""),
            fmt(metrics.bpm_peak, 1, ""), fmt(metrics.bpm_fft, 1, ""), fmt(metrics.bpm_autocorr, 1, ""),
            fmt(metrics.quality, 1, ""), metrics.quality_label, fmt(metrics.spo2, 1, ""), fmt(metrics.ratio_r, 5, ""),
            fmt(metrics.resp_rate_rpm, 1, ""), fmt(metrics.resp_quality, 0, ""), metrics.resp_reason,
            fmt(temp["temp_c_final_max_5s"], 2, ""), fmt(temp["temp_c_final_time_s"], 3, ""), fmt(temp["temp_c_final_raw_at_max"], 0, ""), fmt(temp["temp_c_last"], 2, ""), fmt(temp["temp_c_mean"], 2, ""), fmt(temp["temp_raw_last"], 0, ""),
            fmt(temp["temp_rt_c_final_max_5s"], 2, ""), fmt(temp["temp_rt_c_final_time_s"], 3, ""), fmt(temp["temp_rt_c_final_raw_at_max"], 0, ""), fmt(temp["temp_rt_c_last"], 2, ""), fmt(temp["temp_rt_c_mean"], 2, ""), fmt(temp["temp_rt_raw_last"], 0, ""),
            fmt(temp["temp_lt_c_final_max_5s"], 2, ""), fmt(temp["temp_lt_c_final_time_s"], 3, ""), fmt(temp["temp_lt_c_final_raw_at_max"], 0, ""), fmt(temp["temp_lt_c_last"], 2, ""), fmt(temp["temp_lt_c_mean"], 2, ""), fmt(temp["temp_lt_raw_last"], 0, ""),
            fmt(temp["temp_a0_c_final_max_5s"], 2, ""), fmt(temp["temp_a0_c_final_time_s"], 3, ""), fmt(temp["temp_a0_c_final_raw_at_max"], 0, ""), fmt(temp["temp_a0_c_last"], 2, ""), fmt(temp["temp_a0_c_mean"], 2, ""), fmt(temp["temp_a0_raw_last"], 0, ""),
            fmt(temp["temp_a1_c_final_max_5s"], 2, ""), fmt(temp["temp_a1_c_final_time_s"], 3, ""), fmt(temp["temp_a1_c_final_raw_at_max"], 0, ""), fmt(temp["temp_a1_c_last"], 2, ""), fmt(temp["temp_a1_c_mean"], 2, ""), fmt(temp["temp_a1_raw_last"], 0, ""),
            fmt(metrics.pi_ir_pct, 4, ""), fmt(metrics.pi_red_pct, 4, ""), fmt(metrics.artifact_ir_pct, 1, ""),
            fmt(metrics.artifact_red_pct, 1, ""), metrics.contact_label, self.last_config_ack, segment.pulse_prev,
            segment.pulse_final_pulsio, segment.pulse_final_fonendo, raw_file.name, processed_file.name, plot_file.name,
            "", summary_file.name, st.config_file.name if st.config_file else "", json.dumps(blocks, ensure_ascii=False),
            blocks_file.name,
        ])
        return True

    def update_plots(self):
        t, red, ir = self.arrays()
        if t.size < 2:
            self.ir_curve.setData([], [])
            self.red_curve.setData([], [])
            return
        cfg = self.analysis_widget.get_config()
        hz = estimate_hz(t)
        mask = t >= t[-1] - 30 if self.state.capturing and t[-1] > 30 else np.ones_like(t, dtype=bool)
        tt = t[mask]
        self.ir_curve.setData(tt, processed_for_plot(ir[mask], hz, cfg))
        self.red_curve.setData(tt, processed_for_plot(red[mask], hz, cfg))
        self.plot_main.setXRange(float(tt[0]), max(float(tt[-1]), float(tt[0]) + 1), padding=0.01)
        if self.state.rolling_t:
            self.trend_bpm_curve.setData(self.state.rolling_t, self.state.rolling_bpm)
            self.trend_spo2_curve.setData(self.state.rolling_t, self.state.rolling_spo2)

    def update_info(self):
        st = self.state
        m = st.metrics
        temp = self.temperature_summary()
        spo2_warning = spo2_support_message(m)
        spo2_warning_line = f"{spo2_warning}\n" if spo2_warning else ""
        elapsed = time.time() - st.capture_start_wall if st.capturing else 0.0
        step_elapsed = time.time() - self.scheduled_step_start_wall if st.capturing else 0.0
        step = self.scheduled_steps[self.scheduled_step_index]
        remaining = max(0.0, self.scheduled_step_duration_s - step_elapsed)
        pulse_prev = self.scheduled_segments[-1].pulse_prev if self.scheduled_segments else st.pulse_prev
        self.info.setText(
            f"{self.scheduled_title}\n"
            f"Puerto: {self.port_name}\n"
            f"Estado: {'CAPTURANDO' if st.capturing else ('READY | preparado' if st.sensor_ready else 'esperando READY')}\n"
            f"Crotal: {st.crotal_id}\n"
            f"Bloque: {self.scheduled_step_index + 1}/{len(self.scheduled_steps)}\n"
            f"{step.label}\n"
            f"{step.description}\n"
            f"Tiempo config: {step_elapsed:.1f}s | quedan {remaining:.1f}s\n"
            f"Tiempo bloque: {elapsed:.1f}s | pulso ref. inicial: {pulse_prev or '-'}\n"
            f"Config Arduino: {self.last_config_ack} | {self.last_config_line[:80]}\n\n"
            f"Muestras: {len(st.t)} | descartadas: {st.discarded_lines}\n"
            f"BPM: {fmt(m.bpm,0)} | calidad {fmt(m.quality,0)} ({m.quality_label})\n"
            f"SpO2 estimada: {fmt(m.spo2,1)} % | R={fmt(m.ratio_r,4)}\n"
            f"{spo2_warning_line}"
            f"PI IR/RED: {fmt(m.pi_ir_pct,3)} / {fmt(m.pi_red_pct,3)} %\n"
            f"Artefactos IR/RED: {fmt(m.artifact_ir_pct,1)} / {fmt(m.artifact_red_pct,1)} % | Saturacion ADC: {fmt(m.saturation_pct,1)} %\n"
            f"Respiraciones (experimental): {fmt(m.resp_rate_rpm,1)} resp/min | calidad {fmt(m.resp_quality,0)}\n"
            f"Temp: {fmt(temp['temp_c_last'],1)} °C | raw {fmt(temp['temp_raw_last'],0)}\n"
            f"Contacto: {m.contact_label}\n"
            f"Raw: {st.raw_file.name if st.raw_file else '-'}\n"
        )


class ConfigTableWidget(QtWidgets.QTableWidget):
    headers = ["label", "red", "ir", "avg", "rate", "width", "adc", "skip", "debug", "descripcion"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(len(self.headers))
        self.setHorizontalHeaderLabels(self.headers)
        self.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ContiguousSelection)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectItems)

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        if event.matches(QtGui.QKeySequence.StandardKey.Paste):
            self.paste_from_clipboard()
            return
        if event.matches(QtGui.QKeySequence.StandardKey.Copy):
            self.copy_to_clipboard()
            return
        super().keyPressEvent(event)

    def paste_from_clipboard(self):
        text = QtWidgets.QApplication.clipboard().text()
        if not text:
            return
        start_row = self.currentRow() if self.currentRow() >= 0 else 0
        start_col = self.currentColumn() if self.currentColumn() >= 0 else 0
        rows = [r for r in text.splitlines() if r != ""]
        needed_rows = start_row + len(rows)
        if needed_rows > self.rowCount():
            self.setRowCount(needed_rows)
        for r_offset, row_text in enumerate(rows):
            cells = row_text.split("\t")
            for c_offset, value in enumerate(cells):
                row = start_row + r_offset
                col = start_col + c_offset
                if col >= self.columnCount():
                    continue
                self.setItem(row, col, QtWidgets.QTableWidgetItem(value.strip()))

    def copy_to_clipboard(self):
        ranges = self.selectedRanges()
        if not ranges:
            return
        r = ranges[0]
        lines = []
        for row in range(r.topRow(), r.bottomRow() + 1):
            values = []
            for col in range(r.leftColumn(), r.rightColumn() + 1):
                item = self.item(row, col)
                values.append(item.text() if item else "")
            lines.append("\t".join(values))
        QtWidgets.QApplication.clipboard().setText("\n".join(lines))


class ConfigurationsWindow(ScheduledConfigWindow):
    def __init__(self):
        self.table_ready = False
        super().__init__(
            "Configuraciones personalizadas",
            build_12_config_steps(),
            20 * 60,
            "toma con tabla personalizada de configuraciones",
        )

    def build_ui(self):
        super().build_ui()
        self.setWindowTitle("PPG Suite v8 | Configuraciones personalizadas")
        self.btn_start.setText("Iniciar tabla")

        self.count_spin = NoWheelSpinBox()
        self.count_spin.setRange(1, 200)
        self.count_spin.setValue(len(self.scheduled_steps))
        self.btn_resize_table = QtWidgets.QPushButton("Crear cuadricula")
        self.btn_load_12 = QtWidgets.QPushButton("Cargar plantilla 12")
        self.btn_load_64 = QtWidgets.QPushButton("Cargar plantilla 64")

        controls = QtWidgets.QGroupBox("Tabla de configuraciones")
        controls_layout = QtWidgets.QGridLayout(controls)
        controls_layout.addWidget(QtWidgets.QLabel("Numero de configuraciones:"), 0, 0)
        controls_layout.addWidget(self.count_spin, 0, 1)
        controls_layout.addWidget(self.btn_resize_table, 0, 2)
        controls_layout.addWidget(self.btn_load_12, 1, 0)
        controls_layout.addWidget(self.btn_load_64, 1, 1)

        self.config_table = ConfigTableWidget()
        self.config_table.setMinimumHeight(260)

        table_panel = QtWidgets.QWidget()
        table_layout = QtWidgets.QVBoxLayout(table_panel)
        table_layout.addWidget(controls)
        table_layout.addWidget(self.config_table)
        self.tabs.insertTab(0, table_panel, "Tabla")
        self.tabs.setCurrentWidget(table_panel)

        self.btn_resize_table.clicked.connect(lambda: self.set_table_row_count(self.count_spin.value()))
        self.btn_load_12.clicked.connect(lambda: self.load_steps_to_table(build_12_config_steps()))
        self.btn_load_64.clicked.connect(lambda: self.load_steps_to_table(build_64_config_steps()))

        self.load_steps_to_table(self.scheduled_steps)
        self.table_ready = True

    def set_table_row_count(self, count: int):
        old = self.config_table.rowCount()
        self.config_table.setRowCount(count)
        for row in range(old, count):
            defaults = [f"CONFIG {row + 1:02d}", "63", "63", "4", "800", "411", "16384", "50", "0", ""]
            for col, value in enumerate(defaults):
                self.config_table.setItem(row, col, QtWidgets.QTableWidgetItem(value))

    def load_steps_to_table(self, steps: list[ScheduledStep]):
        self.scheduled_steps = steps
        self.count_spin.setValue(len(steps))
        self.config_table.setRowCount(len(steps))
        for row, step in enumerate(steps):
            values = [
                step.label,
                str(step.config.red),
                str(step.config.ir),
                str(step.config.avg),
                str(step.config.rate),
                str(step.config.width),
                str(step.config.adc),
                str(step.config.skip),
                "1" if step.config.debug else "0",
                step.description,
            ]
            for col, value in enumerate(values):
                self.config_table.setItem(row, col, QtWidgets.QTableWidgetItem(value))

    def steps_from_table(self) -> list[ScheduledStep]:
        steps: list[ScheduledStep] = []
        for row in range(self.config_table.rowCount()):
            vals = []
            for col in range(self.config_table.columnCount()):
                item = self.config_table.item(row, col)
                vals.append(item.text().strip() if item else "")
            label = vals[0] or f"CONFIG {row + 1:02d}"
            try:
                cfg = SensorConfig(
                    red=int(vals[1] or 63),
                    ir=int(vals[2] or 63),
                    avg=int(vals[3] or 4),
                    rate=int(vals[4] or 800),
                    width=int(vals[5] or 411),
                    adc=int(vals[6] or 16384),
                    skip=int(vals[7] or 50),
                    debug=(vals[8].lower() in ("1", "true", "si", "sí", "yes")),
                ).clean()
            except ValueError as exc:
                raise ValueError(f"Fila {row + 1}: valor numerico no valido") from exc
            desc = vals[9] or f"Configuracion personalizada {row + 1}"
            steps.append(ScheduledStep(label, desc, cfg))
        if not steps:
            raise ValueError("La tabla no tiene configuraciones.")
        return steps

    def start_scheduled_capture(self):
        try:
            self.scheduled_steps = self.steps_from_table()
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Tabla de configuraciones", str(exc))
            return
        seconds_per_config = float(self.duration_spin.value()) * 60.0 / max(1, len(self.scheduled_steps))
        if seconds_per_config < 10.0:
            QtWidgets.QMessageBox.warning(
                self,
                "Duracion corta por configuracion",
                "Cada configuracion tendra menos de 10 segundos.\n\n"
                "Puede guardarse igualmente, pero el BPM, la calidad y la saturacion pueden salir vacios o poco fiables.\n"
                "Para comparar configuraciones se recomienda usar al menos 10-15 segundos por fila.",
            )
        self.scheduled_title = f"Configuraciones personalizadas ({len(self.scheduled_steps)})"
        super().start_scheduled_capture()


class Experiment3MWindow(ScheduledConfigWindow):
    def __init__(self):
        self.experiment_search_space = build_3m_search_space()
        self.experiment_max_steps = 12
        self.experiment_history: list[dict[str, float | str | int]] = []
        self.experiment_decisions: list[dict[str, str]] = []
        self.experiment_last_decision = "Inicio: RED/IR bajos, AVG1 y ADC amplio para evitar saturacion."
        first_cfg = SensorConfig(red=63, ir=63, avg=4, rate=800, width=411, adc=16384, skip=50)
        placeholder = make_3m_step(1, first_cfg, "inicio conservador")
        steps = [placeholder]
        for idx in range(2, self.experiment_max_steps + 1):
            steps.append(make_3m_step(idx, first_cfg, "pendiente de decision adaptativa"))
        super().__init__(
            "Experimento 3M",
            steps,
            total_duration_s=20 * 60,
            condition="Experimento 3M: optimizacion adaptativa con BPM manual, PPG y SpO2",
        )

    def capture_mode_name(self) -> str:
        return "experimento_3m"

    def build_ui(self):
        super().build_ui()
        self.duration_spin.setRange(4, 20)
        self.duration_spin.setValue(20)
        self.duration_warning.setText(
            "Experimento 3M prueba hasta 12 ajustes en 20 minutos. "
            "Tras cada tramo pide BPM manual y cambia RED/IR/AVG/ADC buscando pulso parecido a referencia, SpO2 usable, PI suficiente, pocos artefactos y sin saturacion."
        )

    def start_scheduled_capture(self):
        first_cfg = SensorConfig(red=63, ir=63, avg=4, rate=800, width=411, adc=16384, skip=50)
        self.scheduled_steps = [make_3m_step(1, first_cfg, "inicio conservador")]
        for idx in range(2, self.experiment_max_steps + 1):
            self.scheduled_steps.append(make_3m_step(idx, first_cfg, "pendiente de decision adaptativa"))
        self.experiment_history = []
        self.experiment_decisions = []
        self.experiment_last_decision = "Inicio: RED/IR bajos, AVG1 y ADC amplio para evitar saturacion."
        super().start_scheduled_capture()

    def _experiment_quality_note(self, diff: float, quality: float, pi_ir: float, pi_red: float, artifact: float, saturation: float, spo2_ready: bool) -> str:
        notes: list[str] = []
        if math.isfinite(diff):
            if diff <= 5:
                notes.append("BPM muy cerca de referencia")
            elif diff <= 10:
                notes.append("BPM cerca de referencia")
            elif diff <= 18:
                notes.append("BPM algo separada de referencia")
            else:
                notes.append("BPM lejos de referencia")
        else:
            notes.append("sin referencia manual valida")
        if math.isfinite(quality):
            notes.append("calidad alta" if quality >= 70 else ("calidad media" if quality >= 45 else "calidad baja"))
        if math.isfinite(pi_ir):
            notes.append("PI IR bajo" if pi_ir < 0.20 else "PI IR suficiente")
        if math.isfinite(pi_red):
            notes.append("PI RED bajo" if pi_red < 0.08 else "PI RED suficiente")
        notes.append("SpO2 usable" if spo2_ready else "SpO2 aun no fiable")
        if math.isfinite(artifact) and artifact > 8:
            notes.append("artefactos altos")
        if math.isfinite(saturation) and saturation > 0:
            notes.append("saturacion detectada")
        return "; ".join(notes)

    def _evaluate_experiment_segment(self, segment: ScheduledSegment) -> dict[str, float | str | int]:
        t, red, ir, _temp_c, _temp_raw, _temp_a0_c, _temp_a0_raw, _temp_a1_c, _temp_a1_raw = self.segment_arrays(segment)
        metrics = score_and_merge_metrics(t, red, ir, segment.step.config, self.analysis_widget.get_config())
        ref_avg, ref_count = _ref_average(segment.pulse_prev, segment.pulse_final_pulsio, segment.pulse_final_fonendo)
        bpm = metrics.bpm_fft if math.isfinite(metrics.bpm_fft) else metrics.bpm
        diff = abs(bpm - ref_avg) if math.isfinite(bpm) and math.isfinite(ref_avg) else math.nan
        spo2_ready = (
            math.isfinite(metrics.spo2)
            and 70.0 <= metrics.spo2 <= 105.0
            and math.isfinite(metrics.ratio_r)
            and math.isfinite(metrics.pi_red_pct)
            and math.isfinite(metrics.pi_ir_pct)
            and metrics.pi_red_pct >= 0.08
            and metrics.pi_ir_pct >= 0.15
        )
        score = float(metrics.quality)
        if math.isfinite(diff):
            if diff <= 5:
                score += 30
            elif diff <= 10:
                score += 16
            elif diff <= 18:
                score += 4
            else:
                score -= min(35.0, diff)
        score += 12 if spo2_ready else -8
        if math.isfinite(metrics.pi_red_pct) and metrics.pi_red_pct < 0.08:
            score -= 8
        score -= min(20.0, max(0.0, metrics.artifact_ir_pct) * 1.2) if math.isfinite(metrics.artifact_ir_pct) else 0.0
        score -= min(35.0, max(0.0, metrics.saturation_pct) * 2.0) if math.isfinite(metrics.saturation_pct) else 0.0
        score = float(np.clip(score, 0.0, 100.0))
        note = self._experiment_quality_note(
            diff,
            float(metrics.quality),
            float(metrics.pi_ir_pct),
            float(metrics.pi_red_pct),
            float(metrics.artifact_ir_pct),
            float(metrics.saturation_pct),
            spo2_ready,
        )
        return {
            "label": segment.step.label,
            "description": segment.step.description,
            "red": segment.step.config.red,
            "ir": segment.step.config.ir,
            "avg": segment.step.config.avg,
            "adc": segment.step.config.adc,
            "bpm": float(bpm) if math.isfinite(bpm) else math.nan,
            "ref": ref_avg,
            "ref_count": float(ref_count),
            "diff": diff,
            "quality": float(metrics.quality),
            "pi_ir": float(metrics.pi_ir_pct),
            "pi_red": float(metrics.pi_red_pct),
            "artifact_ir": float(metrics.artifact_ir_pct),
            "artifact_red": float(metrics.artifact_red_pct),
            "saturation": float(metrics.saturation_pct),
            "spo2": float(metrics.spo2) if math.isfinite(metrics.spo2) else math.nan,
            "ratio_r": float(metrics.ratio_r) if math.isfinite(metrics.ratio_r) else math.nan,
            "spo2_ready": 1 if spo2_ready else 0,
            "score": score,
            "note": note,
        }

    def _should_stop_experiment(self) -> bool:
        if len(self.experiment_history) < 4:
            return False
        best = max(self.experiment_history, key=self._experiment_rank_key)
        diff = float(best.get("diff", math.nan))
        quality = float(best.get("quality", 0.0))
        spo2_ready = bool(best.get("spo2_ready", 0))
        saturation = float(best.get("saturation", math.nan))
        return math.isfinite(diff) and diff <= 5.0 and quality >= 55.0 and spo2_ready and (not math.isfinite(saturation) or saturation <= 0.5)

    def _experiment_rank_key(self, item: dict[str, float | str | int]) -> tuple[float, float, float, float, float, float]:
        score = float(item.get("score", 0.0))
        diff = float(item.get("diff", math.nan))
        quality = float(item.get("quality", 0.0))
        pi_ir = float(item.get("pi_ir", math.nan))
        pi_red = float(item.get("pi_red", math.nan))
        saturation = float(item.get("saturation", math.nan))
        return (
            score,
            -diff if math.isfinite(diff) else -999.0,
            quality,
            pi_ir if math.isfinite(pi_ir) else -1.0,
            pi_red if math.isfinite(pi_red) else -1.0,
            -saturation if math.isfinite(saturation) else 0.0,
        )

    def _best_experiment_result(self) -> dict[str, float | str | int] | None:
        if not self.experiment_history:
            return None
        return max(self.experiment_history, key=self._experiment_rank_key)

    def _config_key(self, cfg: SensorConfig) -> tuple[int, int, int, int]:
        return cfg.red, cfg.ir, cfg.avg, cfg.adc

    def _choose_next_experiment_step(self, index: int) -> tuple[ScheduledStep | None, str]:
        tried = {
            (int(item.get("red", 0)), int(item.get("ir", 0)), int(item.get("avg", 0)), int(item.get("adc", 0)))
            for item in self.experiment_history
        }
        remaining = [cfg for cfg in self.experiment_search_space if self._config_key(cfg) not in tried]
        if not remaining:
            return None, "No quedan configuraciones sin probar en el espacio 3M."
        if not self.experiment_history:
            cfg = SensorConfig(red=63, ir=63, avg=4, rate=800, width=411, adc=16384, skip=50)
            return make_3m_step(index + 1, cfg, "primera configuracion adaptativa"), "Primera configuracion del protocolo 3M."
        last = self.experiment_history[-1]
        best = self._best_experiment_result() or last
        tried_avgs = {int(item.get("avg", 0)) for item in self.experiment_history}
        target_brightness = None
        target_avg = None
        target_adc = None
        target_red_bias = 1.0
        reason_bits: list[str] = []
        pi = float(last.get("pi_ir", math.nan))
        pi_red = float(last.get("pi_red", math.nan))
        artifact = float(last.get("artifact_ir", math.nan))
        saturation = float(last.get("saturation", math.nan))
        spo2_ready = bool(last.get("spo2_ready", 0))
        if math.isfinite(saturation) and saturation > 0:
            target_adc = 16384
            target_brightness = max(24, int(float(last.get("ir", 31)) * 0.65))
            reason_bits.append("baja brillo y mantiene ADC amplio por saturacion")
        elif math.isfinite(pi) and pi < 0.08:
            target_brightness = min(159, int(float(last.get("ir", 31)) * 1.8))
            reason_bits.append("sube IR porque el PI IR es muy bajo")
        elif math.isfinite(pi) and pi < 0.20:
            target_brightness = min(159, int(float(last.get("ir", 31)) * 1.35))
            reason_bits.append("sube IR porque el PI IR es bajo")
        if not spo2_ready:
            if math.isfinite(pi_red) and pi_red < 0.08:
                target_red_bias = 1.25
                reason_bits.append("sube RED relativo a IR para mejorar SpO2")
            else:
                reason_bits.append("busca una pareja RED/IR con SpO2 calculable")
        if math.isfinite(artifact) and artifact > 8:
            target_avg = 4
            reason_bits.append("usa AVG4 porque hay artefactos/ruido")
        elif not spo2_ready and len(self.experiment_history) >= 3 and 2 not in tried_avgs and float(best.get("score", 0.0)) >= 65:
            target_avg = 2
            reason_bits.append("prueba AVG2 como suavizado intermedio sin perder tanta dinamica como AVG4")
        elif float(best.get("score", 0.0)) >= 55:
            target_avg = int(best.get("avg", 1))
            reason_bits.append("mantiene el AVG de la mejor candidata provisional")

        best_ir = int(best.get("ir", 31))
        best_red = int(best.get("red", 31))

        def rank(cfg: SensorConfig) -> tuple[float, int]:
            rank_score = 0.0
            ir_target = target_brightness if target_brightness is not None else best_ir
            red_target = int(round(ir_target * target_red_bias)) if target_brightness is not None else best_red
            rank_score += abs(cfg.ir - ir_target) / 8.0
            rank_score += abs(cfg.red - red_target) / 10.0
            if target_avg is not None:
                rank_score += 0.0 if cfg.avg == target_avg else abs(cfg.avg - target_avg) * 1.5
            if target_adc is not None:
                rank_score += 0.0 if cfg.adc == target_adc else 5.0
            if cfg.adc == 8192 and (math.isfinite(saturation) and saturation > 0):
                rank_score += 8.0
            if cfg.avg == 8 and not (math.isfinite(artifact) and artifact > 8):
                rank_score += 2.5
            return rank_score, self.experiment_search_space.index(cfg)

        chosen_cfg = sorted(remaining, key=rank)[0]
        if not reason_bits:
            reason_bits.append("explora alrededor de la mejor candidata provisional")
        return make_3m_step(index + 1, chosen_cfg, "; ".join(reason_bits)), "; ".join(reason_bits)

    def _ensure_history_for_finished_segments(self):
        known = {str(item.get("label", "")) for item in self.experiment_history}
        for segment in self.scheduled_segments:
            if str(segment.step.label) in known:
                continue
            if segment.end_sample is None:
                segment.end_sample = len(self.state.t)
            if segment.end_sample <= segment.start_sample:
                continue
            self.experiment_history.append(self._evaluate_experiment_segment(segment))
            known.add(segment.step.label)

    def _write_experiment_report(self, reason: str):
        self._ensure_history_for_finished_segments()
        if not self.state.base_name:
            return
        best = self._best_experiment_result()
        report_file = REPORT_DIR / f"experimento_3m_decision_{self.state.base_name}.json"
        payload = {
            "created": datetime.now().isoformat(),
            "base_name": self.state.base_name,
            "animal": self.state.crotal_id,
            "reason": reason,
            "protocol": {
                "max_duration_min": float(self.duration_spin.value()),
                "max_steps": self.experiment_max_steps,
                "search_space_size": len(self.experiment_search_space),
                "reference_rule": "media de pulso previo, pulsioximetro final y fonendo final; 0/vacio se ignora",
                "decision_rule": "cercania a referencia manual + calidad PPG + SpO2 usable + PI IR/RED + artefactos + saturacion",
                "stop_rule": "minimo 4 tramos y mejor candidata con diferencia <=5 BPM, calidad >=55/100, SpO2 usable y saturacion baja",
            },
            "best_candidate": best,
            "history": self.experiment_history,
            "decisions": self.experiment_decisions,
        }
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        pdf_file = DOCUMENTS_DIR / f"informe_experimento_3m_{self.state.base_name}.pdf"
        try:
            DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
            self._write_experiment_pdf_report(pdf_file, reason, best)
            pdf_note = f"\nPDF Experimento 3M: {pdf_file.name}"
        except Exception as exc:
            log_note = f"No se pudo generar PDF Experimento 3M: {exc}"
            pdf_note = f"\n{log_note}"
        self.info.setText(self.info.text() + f"\nInforme Experimento 3M JSON: {report_file.name}{pdf_note}\n")

    def _write_experiment_pdf_report(self, path, reason: str, best: dict[str, float | str | int] | None):
        writer = QtGui.QPdfWriter(str(path))
        writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.PageSizeId.A4))
        writer.setResolution(96)
        painter = QtGui.QPainter(writer)
        if not painter.isActive():
            raise RuntimeError("No se pudo iniciar el escritor PDF.")

        page = writer.pageLayout().paintRectPixels(writer.resolution())
        margin = 42
        x0 = page.x() + margin
        y = page.y() + margin
        width = page.width() - margin * 2
        height = page.height() - margin * 2
        page_no = 1
        colors = {
            "ink": QtGui.QColor("#17202a"),
            "muted": QtGui.QColor("#586673"),
            "blue": QtGui.QColor("#103b63"),
            "line": QtGui.QColor("#d5dde5"),
            "soft": QtGui.QColor("#f3f6f9"),
            "green": QtGui.QColor("#dff3e4"),
        }

        def font(size: int, bold: bool = False) -> QtGui.QFont:
            f = QtGui.QFont("Arial", size)
            f.setBold(bold)
            return f

        def footer():
            painter.setFont(font(8))
            painter.setPen(colors["muted"])
            painter.drawText(QtCore.QRectF(x0, page.y() + page.height() - 28, width, 18), QtCore.Qt.AlignmentFlag.AlignRight, f"mtestv2 | pagina {page_no}")

        def new_page():
            nonlocal y, page_no
            footer()
            writer.newPage()
            page_no += 1
            y = page.y() + margin

        def ensure(block_h: float):
            if y > page.y() + margin and y + block_h > page.y() + margin + height:
                new_page()

        def draw_text(text: str, size: int = 10, bold: bool = False, color: str = "ink", gap: int = 8):
            nonlocal y
            painter.setFont(font(size, bold))
            painter.setPen(colors[color])
            fm = QtGui.QFontMetrics(painter.font())
            rect = fm.boundingRect(QtCore.QRect(0, 0, int(width), 10000), int(QtCore.Qt.TextFlag.TextWordWrap), text)
            ensure(rect.height() + gap)
            painter.drawText(QtCore.QRectF(x0, y, width, rect.height() + 4), int(QtCore.Qt.TextFlag.TextWordWrap), text)
            y += rect.height() + gap

        def draw_rule(gap: int = 14):
            nonlocal y
            ensure(gap + 2)
            painter.setPen(QtGui.QPen(colors["line"], 1))
            painter.drawLine(int(x0), int(y), int(x0 + width), int(y))
            y += gap

        def draw_table(headers: list[str], rows: list[list[str]], col_widths: list[float], row_h: int = 34):
            nonlocal y
            header_h = 30
            ensure(header_h + row_h + 8)
            painter.setFont(font(8, True))
            painter.fillRect(QtCore.QRectF(x0, y, width, header_h), QtGui.QColor("#eaf0f6"))
            painter.setPen(colors["line"])
            painter.drawRect(QtCore.QRectF(x0, y, width, header_h))
            cx = x0
            for h, cw in zip(headers, col_widths):
                painter.setPen(colors["ink"])
                painter.drawText(QtCore.QRectF(cx + 4, y + 4, cw - 8, header_h - 8), int(QtCore.Qt.TextFlag.TextWordWrap), h)
                cx += cw
            y += header_h
            painter.setFont(font(8))
            for row in rows:
                ensure(row_h + 4)
                painter.fillRect(QtCore.QRectF(x0, y, width, row_h), QtGui.QColor("#ffffff"))
                painter.setPen(colors["line"])
                painter.drawRect(QtCore.QRectF(x0, y, width, row_h))
                cx = x0
                for value, cw in zip(row, col_widths):
                    painter.setPen(colors["ink"])
                    painter.drawText(QtCore.QRectF(cx + 4, y + 4, cw - 8, row_h - 6), int(QtCore.Qt.TextFlag.TextWordWrap), value)
                    cx += cw
                y += row_h
            y += 12

        try:
            created = datetime.now()
            painter.fillRect(QtCore.QRectF(page.x(), page.y(), page.width(), 112), colors["blue"])
            painter.setPen(QtGui.QColor("#ffffff"))
            painter.setFont(font(22, True))
            painter.drawText(QtCore.QRectF(x0, page.y() + 28, width, 34), "Informe final Experimento 3M")
            painter.setFont(font(10))
            painter.drawText(QtCore.QRectF(x0, page.y() + 68, width, 20), f"mtestv2 | generado el {created.strftime('%d/%m/%Y %H:%M:%S')}")
            y = page.y() + 138

            draw_text("Resumen", 16, True)
            draw_text(
                f"Animal/crotal: {self.state.crotal_id or '-'} | Motivo de cierre: {reason}. "
                "El Experimento 3M compara configuraciones del MAX3010x usando la referencia manual como ancla principal, "
                "y despues penaliza ruido, artefactos, saturacion, PI bajo y SpO2 no usable.",
                10,
            )
            if best:
                best_cfg = f"RED {best.get('red')} | IR {best.get('ir')} | AVG {best.get('avg')} | ADC {best.get('adc')}"
                draw_table(
                    ["Resultado", "Valor"],
                    [
                        ["Mejor configuracion", str(best.get("label", "-"))],
                        ["Parametros sensor", best_cfg],
                        ["Puntuacion", f"{fmt(float(best.get('score', math.nan)), 1, '-')} / 100"],
                        ["Pulso referencia", f"{fmt(float(best.get('ref', math.nan)), 1, '-')} BPM"],
                        ["BPM PPG", f"{fmt(float(best.get('bpm', math.nan)), 1, '-')} BPM"],
                        ["Diferencia", f"{fmt(float(best.get('diff', math.nan)), 1, '-')} BPM"],
                        ["SpO2 experimental", f"{fmt(float(best.get('spo2', math.nan)), 1, '-')} %"],
                        ["Lectura", str(best.get("note", ""))],
                    ],
                    [150, width - 150],
                    row_h=38,
                )
            else:
                draw_text("No hay tramos evaluados suficientes para elegir una configuracion.", 10)

            draw_text("Historial de tramos", 15, True)
            rows = []
            history = sorted(self.experiment_history, key=self._experiment_rank_key, reverse=True)
            for idx, item in enumerate(history, start=1):
                rows.append([
                    str(idx),
                    str(item.get("label", "-")),
                    fmt(float(item.get("ref", math.nan)), 1, "-"),
                    fmt(float(item.get("bpm", math.nan)), 1, "-"),
                    fmt(float(item.get("diff", math.nan)), 1, "-"),
                    fmt(float(item.get("score", math.nan)), 1, "-"),
                    "si" if bool(item.get("spo2_ready", 0)) else "no",
                    str(item.get("note", "")),
                ])
            draw_table(
                ["#", "Config.", "Ref.", "BPM", "Dif.", "Score", "SpO2", "Nota"],
                rows or [["-", "Sin datos", "-", "-", "-", "-", "-", "-"]],
                [22, 118, 42, 42, 38, 44, 38, width - 344],
                row_h=48,
            )

            draw_text("Decisiones tomadas", 15, True)
            if self.experiment_decisions:
                decision_rows = [
                    [str(i), item.get("after", "-"), item.get("next", "-"), item.get("reason", "-")]
                    for i, item in enumerate(self.experiment_decisions, start=1)
                ]
                draw_table(["#", "Despues de", "Siguiente", "Motivo"], decision_rows, [22, 150, 150, width - 322], row_h=46)
            else:
                draw_text("No se registraron decisiones adaptativas posteriores al primer tramo.", 10)

            draw_rule()
            draw_text("Criterio de lectura", 15, True)
            draw_text(
                "La referencia manual es la media de pulso previo, pulsioximetro final y fonendo final; valores 0 o vacios se ignoran. "
                "La mejor candidata es la que queda mas cerca de esa referencia y mantiene una senal PPG defendible: PI suficiente, pocos artefactos, "
                "saturacion baja y SpO2 experimental calculable. El resultado orienta la configuracion del sensor; no sustituye validacion fisiologica externa.",
                10,
            )
            footer()
        finally:
            painter.end()

    def _finish_experiment_capture(self, reason: str):
        self.state.finished = True
        if self.state.raw_handle:
            self.state.raw_handle.flush()
            self.state.raw_handle.close()
            self.state.raw_handle = None
            self.state.raw_writer = None
        self.finalize_capture(reason)

    def finalize_capture(self, reason: str):
        super().finalize_capture(reason)
        self._write_experiment_report(reason)

    def update_info(self):
        super().update_info()
        if not self.experiment_history:
            self.info.setText(self.info.text() + f"\nExperimento 3M decision:\n{self.experiment_last_decision}\n")
            return
        best = self._best_experiment_result()
        tail = self.experiment_history[-3:]
        lines = ["", "Experimento 3M decision:", self.experiment_last_decision, "", "Ultimos tramos:"]
        for item in tail:
            lines.append(
                f"- {item.get('label')}: score {fmt(float(item.get('score', math.nan)),1)} | "
                f"ref {fmt(float(item.get('ref', math.nan)),1)} | bpm {fmt(float(item.get('bpm', math.nan)),1)} | "
                f"dif {fmt(float(item.get('diff', math.nan)),1)} | {item.get('note', '')}"
            )
        if best:
            lines.extend([
                "",
                "Mejor provisional:",
                f"{best.get('label')} | score {fmt(float(best.get('score', math.nan)),1)} | dif {fmt(float(best.get('diff', math.nan)),1)} BPM",
            ])
        self.info.setText(self.info.text() + "\n".join(lines) + "\n")

    def apply_scheduled_step(self, index: int):
        if self.scheduled_segments and self.scheduled_segments[-1].end_sample is None:
            self.scheduled_segments[-1].end_sample = len(self.state.t)
        previous_segment = self.scheduled_segments[-1] if self.scheduled_segments else None
        if previous_segment is None:
            return
        self.state.capturing = False
        self.send_command("STOP")
        if self.state.raw_handle:
            self.state.raw_handle.flush()
        QtWidgets.QApplication.processEvents()
        pulse_between, fonendo_between = self.ask_transition_reference(previous_segment.step, None)
        previous_segment.pulse_final_pulsio = pulse_between
        previous_segment.pulse_final_fonendo = fonendo_between
        self.state.pulse_prev = pulse_between or fonendo_between
        self.prev_pulse_edit.setText(self.state.pulse_prev)
        self.experiment_history.append(self._evaluate_experiment_segment(previous_segment))
        if index >= self.experiment_max_steps or self._should_stop_experiment():
            self._finish_experiment_capture("EXPERIMENTO_3M_OPTIMO_ESTIMADO")
            return
        step, decision_reason = self._choose_next_experiment_step(index)
        if step is None:
            self._finish_experiment_capture("EXPERIMENTO_3M_SIN_CANDIDATOS")
            return
        self.experiment_last_decision = f"Siguiente: {step.label}. Motivo: {decision_reason}."
        self.experiment_decisions.append({
            "after": previous_segment.step.label,
            "next": step.label,
            "reason": decision_reason,
            "created": datetime.now().isoformat(),
        })
        if index < len(self.scheduled_steps):
            self.scheduled_steps[index] = step
        else:
            self.scheduled_steps.append(step)
        self.scheduled_step_index = index
        self.state.config_label = step.label
        self.sensor_widget.set_config(step.config)
        self.apply_sensor_config(step.config)
        try:
            self.serial_port.reset_input_buffer()
        except Exception:
            pass
        self.scheduled_segments.append(ScheduledSegment(step, index, len(self.state.t), pulse_prev=self.state.pulse_prev))
        self.send_command("START_CONTINUOUS")
        self.scheduled_step_start_wall = time.time()
        self.state.capturing = True
