from __future__ import annotations

import math
import time

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
        self.vacuum_combo = QtWidgets.QComboBox()
        self.vacuum_combo.addItems(["", "con vacio", "sin vacio"])
        self.condition_edit = QtWidgets.QLineEdit("solo temperatura en campo")
        self.animal_combo.currentIndexChanged.connect(self.refresh_animal_dependent_controls)
        form.addRow("Crotal:", self.crotal_edit)
        form.addRow("Animal:", self.animal_combo)
        form.addRow("Duración:", self.duration_spin)
        form.addRow("Sensor:", self.udder_combo)
        form.addRow("Termometros:", self.temp_mapping_widget)
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

        self.plot_temp = pg.PlotWidget(title="Temperatura A0 / A1 / A2 / A3")
        self.plot_temp.setBackground("w")
        self.plot_temp.showGrid(x=True, y=True, alpha=0.25)
        self.plot_temp.setLabel("bottom", "Tiempo", units="s")
        self.temp_a0_curve = self.plot_temp.plot([], [], pen=pg.mkPen((180, 60, 60), width=2), name="A0")
        self.temp_a1_curve = self.plot_temp.plot([], [], pen=pg.mkPen((40, 100, 210), width=2), name="A1")
        self.temp_a2_curve = self.plot_temp.plot([], [], pen=pg.mkPen((220, 140, 30), width=2), name="A2")
        self.temp_a3_curve = self.plot_temp.plot([], [], pen=pg.mkPen((80, 160, 80), width=2), name="A3")
        self.plot_temp.addLegend()
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
        temp_a0_c, _temp_a0_raw = channels["A0"]
        temp_a1_c, _temp_a1_raw = channels["A1"]
        temp_a2_c, _temp_a2_raw = channels["A2"]
        temp_a3_c, _temp_a3_raw = channels["A3"]
        n = min(t.size, temp_a0_c.size, temp_a1_c.size, temp_a2_c.size, temp_a3_c.size)
        if n < 2:
            self.temp_a0_curve.setData([], [])
            self.temp_a1_curve.setData([], [])
            self.temp_a2_curve.setData([], [])
            self.temp_a3_curve.setData([], [])
            return
        self.temp_a0_curve.setData(t[:n], temp_a0_c[:n])
        self.temp_a1_curve.setData(t[:n], temp_a1_c[:n])
        self.temp_a2_curve.setData(t[:n], temp_a2_c[:n])
        self.temp_a3_curve.setData(t[:n], temp_a3_c[:n])
        self.plot_temp.setXRange(float(t[0]), max(float(t[n - 1]), float(t[0]) + 1), padding=0.01)

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
        a0_status = self._temp_channel_status("A0", float(temp["temp_a0_c_last"]), float(temp["temp_a0_raw_last"]))
        a1_status = self._temp_channel_status("A1", float(temp["temp_a1_c_last"]), float(temp["temp_a1_raw_last"]))
        a2_status = self._temp_channel_status("A2", float(temp["temp_a2_c_last"]), float(temp["temp_a2_raw_last"]))
        a3_status = self._temp_channel_status("A3", float(temp["temp_a3_c_last"]), float(temp["temp_a3_raw_last"]))
        self.info.setText(
            f"MODO CAMPO - SOLO TEMPERATURA\n"
            f"Puerto: {self.port_name}\n"
            f"Estado: {status}\n"
            f"Crotal: {st.crotal_id}\n"
            f"Muestras A0: {temp['temp_a0_samples']} temp / {temp['temp_a0_raw_samples']} raw\n"
            f"Muestras A1: {temp['temp_a1_samples']} temp / {temp['temp_a1_raw_samples']} raw\n"
            f"Muestras A2: {temp['temp_a2_samples']} temp / {temp['temp_a2_raw_samples']} raw\n"
            f"Muestras A3: {temp['temp_a3_samples']} temp / {temp['temp_a3_raw_samples']} raw\n"
            f"Lineas OK: {st.valid_lines} | descartadas: {st.discarded_lines}\n"
            f"Serial: {format_status}\n\n"
            f"RT final: {fmt(temp['temp_rt_c_final_max_5s'], 2)} C | ult. {fmt(temp['temp_rt_c_last'], 2)} C\n"
            f"LT final: {fmt(temp['temp_lt_c_final_max_5s'], 2)} C | ult. {fmt(temp['temp_lt_c_last'], 2)} C\n"
            f"Vaca FLT/FRT/RLT/RRT final: {fmt(temp['temp_flt_c_final_max_5s'], 2)} / {fmt(temp['temp_frt_c_final_max_5s'], 2)} / {fmt(temp['temp_rlt_c_final_max_5s'], 2)} / {fmt(temp['temp_rrt_c_final_max_5s'], 2)} C\n"
            f"A0 actual: {fmt(temp['temp_a0_c_last'], 2)} C | final {fmt(temp['temp_a0_c_final_max_5s'], 2)} C\n"
            f"A0 min/max: {fmt(temp['temp_a0_c_min'], 2)} / {fmt(temp['temp_a0_c_max'], 2)} C | raw {fmt(temp['temp_a0_raw_last'], 0)}\n"
            f"A1 actual: {fmt(temp['temp_a1_c_last'], 2)} C | final {fmt(temp['temp_a1_c_final_max_5s'], 2)} C\n"
            f"A1 min/max: {fmt(temp['temp_a1_c_min'], 2)} / {fmt(temp['temp_a1_c_max'], 2)} C | raw {fmt(temp['temp_a1_raw_last'], 0)}\n\n"
            f"Diagnostico rapido:\n"
            f"{a0_status}\n"
            f"{a1_status}\n"
            f"{a2_status}\n"
            f"{a3_status}\n\n"
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
