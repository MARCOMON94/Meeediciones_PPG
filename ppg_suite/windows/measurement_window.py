from __future__ import annotations

import csv
import json
import math
import time
from dataclasses import asdict
from datetime import datetime
from typing import Optional

import numpy as np
import serial
from serial.tools import list_ports
from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from ..menu import AppMode
from ..models import AnalysisConfig, CaptureState, Metrics, SensorConfig
from ..paths import BASE_DIR, CONFIG_DIR, FIGURES_DIR, LOG_DIR, PROCESSED_DIR, RAW_DIR, REPORT_DIR, SCREENSHOT_DIR, SESSION_DIR, log
from ..processing import (
    block_bpm, detect_artifacts, estimate_bpm_peaks, estimate_hz, find_local_peaks,
    processed_for_plot, processed_ppg, robust_normalize, score_and_merge_metrics, uniform_resample,
)
from ..utils import fmt, now_stamp, open_folder, safe_float_text, sanitize_id
from ..widgets import AnalysisConfigWidget, NoWheelDoubleSpinBox, SensorConfigWidget


class PPGSuite(QtWidgets.QMainWindow):
    back_to_menu = QtCore.pyqtSignal()
    open_reajustes_requested = QtCore.pyqtSignal()

    def __init__(self, app_mode: AppMode = "real"):
        super().__init__()
        self.app_mode: AppMode = app_mode
        self.setWindowTitle(f"PPG Suite v8 | MAX3010x | BPM + SpO2 | modo {app_mode}")
        if app_mode == "real":
            self.resize(1120, 740)
        elif app_mode == "test":
            self.resize(1220, 780)
        else:
            self.resize(1250, 800)
        self.state = CaptureState()
        self.serial_port: Optional[serial.Serial] = None
        self.rx_buffer = ""
        self.port_name = "NO CONECTADO"
        self.last_sensor_config = SensorConfig()
        self.last_config_command = ""
        self.last_config_ack = "sin confirmar"
        self.last_config_line = ""
        self.last_config_sent_at = 0.0

        self._last_info_update = 0.0
        self._last_metric_update = 0.0
        self._last_plot_update = 0.0
        self._last_heavy_plot_update = 0.0
        self._last_long_window_refresh = 0.0

        if self.app_mode == "real":
            self.info_update_interval = 0.25
            self.metric_update_interval = 1.00
            self.plot_update_interval = 0.12
            self.heavy_plot_interval = 9999.0
        elif self.app_mode == "test":
            self.info_update_interval = 0.20
            self.metric_update_interval = 0.50
            self.plot_update_interval = 0.10
            self.heavy_plot_interval = 1.25
        else:
            self.info_update_interval = 0.15
            self.metric_update_interval = 0.35
            self.plot_update_interval = 0.08
            self.heavy_plot_interval = 0.80
        self.session_file = SESSION_DIR / f"session_{now_stamp()}.csv"
        self.session_handle = open(self.session_file, "w", newline="", encoding="utf-8")
        self.session_writer = csv.writer(self.session_handle, delimiter=";")
        self.write_session_header()
        self.build_ui()
        self.refresh_ports()
        self.try_auto_connect()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(40 if self.app_mode != "real" else 60)
        log.warning("PPG Suite v8 arrancado modo=%s", self.app_mode)

    def build_ui(self):
        central = QtWidgets.QWidget(); self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)
        left_width = 390 if self.app_mode == "real" else 430
        left_scroll = QtWidgets.QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFixedWidth(left_width)
        left_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Expanding)
        left_widget = QtWidgets.QWidget()
        left_widget.setMinimumWidth(left_width - 24)
        left_widget.setMaximumWidth(left_width - 24)
        left_scroll.setWidget(left_widget)
        left = QtWidgets.QVBoxLayout(left_widget)
        root.addWidget(left_scroll, stretch=0)

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

        capture_group = QtWidgets.QGroupBox("Toma normal")
        cap = QtWidgets.QFormLayout(capture_group)
        self.crotal_edit = QtWidgets.QLineEdit("SIN_CROTAL")
        self.duration_spin = NoWheelDoubleSpinBox(); self.duration_spin.setRange(2, 3600); self.duration_spin.setDecimals(1); self.duration_spin.setValue(20.0); self.duration_spin.setSuffix(" s")
        self.prev_pulse_edit = QtWidgets.QLineEdit()
        self.condition_edit = QtWidgets.QLineEdit()
        self.condition_edit.setPlaceholderText("Ej.: campo, ordeño activo, sensor reajustado, animal inquieto...")
        cap.addRow("Crotal:", self.crotal_edit)
        cap.addRow("Duración:", self.duration_spin)
        cap.addRow("Pulso previo ref.:", self.prev_pulse_edit)
        cap.addRow("Condiciones:", self.condition_edit)
        left.addWidget(capture_group)

        self.sensor_widget = SensorConfigWidget()
        left.addWidget(self.sensor_widget)
        self.analysis_widget = AnalysisConfigWidget()
        left.addWidget(self.analysis_widget)

        self.btn_toggle_advanced = QtWidgets.QPushButton("Mostrar/ocultar configuración avanzada")
        left.addWidget(self.btn_toggle_advanced)
        self.btn_toggle_advanced.clicked.connect(self.toggle_advanced_controls)

        if self.app_mode == "real":
            self.sensor_widget.setVisible(False)
            self.analysis_widget.setVisible(False)
            self.btn_toggle_advanced.setVisible(True)
        else:
            self.btn_toggle_advanced.setVisible(False)

        self.btn_apply_config = QtWidgets.QPushButton("Aplicar configuración sensor")
        self.btn_start = QtWidgets.QPushButton("Iniciar medición real" if self.app_mode == "real" else "Iniciar toma")
        self.btn_stop = QtWidgets.QPushButton("Parar")
        self.btn_back_menu = QtWidgets.QPushButton("Volver al menú inicial")
        self.btn_long = QtWidgets.QPushButton("Abrir reajustes / larga duración")
        self.btn_open_base = QtWidgets.QPushButton("Abrir carpeta mtest/resultados")
        self.btn_open_logs = QtWidgets.QPushButton("Abrir logs")
        for b in [self.btn_apply_config, self.btn_start, self.btn_stop, self.btn_back_menu, self.btn_long, self.btn_open_base, self.btn_open_logs]:
            left.addWidget(b)
        if self.app_mode == "real":
            self.btn_apply_config.setVisible(False)
            self.btn_long.setVisible(False)
            self.btn_open_logs.setVisible(False)
        self.btn_apply_config.clicked.connect(lambda: self.apply_sensor_config(self.sensor_widget.get_config()))
        self.btn_start.clicked.connect(self.start_normal_capture)
        self.btn_stop.clicked.connect(lambda: self.stop_capture("STOP_MANUAL"))
        self.btn_back_menu.clicked.connect(self.return_to_menu)
        self.btn_long.clicked.connect(self.open_long_window)
        self.btn_open_base.clicked.connect(lambda: open_folder(BASE_DIR))
        self.btn_open_logs.clicked.connect(lambda: open_folder(LOG_DIR))

        self.info = QtWidgets.QLabel()
        self.info.setFont(QtGui.QFont("Consolas", 9))
        self.info.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.info.setWordWrap(True)
        self.info.setMaximumWidth(left_width - 38)
        left.addWidget(self.info, stretch=1)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setMaximumWidth(680 if self.app_mode == "real" else 820)
        root.addWidget(self.tabs, stretch=1)
        self.plot_main = pg.PlotWidget(title="IR y RED normalizadas/procesadas")
        self.plot_main.setBackground("w"); self.plot_main.showGrid(x=True, y=True, alpha=0.25); self.plot_main.setLabel("bottom", "Tiempo", units="s")
        self.ir_curve = self.plot_main.plot([], [], pen=pg.mkPen((0, 80, 220), width=2), name="IR")
        self.red_curve = self.plot_main.plot([], [], pen=pg.mkPen((220, 30, 30), width=1), name="RED")
        self.tabs.addTab(self.plot_main, "Señal")

        self.plot_fft = pg.PlotWidget(title="FFT IR")
        self.plot_fft.setBackground("w"); self.plot_fft.showGrid(x=True, y=True, alpha=0.25); self.plot_fft.setLabel("bottom", "BPM")
        self.fft_curve = self.plot_fft.plot([], [], pen=pg.mkPen((100, 60, 160), width=2))
        self.tabs.addTab(self.plot_fft, "FFT")

        self.plot_peaks = pg.PlotWidget(title="Picos detectados sobre IR procesada")
        self.plot_peaks.setBackground("w"); self.plot_peaks.showGrid(x=True, y=True, alpha=0.25); self.plot_peaks.setLabel("bottom", "Tiempo", units="s")
        self.peak_curve = self.plot_peaks.plot([], [], pen=pg.mkPen((0, 80, 220), width=1))
        self.peak_scatter = pg.ScatterPlotItem(size=8, brush=pg.mkBrush(255, 120, 0))
        self.plot_peaks.addItem(self.peak_scatter)
        self.tabs.addTab(self.plot_peaks, "Picos")

        self.plot_trend = pg.PlotWidget(title="Rolling vivo | BPM / SpO2")
        self.plot_trend.setBackground("w"); self.plot_trend.showGrid(x=True, y=True, alpha=0.25)
        self.trend_bpm_curve = self.plot_trend.plot([], [], pen=pg.mkPen((30, 140, 40), width=2))
        self.trend_spo2_curve = self.plot_trend.plot([], [], pen=pg.mkPen((160, 60, 160), width=2))
        self.tabs.addTab(self.plot_trend, "Rolling")
        if self.app_mode == "real":
            self.tabs.setTabVisible(1, False)
            self.tabs.setTabVisible(2, False)
            self.tabs.setTabVisible(3, False)

    def toggle_advanced_controls(self):
        visible = not self.sensor_widget.isVisible()
        self.sensor_widget.setVisible(visible)
        self.analysis_widget.setVisible(visible)
        self.btn_apply_config.setVisible(visible)

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        key = event.key()
        if key == QtCore.Qt.Key.Key_N:
            self.start_normal_capture()
        elif key == QtCore.Qt.Key.Key_S:
            self.stop_capture("STOP_MANUAL")
        elif key == QtCore.Qt.Key.Key_L:
            self.open_long_window()
        else:
            super().keyPressEvent(event)

    def refresh_ports(self):
        self.port_combo.clear()
        ports = list(list_ports.comports())
        for p in ports:
            self.port_combo.addItem(f"{p.device} | {p.description}", p.device)
        if not ports:
            self.port_combo.addItem("Sin puertos", "")

    def find_auto_port(self) -> Optional[str]:
        ports = list(list_ports.comports())
        ranked: list[tuple[int, str]] = []
        for p in ports:
            txt = f"{p.device} {p.description} {p.hwid}".upper()
            score = 0
            if any(k in txt for k in ["ARDUINO", "GENUINO", "NANO 33", "NANO33", "MKR"]):
                score += 100
            if any(k in txt for k in ["VID:2341", "VID_2341", "VID:2A03", "VID_2A03"]):
                score += 80
            if any(k in txt for k in ["CH340", "CH341", "CP210", "FTDI", "USB SERIAL", "USB-SERIAL"]):
                score += 50
            if "BLUETOOTH" in txt:
                score -= 100
            if score > 0:
                ranked.append((score, p.device))
        if not ranked:
            return None
        ranked.sort(reverse=True)
        return ranked[0][1]

    def try_auto_connect(self):
        port = self.find_auto_port()
        if port:
            self.connect_port(port)

    def connect_selected_port(self):
        port = self.port_combo.currentData()
        if port:
            self.connect_port(str(port))

    def connect_port(self, port: str):
        try:
            if self.serial_port and self.serial_port.is_open:
                self.serial_port.close()
            log.info("Abriendo puerto %s @ 115200", port)
            self.serial_port = serial.Serial(port, 115200, timeout=0, write_timeout=1)
            self.serial_port.reset_output_buffer()
            time.sleep(2.0)
            self.port_name = port
            self.send_command("STATUS")
            # El firmware recibe la configuración actual al conectar, no solo al iniciar toma.
            self.last_sensor_config = self.sensor_widget.get_config()
            self.send_command(self.last_sensor_config.command())
        except Exception as exc:
            self.port_name = "ERROR"
            log.exception("Error abriendo puerto")
            QtWidgets.QMessageBox.critical(self, "Error serial", str(exc))

    def send_command(self, cmd: str):
        if not self.serial_port or not self.serial_port.is_open:
            log.warning("TX cancelado, puerto cerrado: %s", cmd)
            return
        payload = (cmd.strip() + "\n").encode("utf-8")
        self.serial_port.write(payload)
        try:
            self.serial_port.flush()
        except Exception:
            pass
        log.info("TX -> %s", cmd.strip())

    def apply_sensor_config(self, cfg: SensorConfig):
        self.last_sensor_config = cfg.clean()
        self.last_config_command = self.last_sensor_config.command()
        self.last_config_ack = "pendiente"
        self.last_config_line = ""
        self.last_config_sent_at = time.time()
        self.state.last_config_ack = self.last_config_ack
        self.state.last_config_line = self.last_config_line
        self.send_command(self.last_config_command)
        self.save_current_config_json(prefix="sensor_config")

    def parse_cfg_line(self, line: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for part in line.split()[1:]:
            if "=" in part:
                key, value = part.split("=", 1)
                out[key.upper()] = value
        return out

    def update_config_ack_from_line(self, line: str):
        values = self.parse_cfg_line(line)
        cfg = self.last_sensor_config
        expected = {
            "RED": str(cfg.red),
            "IR": str(cfg.ir),
            "AVG": str(cfg.avg),
            "RATE": str(cfg.rate),
            "WIDTH": str(cfg.width),
            "ADC": str(cfg.adc),
            "SKIP": str(cfg.skip),
            "DEBUG": "1" if cfg.debug else "0",
        }
        ok = all(values.get(key) == value for key, value in expected.items())
        self.last_config_ack = "confirmada" if ok else "distinta"
        self.last_config_line = line
        self.state.last_config_ack = self.last_config_ack
        self.state.last_config_line = self.last_config_line

    def current_condition_text(self) -> str:
        if hasattr(self, "condition_edit"):
            return self.condition_edit.text().strip()
        return ""

    def read_serial(self):
        if not self.serial_port or not self.serial_port.is_open:
            return
        try:
            n = self.serial_port.in_waiting
            if n <= 0:
                return
            raw = self.serial_port.read(n)
            self.state.rx_bytes += len(raw)
            text = raw.decode("utf-8", errors="ignore")
            self.rx_buffer += text
            while "\n" in self.rx_buffer:
                line, self.rx_buffer = self.rx_buffer.split("\n", 1)
                self.handle_line(line.strip())
        except Exception as exc:
            self.state.discarded_lines += 1
            self.state.last_line = f"ERROR_SERIAL: {exc}"
            log.exception("Error leyendo serial")

    def looks_like_data(self, line: str) -> bool:
        parts = line.split(",")

        # Formatos aceptados:
        # micros,red,ir
        # micros,red,ir,tempC
        # micros,red,ir,tempC,tempRaw
        if len(parts) not in (3, 4, 5):
            return False

        try:
            int(parts[0])
            float(parts[1])
            float(parts[2])

            if len(parts) >= 4 and parts[3].lower() != "nan":
                float(parts[3])

            if len(parts) >= 5 and parts[4].lower() != "nan":
                float(parts[4])

            return True
        except Exception:
            return False

    def handle_line(self, line: str):
        if not line:
            return
        st = self.state
        st.last_line = line
        st.rx_lines += 1
        if self.looks_like_data(line):
            st.sensor_ready = True
            if st.capturing:
                self.process_data_line(line)
            return
        st.control_messages += 1
        st.last_control = line
        if line == "READY":
            st.sensor_ready = True
            return
        if line.startswith("CFG"):
            self.update_config_ack_from_line(line)
            log.info("ARDUINO %s", line)
            return
        if line.startswith("STATUS") or line.startswith("OK_CONFIG") or line.startswith("OK_DEBUG"):
            log.info("ARDUINO %s", line)
            return
        if line.startswith("OK_START") or line.startswith("OK_START_CONTINUOUS"):
            log.info("ARDUINO %s", line)
            return
        if line == "DONE":
            log.info("ARDUINO DONE")
            if st.capturing:
                self.stop_capture("DONE_ARDUINO")
            return
        if line == "OK_STOP":
            log.info("ARDUINO OK_STOP")
            return
        if line.startswith("ERR") or line.startswith("ERROR") or line.startswith("WARN"):
            log.warning("ARDUINO %s", line)
            return
        if line.startswith("DBG"):
            log.info("ARDUINO %s", line)
            return
        log.info("LINEA CONTROL no reconocida: %s", line)

    def process_data_line(self, line: str):
        st = self.state
        try:
            parts = line.split(",")

            if len(parts) < 3:
                st.discarded_lines += 1
                return

            micros_s = parts[0]
            red_s = parts[1]
            ir_s = parts[2]

            tmicro = int(micros_s.strip())
            red = float(red_s.strip())
            ir = float(ir_s.strip())
            temp_c = math.nan
            temp_raw = math.nan

            if len(parts) >= 4 and parts[3].strip().lower() != "nan":
                temp_c = float(parts[3].strip())
            if len(parts) >= 5 and parts[4].strip().lower() != "nan":
                temp_raw = float(parts[4].strip())

            if red == 0 and ir == 0 and not np.isfinite(temp_c) and not np.isfinite(temp_raw):
                st.discarded_lines += 1
                return

            if st.first_micro is None:
                st.first_micro = tmicro

            trel = (tmicro - st.first_micro) / 1_000_000.0

            if trel < -0.01:
                st.discarded_lines += 1
                return

            st.t.append(trel)
            st.red.append(red)
            st.ir.append(ir)
            st.temp_c.append(temp_c)
            st.temp_raw.append(temp_raw)
            st.valid_lines += 1

            if st.raw_writer:
                cfg = self.last_sensor_config
                st.raw_writer.writerow([
                    st.crotal_id,
                    st.mode,
                    st.measurement_condition,
                    st.config_label,
                    f"{trel:.6f}",
                    f"{red:.0f}",
                    f"{ir:.0f}",
                    fmt(temp_c, 2, ""),
                    fmt(temp_raw, 0, ""),
                    cfg.red,
                    cfg.ir,
                    cfg.avg,
                    cfg.rate,
                    cfg.width,
                    cfg.adc,
                    cfg.skip,
                    1 if cfg.debug else 0,
                    self.last_config_ack,
                    datetime.now().isoformat(timespec="milliseconds")
                ])

        except Exception as exc:
            st.discarded_lines += 1
            log.warning("Dato descartado '%s': %s", line, exc)

    def reset_capture_state(self, keep_identity: bool = True):
        old = self.state
        crotal = old.crotal_id if keep_identity else sanitize_id(self.crotal_edit.text())
        prev = old.pulse_prev if keep_identity else safe_float_text(self.prev_pulse_edit.text())
        condition = old.measurement_condition if keep_identity else self.current_condition_text()
        self.state = CaptureState(
            crotal_id=crotal,
            pulse_prev=prev,
            measurement_condition=condition,
            sensor_ready=old.sensor_ready,
            last_config_ack=self.last_config_ack,
            last_config_line=self.last_config_line,
        )

    def open_raw_file(self):
        st = self.state
        st.raw_file = RAW_DIR / f"raw_{st.base_name}.csv"
        st.raw_handle = open(st.raw_file, "w", newline="", encoding="utf-8")
        st.raw_writer = csv.writer(st.raw_handle, delimiter=";")
        st.raw_writer.writerow([
            "id", "modo", "condiciones_medida", "config_label", "tiempo_s",
            "red_raw", "ir_raw", "temp_c", "temp_raw",
            "cfg_red", "cfg_ir", "cfg_avg", "cfg_rate", "cfg_width", "cfg_adc", "cfg_skip", "cfg_debug",
            "cfg_confirmacion", "system_time"
        ])
        st.raw_handle.flush()

    def start_normal_capture(self):
        if not self.serial_port or not self.serial_port.is_open:
            QtWidgets.QMessageBox.warning(self, "Serial", "No hay puerto serie abierto.")
            return
        if self.state.capturing:
            return
        self.reset_capture_state(keep_identity=False)
        st = self.state
        st.mode = "normal"
        st.requested_duration_s = float(self.duration_spin.value())
        st.crotal_id = sanitize_id(self.crotal_edit.text())
        st.pulse_prev = safe_float_text(self.prev_pulse_edit.text())
        st.measurement_condition = self.current_condition_text()
        st.config_label = "manual"
        st.base_name = f"{st.crotal_id}_{now_stamp()}"
        st.capture_start_wall = time.time()
        st.capturing = True
        st.finished = False
        try:
            self.serial_port.reset_input_buffer(); self.serial_port.reset_output_buffer()
        except Exception:
            pass
        self.apply_sensor_config(self.sensor_widget.get_config())
        self.open_raw_file()
        self.save_current_config_json(prefix=f"config_{st.base_name}")
        self.send_command("START_CONTINUOUS")
        log.info("Inicio toma normal: %s duración %.1fs", st.base_name, st.requested_duration_s)

    def start_long_capture(self):
        if not self.serial_port or not self.serial_port.is_open:
            QtWidgets.QMessageBox.warning(self, "Serial", "No hay puerto serie abierto.")
            return
        if self.state.capturing:
            self.stop_capture("RESTART_LONG")
        self.reset_capture_state(keep_identity=False)
        st = self.state
        st.mode = "long"
        st.requested_duration_s = math.inf
        st.crotal_id = sanitize_id(self.crotal_edit.text())
        st.pulse_prev = safe_float_text(self.prev_pulse_edit.text())
        st.measurement_condition = self.current_condition_text()
        st.config_label = "larga_manual"
        st.base_name = f"LONG_{st.crotal_id}_{now_stamp()}"
        st.capture_start_wall = time.time()
        st.capturing = True
        try:
            self.serial_port.reset_input_buffer(); self.serial_port.reset_output_buffer()
        except Exception:
            pass
        self.apply_sensor_config(self.sensor_widget.get_config())
        self.open_raw_file()
        self.save_current_config_json(prefix=f"config_{st.base_name}")
        self.send_command("START_CONTINUOUS")
        log.info("Inicio larga duración: %s", st.base_name)

    def start_temperature_capture(self):
        if not self.serial_port or not self.serial_port.is_open:
            QtWidgets.QMessageBox.warning(self, "Serial", "No hay puerto serie abierto.")
            return
        if self.state.capturing:
            self.stop_capture("RESTART_TEMP")
        self.reset_capture_state(keep_identity=False)
        st = self.state
        st.mode = "temp"
        st.requested_duration_s = math.inf
        st.crotal_id = sanitize_id(self.crotal_edit.text())
        st.pulse_prev = safe_float_text(self.prev_pulse_edit.text())
        st.measurement_condition = self.current_condition_text() or "solo temperatura"
        st.config_label = "solo_temperatura"
        st.base_name = f"TEMP_{st.crotal_id}_{now_stamp()}"
        st.capture_start_wall = time.time()
        st.capturing = True
        self.last_config_ack = "no aplica en solo temperatura"
        self.last_config_line = ""
        st.last_config_ack = self.last_config_ack
        st.last_config_line = self.last_config_line
        try:
            self.serial_port.reset_input_buffer(); self.serial_port.reset_output_buffer()
        except Exception:
            pass
        self.open_raw_file()
        self.save_current_config_json(prefix=f"config_{st.base_name}")
        self.send_command("START_TEMP")
        log.info("Inicio solo temperatura: %s", st.base_name)

    def send_diagnostic_command(self):
        if not self.serial_port or not self.serial_port.is_open:
            QtWidgets.QMessageBox.warning(self, "Serial", "No hay puerto serie abierto.")
            return
        self.send_command("DIAGNOSTICO")

    def stop_capture(self, reason: str):
        st = self.state
        if not st.capturing:
            return
        st.capturing = False
        st.finished = True
        self.send_command("STOP")
        if st.raw_handle:
            st.raw_handle.flush(); st.raw_handle.close(); st.raw_handle = None; st.raw_writer = None
        self.finalize_capture(reason)

    def check_auto_stop(self):
        st = self.state
        if st.capturing and st.mode == "normal":
            if time.time() - st.capture_start_wall >= st.requested_duration_s:
                self.stop_capture("DURACION_COMPLETADA_PYTHON")

    def arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        st = self.state
        n = min(len(st.t), len(st.red), len(st.ir))
        return np.asarray(st.t[:n], dtype=float), np.asarray(st.red[:n], dtype=float), np.asarray(st.ir[:n], dtype=float)

    def temp_arrays(self) -> tuple[np.ndarray, np.ndarray]:
        st = self.state
        n = min(len(st.t), len(st.temp_c), len(st.temp_raw))
        return np.asarray(st.temp_c[:n], dtype=float), np.asarray(st.temp_raw[:n], dtype=float)

    def temperature_summary(self) -> dict[str, float | int]:
        temp_c, temp_raw = self.temp_arrays()
        finite_temp = temp_c[np.isfinite(temp_c)] if temp_c.size else np.asarray([], dtype=float)
        finite_raw = temp_raw[np.isfinite(temp_raw)] if temp_raw.size else np.asarray([], dtype=float)
        return {
            "temp_samples": int(finite_temp.size),
            "temp_c_last": float(finite_temp[-1]) if finite_temp.size else math.nan,
            "temp_c_mean": float(np.mean(finite_temp)) if finite_temp.size else math.nan,
            "temp_c_min": float(np.min(finite_temp)) if finite_temp.size else math.nan,
            "temp_c_max": float(np.max(finite_temp)) if finite_temp.size else math.nan,
            "temp_raw_last": float(finite_raw[-1]) if finite_raw.size else math.nan,
        }

    def compute_current_metrics(self, window_s: Optional[float] = None) -> Metrics:
        t, red, ir = self.arrays()
        if window_s is not None and t.size > 0:
            start = max(float(t[0]), float(t[-1]) - window_s)
            mask = t >= start
            if int(np.sum(mask)) > 20:
                t2 = t[mask] - t[mask][0]
                return score_and_merge_metrics(t2, red[mask], ir[mask], self.sensor_widget.get_config(), self.analysis_widget.get_config())
        return score_and_merge_metrics(t, red, ir, self.sensor_widget.get_config(), self.analysis_widget.get_config())

    def update_live_metrics(self):
        st = self.state
        if not st.capturing or len(st.t) < 100:
            return
        window = 5.0 if st.mode == "long" else min(8.0, max(5.0, float(self.duration_spin.value()) / 2.0))
        met = self.compute_current_metrics(window)
        st.metrics = met
        if len(st.t) and (not st.rolling_t or st.t[-1] - st.rolling_t[-1] >= 0.5):
            st.rolling_t.append(st.t[-1])
            st.rolling_bpm.append(float(met.bpm) if np.isfinite(met.bpm) else np.nan)
            st.rolling_spo2.append(float(met.spo2) if np.isfinite(met.spo2) else np.nan)

    def finalize_capture(self, reason: str):
        st = self.state
        t, red, ir = self.arrays()
        if t.size >= 20:
            st.metrics = score_and_merge_metrics(t, red, ir, self.sensor_widget.get_config(), self.analysis_widget.get_config())
            st.bpm_blocks = block_bpm(t, ir, self.sensor_widget.get_config(), self.analysis_widget.get_config(), block_s=2)
            st.bpm_blocks_10s = block_bpm(t, ir, self.sensor_widget.get_config(), self.analysis_widget.get_config(), block_s=10)
        if st.mode == "normal":
            self.ask_final_reference()
        self.save_processed()
        self.save_blocks_file()
        self.save_images()
        self.save_summary(reason)
        self.write_session_row(reason)
        log.info("Captura finalizada: %s muestras=%s motivo=%s", st.base_name, len(st.t), reason)

    def ask_final_reference(self):
        st = self.state
        dialog = QtWidgets.QDialog(self); dialog.setWindowTitle("Datos finales de la toma")
        form = QtWidgets.QFormLayout(dialog)
        pulsio = QtWidgets.QLineEdit(st.pulse_final_pulsio)
        fonendo = QtWidgets.QLineEdit(st.pulse_final_fonendo)
        form.addRow("Pulsaciones finales pulsioxímetro:", pulsio)
        form.addRow("Pulsaciones finales fonendo:", fonendo)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            st.pulse_final_pulsio = safe_float_text(pulsio.text())
            st.pulse_final_fonendo = safe_float_text(fonendo.text())

    def save_current_config_json(self, prefix: str):
        data = {"sensor": asdict(self.sensor_widget.get_config()), "analysis": asdict(self.analysis_widget.get_config()), "base_dir": str(BASE_DIR), "created": datetime.now().isoformat()}
        path = CONFIG_DIR / f"{prefix}_{now_stamp()}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.state.config_file = path

    def save_processed(self):
        st = self.state
        t, red, ir = self.arrays()
        temp_c, temp_raw = self.temp_arrays()
        if t.size < 2 or not st.base_name:
            return
        cfg = self.analysis_widget.get_config()
        sensor_cfg = self.sensor_widget.get_config()
        hz = estimate_hz(t)
        red_proc = processed_for_plot(red, hz, cfg)
        ir_proc = processed_for_plot(ir, hz, cfg)
        art_red = detect_artifacts(red); art_ir = detect_artifacts(ir)
        bpm_rolling = np.full(t.size, np.nan)
        spo2_rolling = np.full(t.size, np.nan)
        ratio_rolling = np.full(t.size, np.nan)
        quality_rolling = np.full(t.size, np.nan)
        stride = max(1, int(round((hz if np.isfinite(hz) and hz > 0 else 100.0) / 2.0)))
        for idx in range(100, t.size, stride):
            start = max(0.0, t[idx] - 5.0)
            mask = (t >= start) & (t <= t[idx])
            if int(np.sum(mask)) >= 100:
                met = score_and_merge_metrics(t[mask] - t[mask][0], red[mask], ir[mask], sensor_cfg, cfg)
                bpm_rolling[idx] = met.bpm
                spo2_rolling[idx] = met.spo2
                ratio_rolling[idx] = met.ratio_r
                quality_rolling[idx] = met.quality
        met_bpm, q, reason, pol, peaks, peak_t = estimate_bpm_peaks(t, ir, cfg)
        peak_flags = np.zeros(t.size, dtype=int)
        if peaks.size and peak_t.size:
            peak_times = peak_t[peaks]
            nearest = np.searchsorted(t, peak_times)
            nearest = nearest[(nearest >= 0) & (nearest < t.size)]
            peak_flags[nearest] = 1
        st.processed_file = PROCESSED_DIR / f"proc_{st.base_name}.csv"
        with open(st.processed_file, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([
                "id", "modo", "condiciones_medida", "config_label", "tiempo_s",
                "red_raw", "ir_raw", "temp_c", "temp_raw",
                "red_proc_norm", "ir_proc_norm", "artifact_red", "artifact_ir", "peak_ir",
                "bpm_rolling_5s", "spo2_rolling_5s", "ratio_r_rolling_5s", "quality_rolling_5s"
            ])
            for i in range(t.size):
                tc = temp_c[i] if i < temp_c.size else math.nan
                tr = temp_raw[i] if i < temp_raw.size else math.nan
                w.writerow([
                    st.crotal_id, st.mode, st.measurement_condition, st.config_label, f"{t[i]:.6f}",
                    f"{red[i]:.0f}", f"{ir[i]:.0f}", fmt(tc, 2, ""), fmt(tr, 0, ""),
                    f"{red_proc[i]:.5f}", f"{ir_proc[i]:.5f}", int(art_red[i]), int(art_ir[i]), int(peak_flags[i]),
                    fmt(bpm_rolling[i], 2, ""), fmt(spo2_rolling[i], 2, ""), fmt(ratio_rolling[i], 5, ""), fmt(quality_rolling[i], 1, "")
                ])

    def save_blocks_file(self):
        st = self.state
        if not st.base_name:
            return
        st.blocks_file = REPORT_DIR / f"bpm_blocks_10s_{st.base_name}.csv"
        with open(st.blocks_file, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["id", "modo", "bloque", "inicio_s", "fin_s", "bpm_medio_10s"] )
            for i, bpm in enumerate(st.bpm_blocks_10s):
                start = i * 10
                end = start + 10
                w.writerow([st.crotal_id, st.mode, i + 1, start, end, fmt(bpm, 2, "")])

    def save_images(self):
        st = self.state
        if not st.base_name:
            return
        st.plot_file = FIGURES_DIR / f"plot_{st.base_name}.png"
        st.screenshot_file = SCREENSHOT_DIR / f"screen_{st.base_name}.png"
        self.tabs.grab().save(str(st.plot_file), "PNG")
        self.grab().save(str(st.screenshot_file), "PNG")

    def save_summary(self, reason: str):
        st = self.state
        if not st.base_name:
            return
        st.summary_file = REPORT_DIR / f"summary_{st.base_name}.json"
        temp = self.temperature_summary()
        data = {
            "id": st.crotal_id,
            "mode": st.mode,
            "measurement_condition": st.measurement_condition,
            "config_label": st.config_label,
            "reason": reason,
            "requested_duration_s": st.requested_duration_s if np.isfinite(st.requested_duration_s) else None,
            "samples": len(st.t),
            "metrics": asdict(st.metrics),
            "temperature": temp,
            "bpm_blocks_2s_orientative": st.bpm_blocks,
            "bpm_blocks_10s_mean": st.bpm_blocks_10s,
            "sensor_config": asdict(self.sensor_widget.get_config()),
            "analysis_config": asdict(self.analysis_widget.get_config()),
            "config_confirmation": {
                "status": self.last_config_ack,
                "command": self.last_config_command,
                "arduino_cfg_line": self.last_config_line,
            },
            "files": {
                "raw": str(st.raw_file) if st.raw_file else "",
                "processed": str(st.processed_file) if st.processed_file else "",
                "plot": str(st.plot_file) if st.plot_file else "",
                "screenshot": str(st.screenshot_file) if st.screenshot_file else "",
                "config": str(st.config_file) if st.config_file else "",
                "bpm_blocks_10s": str(st.blocks_file) if st.blocks_file else "",
            },
            "created": datetime.now().isoformat(),
        }
        with open(st.summary_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def write_session_header(self):
        header = ["id", "fecha", "hora", "modo", "condiciones_medida", "config_label", "motivo_fin", "duracion_solicitada_s", "muestras", "duracion_real_s", "hz_real", "bpm", "bpm_peak", "bpm_fft", "bpm_autocorr", "calidad", "calidad_label", "spo2_pct", "ratio_r", "temp_c_media", "temp_c_ultima", "temp_raw_ultima", "pi_ir_pct", "pi_red_pct", "artefactos_ir_pct", "artefactos_red_pct", "contacto", "cfg_confirmacion", "pulso_previo", "pulso_final_pulsio", "pulso_final_fonendo", "raw", "processed", "plot", "screenshot", "summary", "config", "bpm_blocks_10s_json", "blocks_10s_file"]
        self.session_writer.writerow(header); self.session_handle.flush()

    def write_session_row(self, reason: str):
        st = self.state; m = st.metrics; now = datetime.now()
        temp = self.temperature_summary()
        row = [st.crotal_id, now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), st.mode, st.measurement_condition, st.config_label, reason, fmt(st.requested_duration_s, 1, ""), len(st.t), fmt(m.duration_s, 3, ""), fmt(m.hz, 2, ""), fmt(m.bpm, 1, ""), fmt(m.bpm_peak, 1, ""), fmt(m.bpm_fft, 1, ""), fmt(m.bpm_autocorr, 1, ""), fmt(m.quality, 1, ""), m.quality_label, fmt(m.spo2, 1, ""), fmt(m.ratio_r, 5, ""), fmt(temp["temp_c_mean"], 2, ""), fmt(temp["temp_c_last"], 2, ""), fmt(temp["temp_raw_last"], 0, ""), fmt(m.pi_ir_pct, 4, ""), fmt(m.pi_red_pct, 4, ""), fmt(m.artifact_ir_pct, 1, ""), fmt(m.artifact_red_pct, 1, ""), m.contact_label, self.last_config_ack, st.pulse_prev, st.pulse_final_pulsio, st.pulse_final_fonendo, st.raw_file.name if st.raw_file else "", st.processed_file.name if st.processed_file else "", st.plot_file.name if st.plot_file else "", st.screenshot_file.name if st.screenshot_file else "", st.summary_file.name if st.summary_file else "", st.config_file.name if st.config_file else "", json.dumps(st.bpm_blocks_10s, ensure_ascii=False), st.blocks_file.name if st.blocks_file else ""]
        self.session_writer.writerow(row); self.session_handle.flush()

    def tick(self):
        self.read_serial()
        self.check_auto_stop()
        now = time.time()

        if now - self._last_metric_update >= self.metric_update_interval:
            self._last_metric_update = now
            self.update_live_metrics()

        if now - self._last_info_update >= self.info_update_interval:
            self._last_info_update = now
            self.update_info()

        if now - self._last_plot_update >= self.plot_update_interval:
            self._last_plot_update = now
            self.update_plots()

    def update_info(self):
        st = self.state; m = st.metrics
        temp = self.temperature_summary()
        if st.capturing:
            elapsed = time.time() - st.capture_start_wall
            status = f"capturando {st.mode}... {elapsed:.1f} s"
        elif st.sensor_ready:
            status = "READY | preparado"
        else:
            status = "esperando READY"
        block_text = ""
        if st.bpm_blocks_10s:
            lines = []
            for i, b in enumerate(st.bpm_blocks_10s[:12]):
                lines.append(f"{10*i:02d}-{10*i+10:02d}s: {fmt(b,0)}")
            block_text = "\nBPM medio por bloques de 10 s:\n" + "\n".join(lines)
        if self.app_mode == "real":
            self.info.setText(
                f"MODO REAL - campo\n"
                f"Puerto: {self.port_name}\n"
                f"Estado: {status}\n"
                f"Crotal: {st.crotal_id}\n"
                f"Muestras: {len(st.t)} | descartadas: {st.discarded_lines}\n\n"
                f"BPM: {fmt(m.bpm,0)}\n"
                f"Calidad: {fmt(m.quality,0)} ({m.quality_label})\n"
                f"SpO2 estimada: {fmt(m.spo2,1)} %\n"
                f"Temp: {fmt(temp['temp_c_last'],1)} °C\n"
                f"Hz real: {fmt(m.hz,2)}\n"
                f"Contacto: {m.contact_label}\n\n"
                f"Config Arduino: {self.last_config_ack}\n"
                f"Raw: {st.raw_file.name if st.raw_file else '-'}\n"
                f"{block_text}\n"
            )
        else:
            self.info.setText(
                f"Sesión global: {self.session_file.name}\n"
                f"Puerto: {self.port_name}\n"
                f"Estado: {status}\n"
                f"Crotal: {st.crotal_id}\n"
                f"Muestras: {len(st.t)} | descartadas: {st.discarded_lines}\n"
                f"Última línea: {st.last_line[:80]}\n"
                f"Último control: {st.last_control[:80]}\n\n"
                f"BPM: {fmt(m.bpm,0)} | calidad {fmt(m.quality,0)} ({m.quality_label})\n"
                f"  picos {fmt(m.bpm_peak,0)} | FFT {fmt(m.bpm_fft,0)} | autocorr {fmt(m.bpm_autocorr,0)}\n"
                f"SpO2 estimada: {fmt(m.spo2,1)} % | R={fmt(m.ratio_r,4)}\n"
                f"Temp: {fmt(temp['temp_c_last'],1)} °C | media {fmt(temp['temp_c_mean'],1)} °C | raw {fmt(temp['temp_raw_last'],0)}\n"
                f"Hz real: {fmt(m.hz,2)} | duración señal {fmt(m.duration_s,3)} s\n"
                f"PI IR/RED: {fmt(m.pi_ir_pct,3)} / {fmt(m.pi_red_pct,3)} %\n"
                f"Artefactos IR/RED: {fmt(m.artifact_ir_pct,1)} / {fmt(m.artifact_red_pct,1)} %\n"
                f"Contacto: {m.contact_label}\n"
                f"Config Arduino: {self.last_config_ack} | {self.last_config_line[:80]}\n"
                f"Diagnóstico: {m.reason[:220]}\n"
                f"Raw: {st.raw_file.name if st.raw_file else '-'}\n"
                f"Procesado: {st.processed_file.name if st.processed_file else '-'}\n"
                f"{block_text}"
            )

    def update_plots(self):
        t, red, ir = self.arrays()
        if t.size < 2:
            self.ir_curve.setData([], []); self.red_curve.setData([], [])
            return
        cfg = self.analysis_widget.get_config()
        hz = estimate_hz(t)
        if self.state.capturing and t[-1] > 30:
            mask = t >= t[-1] - 30
        else:
            mask = np.ones_like(t, dtype=bool)
        tt = t[mask]; rr = red[mask]; ii = ir[mask]
        self.ir_curve.setData(tt, processed_for_plot(ii, hz, cfg))
        self.red_curve.setData(tt, processed_for_plot(rr, hz, cfg))
        self.plot_main.setXRange(float(tt[0]), max(float(tt[-1]), float(tt[0])+1), padding=0.01)

        now = time.time()
        if self.app_mode != "real" and now - self._last_heavy_plot_update >= self.heavy_plot_interval:
            self._last_heavy_plot_update = now
            current_tab = self.tabs.currentIndex()
            if current_tab == 1:
                self.update_fft_plot(t, ir, cfg)
            elif current_tab == 2:
                self.update_peak_plot(t, ir, cfg)
            elif current_tab == 3 and self.state.rolling_t:
                self.trend_bpm_curve.setData(self.state.rolling_t, self.state.rolling_bpm)
                self.trend_spo2_curve.setData(self.state.rolling_t, self.state.rolling_spo2)

    def update_fft_plot(self, t: np.ndarray, ir: np.ndarray, cfg: AnalysisConfig):
        if t.size < 100:
            self.fft_curve.setData([], []); return
        hz = estimate_hz(t)
        sig = processed_ppg(ir, hz, cfg)
        tt, yy, hz_u = uniform_resample(t, sig, hz)
        if yy.size < 128:
            return
        yy = yy - np.mean(yy)
        spec = np.abs(np.fft.rfft(yy * np.hanning(yy.size)))
        freqs = np.fft.rfftfreq(yy.size, d=1.0/hz_u) * 60.0
        mask = (freqs >= 20) & (freqs <= 240)
        self.fft_curve.setData(freqs[mask], spec[mask])
        self.plot_fft.setXRange(20, 240, padding=0.01)

    def update_peak_plot(self, t: np.ndarray, ir: np.ndarray, cfg: AnalysisConfig):
        if t.size < 100:
            self.peak_curve.setData([], []); self.peak_scatter.setData([], []); return
        hz = estimate_hz(t)
        sig = processed_ppg(ir, hz, cfg)
        tt, yy, hz_u = uniform_resample(t, sig, hz)
        peaks, _ = find_local_peaks(yy, hz_u, cfg)
        yy_norm = robust_normalize(yy)
        self.peak_curve.setData(tt, yy_norm)
        if peaks.size:
            self.peak_scatter.setData(tt[peaks], yy_norm[peaks])
        else:
            self.peak_scatter.setData([], [])

    def open_long_window(self):
        if self.state.capturing:
            self.stop_capture("ABRIR_REAJUSTES")
        self.open_reajustes_requested.emit()

    def return_to_menu(self):
        if self.state.capturing:
            self.stop_capture("VOLVER_MENU")
        self.back_to_menu.emit()

    def closeEvent(self, event: QtGui.QCloseEvent):
        try:
            if self.state.capturing:
                self.send_command("STOP")
            if self.state.raw_handle:
                self.state.raw_handle.flush(); self.state.raw_handle.close()
            if self.session_handle:
                self.session_handle.flush(); self.session_handle.close()
            if self.serial_port and self.serial_port.is_open:
                self.serial_port.close()
        finally:
            event.accept()

