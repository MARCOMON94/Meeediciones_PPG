from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets

from ..models import SensorConfig
from ..processing import estimate_hz, processed_for_plot
from ..utils import fmt, safe_float_text, sanitize_id, now_stamp
from ..widgets import AnalysisConfigWidget, NoWheelDoubleSpinBox, NoWheelSpinBox, SensorConfigWidget
from ..paths import RESULTS_DIR
from ..utils import open_folder
from .measurement_window import PPGSuite


@dataclass(frozen=True)
class ScheduledStep:
    label: str
    description: str
    config: SensorConfig


def build_64_config_steps() -> list[ScheduledStep]:
    steps: list[ScheduledStep] = []
    idx = 1
    for adc in (8192, 16384):
        for avg in (1, 4):
            for ir in (31, 63, 95, 127):
                for red in (31, 63, 95, 127):
                    label = f"CONFIG {idx:02d} - RED{red} IR{ir} AVG{avg} ADC{adc}"
                    desc = f"Barrido 64 configuraciones: RED={red}, IR={ir}, AVG={avg}, ADC={adc}"
                    steps.append(ScheduledStep(label, desc, SensorConfig(red=red, ir=ir, avg=avg, rate=100, width=411, adc=adc, skip=50)))
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
        cfg = SensorConfig(red=brightness, ir=brightness, avg=avg, rate=100, width=411, adc=adc, skip=50)
        steps.append(ScheduledStep(label, desc, cfg))
    return steps


class ScheduledConfigWindow(PPGSuite):
    def __init__(self, title: str, steps: list[ScheduledStep], total_duration_s: float, condition: str):
        self.scheduled_title = title
        self.scheduled_steps = steps
        self.scheduled_total_duration_s = float(total_duration_s)
        self.scheduled_condition = condition
        self.scheduled_step_index = 0
        self.scheduled_step_start_wall = 0.0
        self.scheduled_step_duration_s = self.scheduled_total_duration_s / max(1, len(self.scheduled_steps))
        super().__init__("test")
        self.setWindowTitle(f"PPG Suite v8 | {title}")
        self.resize(1120, 740)

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
        self.condition_edit = QtWidgets.QLineEdit(self.scheduled_condition)
        self.duration_spin = NoWheelDoubleSpinBox()
        self.duration_spin.setRange(1, 120)
        self.duration_spin.setDecimals(1)
        self.duration_spin.setValue(self.scheduled_total_duration_s / 60.0)
        self.duration_spin.setSuffix(" min")
        form.addRow("Crotal:", self.crotal_edit)
        form.addRow("Pulso previo ref.:", self.prev_pulse_edit)
        form.addRow("Condiciones:", self.condition_edit)
        form.addRow("Duración total:", self.duration_spin)
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
        st.mode = "scheduled"
        st.requested_duration_s = float(self.duration_spin.value()) * 60.0
        self.scheduled_total_duration_s = st.requested_duration_s
        self.scheduled_step_duration_s = self.scheduled_total_duration_s / max(1, len(self.scheduled_steps))
        self.scheduled_step_index = 0
        st.crotal_id = sanitize_id(self.crotal_edit.text())
        st.pulse_prev = safe_float_text(self.prev_pulse_edit.text())
        st.measurement_condition = self.current_condition_text() or self.scheduled_condition
        st.base_name = f"BLOQUE_{len(self.scheduled_steps)}CFG_{st.crotal_id}_{now_stamp()}"
        st.capture_start_wall = time.time()
        st.capturing = True
        try:
            self.serial_port.reset_input_buffer()
            self.serial_port.reset_output_buffer()
        except Exception:
            pass
        if not self.confirm_config_before_start(self.scheduled_steps[0].config):
            st.capturing = False
            return
        self.open_raw_file()
        self.apply_scheduled_step(0)
        self.save_current_config_json(prefix=f"config_{st.base_name}")
        self.send_command("START_CONTINUOUS")

    def apply_scheduled_step(self, index: int):
        step = self.scheduled_steps[index]
        self.scheduled_step_index = index
        self.scheduled_step_start_wall = time.time()
        self.state.config_label = step.label
        self.sensor_widget.set_config(step.config)
        self.apply_sensor_config(step.config)

    def check_auto_stop(self):
        st = self.state
        if not st.capturing:
            return
        elapsed = time.time() - st.capture_start_wall
        if elapsed >= self.scheduled_total_duration_s:
            self.stop_capture("BLOQUE_COMPLETADO")
            return
        next_index = min(int(elapsed // self.scheduled_step_duration_s), len(self.scheduled_steps) - 1)
        if next_index != self.scheduled_step_index:
            self.apply_scheduled_step(next_index)

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
        elapsed = time.time() - st.capture_start_wall if st.capturing else 0.0
        step = self.scheduled_steps[self.scheduled_step_index]
        remaining = max(0.0, self.scheduled_total_duration_s - elapsed)
        self.info.setText(
            f"{self.scheduled_title}\n"
            f"Puerto: {self.port_name}\n"
            f"Estado: {'CAPTURANDO' if st.capturing else ('READY | preparado' if st.sensor_ready else 'esperando READY')}\n"
            f"Crotal: {st.crotal_id}\n"
            f"Bloque: {self.scheduled_step_index + 1}/{len(self.scheduled_steps)}\n"
            f"{step.label}\n"
            f"{step.description}\n"
            f"Tiempo: {elapsed:.1f}s | quedan {remaining:.1f}s\n"
            f"Config Arduino: {self.last_config_ack} | {self.last_config_line[:80]}\n\n"
            f"Muestras: {len(st.t)} | descartadas: {st.discarded_lines}\n"
            f"BPM: {fmt(m.bpm,0)} | calidad {fmt(m.quality,0)} ({m.quality_label})\n"
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
            defaults = [f"CONFIG {row + 1:02d}", "31", "31", "1", "100", "411", "16384", "50", "0", ""]
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
                    red=int(vals[1] or 31),
                    ir=int(vals[2] or 31),
                    avg=int(vals[3] or 1),
                    rate=int(vals[4] or 100),
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
        self.scheduled_title = f"Configuraciones personalizadas ({len(self.scheduled_steps)})"
        super().start_scheduled_capture()
