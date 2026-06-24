from __future__ import annotations

import math
import time

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from ..utils import fmt, now_stamp, safe_float_text, sanitize_id
from ..widgets import AnalysisConfigWidget, NoWheelDoubleSpinBox, SensorConfigWidget
from .measurement_window import PPGSuite


class TemperatureWindow(PPGSuite):
    def __init__(self):
        super().__init__("real")
        self.setWindowTitle("PPG Suite v8 | Campo - solo temperatura")
        self.resize(900, 620)

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

        self.sensor_widget = SensorConfigWidget("Configuracion MAX3010x")
        left.addWidget(self.sensor_widget)
        self.btn_save_animal_config = QtWidgets.QPushButton("Guardar configuracion especie")
        left.addWidget(self.btn_save_animal_config)
        self.btn_save_animal_config.clicked.connect(self.save_animal_profile_clicked)
        self.analysis_widget = AnalysisConfigWidget()
        self.analysis_widget.setVisible(False)

        capture_group = QtWidgets.QGroupBox("Toma de temperatura")
        form = QtWidgets.QFormLayout(capture_group)
        self.crotal_edit = QtWidgets.QLineEdit("SIN_CROTAL")
        self.duration_spin = NoWheelDoubleSpinBox()
        self.duration_spin.setRange(2, 3600)
        self.duration_spin.setDecimals(1)
        self.duration_spin.setValue(60.0)
        self.duration_spin.setSuffix(" s")
        self.prev_pulse_edit = QtWidgets.QLineEdit()
        self.animal_combo = QtWidgets.QComboBox()
        self.configure_animal_combo(self.animal_combo)
        self.udder_combo = QtWidgets.QComboBox()
        self.configure_udder_combo(self.udder_combo)
        self.temp_mapping_widget = self.create_temp_mapping_widget()
        self.temp_monitor_widget = self.create_temp_monitor_widget()
        self.vacuum_combo = QtWidgets.QComboBox()
        self.vacuum_combo.addItems(["", "con vacio", "sin vacio"])
        self.condition_edit = QtWidgets.QLineEdit("solo temperatura en campo")
        self.animal_combo.currentIndexChanged.connect(self.refresh_animal_dependent_controls)
        form.addRow("Crotal:", self.crotal_edit)
        form.addRow("Animal:", self.animal_combo)
        form.addRow("Duración:", self.duration_spin)
        form.addRow("Sensor:", self.udder_combo)
        form.addRow("Termometros:", self.temp_mapping_widget)
        form.addRow("Temperatura:", self.temp_monitor_widget)
        form.addRow("Medicion:", self.vacuum_combo)
        form.addRow("Anotaciones inicio:", self.condition_edit)
        left.addWidget(capture_group)
        self.refresh_animal_dependent_controls()

        wiring = QtWidgets.QLabel(
            "Conexiones NTC:\n"
            "A0-A3: 3.3V -> NTC -> Ax -> resistencia fija 10k -> GND\n"
            "Todos los divisores comparten 3.3V y GND. No conectes la NTC sola al pin: sin la resistencia a GND queda flotante."
        )
        wiring.setWordWrap(True)
        wiring.setStyleSheet("color: #24415f; font-weight: bold;")
        left.addWidget(wiring)

        self.btn_start = QtWidgets.QPushButton("Iniciar temperatura")
        self.btn_diagnostic = QtWidgets.QPushButton("Diagnostico Arduino")
        self.btn_stop = QtWidgets.QPushButton("Parar")
        self.btn_back_menu = QtWidgets.QPushButton("Volver al menú inicial")
        self.btn_open_base = QtWidgets.QPushButton("Mostrar resultados")
        for b in [self.btn_start, self.btn_diagnostic, self.btn_stop, self.btn_back_menu, self.btn_open_base]:
            b.setMinimumHeight(42)
            left.addWidget(b)
        self.btn_start.clicked.connect(self.start_temperature_capture)
        self.btn_diagnostic.clicked.connect(self.send_diagnostic_command)
        self.btn_stop.clicked.connect(lambda: self.stop_capture("STOP_TEMP_MANUAL"))
        self.btn_back_menu.clicked.connect(self.return_to_menu)
        self.btn_open_base.clicked.connect(self.open_statistics_window)

        self.info = QtWidgets.QLabel()
        self.info.setFont(QtGui.QFont("Consolas", 10))
        self.info.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.info.setWordWrap(True)
        self.info.setMinimumWidth(320)
        left.addWidget(self.info, stretch=1)

        self.plot_temp = pg.PlotWidget(title="Temperatura inicial")
        self.plot_temp.setBackground("w")
        self.plot_temp.showGrid(x=True, y=True, alpha=0.25)
        self.plot_temp.setLabel("bottom", "Tiempo", units="s")
        self.temp_live_curves = {
            "A0": self.plot_temp.plot([], [], pen=pg.mkPen((180, 60, 60), width=2), name="A0"),
            "A1": self.plot_temp.plot([], [], pen=pg.mkPen((40, 100, 210), width=2), name="A1"),
            "A2": self.plot_temp.plot([], [], pen=pg.mkPen((220, 140, 30), width=2), name="A2"),
            "A3": self.plot_temp.plot([], [], pen=pg.mkPen((80, 160, 80), width=2), name="A3"),
        }
        self.temp_a0_curve = self.temp_live_curves["A0"]
        self.temp_a1_curve = self.temp_live_curves["A1"]
        self.temp_a2_curve = self.temp_live_curves["A2"]
        self.temp_a3_curve = self.temp_live_curves["A3"]
        self.temp_alert_line = pg.InfiniteLine(
            angle=0,
            movable=False,
            pen=pg.mkPen((220, 60, 40), width=1, style=QtCore.Qt.PenStyle.DashLine),
        )
        self.plot_temp.addItem(self.temp_alert_line)
        self.temp_live_legend = self.plot_temp.addLegend()
        root.addWidget(self.plot_temp, stretch=1)

    def start_temperature_capture(self):
        if not self.serial_port or not self.serial_port.is_open:
            QtWidgets.QMessageBox.warning(self, "Serial", "No hay puerto serie abierto.")
            return
        if self.state.capturing:
            return
        self.reset_capture_state(keep_identity=False)
        st = self.state
        st.mode = "temp"
        st.requested_duration_s = float(self.duration_spin.value())
        st.crotal_id = sanitize_id(self.crotal_edit.text())
        st.pulse_prev = safe_float_text(self.prev_pulse_edit.text())
        st.measurement_condition = self.current_condition_text() or "solo temperatura en campo"
        st.config_label = "solo_temperatura"
        st.base_name = f"TEMP_CAMPO_{st.crotal_id}_{now_stamp()}"
        st.capture_start_wall = time.time()
        st.capturing = True
        self.last_config_ack = "no aplica en solo temperatura"
        self.last_config_line = ""
        try:
            self.serial_port.reset_input_buffer()
            self.serial_port.reset_output_buffer()
        except Exception:
            pass
        self.open_raw_file()
        self.save_current_config_json(prefix=f"config_{st.base_name}")
        self.send_command("START_TEMP")

    def check_auto_stop(self):
        st = self.state
        if st.capturing and st.mode == "temp":
            if time.time() - st.capture_start_wall >= st.requested_duration_s:
                self.stop_capture("DURACION_TEMP_COMPLETADA")

    def update_live_metrics(self):
        return

    def update_plots(self):
        t, _red, _ir = self.arrays()
        channels = self.temp_channel_arrays()
        active_channels = set(self.sync_temperature_curve_visibility(self.temp_live_curves, getattr(self, "temp_live_legend", None)))
        if t.size < 2:
            for curve in self.temp_live_curves.values():
                curve.setData([], [])
            return
        window_s = max(1.0, float(self.state.temp_monitor_seconds or self.current_temp_monitor_seconds()))
        for channel, curve in self.temp_live_curves.items():
            if channel not in active_channels:
                curve.setData([], [])
                continue
            values, _raw = channels[channel]
            n = min(t.size, values.size)
            if n < 2:
                curve.setData([], [])
                continue
            rel = t[:n] - float(t[0])
            mask = np.isfinite(rel) & np.isfinite(values[:n]) & (rel >= 0.0) & (rel <= window_s)
            curve.setData(rel[mask], values[:n][mask])
        self.temp_alert_line.setValue(float(self.state.temp_alert_threshold_c or self.current_temp_alert_threshold()))
        label, value, at_s = self.temperature_window_max()
        channel_names = self.format_active_temp_channels()
        if math.isfinite(value):
            self.plot_temp.setTitle(f"Temperatura inicial ({channel_names}) | max {fmt(value,1)} C {label} a {fmt(at_s,1)} s")
        else:
            self.plot_temp.setTitle(f"Temperatura inicial ({channel_names})")
        self.plot_temp.setXRange(0.0, window_s, padding=0.02)

    def _temp_channel_status(self, name: str, temp_c: float, raw: float) -> str:
        if not math.isfinite(raw):
            return f"{name}: sin dato raw"
        adc_max = 4095.0
        if raw <= 5:
            return f"{name}: ERROR raw casi 0 -> posible GND/cableado"
        if raw >= adc_max - 5:
            return f"{name}: ERROR raw casi max -> posible VCC/abierto"
        if not math.isfinite(temp_c):
            return f"{name}: raw {fmt(raw,0)} pero temp NaN -> revisar divisor/config temp"
        if temp_c < -10 or temp_c > 80:
            return f"{name}: temp rara ({fmt(temp_c,2)} C) raw {fmt(raw,0)}"
        return f"{name}: OK probable ({fmt(temp_c,2)} C | raw {fmt(raw,0)})"

    def _last_data_fields(self) -> int:
        parts = self.state.last_line.split(",")
        if len(parts) >= 3:
            try:
                int(parts[0].strip())
                float(parts[1].strip())
                float(parts[2].strip())
                return len(parts)
            except Exception:
                return 0
        return 0

    def update_info(self):
        st = self.state
        temp = self.temperature_summary()
        if st.capturing:
            elapsed = time.time() - st.capture_start_wall
            status = f"capturando temperatura... {elapsed:.1f} s"
        elif st.sensor_ready:
            status = "READY | preparado"
        else:
            status = "esperando READY"
        fields = self._last_data_fields()
        format_status = "sin datos de temperatura"
        if fields == 5:
            format_status = "formato antiguo: solo A0 (5 campos)"
        elif fields == 7:
            format_status = "formato A0 + A1 (7 campos)"
        elif fields == 11:
            format_status = "formato A0 + A1 + A2 + A3 (11 campos)"
        elif fields:
            format_status = f"formato inesperado: {fields} campos"
        active_channels = self.active_temp_channels()
        if fields == 11 and len(active_channels) == 2:
            format_status += "; mostrando A0 + A1 por especie"
        sample_lines = "".join(
            f"Muestras {channel}: {temp[f'temp_{channel.lower()}_samples']} temp / {temp[f'temp_{channel.lower()}_raw_samples']} raw\n"
            for channel in active_channels
        )
        if len(active_channels) == 4:
            position_lines = (
                f"Vaca FLT/FRT/RLT/RRT final: {fmt(temp['temp_flt_c_final_max_5s'], 2)} / {fmt(temp['temp_frt_c_final_max_5s'], 2)} / "
                f"{fmt(temp['temp_rlt_c_final_max_5s'], 2)} / {fmt(temp['temp_rrt_c_final_max_5s'], 2)} C\n"
            )
        else:
            position_lines = (
                f"RT final: {fmt(temp['temp_rt_c_final_max_5s'], 2)} C | ult. {fmt(temp['temp_rt_c_last'], 2)} C\n"
                f"LT final: {fmt(temp['temp_lt_c_final_max_5s'], 2)} C | ult. {fmt(temp['temp_lt_c_last'], 2)} C\n"
            )
        channel_lines = ""
        diagnostic_lines = []
        for channel in active_channels:
            prefix = f"temp_{channel.lower()}"
            channel_lines += (
                f"{channel} actual: {fmt(temp[f'{prefix}_c_last'], 2)} C | final {fmt(temp[f'{prefix}_c_final_max_5s'], 2)} C\n"
                f"{channel} min/max: {fmt(temp[f'{prefix}_c_min'], 2)} / {fmt(temp[f'{prefix}_c_max'], 2)} C | raw {fmt(temp[f'{prefix}_raw_last'], 0)}\n"
            )
            diagnostic_lines.append(self._temp_channel_status(channel, float(temp[f"{prefix}_c_last"]), float(temp[f"{prefix}_raw_last"])))
        self.info.setText(
            f"MODO CAMPO - SOLO TEMPERATURA\n"
            f"Puerto: {self.port_name}\n"
            f"Estado: {status}\n"
            f"Crotal: {st.crotal_id}\n"
            f"{sample_lines}"
            f"Lineas OK: {st.valid_lines} | descartadas: {st.discarded_lines}\n"
            f"Serial: {format_status}\n\n"
            f"{position_lines}"
            f"{self.temp_monitor_status_line()}\n"
            f"{channel_lines}\n"
            f"Diagnostico rapido:\n"
            f"{chr(10).join(diagnostic_lines)}\n\n"
            f"Conexiones:\n"
            f"A0-A3: 3.3V -> NTC -> Ax -> R fija 10k -> GND\n\n"
            f"Ultima linea: {st.last_line[:110]}\n"
            f"Ultimo control: {st.last_control[:110]}\n\n"
            f"Raw: {st.raw_file.name if st.raw_file else '-'}\n"
        )
        return
        self.info.setText(
            f"MODO CAMPO - SOLO TEMPERATURA\n"
            f"Puerto: {self.port_name}\n"
            f"Estado: {status}\n"
            f"Crotal: {st.crotal_id}\n"
            f"Muestras temp: {temp['temp_samples']}\n\n"
            f"Temp actual: {fmt(temp['temp_c_last'], 2)} °C\n"
            f"Media: {fmt(temp['temp_c_mean'], 2)} °C\n"
            f"Mín/Máx: {fmt(temp['temp_c_min'], 2)} / {fmt(temp['temp_c_max'], 2)} °C\n"
            f"Raw NTC: {fmt(temp['temp_raw_last'], 0)}\n\n"
            f"Raw: {st.raw_file.name if st.raw_file else '-'}\n"
        )

    def save_images(self):
        return

    def finalize_capture(self, reason: str):
        self.ask_final_reference(include_pulse=False)
        self.update_raw_manual_reference()
        self.save_summary(reason)
        self.write_session_row(reason)
