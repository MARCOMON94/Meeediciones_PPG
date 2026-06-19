from __future__ import annotations

import math
import time

from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from ..paths import RESULTS_DIR
from ..utils import fmt, now_stamp, open_folder, safe_float_text, sanitize_id
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

        self.sensor_widget = SensorConfigWidget()
        self.sensor_widget.setVisible(False)
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
        self.udder_combo = QtWidgets.QComboBox()
        self.udder_combo.addItems(["", "ubre", "right", "left"])
        self.vacuum_combo = QtWidgets.QComboBox()
        self.vacuum_combo.addItems(["", "con vacio", "sin vacio"])
        self.condition_edit = QtWidgets.QLineEdit("solo temperatura en campo")
        form.addRow("Crotal:", self.crotal_edit)
        form.addRow("Duración:", self.duration_spin)
        form.addRow("Ubre:", self.udder_combo)
        form.addRow("Medicion:", self.vacuum_combo)
        form.addRow("Condiciones:", self.condition_edit)
        left.addWidget(capture_group)

        self.btn_start = QtWidgets.QPushButton("Iniciar temperatura")
        self.btn_stop = QtWidgets.QPushButton("Parar")
        self.btn_back_menu = QtWidgets.QPushButton("Volver al menú inicial")
        self.btn_open_base = QtWidgets.QPushButton("Abrir resultados")
        for b in [self.btn_start, self.btn_stop, self.btn_back_menu, self.btn_open_base]:
            b.setMinimumHeight(42)
            left.addWidget(b)
        self.btn_start.clicked.connect(self.start_temperature_capture)
        self.btn_stop.clicked.connect(lambda: self.stop_capture("STOP_TEMP_MANUAL"))
        self.btn_back_menu.clicked.connect(self.return_to_menu)
        self.btn_open_base.clicked.connect(lambda: open_folder(RESULTS_DIR))

        self.info = QtWidgets.QLabel()
        self.info.setFont(QtGui.QFont("Consolas", 10))
        self.info.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.info.setWordWrap(True)
        self.info.setMinimumWidth(320)
        left.addWidget(self.info, stretch=1)

        self.plot_temp = pg.PlotWidget(title="Temperatura")
        self.plot_temp.setBackground("w")
        self.plot_temp.showGrid(x=True, y=True, alpha=0.25)
        self.plot_temp.setLabel("bottom", "Tiempo", units="s")
        self.temp_curve = self.plot_temp.plot([], [], pen=pg.mkPen((180, 60, 60), width=2))
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
        temp_c, _raw = self.temp_arrays()
        n = min(t.size, temp_c.size)
        if n < 2:
            self.temp_curve.setData([], [])
            return
        self.temp_curve.setData(t[:n], temp_c[:n])
        self.plot_temp.setXRange(float(t[0]), max(float(t[n - 1]), float(t[0]) + 1), padding=0.01)

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
        self.save_summary(reason)
        self.write_session_row(reason)
