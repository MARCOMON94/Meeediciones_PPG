from __future__ import annotations

import csv
import asyncio
import json
import math
import threading
import time
from dataclasses import asdict
from datetime import datetime
from typing import Optional

import numpy as np
import serial
from serial.tools import list_ports
from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

try:
    from bleak import BleakClient, BleakScanner
except Exception:
    BleakClient = None
    BleakScanner = None

from ..animal_config import (
    ANIMAL_COW,
    ANIMAL_OPTIONS,
    POSITION_LABELS,
    POSITION_SUMMARY_PREFIXES,
    TEMP_CHANNELS,
    TEMP_MAPPING_DEFAULT,
    TEMP_MAPPING_INVERTED,
    active_temp_channels_for_animal,
    animal_label,
    default_mapping_for_animal,
    default_position_for_animal,
    inverted_mapping_for_animal,
    mapping_from_assignments,
    normalize_animal_type,
    normalize_position,
    parse_temp_mapping,
    primary_channel_for,
    positions_for_animal,
)
from ..menu import AppMode
from ..models import AnalysisConfig, CaptureState, Metrics, SensorConfig
from ..paths import BASE_DIR, CONFIG_DIR, FIGURES_DIR, PROCESSED_DIR, RAW_DIR, REPORT_DIR, RESULTS_DIR, SCREENSHOT_DIR, SESSION_DIR, log
from ..processing import (
    block_bpm, detect_artifacts, estimate_bpm_peaks, estimate_hz, find_local_peaks,
    processed_for_plot, processed_ppg, robust_normalize, score_and_merge_metrics, spo2_support_message, uniform_resample,
)
from ..utils import fmt, now_stamp, safe_float_text, sanitize_id
from ..widgets import AnalysisConfigWidget, NoWheelDoubleSpinBox, SensorConfigWidget


TEMP_SETTLE_S = 1.0
TEMP_FINAL_WINDOW_S = 5.0
TEMP_MONITOR_DEFAULT_S = 5.0
TEMP_ALERT_DEFAULT_C = 40.0
BLE_PORT_ID = "BLE:MTESTV2_NANO33IOT"
BLE_DEVICE_NAME_HINTS = ("mtestv2", "Nano33IoT", "Nano 33 IoT")
BLE_SERVICE_UUID = "7f510001-1b15-4b91-9f4b-3a4d5f6e0001"
BLE_RX_UUID = "7f510002-1b15-4b91-9f4b-3a4d5f6e0001"
BLE_TX_UUID = "7f510003-1b15-4b91-9f4b-3a4d5f6e0001"


def normalize_udder_text(value: str) -> str:
    return normalize_position(value)


def temp_primary_channel_for(position: str, temp_mapping: str, animal_type: str = "") -> str:
    return primary_channel_for(position, temp_mapping, animal_type)


def temperature_channel_summary(
    t: np.ndarray,
    temp_c: np.ndarray,
    temp_raw: np.ndarray,
    settle_s: float = TEMP_SETTLE_S,
    window_s: float = TEMP_FINAL_WINDOW_S,
) -> dict[str, float | int]:
    n = min(t.size, temp_c.size)
    tt = t[:n]
    values = temp_c[:n]
    raw = temp_raw[: min(n, temp_raw.size)] if temp_raw.size else np.asarray([], dtype=float)
    finite_values = values[np.isfinite(values)] if values.size else np.asarray([], dtype=float)
    finite_raw = temp_raw[np.isfinite(temp_raw)] if temp_raw.size else np.asarray([], dtype=float)

    valid = np.isfinite(tt) & np.isfinite(values)
    window_used = ""
    if np.any(valid):
        rel = tt - float(tt[valid][0])
        final_mask = valid & (rel >= settle_s) & (rel <= settle_s + window_s)
        if not np.any(final_mask):
            final_mask = valid & (rel >= 0.0) & (rel <= window_s)
            window_used = f"fallback_0_{window_s:g}s"
        else:
            window_used = f"initial_0_{window_s:g}s" if settle_s <= 0 else f"settled_{settle_s:g}_{settle_s + window_s:g}s"
    else:
        rel = np.asarray([], dtype=float)
        final_mask = np.zeros_like(values, dtype=bool)

    if np.any(final_mask):
        indices = np.flatnonzero(final_mask)
        selected = values[indices]
        local_idx = int(np.nanargmax(selected))
        idx = int(indices[local_idx])
        final_max = float(values[idx])
        final_time = float(rel[idx]) if rel.size == values.size else math.nan
        raw_at_max = float(raw[idx]) if idx < raw.size and np.isfinite(raw[idx]) else math.nan
        final_samples = int(indices.size)
    else:
        final_max = math.nan
        final_time = math.nan
        raw_at_max = math.nan
        final_samples = 0

    return {
        "samples": int(finite_values.size),
        "raw_samples": int(finite_raw.size),
        "last": float(finite_values[-1]) if finite_values.size else math.nan,
        "mean": float(np.mean(finite_values)) if finite_values.size else math.nan,
        "min": float(np.min(finite_values)) if finite_values.size else math.nan,
        "max": float(np.max(finite_values)) if finite_values.size else math.nan,
        "raw_last": float(finite_raw[-1]) if finite_raw.size else math.nan,
        "final_max_5s": final_max,
        "final_time_s": final_time,
        "final_raw_at_max": raw_at_max,
        "final_samples": final_samples,
        "final_window_start_s": float(settle_s),
        "final_window_end_s": float(settle_s + window_s),
        "final_window_used": window_used,
    }


class BleSerialAdapter:
    def __init__(self, name_hints=BLE_DEVICE_NAME_HINTS):
        if BleakClient is None or BleakScanner is None:
            raise RuntimeError("Falta la dependencia 'bleak'. Ejecuta instalarmtestv2.cmd o instala requirements.txt.")
        self.name_hints = tuple(h.lower() for h in name_hints)
        self.is_open = False
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._client = None
        future = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        future.result(timeout=12)

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _connect(self):
        devices = await BleakScanner.discover(timeout=5.0)
        selected = None
        for device in devices:
            name = (device.name or "").lower()
            if any(hint in name for hint in self.name_hints):
                selected = device
                break
        if selected is None:
            for device in devices:
                uuids = [u.lower() for u in (getattr(device, "metadata", {}) or {}).get("uuids", [])]
                if BLE_SERVICE_UUID.lower() in uuids:
                    selected = device
                    break
        if selected is None:
            raise RuntimeError("No se encontro el Arduino BLE mtestv2. Revisa que el Nano 33 IoT este encendido y con el firmware BLE cargado.")

        self._client = BleakClient(selected)
        await self._client.connect()
        await self._client.start_notify(BLE_TX_UUID, self._on_notify)
        self.is_open = True

    def _on_notify(self, _sender, data: bytearray):
        with self._lock:
            self._buffer.extend(bytes(data))

    @property
    def in_waiting(self) -> int:
        with self._lock:
            return len(self._buffer)

    def read(self, n: int) -> bytes:
        with self._lock:
            take = min(max(0, int(n)), len(self._buffer))
            data = bytes(self._buffer[:take])
            del self._buffer[:take]
            return data

    def write(self, payload: bytes):
        if not self.is_open or self._client is None:
            raise RuntimeError("BLE no conectado")
        data = bytes(payload)
        future = asyncio.run_coroutine_threadsafe(self._write(data), self._loop)
        future.result(timeout=2)
        return len(data)

    async def _write(self, payload: bytes):
        for start in range(0, len(payload), 180):
            await self._client.write_gatt_char(BLE_RX_UUID, payload[start:start + 180], response=True)

    def flush(self):
        return

    def reset_input_buffer(self):
        with self._lock:
            self._buffer.clear()

    def reset_output_buffer(self):
        return

    def close(self):
        self.is_open = False
        if self._client is not None:
            try:
                future = asyncio.run_coroutine_threadsafe(self._client.disconnect(), self._loop)
                future.result(timeout=3)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)


class PPGSuite(QtWidgets.QMainWindow):
    back_to_menu = QtCore.pyqtSignal()
    open_statistics_requested = QtCore.pyqtSignal()

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
        self.results_dir = getattr(self, "results_dir", RESULTS_DIR)
        self.raw_dir = getattr(self, "raw_dir", RAW_DIR)
        self.processed_dir = getattr(self, "processed_dir", PROCESSED_DIR)
        self.session_dir = getattr(self, "session_dir", SESSION_DIR)
        self.figures_dir = getattr(self, "figures_dir", FIGURES_DIR)
        self.screenshot_dir = getattr(self, "screenshot_dir", SCREENSHOT_DIR)
        self.config_dir = getattr(self, "config_dir", CONFIG_DIR)
        self.report_dir = getattr(self, "report_dir", REPORT_DIR)
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
            self.info_update_interval = 0.75
            self.metric_update_interval = 2.00
            self.plot_update_interval = 1.20
            self.heavy_plot_interval = 9999.0
        elif self.app_mode == "test":
            self.info_update_interval = 0.75
            self.metric_update_interval = 2.00
            self.plot_update_interval = 1.20
            self.heavy_plot_interval = 3.00
        else:
            self.info_update_interval = 0.75
            self.metric_update_interval = 2.00
            self.plot_update_interval = 1.20
            self.heavy_plot_interval = 3.00
        self.session_file = self.session_dir / f"session_{now_stamp()}.csv"
        self.session_handle = open(self.session_file, "w", newline="", encoding="utf-8")
        self.session_writer = csv.writer(self.session_handle, delimiter=";")
        self.write_session_header()
        self.build_ui()
        self.refresh_ports()
        self.try_auto_connect()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(40 if self.app_mode != "real" else 60)
        QtCore.QTimer.singleShot(900, self.try_auto_connect)
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
        self.duration_spin = NoWheelDoubleSpinBox(); self.duration_spin.setRange(2, 3600); self.duration_spin.setDecimals(1); self.duration_spin.setValue(90.0); self.duration_spin.setSuffix(" s")
        self.prev_pulse_edit = QtWidgets.QLineEdit()
        self.temp_manual_initial_edit = QtWidgets.QLineEdit()
        self.temp_manual_initial_edit.setPlaceholderText("Opcional. Ej.: 38.6")
        self.animal_combo = QtWidgets.QComboBox()
        self.configure_animal_combo(self.animal_combo)
        self.udder_combo = QtWidgets.QComboBox()
        self.configure_udder_combo(self.udder_combo)
        self.temp_mapping_widget = self.create_temp_mapping_widget()
        self.temp_monitor_widget = self.create_temp_monitor_widget()
        self.vacuum_combo = QtWidgets.QComboBox()
        self.vacuum_combo.addItems(["", "con vacio", "sin vacio"])
        self.condition_edit = QtWidgets.QLineEdit()
        self.animal_combo.currentIndexChanged.connect(self.refresh_animal_dependent_controls)
        self.condition_edit.setPlaceholderText("Ej.: campo, ordeño activo, sensor reajustado, animal inquieto...")
        cap.addRow("Crotal:", self.crotal_edit)
        cap.addRow("Animal:", self.animal_combo)
        cap.addRow("Duración:", self.duration_spin)
        cap.addRow("Pulso previo ref.:", self.prev_pulse_edit)
        cap.addRow("Temp. manual inicio (C):", self.temp_manual_initial_edit)
        cap.addRow("Sensor:", self.udder_combo)
        cap.addRow("Termometros:", self.temp_mapping_widget)
        cap.addRow("Temperatura:", self.temp_monitor_widget)
        cap.addRow("Medicion:", self.vacuum_combo)
        cap.addRow("Anotaciones inicio:", self.condition_edit)
        left.addWidget(capture_group)
        self.refresh_animal_dependent_controls()

        self.sensor_widget = SensorConfigWidget()
        left.addWidget(self.sensor_widget)
        self.btn_save_animal_config = QtWidgets.QPushButton("Guardar configuracion especie")
        left.addWidget(self.btn_save_animal_config)
        self.btn_save_animal_config.clicked.connect(self.save_animal_profile_clicked)
        self.analysis_widget = AnalysisConfigWidget()
        left.addWidget(self.analysis_widget)

        self.btn_toggle_advanced = QtWidgets.QPushButton("Mostrar/ocultar configuración avanzada")
        left.addWidget(self.btn_toggle_advanced)
        self.btn_toggle_advanced.clicked.connect(self.toggle_advanced_controls)

        if self.app_mode == "real":
            self.sensor_widget.setVisible(False)
            self.btn_save_animal_config.setVisible(False)
            self.analysis_widget.setVisible(False)
            self.btn_toggle_advanced.setVisible(True)
        else:
            self.sensor_widget.setVisible(False)
            self.btn_save_animal_config.setVisible(False)
            self.analysis_widget.setVisible(False)
            self.btn_toggle_advanced.setVisible(True)

        self.btn_apply_config = QtWidgets.QPushButton("Aplicar configuración sensor")
        self.btn_start = QtWidgets.QPushButton("Iniciar medición real" if self.app_mode == "real" else "Iniciar toma")
        self.btn_stop = QtWidgets.QPushButton("Parar")
        self.btn_back_menu = QtWidgets.QPushButton("Volver al menú inicial")
        self.btn_open_base = QtWidgets.QPushButton("Mostrar resultados")
        for b in [self.btn_apply_config, self.btn_start, self.btn_stop, self.btn_open_base, self.btn_back_menu]:
            left.addWidget(b)
        if self.app_mode == "real":
            self.btn_apply_config.setVisible(False)
        elif self.app_mode == "test":
            self.btn_apply_config.setVisible(False)
        self.btn_apply_config.clicked.connect(lambda: self.apply_sensor_config(self.sensor_widget.get_config()))
        self.btn_start.clicked.connect(self.start_normal_capture)
        self.btn_stop.clicked.connect(lambda: self.stop_capture("STOP_MANUAL"))
        self.btn_back_menu.clicked.connect(self.return_to_menu)
        self.btn_open_base.clicked.connect(self.open_statistics_window)

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
        self.plot_temp_live = pg.PlotWidget(title="Temperatura inicial")
        self.plot_temp_live.setBackground("w")
        self.plot_temp_live.showGrid(x=True, y=True, alpha=0.25)
        self.plot_temp_live.setLabel("bottom", "Tiempo", units="s")
        self.plot_temp_live.setLabel("left", "Temp", units="C")
        self.temp_live_curves = {
            "A0": self.plot_temp_live.plot([], [], pen=pg.mkPen((180, 60, 60), width=2), name="A0"),
            "A1": self.plot_temp_live.plot([], [], pen=pg.mkPen((40, 100, 210), width=2), name="A1"),
            "A2": self.plot_temp_live.plot([], [], pen=pg.mkPen((220, 140, 30), width=2), name="A2"),
            "A3": self.plot_temp_live.plot([], [], pen=pg.mkPen((80, 160, 80), width=2), name="A3"),
        }
        self.temp_alert_line = pg.InfiniteLine(
            angle=0,
            movable=False,
            pen=pg.mkPen((220, 60, 40), width=1, style=QtCore.Qt.PenStyle.DashLine),
        )
        self.plot_temp_live.addItem(self.temp_alert_line)
        self.temp_live_legend = self.plot_temp_live.addLegend()
        signal_page = QtWidgets.QWidget()
        signal_layout = QtWidgets.QVBoxLayout(signal_page)
        signal_layout.setContentsMargins(0, 0, 0, 0)
        signal_layout.addWidget(self.plot_main, stretch=3)
        signal_layout.addWidget(self.plot_temp_live, stretch=2)
        self.tabs.addTab(signal_page, "Señal")

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
        self.btn_save_animal_config.setVisible(visible)
        self.analysis_widget.setVisible(visible)
        self.btn_apply_config.setVisible(visible)

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        key = event.key()
        if key == QtCore.Qt.Key.Key_N:
            self.start_normal_capture()
        elif key == QtCore.Qt.Key.Key_S:
            self.stop_capture("STOP_MANUAL")
        else:
            super().keyPressEvent(event)

    def configure_animal_combo(self, combo: QtWidgets.QComboBox):
        combo.clear()
        for label, value in ANIMAL_OPTIONS:
            combo.addItem(label, value)

    def current_animal_type(self) -> str:
        if hasattr(self, "animal_combo"):
            data = self.animal_combo.currentData()
            if data:
                return normalize_animal_type(str(data))
            return normalize_animal_type(self.animal_combo.currentText())
        return normalize_animal_type(getattr(self.state, "animal_type", ""))

    def configure_udder_combo(self, combo: QtWidgets.QComboBox):
        combo.clear()
        animal_type = self.current_animal_type()
        for position in positions_for_animal(animal_type):
            combo.addItem(f"Sensor {POSITION_LABELS.get(position, position)}", position)

    def configure_temp_mapping_combo(self, combo: QtWidgets.QComboBox):
        combo.clear()
        combo.addItem("A0 derecha / A1 izquierda", TEMP_MAPPING_DEFAULT)
        combo.addItem("A0 izquierda / A1 derecha", TEMP_MAPPING_INVERTED)

    def create_temp_mapping_widget(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(2)
        self.temp_channel_combos: dict[str, QtWidgets.QComboBox] = {}
        self.temp_channel_labels: dict[str, QtWidgets.QLabel] = {}
        for row, channel in enumerate(TEMP_CHANNELS):
            label = QtWidgets.QLabel(channel)
            combo = QtWidgets.QComboBox()
            self.temp_channel_labels[channel] = label
            self.temp_channel_combos[channel] = combo
            layout.addWidget(label, row, 0)
            layout.addWidget(combo, row, 1)
        return widget

    def create_temp_monitor_widget(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(2)
        self.temp_monitor_seconds_spin = NoWheelDoubleSpinBox()
        self.temp_monitor_seconds_spin.setRange(1.0, 60.0)
        self.temp_monitor_seconds_spin.setDecimals(1)
        self.temp_monitor_seconds_spin.setValue(TEMP_MONITOR_DEFAULT_S)
        self.temp_monitor_seconds_spin.setSuffix(" s")
        self.temp_alert_threshold_spin = NoWheelDoubleSpinBox()
        self.temp_alert_threshold_spin.setRange(20.0, 80.0)
        self.temp_alert_threshold_spin.setDecimals(1)
        self.temp_alert_threshold_spin.setValue(TEMP_ALERT_DEFAULT_C)
        self.temp_alert_threshold_spin.setSuffix(" C")
        layout.addWidget(QtWidgets.QLabel("Medir"), 0, 0)
        layout.addWidget(self.temp_monitor_seconds_spin, 0, 1)
        layout.addWidget(QtWidgets.QLabel("Avisar >"), 1, 0)
        layout.addWidget(self.temp_alert_threshold_spin, 1, 1)
        return widget

    def current_temp_monitor_seconds(self) -> float:
        if hasattr(self, "temp_monitor_seconds_spin"):
            return max(1.0, float(self.temp_monitor_seconds_spin.value()))
        return TEMP_MONITOR_DEFAULT_S

    def current_temp_alert_threshold(self) -> float:
        if hasattr(self, "temp_alert_threshold_spin"):
            return float(self.temp_alert_threshold_spin.value())
        return TEMP_ALERT_DEFAULT_C

    def apply_temperature_monitor_config_to_state(self):
        self.state.temp_monitor_seconds = self.current_temp_monitor_seconds()
        self.state.temp_alert_threshold_c = self.current_temp_alert_threshold()
        self.state.temp_alert_triggered = False
        self.state.temp_alert_value_c = math.nan
        self.state.temp_alert_label = ""
        self.state.temp_alert_time_s = math.nan

    def refresh_animal_dependent_controls(self):
        animal_type = self.current_animal_type()
        current_position = self.current_udder_text() if hasattr(self, "udder_combo") else ""
        if hasattr(self, "udder_combo"):
            self.udder_combo.blockSignals(True)
            self.configure_udder_combo(self.udder_combo)
            positions = positions_for_animal(animal_type)
            wanted = normalize_position(current_position, animal_type) if current_position else default_position_for_animal(animal_type)
            if wanted not in positions:
                wanted = default_position_for_animal(animal_type)
            for i in range(self.udder_combo.count()):
                if self.udder_combo.itemData(i) == wanted:
                    self.udder_combo.setCurrentIndex(i)
                    break
            self.udder_combo.blockSignals(False)
        self.configure_temp_mapping_editor(default_mapping_for_animal(animal_type))
        self.refresh_temperature_curve_channels()

    def active_temp_channels(self) -> tuple[str, ...]:
        animal_type = getattr(self.state, "animal_type", "")
        if hasattr(self, "animal_combo") and not getattr(self.state, "capturing", False):
            animal_type = self.current_animal_type()
        return active_temp_channels_for_animal(animal_type)

    def format_active_temp_channels(self) -> str:
        return " / ".join(self.active_temp_channels())

    def sync_temperature_curve_visibility(self, curves: dict[str, object], legend=None) -> tuple[str, ...]:
        active = set(self.active_temp_channels())
        for channel, curve in curves.items():
            visible = channel in active
            if hasattr(curve, "setVisible"):
                curve.setVisible(visible)
            if not visible and hasattr(curve, "setData"):
                curve.setData([], [])
        if legend is not None and hasattr(legend, "items"):
            for channel, item in zip(TEMP_CHANNELS, legend.items):
                visible = channel in active
                for part in item:
                    if hasattr(part, "setVisible"):
                        part.setVisible(visible)
        return tuple(channel for channel in TEMP_CHANNELS if channel in active)

    def refresh_temperature_curve_channels(self):
        if hasattr(self, "temp_live_curves"):
            self.sync_temperature_curve_visibility(self.temp_live_curves, getattr(self, "temp_live_legend", None))

    def configure_temp_mapping_editor(self, mapping: str = ""):
        if not hasattr(self, "temp_channel_combos"):
            return
        animal_type = self.current_animal_type()
        positions = positions_for_animal(animal_type)
        assignments = parse_temp_mapping(mapping or default_mapping_for_animal(animal_type), animal_type)
        for channel in TEMP_CHANNELS:
            combo = self.temp_channel_combos[channel]
            combo.blockSignals(True)
            combo.clear()
            for position in positions:
                combo.addItem(POSITION_LABELS.get(position, position), position)
            wanted = assignments.get(channel)
            if wanted not in positions:
                wanted = positions[0]
            for i in range(combo.count()):
                if combo.itemData(i) == wanted:
                    combo.setCurrentIndex(i)
                    break
            visible = animal_type == ANIMAL_COW or channel in ("A0", "A1")
            combo.setVisible(visible)
            self.temp_channel_labels[channel].setVisible(visible)
            combo.blockSignals(False)

    def current_temp_assignments(self) -> dict[str, str]:
        if hasattr(self, "temp_channel_combos"):
            return {
                channel: str(combo.currentData() or combo.currentText())
                for channel, combo in self.temp_channel_combos.items()
                if combo.isVisible()
            }
        return parse_temp_mapping(default_mapping_for_animal(self.current_animal_type()), self.current_animal_type())

    def animal_profile_path(self):
        return self.config_dir / "animal_profiles.json"

    def load_animal_profiles(self) -> dict[str, dict]:
        path = self.animal_profile_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save_animal_profiles(self, profiles: dict[str, dict]):
        path = self.animal_profile_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profiles, f, indent=2, ensure_ascii=False)

    def current_animal_profile(self) -> dict:
        animal_type = self.current_animal_type()
        return {
            "profile_type": "species_sensor_config",
            "animal_type": animal_type,
            "animal_label": animal_label(animal_type),
            "sensor_config": asdict(self.sensor_widget.get_config()),
            "updated": datetime.now().isoformat(),
        }

    def profile_summary_text(self, profile: dict) -> str:
        sensor_cfg = profile.get("sensor_config") or {}
        animal_type = normalize_animal_type(str(profile.get("animal_type") or ""))
        return (
            f"Especie: {profile.get('animal_label') or animal_label(animal_type)}\n"
            f"RED={sensor_cfg.get('red', '-')} IR={sensor_cfg.get('ir', '-')} AVG={sensor_cfg.get('avg', '-')} "
            f"RATE={sensor_cfg.get('rate', '-')} WIDTH={sensor_cfg.get('width', '-')} ADC={sensor_cfg.get('adc', '-')} "
            f"SKIP={sensor_cfg.get('skip', '-')} DEBUG={sensor_cfg.get('debug', '-')}"
        )

    def save_animal_profile_clicked(self):
        profile = self.current_animal_profile()
        animal_key = normalize_animal_type(str(profile.get("animal_type") or ""))
        animal_name = profile.get("animal_label") or animal_label(animal_key)
        profiles = self.load_animal_profiles()
        previous = profiles.get(animal_key)
        if previous:
            msg = QtWidgets.QMessageBox(self)
            msg.setIcon(QtWidgets.QMessageBox.Icon.Question)
            msg.setWindowTitle("Cambiar configuracion de especie")
            msg.setText(f"La configuracion anterior predefinida para {animal_name} es esta:")
            msg.setInformativeText(
                f"{self.profile_summary_text(previous)}\n\n"
                f"La configuracion seleccionada ahora es esta:\n"
                f"{self.profile_summary_text(profile)}\n\n"
                "Quieres cambiarla por la seleccionada?"
            )
            change_btn = msg.addButton("Cambiar configuracion", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
            msg.addButton("Cancelar", QtWidgets.QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            if msg.clickedButton() != change_btn:
                return
        profiles[animal_key] = profile
        self.save_animal_profiles(profiles)
        QtWidgets.QMessageBox.information(self, "Configuracion de especie", f"Configuracion guardada para {animal_name}.")

    def is_bluetooth_port(self, port_info) -> bool:
        txt = f"{getattr(port_info, 'device', '')} {getattr(port_info, 'description', '')} {getattr(port_info, 'hwid', '')}".upper()
        return "BLUETOOTH" in txt

    def refresh_ports(self):
        self.port_combo.clear()
        ports = list(list_ports.comports())
        for p in ports:
            self.port_combo.addItem(f"{p.device} | {p.description}", p.device)
        self.port_combo.addItem("BLE Nano 33 IoT mtestv2", BLE_PORT_ID)
        if not ports:
            self.port_combo.addItem("Sin puertos USB", "")

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
            if self.is_bluetooth_port(p):
                score -= 100
            if score > 0:
                ranked.append((score, p.device))
        if not ranked:
            return None
        ranked.sort(reverse=True)
        return ranked[0][1]

    def try_auto_connect(self):
        if self.serial_port and self.serial_port.is_open:
            return
        self.refresh_ports()
        port = self.find_auto_port()
        if port:
            for i in range(self.port_combo.count()):
                if self.port_combo.itemData(i) == port:
                    self.port_combo.setCurrentIndex(i)
                    break
            self.connect_port(port)

    def connect_selected_port(self):
        port = self.port_combo.currentData()
        if port:
            self.connect_port(str(port))

    def connect_port(self, port: str):
        try:
            if self.serial_port and self.serial_port.is_open:
                self.serial_port.close()
            if port == BLE_PORT_ID:
                log.info("Abriendo BLE Nano 33 IoT")
                self.serial_port = BleSerialAdapter()
                self.port_name = "BLE Nano 33 IoT"
            else:
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

    def expected_config_matches_last_ack(self, cfg: SensorConfig) -> bool:
        if self.last_config_ack != "confirmada" or not self.last_config_line:
            return False
        values = self.parse_cfg_line(self.last_config_line)
        expected = cfg.clean()
        return all(
            values.get(key) == value
            for key, value in {
                "RED": str(expected.red),
                "IR": str(expected.ir),
                "AVG": str(expected.avg),
                "RATE": str(expected.rate),
                "WIDTH": str(expected.width),
                "ADC": str(expected.adc),
                "SKIP": str(expected.skip),
                "DEBUG": "1" if expected.debug else "0",
            }.items()
        )

    def confirm_config_before_start(self, cfg: SensorConfig) -> bool:
        if self.expected_config_matches_last_ack(cfg):
            return True
        msg = QtWidgets.QMessageBox(self)
        msg.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        msg.setWindowTitle("Configuración Arduino no confirmada")
        msg.setText("La configuración confirmada por Arduino no coincide con la que se va a usar.")
        msg.setInformativeText(
            "Pulsa Aplicar y continuar para enviar la configuración actual al Arduino antes de iniciar."
        )
        apply_btn = msg.addButton("Aplicar y continuar", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = msg.addButton("Cancelar", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if msg.clickedButton() != apply_btn:
            return False
        return self.apply_config_and_wait(cfg, show_warning=True)

    def apply_config_and_wait(self, cfg: SensorConfig, show_warning: bool = True, timeout_s: float = 1.5) -> bool:
        try:
            self.serial_port.reset_input_buffer()
        except Exception:
            pass
        self.apply_sensor_config(cfg)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            self.read_serial()
            if self.expected_config_matches_last_ack(cfg):
                return True
            QtWidgets.QApplication.processEvents()
            time.sleep(0.03)
        if show_warning:
            QtWidgets.QMessageBox.warning(
                self,
                "Configuración sin confirmar",
                "Se envió la configuración, pero Arduino no la confirmó a tiempo. Revisa el puerto serie.",
            )
        return False

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

    def current_udder_text(self) -> str:
        if hasattr(self, "udder_combo"):
            data = self.udder_combo.currentData()
            if data:
                return normalize_position(str(data), self.current_animal_type())
            return normalize_position(self.udder_combo.currentText(), self.current_animal_type())
        return ""

    def current_temp_mapping(self) -> str:
        if hasattr(self, "temp_channel_combos"):
            return mapping_from_assignments(self.current_temp_assignments(), self.current_animal_type())
        if hasattr(self, "temp_mapping_combo"):
            data = self.temp_mapping_combo.currentData()
            if data:
                return str(data)
        return default_mapping_for_animal(self.current_animal_type())

    def current_temp_primary_channel(self) -> str:
        return temp_primary_channel_for(self.current_udder_text(), self.current_temp_mapping(), self.current_animal_type())

    def current_vacuum_text(self) -> str:
        if hasattr(self, "vacuum_combo"):
            return self.vacuum_combo.currentText().strip()
        return ""

    def ensure_initial_pulse_or_confirm(self) -> str | None:
        current = safe_float_text(self.prev_pulse_edit.text())
        try:
            bpm = float(current.replace(",", "."))
        except ValueError:
            bpm = math.nan
        if math.isfinite(bpm) and bpm > 0:
            return current

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Pulso inicial no indicado")
        layout = QtWidgets.QVBoxLayout(dialog)
        info = QtWidgets.QLabel(
            "No has puesto pulso inicial.\n\n"
            "Ahora se usa para comparar las BPM del sensor con la referencia manual. "
            "Puedes introducirlo ahora, revisar tambien la temperatura manual inicial, "
            "iniciar igualmente sin BPM o cancelar."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        pulse_edit = QtWidgets.QLineEdit()
        pulse_edit.setPlaceholderText("Ej.: 72")
        temp_edit = QtWidgets.QLineEdit()
        temp_edit.setText(self.temp_manual_initial_edit.text().strip() if hasattr(self, "temp_manual_initial_edit") else "")
        temp_edit.setPlaceholderText("Opcional. Ej.: 38.6")
        form = QtWidgets.QFormLayout()
        form.addRow("BPM inicial:", pulse_edit)
        form.addRow("Temp. manual inicio (C):", temp_edit)
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox()
        start_with_bpm = buttons.addButton("Iniciar con BPM", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        start_without_bpm = buttons.addButton("Iniciar sin BPM", QtWidgets.QDialogButtonBox.ButtonRole.DestructiveRole)
        cancel_btn = buttons.addButton("Cancelar", QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)

        chosen: dict[str, object] = {"button": None}

        def choose(button: QtWidgets.QAbstractButton):
            chosen["button"] = button
            dialog.accept()

        buttons.clicked.connect(choose)
        pulse_edit.returnPressed.connect(lambda: choose(start_with_bpm))
        dialog.exec()

        button = chosen.get("button")
        if button is cancel_btn or button is None:
            return None
        temp_value = safe_float_text(temp_edit.text())
        if hasattr(self, "temp_manual_initial_edit"):
            self.temp_manual_initial_edit.setText(temp_value)
        if button is start_without_bpm:
            return ""
        value = safe_float_text(pulse_edit.text())
        try:
            bpm = float(value.replace(",", "."))
        except ValueError:
            bpm = math.nan
        if not (math.isfinite(bpm) and bpm > 0):
            QtWidgets.QMessageBox.warning(self, "Pulso inicial", "Introduce un BPM inicial valido o inicia sin BPM.")
            return self.ensure_initial_pulse_or_confirm()
        self.prev_pulse_edit.setText(value)
        return value

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
        # micros,red,ir,tempA0C,tempA0Raw,tempA1C,tempA1Raw
        if len(parts) not in (3, 4, 5, 6, 7, 9, 11):
            return False

        try:
            int(parts[0])
            float(parts[1])
            float(parts[2])

            if len(parts) >= 4 and parts[3].lower() != "nan":
                float(parts[3])

            for value in parts[4:]:
                if value.lower() != "nan":
                    float(value)

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
            temp_a0_c = math.nan
            temp_a0_raw = math.nan
            temp_a1_c = math.nan
            temp_a1_raw = math.nan
            temp_a2_c = math.nan
            temp_a2_raw = math.nan
            temp_a3_c = math.nan
            temp_a3_raw = math.nan

            if len(parts) >= 4 and parts[3].strip().lower() != "nan":
                temp_a0_c = float(parts[3].strip())
            if len(parts) >= 5 and parts[4].strip().lower() != "nan":
                temp_a0_raw = float(parts[4].strip())
            if len(parts) >= 6 and parts[5].strip().lower() != "nan":
                temp_a1_c = float(parts[5].strip())
            if len(parts) >= 7 and parts[6].strip().lower() != "nan":
                temp_a1_raw = float(parts[6].strip())
            if len(parts) >= 8 and parts[7].strip().lower() != "nan":
                temp_a2_c = float(parts[7].strip())
            if len(parts) >= 9 and parts[8].strip().lower() != "nan":
                temp_a2_raw = float(parts[8].strip())
            if len(parts) >= 10 and parts[9].strip().lower() != "nan":
                temp_a3_c = float(parts[9].strip())
            if len(parts) >= 11 and parts[10].strip().lower() != "nan":
                temp_a3_raw = float(parts[10].strip())

            channel_values = {
                "A0": (temp_a0_c, temp_a0_raw),
                "A1": (temp_a1_c, temp_a1_raw),
                "A2": (temp_a2_c, temp_a2_raw),
                "A3": (temp_a3_c, temp_a3_raw),
            }
            st.temp_primary_channel = temp_primary_channel_for(st.udder_side, st.temp_mapping, st.animal_type)
            temp_c, temp_raw = channel_values.get(st.temp_primary_channel, channel_values["A0"])
            assignments = parse_temp_mapping(st.temp_mapping, st.animal_type)
            position_values = {
                position: channel_values.get(channel, (math.nan, math.nan))
                for channel, position in assignments.items()
            }
            temp_rt_c, temp_rt_raw = position_values.get("RT", (math.nan, math.nan))
            temp_lt_c, temp_lt_raw = position_values.get("LT", (math.nan, math.nan))
            temp_flt_c, temp_flt_raw = position_values.get("FLT", (math.nan, math.nan))
            temp_frt_c, temp_frt_raw = position_values.get("FRT", (math.nan, math.nan))
            temp_rlt_c, temp_rlt_raw = position_values.get("RLT", (math.nan, math.nan))
            temp_rrt_c, temp_rrt_raw = position_values.get("RRT", (math.nan, math.nan))

            if red == 0 and ir == 0 and not any(np.isfinite(v) for pair in channel_values.values() for v in pair):
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
            st.temp_a0_c.append(temp_a0_c)
            st.temp_a0_raw.append(temp_a0_raw)
            st.temp_a1_c.append(temp_a1_c)
            st.temp_a1_raw.append(temp_a1_raw)
            st.temp_a2_c.append(temp_a2_c)
            st.temp_a2_raw.append(temp_a2_raw)
            st.temp_a3_c.append(temp_a3_c)
            st.temp_a3_raw.append(temp_a3_raw)
            st.valid_lines += 1
            self.check_temperature_alert(trel, channel_values, position_values)

            if st.raw_writer:
                cfg = self.last_sensor_config
                st.raw_writer.writerow([
                    st.session_id or st.base_name,
                    st.crotal_id,
                    st.base_name,
                    st.mode,
                    st.animal_type,
                    st.measurement_condition,
                    st.udder_side,
                    st.temp_mapping,
                    st.temp_primary_channel,
                    st.vacuum_condition,
                    st.config_label,
                    st.valid_lines,
                    f"{trel:.6f}",
                    f"{red:.0f}",
                    f"{ir:.0f}",
                    fmt(temp_c, 2, ""),
                    fmt(temp_raw, 0, ""),
                    fmt(temp_a0_c, 2, ""),
                    fmt(temp_a0_raw, 0, ""),
                    fmt(temp_a1_c, 2, ""),
                    fmt(temp_a1_raw, 0, ""),
                    fmt(temp_a2_c, 2, ""),
                    fmt(temp_a2_raw, 0, ""),
                    fmt(temp_a3_c, 2, ""),
                    fmt(temp_a3_raw, 0, ""),
                    fmt(temp_rt_c, 2, ""),
                    fmt(temp_rt_raw, 0, ""),
                    fmt(temp_lt_c, 2, ""),
                    fmt(temp_lt_raw, 0, ""),
                    fmt(temp_flt_c, 2, ""),
                    fmt(temp_flt_raw, 0, ""),
                    fmt(temp_frt_c, 2, ""),
                    fmt(temp_frt_raw, 0, ""),
                    fmt(temp_rlt_c, 2, ""),
                    fmt(temp_rlt_raw, 0, ""),
                    fmt(temp_rrt_c, 2, ""),
                    fmt(temp_rrt_raw, 0, ""),
                    cfg.red,
                    cfg.ir,
                    cfg.avg,
                    cfg.rate,
                    cfg.width,
                    cfg.adc,
                    cfg.skip,
                    1 if cfg.debug else 0,
                    st.pulse_prev,
                    st.temp_manual_initial_c,
                    st.pulse_final_pulsio,
                    st.pulse_final_fonendo,
                    self.last_config_ack,
                    datetime.now().isoformat(timespec="milliseconds"),
                    st.measurement_condition,
                    st.final_annotations,
                ])

        except Exception as exc:
            st.discarded_lines += 1
            log.warning("Dato descartado '%s': %s", line, exc)

    def reset_capture_state(self, keep_identity: bool = True):
        old = self.state
        crotal = old.crotal_id if keep_identity else sanitize_id(self.crotal_edit.text())
        animal_type = old.animal_type if keep_identity else self.current_animal_type()
        prev = old.pulse_prev if keep_identity else safe_float_text(self.prev_pulse_edit.text())
        temp_manual_initial = old.temp_manual_initial_c if keep_identity else safe_float_text(self.temp_manual_initial_edit.text())
        condition = old.measurement_condition if keep_identity else self.current_condition_text()
        udder = old.udder_side if keep_identity else self.current_udder_text()
        temp_mapping = old.temp_mapping if keep_identity else self.current_temp_mapping()
        temp_primary_channel = temp_primary_channel_for(udder, temp_mapping, animal_type)
        vacuum = old.vacuum_condition if keep_identity else self.current_vacuum_text()
        final_annotations = old.final_annotations if keep_identity else ""
        temp_monitor_seconds = old.temp_monitor_seconds if keep_identity else self.current_temp_monitor_seconds()
        temp_alert_threshold_c = old.temp_alert_threshold_c if keep_identity else self.current_temp_alert_threshold()
        self.state = CaptureState(
            crotal_id=crotal,
            animal_type=animal_type,
            pulse_prev=prev,
            temp_manual_initial_c=temp_manual_initial,
            measurement_condition=condition,
            final_annotations=final_annotations,
            udder_side=udder,
            temp_mapping=temp_mapping,
            temp_primary_channel=temp_primary_channel,
            temp_monitor_seconds=temp_monitor_seconds,
            temp_alert_threshold_c=temp_alert_threshold_c,
            vacuum_condition=vacuum,
            sensor_ready=old.sensor_ready,
            last_config_ack=self.last_config_ack,
            last_config_line=self.last_config_line,
        )

    def open_raw_file(self):
        st = self.state
        st.raw_file = self.raw_dir / f"raw_{st.base_name}.csv"
        st.raw_handle = open(st.raw_file, "w", newline="", encoding="utf-8")
        st.raw_writer = csv.writer(st.raw_handle, delimiter=";")
        st.raw_writer.writerow([
            "session_id", "id", "base_name", "modo", "animal_type", "condiciones_medida", "ubre", "temp_mapping", "temp_primary_channel", "medicion_vacio", "config_label", "sample_index", "tiempo_s",
            "red_raw", "ir_raw", "temp_c", "temp_raw", "temp_a0_c", "temp_a0_raw", "temp_a1_c", "temp_a1_raw", "temp_a2_c", "temp_a2_raw", "temp_a3_c", "temp_a3_raw",
            "temp_rt_c", "temp_rt_raw", "temp_lt_c", "temp_lt_raw", "temp_flt_c", "temp_flt_raw", "temp_frt_c", "temp_frt_raw", "temp_rlt_c", "temp_rlt_raw", "temp_rrt_c", "temp_rrt_raw",
            "cfg_red", "cfg_ir", "cfg_avg", "cfg_rate", "cfg_width", "cfg_adc", "cfg_skip", "cfg_debug",
            "pulso_previo", "temperatura_manual_inicio_c", "pulso_final_pulsio", "pulso_final_fonendo",
            "cfg_confirmacion", "system_time", "anotaciones_inicio", "anotaciones_finales"
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
        pulse_prev = self.ensure_initial_pulse_or_confirm()
        if pulse_prev is None:
            return
        st.pulse_prev = pulse_prev
        st.measurement_condition = self.current_condition_text()
        st.config_label = "manual"
        st.base_name = f"{st.crotal_id}_{now_stamp()}"
        st.session_id = st.base_name
        st.capture_start_wall = time.time()
        st.capturing = True
        st.finished = False
        try:
            self.serial_port.reset_input_buffer(); self.serial_port.reset_output_buffer()
        except Exception:
            pass
        cfg = self.sensor_widget.get_config()
        if not self.confirm_config_before_start(cfg):
            st.capturing = False
            return
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
        pulse_prev = self.ensure_initial_pulse_or_confirm()
        if pulse_prev is None:
            return
        st.pulse_prev = pulse_prev
        st.measurement_condition = self.current_condition_text()
        st.config_label = "larga_manual"
        st.base_name = f"LONG_{st.crotal_id}_{now_stamp()}"
        st.session_id = st.base_name
        st.capture_start_wall = time.time()
        st.capturing = True
        try:
            self.serial_port.reset_input_buffer(); self.serial_port.reset_output_buffer()
        except Exception:
            pass
        cfg = self.sensor_widget.get_config()
        if not self.confirm_config_before_start(cfg):
            st.capturing = False
            return
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
        st.session_id = st.base_name
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
        if st.capturing and st.mode in ("normal", "experimento_vacio"):
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

    def temp_dual_arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        st = self.state
        n = min(len(st.t), len(st.temp_a0_c), len(st.temp_a0_raw), len(st.temp_a1_c), len(st.temp_a1_raw))
        return (
            np.asarray(st.temp_a0_c[:n], dtype=float),
            np.asarray(st.temp_a0_raw[:n], dtype=float),
            np.asarray(st.temp_a1_c[:n], dtype=float),
            np.asarray(st.temp_a1_raw[:n], dtype=float),
        )

    def temp_channel_arrays(self) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        st = self.state
        out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for channel in TEMP_CHANNELS:
            values = getattr(st, f"temp_{channel.lower()}_c", [])
            raw = getattr(st, f"temp_{channel.lower()}_raw", [])
            n = min(len(st.t), len(values), len(raw))
            out[channel] = (
                np.asarray(values[:n], dtype=float),
                np.asarray(raw[:n], dtype=float),
            )
        return out

    def temperature_monitor_start_index(self) -> int:
        return 0

    def temperature_monitor_elapsed(self, trel: float) -> float:
        t, _red, _ir = self.arrays()
        finite_t = t[np.isfinite(t)]
        if finite_t.size:
            return float(trel - finite_t[0])
        return float(trel)

    def temperature_window_max(self) -> tuple[str, float, float]:
        st = self.state
        t = np.asarray(st.t, dtype=float)
        if not t.size:
            return "", math.nan, math.nan
        start_idx = max(0, min(self.temperature_monitor_start_index(), t.size - 1))
        window_s = max(1.0, float(getattr(st, "temp_monitor_seconds", TEMP_MONITOR_DEFAULT_S) or TEMP_MONITOR_DEFAULT_S))
        rel = t[start_idx:] - float(t[start_idx])
        mask = np.isfinite(rel) & (rel >= 0.0) & (rel <= window_s)
        if not np.any(mask):
            return "", math.nan, math.nan
        assignments = parse_temp_mapping(st.temp_mapping, st.animal_type)
        channel_positions = {channel: position for channel, position in assignments.items()}
        best_label = ""
        best_value = math.nan
        best_time = math.nan
        for channel in self.active_temp_channels():
            values = np.asarray(getattr(st, f"temp_{channel.lower()}_c", []), dtype=float)
            n = min(t.size, values.size)
            if start_idx >= n:
                continue
            local_rel = t[start_idx:n] - float(t[start_idx])
            local_values = values[start_idx:n]
            local_mask = np.isfinite(local_rel) & np.isfinite(local_values) & (local_rel >= 0.0) & (local_rel <= window_s)
            if not np.any(local_mask):
                continue
            selected = local_values[local_mask]
            idx = int(np.nanargmax(selected))
            value = float(selected[idx])
            if not np.isfinite(best_value) or value > best_value:
                selected_rel = local_rel[local_mask]
                position = channel_positions.get(channel, "")
                label = f"{position} ({channel})" if position else channel
                best_label = label
                best_value = value
                best_time = float(selected_rel[idx])
        return best_label, best_value, best_time

    def temp_monitor_status_line(self) -> str:
        st = self.state
        label, value, at_s = self.temperature_window_max()
        threshold = float(getattr(st, "temp_alert_threshold_c", TEMP_ALERT_DEFAULT_C) or TEMP_ALERT_DEFAULT_C)
        window_s = float(getattr(st, "temp_monitor_seconds", TEMP_MONITOR_DEFAULT_S) or TEMP_MONITOR_DEFAULT_S)
        if np.isfinite(value):
            base = f"Temp inicial {fmt(value,1)} C en {label or '-'} ({fmt(at_s,1)}s/{fmt(window_s,1)}s), aviso > {fmt(threshold,1)} C"
        else:
            base = f"Temp inicial: sin datos en {fmt(window_s,1)}s, aviso > {fmt(threshold,1)} C"
        if getattr(st, "temp_alert_triggered", False):
            base += f" | AVISO {fmt(getattr(st, 'temp_alert_value_c', math.nan),1)} C {getattr(st, 'temp_alert_label', '')}"
        return base

    def check_temperature_alert(self, trel: float, channel_values: dict[str, tuple[float, float]], position_values: dict[str, tuple[float, float]]):
        st = self.state
        if not st.capturing or getattr(st, "temp_alert_triggered", False):
            return
        elapsed = self.temperature_monitor_elapsed(trel)
        window_s = max(1.0, float(getattr(st, "temp_monitor_seconds", TEMP_MONITOR_DEFAULT_S) or TEMP_MONITOR_DEFAULT_S))
        if not np.isfinite(elapsed) or elapsed < 0.0 or elapsed > window_s:
            return
        threshold = float(getattr(st, "temp_alert_threshold_c", TEMP_ALERT_DEFAULT_C) or TEMP_ALERT_DEFAULT_C)
        candidates: list[tuple[str, float]] = []
        for position, (temp_c, _raw) in position_values.items():
            if np.isfinite(temp_c):
                candidates.append((f"{position}", float(temp_c)))
        if not candidates:
            active_channels = set(self.active_temp_channels())
            for channel, (temp_c, _raw) in channel_values.items():
                if channel not in active_channels:
                    continue
                if np.isfinite(temp_c):
                    candidates.append((channel, float(temp_c)))
        if not candidates:
            return
        label, value = max(candidates, key=lambda item: item[1])
        if value < threshold:
            return
        st.temp_alert_triggered = True
        st.temp_alert_value_c = value
        st.temp_alert_label = label
        st.temp_alert_time_s = float(elapsed)
        self.show_temperature_alert(label, value, elapsed, threshold)

    def show_temperature_alert(self, label: str, value: float, elapsed: float, threshold: float):
        text = (
            f"La temperatura inicial ha superado el umbral.\n\n"
            f"Sensor: {label}\n"
            f"Temperatura: {fmt(value, 1)} C\n"
            f"Tiempo: {fmt(elapsed, 1)} s\n"
            f"Umbral: {fmt(threshold, 1)} C"
        )
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Aviso temperatura")
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setText(text)
        box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
        self._temperature_alert_box = box
        box.open()

    def update_live_temperature_plot(self):
        if not hasattr(self, "temp_live_curves"):
            return
        st = self.state
        t = np.asarray(st.t, dtype=float)
        if t.size < 1:
            for curve in self.temp_live_curves.values():
                curve.setData([], [])
            return
        start_idx = max(0, min(self.temperature_monitor_start_index(), t.size - 1))
        window_s = max(1.0, float(getattr(st, "temp_monitor_seconds", TEMP_MONITOR_DEFAULT_S) or TEMP_MONITOR_DEFAULT_S))
        active_channels = set(self.sync_temperature_curve_visibility(self.temp_live_curves, getattr(self, "temp_live_legend", None)))
        for channel, curve in self.temp_live_curves.items():
            if channel not in active_channels:
                curve.setData([], [])
                continue
            values = np.asarray(getattr(st, f"temp_{channel.lower()}_c", []), dtype=float)
            n = min(t.size, values.size)
            if start_idx >= n:
                curve.setData([], [])
                continue
            rel = t[start_idx:n] - float(t[start_idx])
            mask = np.isfinite(rel) & np.isfinite(values[start_idx:n]) & (rel >= 0.0) & (rel <= window_s)
            curve.setData(rel[mask], values[start_idx:n][mask])
        threshold = float(getattr(st, "temp_alert_threshold_c", TEMP_ALERT_DEFAULT_C) or TEMP_ALERT_DEFAULT_C)
        self.temp_alert_line.setValue(threshold)
        label, value, at_s = self.temperature_window_max()
        channel_names = self.format_active_temp_channels()
        if np.isfinite(value):
            self.plot_temp_live.setTitle(f"Temperatura inicial ({channel_names}) | max {fmt(value,1)} C {label} a {fmt(at_s,1)} s")
        else:
            self.plot_temp_live.setTitle(f"Temperatura inicial ({channel_names})")
        self.plot_temp_live.setXRange(0.0, window_s, padding=0.02)

    def temperature_summary(self) -> dict[str, float | int]:
        t, _red, _ir = self.arrays()
        temp_c, temp_raw = self.temp_arrays()
        window_s = max(1.0, float(getattr(self.state, "temp_monitor_seconds", TEMP_MONITOR_DEFAULT_S) or TEMP_MONITOR_DEFAULT_S))
        primary = temperature_channel_summary(t, temp_c, temp_raw, settle_s=0.0, window_s=window_s)
        channel_arrays = self.temp_channel_arrays()
        channel_summaries = {
            channel: temperature_channel_summary(t, values, raw, settle_s=0.0, window_s=window_s)
            for channel, (values, raw) in channel_arrays.items()
        }
        assignments = parse_temp_mapping(self.state.temp_mapping, self.state.animal_type)
        position_summaries = {
            position: channel_summaries.get(channel, temperature_channel_summary(t, np.asarray([], dtype=float), np.asarray([], dtype=float), settle_s=0.0, window_s=window_s))
            for channel, position in assignments.items()
        }
        out = {
            "temp_samples": primary["samples"],
            "temp_raw_samples": primary["raw_samples"],
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
            "temp_monitor_seconds": window_s,
            "temp_alert_threshold_c": getattr(self.state, "temp_alert_threshold_c", TEMP_ALERT_DEFAULT_C),
            "temp_alert_triggered": 1 if getattr(self.state, "temp_alert_triggered", False) else 0,
            "temp_alert_value_c": getattr(self.state, "temp_alert_value_c", math.nan),
            "temp_alert_time_s": getattr(self.state, "temp_alert_time_s", math.nan),
            "temp_alert_label": getattr(self.state, "temp_alert_label", ""),
            "temp_raw_last": primary["raw_last"],
        }
        for channel in TEMP_CHANNELS:
            summary = channel_summaries[channel]
            prefix = f"temp_{channel.lower()}"
            out.update({
                f"{prefix}_samples": summary["samples"],
                f"{prefix}_raw_samples": summary["raw_samples"],
                f"{prefix}_c_last": summary["last"],
                f"{prefix}_c_mean": summary["mean"],
                f"{prefix}_c_min": summary["min"],
                f"{prefix}_c_max": summary["max"],
                f"{prefix}_c_final_max_5s": summary["final_max_5s"],
                f"{prefix}_c_final_time_s": summary["final_time_s"],
                f"{prefix}_c_final_raw_at_max": summary["final_raw_at_max"],
                f"{prefix}_c_final_samples": summary["final_samples"],
                f"{prefix}_raw_last": summary["raw_last"],
            })
        empty = temperature_channel_summary(t, np.asarray([], dtype=float), np.asarray([], dtype=float), settle_s=0.0, window_s=window_s)
        for position, prefix in POSITION_SUMMARY_PREFIXES.items():
            summary = position_summaries.get(position, empty)
            out.update({
                f"{prefix}_c_last": summary["last"],
                f"{prefix}_c_mean": summary["mean"],
                f"{prefix}_c_min": summary["min"],
                f"{prefix}_c_max": summary["max"],
                f"{prefix}_c_final_max_5s": summary["final_max_5s"],
                f"{prefix}_c_final_time_s": summary["final_time_s"],
                f"{prefix}_c_final_raw_at_max": summary["final_raw_at_max"],
                f"{prefix}_c_final_samples": summary["final_samples"],
                f"{prefix}_raw_last": summary["raw_last"],
            })
        return out

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
        self.ask_final_reference(include_pulse=st.mode in ("normal", "long", "experimento_vacio"))
        self.update_raw_manual_reference()
        self.save_processed()
        self.save_blocks_file()
        self.save_images()
        self.save_summary(reason)
        self.write_session_row(reason)
        log.info("Captura finalizada: %s muestras=%s motivo=%s", st.base_name, len(st.t), reason)

    def ask_final_reference(self, include_pulse: bool = True):
        st = self.state
        dialog = QtWidgets.QDialog(self); dialog.setWindowTitle("Datos finales y anotaciones")
        form = QtWidgets.QFormLayout(dialog)
        pulsio = QtWidgets.QLineEdit(st.pulse_final_pulsio)
        fonendo = QtWidgets.QLineEdit(st.pulse_final_fonendo)
        form.addRow("Pulsaciones finales pulsioxímetro:", pulsio)
        form.addRow("Pulsaciones finales fonendo:", fonendo)
        notes = QtWidgets.QPlainTextEdit(st.final_annotations)
        notes.setPlaceholderText("Ej.: animal se movio al final, sensor recolocado, tos, retirada parcial, incidencia...")
        notes.setMinimumHeight(74)
        form.addRow("Anotaciones finales:", notes)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            st.pulse_final_pulsio = safe_float_text(pulsio.text())
            st.pulse_final_fonendo = safe_float_text(fonendo.text())
            st.final_annotations = notes.toPlainText().strip()

    def update_raw_manual_reference(self):
        st = self.state
        path = st.raw_file
        if path is None or not path.exists():
            return
        try:
            with open(path, "r", newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f, delimiter=";")
                rows = [{str(k or "").strip(): str(v or "").strip() for k, v in row.items()} for row in reader]
        except OSError as exc:
            log.warning("No se pudo leer raw para actualizar pulso manual %s: %s", path, exc)
            return
        if not rows:
            return
        fieldnames = list(rows[0].keys())
        for field in ("pulso_previo", "temperatura_manual_inicio_c", "pulso_final_pulsio", "pulso_final_fonendo", "anotaciones_inicio", "anotaciones_finales"):
            if field not in fieldnames:
                fieldnames.append(field)
        for row in rows:
            row["pulso_previo"] = st.pulse_prev
            row["temperatura_manual_inicio_c"] = st.temp_manual_initial_c
            row["pulso_final_pulsio"] = st.pulse_final_pulsio
            row["pulso_final_fonendo"] = st.pulse_final_fonendo
            row["anotaciones_inicio"] = st.measurement_condition
            row["anotaciones_finales"] = st.final_annotations
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";", extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
        except OSError as exc:
            log.warning("No se pudo actualizar pulso manual en raw %s: %s", path, exc)

    def save_current_config_json(self, prefix: str):
        data = {"sensor": asdict(self.sensor_widget.get_config()), "analysis": asdict(self.analysis_widget.get_config()), "base_dir": str(BASE_DIR), "results_dir": str(self.results_dir), "created": datetime.now().isoformat()}
        path = self.config_dir / f"{prefix}_{now_stamp()}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.state.config_file = path

    def save_processed(self):
        st = self.state
        t, red, ir = self.arrays()
        temp_c, temp_raw = self.temp_arrays()
        channel_arrays = self.temp_channel_arrays()
        assignments = parse_temp_mapping(st.temp_mapping, st.animal_type)
        if t.size < 2 or not st.base_name:
            return
        cfg = self.analysis_widget.get_config()
        sensor_cfg = self.sensor_widget.get_config()
        hz = estimate_hz(t)
        red_proc = processed_for_plot(red, hz, cfg)
        ir_proc = processed_for_plot(ir, hz, cfg)
        art_red = detect_artifacts(red, strict=True); art_ir = detect_artifacts(ir, strict=True)
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
        st.processed_file = self.processed_dir / f"proc_{st.base_name}.csv"
        with open(st.processed_file, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([
                "session_id", "id", "base_name", "modo", "animal_type", "condiciones_medida", "ubre", "temp_mapping", "temp_primary_channel", "medicion_vacio", "config_label", "sample_index", "tiempo_s",
                "red_raw", "ir_raw", "temp_c", "temp_raw", "temp_a0_c", "temp_a0_raw", "temp_a1_c", "temp_a1_raw", "temp_a2_c", "temp_a2_raw", "temp_a3_c", "temp_a3_raw",
                "temp_rt_c", "temp_rt_raw", "temp_lt_c", "temp_lt_raw", "temp_flt_c", "temp_flt_raw", "temp_frt_c", "temp_frt_raw", "temp_rlt_c", "temp_rlt_raw", "temp_rrt_c", "temp_rrt_raw",
                "red_proc_norm", "ir_proc_norm", "artifact_red", "artifact_ir", "peak_ir",
                "bpm_rolling_5s", "spo2_rolling_5s", "ratio_r_rolling_5s", "quality_rolling_5s"
            ])
            for i in range(t.size):
                tc = temp_c[i] if i < temp_c.size else math.nan
                tr = temp_raw[i] if i < temp_raw.size else math.nan
                channel_values = {}
                for channel in TEMP_CHANNELS:
                    values, raw = channel_arrays[channel]
                    channel_values[channel] = (
                        values[i] if i < values.size else math.nan,
                        raw[i] if i < raw.size else math.nan,
                    )
                position_values = {
                    position: channel_values.get(channel, (math.nan, math.nan))
                    for channel, position in assignments.items()
                }
                w.writerow([
                    st.session_id or st.base_name, st.crotal_id, st.base_name, st.mode, st.animal_type, st.measurement_condition, st.udder_side, st.temp_mapping, st.temp_primary_channel, st.vacuum_condition, st.config_label, i + 1, f"{t[i]:.6f}",
                    f"{red[i]:.0f}", f"{ir[i]:.0f}", fmt(tc, 2, ""), fmt(tr, 0, ""),
                    fmt(channel_values["A0"][0], 2, ""), fmt(channel_values["A0"][1], 0, ""), fmt(channel_values["A1"][0], 2, ""), fmt(channel_values["A1"][1], 0, ""),
                    fmt(channel_values["A2"][0], 2, ""), fmt(channel_values["A2"][1], 0, ""), fmt(channel_values["A3"][0], 2, ""), fmt(channel_values["A3"][1], 0, ""),
                    fmt(position_values.get("RT", (math.nan, math.nan))[0], 2, ""), fmt(position_values.get("RT", (math.nan, math.nan))[1], 0, ""),
                    fmt(position_values.get("LT", (math.nan, math.nan))[0], 2, ""), fmt(position_values.get("LT", (math.nan, math.nan))[1], 0, ""),
                    fmt(position_values.get("FLT", (math.nan, math.nan))[0], 2, ""), fmt(position_values.get("FLT", (math.nan, math.nan))[1], 0, ""),
                    fmt(position_values.get("FRT", (math.nan, math.nan))[0], 2, ""), fmt(position_values.get("FRT", (math.nan, math.nan))[1], 0, ""),
                    fmt(position_values.get("RLT", (math.nan, math.nan))[0], 2, ""), fmt(position_values.get("RLT", (math.nan, math.nan))[1], 0, ""),
                    fmt(position_values.get("RRT", (math.nan, math.nan))[0], 2, ""), fmt(position_values.get("RRT", (math.nan, math.nan))[1], 0, ""),
                    f"{red_proc[i]:.5f}", f"{ir_proc[i]:.5f}", int(art_red[i]), int(art_ir[i]), int(peak_flags[i]),
                    fmt(bpm_rolling[i], 2, ""), fmt(spo2_rolling[i], 2, ""), fmt(ratio_rolling[i], 5, ""), fmt(quality_rolling[i], 1, "")
                ])

    def save_blocks_file(self):
        st = self.state
        if not st.base_name:
            return
        st.blocks_file = self.report_dir / f"bpm_blocks_10s_{st.base_name}.csv"
        with open(st.blocks_file, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["session_id", "id", "base_name", "modo", "bloque", "inicio_s", "fin_s", "bpm_medio_10s"] )
            for i, bpm in enumerate(st.bpm_blocks_10s):
                start = i * 10
                end = start + 10
                w.writerow([st.session_id or st.base_name, st.crotal_id, st.base_name, st.mode, i + 1, start, end, fmt(bpm, 2, "")])

    def save_images(self):
        st = self.state
        if not st.base_name:
            return
        st.plot_file = self.figures_dir / f"plot_{st.base_name}.png"
        st.screenshot_file = self.screenshot_dir / f"screen_{st.base_name}.png"
        self.tabs.grab().save(str(st.plot_file), "PNG")
        self.grab().save(str(st.screenshot_file), "PNG")

    def save_summary(self, reason: str):
        st = self.state
        if not st.base_name:
            return
        st.summary_file = self.report_dir / f"summary_{st.base_name}.json"
        temp = self.temperature_summary()
        data = {
            "session_id": st.session_id or st.base_name,
            "id": st.crotal_id,
            "base_name": st.base_name,
            "mode": st.mode,
            "animal_type": st.animal_type,
            "animal_label": animal_label(st.animal_type),
            "measurement_condition": st.measurement_condition,
            "udder_side": st.udder_side,
            "temp_mapping": st.temp_mapping,
            "temp_assignments": parse_temp_mapping(st.temp_mapping, st.animal_type),
            "temp_primary_channel": st.temp_primary_channel,
            "vacuum_condition": st.vacuum_condition,
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
            "manual_reference": {
                "pulso_previo": st.pulse_prev,
                "temperatura_manual_inicio_c": st.temp_manual_initial_c,
                "pulso_final_pulsio": st.pulse_final_pulsio,
                "pulso_final_fonendo": st.pulse_final_fonendo,
            },
            "annotations": {
                "initial": st.measurement_condition,
                "final": st.final_annotations,
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
        header = [
            "session_id", "id", "base_name", "fecha", "hora", "modo", "condiciones_medida",
            "animal_type", "ubre", "temp_mapping", "temp_primary_channel", "medicion_vacio", "config_label",
            "motivo_fin", "duracion_solicitada_s", "muestras", "duracion_real_s", "hz_real",
            "bpm", "bpm_peak", "bpm_fft", "bpm_autocorr", "calidad", "calidad_label",
            "spo2_pct", "ratio_r", "resp_min_exp", "resp_calidad_exp", "resp_razon_exp",
            "temp_c_final_max_5s", "temp_c_final_time_s", "temp_c_final_raw_at_max", "temp_c_ultima", "temp_c_media", "temp_raw_ultima",
            "temp_rt_c_final_max_5s", "temp_rt_c_final_time_s", "temp_rt_c_final_raw_at_max", "temp_rt_c_ultima", "temp_rt_c_media", "temp_rt_raw_ultima",
            "temp_lt_c_final_max_5s", "temp_lt_c_final_time_s", "temp_lt_c_final_raw_at_max", "temp_lt_c_ultima", "temp_lt_c_media", "temp_lt_raw_ultima",
            "temp_a0_c_final_max_5s", "temp_a0_c_final_time_s", "temp_a0_c_final_raw_at_max", "temp_a0_c_ultima", "temp_a0_c_media", "temp_a0_raw_ultima",
            "temp_a1_c_final_max_5s", "temp_a1_c_final_time_s", "temp_a1_c_final_raw_at_max", "temp_a1_c_ultima", "temp_a1_c_media", "temp_a1_raw_ultima",
            "temp_a2_c_final_max_5s", "temp_a2_c_final_time_s", "temp_a2_c_final_raw_at_max", "temp_a2_c_ultima", "temp_a2_c_media", "temp_a2_raw_ultima",
            "temp_a3_c_final_max_5s", "temp_a3_c_final_time_s", "temp_a3_c_final_raw_at_max", "temp_a3_c_ultima", "temp_a3_c_media", "temp_a3_raw_ultima",
            "temp_flt_c_final_max_5s", "temp_flt_c_final_time_s", "temp_flt_c_final_raw_at_max", "temp_flt_c_ultima", "temp_flt_c_media", "temp_flt_raw_ultima",
            "temp_frt_c_final_max_5s", "temp_frt_c_final_time_s", "temp_frt_c_final_raw_at_max", "temp_frt_c_ultima", "temp_frt_c_media", "temp_frt_raw_ultima",
            "temp_rlt_c_final_max_5s", "temp_rlt_c_final_time_s", "temp_rlt_c_final_raw_at_max", "temp_rlt_c_ultima", "temp_rlt_c_media", "temp_rlt_raw_ultima",
            "temp_rrt_c_final_max_5s", "temp_rrt_c_final_time_s", "temp_rrt_c_final_raw_at_max", "temp_rrt_c_ultima", "temp_rrt_c_media", "temp_rrt_raw_ultima",
            "pi_ir_pct", "pi_red_pct", "artefactos_ir_pct", "artefactos_red_pct", "contacto",
            "cfg_confirmacion", "pulso_previo", "temperatura_manual_inicio_c", "pulso_final_pulsio", "pulso_final_fonendo", "anotaciones_finales",
            "raw", "processed", "plot", "screenshot", "summary", "config", "bpm_blocks_10s_json", "blocks_10s_file",
        ]
        self.session_writer.writerow(header); self.session_handle.flush()

    def write_session_row(self, reason: str):
        st = self.state; m = st.metrics; now = datetime.now()
        temp = self.temperature_summary()
        row = [
            st.session_id or st.base_name, st.crotal_id, st.base_name, now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"),
            st.mode, st.measurement_condition, st.animal_type, st.udder_side, st.temp_mapping, st.temp_primary_channel, st.vacuum_condition, st.config_label,
            reason, fmt(st.requested_duration_s, 1, ""), len(st.t), fmt(m.duration_s, 3, ""), fmt(m.hz, 2, ""),
            fmt(m.bpm, 1, ""), fmt(m.bpm_peak, 1, ""), fmt(m.bpm_fft, 1, ""), fmt(m.bpm_autocorr, 1, ""),
            fmt(m.quality, 1, ""), m.quality_label, fmt(m.spo2, 1, ""), fmt(m.ratio_r, 5, ""),
            fmt(m.resp_rate_rpm, 1, ""), fmt(m.resp_quality, 0, ""), m.resp_reason,
            fmt(temp["temp_c_final_max_5s"], 2, ""), fmt(temp["temp_c_final_time_s"], 3, ""), fmt(temp["temp_c_final_raw_at_max"], 0, ""), fmt(temp["temp_c_last"], 2, ""), fmt(temp["temp_c_mean"], 2, ""), fmt(temp["temp_raw_last"], 0, ""),
            fmt(temp["temp_rt_c_final_max_5s"], 2, ""), fmt(temp["temp_rt_c_final_time_s"], 3, ""), fmt(temp["temp_rt_c_final_raw_at_max"], 0, ""), fmt(temp["temp_rt_c_last"], 2, ""), fmt(temp["temp_rt_c_mean"], 2, ""), fmt(temp["temp_rt_raw_last"], 0, ""),
            fmt(temp["temp_lt_c_final_max_5s"], 2, ""), fmt(temp["temp_lt_c_final_time_s"], 3, ""), fmt(temp["temp_lt_c_final_raw_at_max"], 0, ""), fmt(temp["temp_lt_c_last"], 2, ""), fmt(temp["temp_lt_c_mean"], 2, ""), fmt(temp["temp_lt_raw_last"], 0, ""),
            fmt(temp["temp_a0_c_final_max_5s"], 2, ""), fmt(temp["temp_a0_c_final_time_s"], 3, ""), fmt(temp["temp_a0_c_final_raw_at_max"], 0, ""), fmt(temp["temp_a0_c_last"], 2, ""), fmt(temp["temp_a0_c_mean"], 2, ""), fmt(temp["temp_a0_raw_last"], 0, ""),
            fmt(temp["temp_a1_c_final_max_5s"], 2, ""), fmt(temp["temp_a1_c_final_time_s"], 3, ""), fmt(temp["temp_a1_c_final_raw_at_max"], 0, ""), fmt(temp["temp_a1_c_last"], 2, ""), fmt(temp["temp_a1_c_mean"], 2, ""), fmt(temp["temp_a1_raw_last"], 0, ""),
            fmt(temp["temp_a2_c_final_max_5s"], 2, ""), fmt(temp["temp_a2_c_final_time_s"], 3, ""), fmt(temp["temp_a2_c_final_raw_at_max"], 0, ""), fmt(temp["temp_a2_c_last"], 2, ""), fmt(temp["temp_a2_c_mean"], 2, ""), fmt(temp["temp_a2_raw_last"], 0, ""),
            fmt(temp["temp_a3_c_final_max_5s"], 2, ""), fmt(temp["temp_a3_c_final_time_s"], 3, ""), fmt(temp["temp_a3_c_final_raw_at_max"], 0, ""), fmt(temp["temp_a3_c_last"], 2, ""), fmt(temp["temp_a3_c_mean"], 2, ""), fmt(temp["temp_a3_raw_last"], 0, ""),
            fmt(temp["temp_flt_c_final_max_5s"], 2, ""), fmt(temp["temp_flt_c_final_time_s"], 3, ""), fmt(temp["temp_flt_c_final_raw_at_max"], 0, ""), fmt(temp["temp_flt_c_last"], 2, ""), fmt(temp["temp_flt_c_mean"], 2, ""), fmt(temp["temp_flt_raw_last"], 0, ""),
            fmt(temp["temp_frt_c_final_max_5s"], 2, ""), fmt(temp["temp_frt_c_final_time_s"], 3, ""), fmt(temp["temp_frt_c_final_raw_at_max"], 0, ""), fmt(temp["temp_frt_c_last"], 2, ""), fmt(temp["temp_frt_c_mean"], 2, ""), fmt(temp["temp_frt_raw_last"], 0, ""),
            fmt(temp["temp_rlt_c_final_max_5s"], 2, ""), fmt(temp["temp_rlt_c_final_time_s"], 3, ""), fmt(temp["temp_rlt_c_final_raw_at_max"], 0, ""), fmt(temp["temp_rlt_c_last"], 2, ""), fmt(temp["temp_rlt_c_mean"], 2, ""), fmt(temp["temp_rlt_raw_last"], 0, ""),
            fmt(temp["temp_rrt_c_final_max_5s"], 2, ""), fmt(temp["temp_rrt_c_final_time_s"], 3, ""), fmt(temp["temp_rrt_c_final_raw_at_max"], 0, ""), fmt(temp["temp_rrt_c_last"], 2, ""), fmt(temp["temp_rrt_c_mean"], 2, ""), fmt(temp["temp_rrt_raw_last"], 0, ""),
            fmt(m.pi_ir_pct, 4, ""), fmt(m.pi_red_pct, 4, ""), fmt(m.artifact_ir_pct, 1, ""), fmt(m.artifact_red_pct, 1, ""),
            m.contact_label, self.last_config_ack, st.pulse_prev, st.temp_manual_initial_c, st.pulse_final_pulsio, st.pulse_final_fonendo,
            st.final_annotations,
            st.raw_file.name if st.raw_file else "", st.processed_file.name if st.processed_file else "",
            st.plot_file.name if st.plot_file else "", st.screenshot_file.name if st.screenshot_file else "",
            st.summary_file.name if st.summary_file else "", st.config_file.name if st.config_file else "",
            json.dumps(st.bpm_blocks_10s, ensure_ascii=False), st.blocks_file.name if st.blocks_file else "",
        ]
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
        spo2_warning = spo2_support_message(m)
        spo2_warning_line = f"{spo2_warning}\n" if spo2_warning else ""
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
                f"{spo2_warning_line}"
                f"Respiraciones (experimental): {fmt(m.resp_rate_rpm,1)} resp/min | calidad {fmt(m.resp_quality,0)}\n"
                f"Temp: {fmt(temp['temp_c_last'],1)} °C\n"
                f"{self.temp_monitor_status_line()}\n"
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
                f"{spo2_warning_line}"
                f"Respiraciones (experimental): {fmt(m.resp_rate_rpm,1)} resp/min | calidad {fmt(m.resp_quality,0)}\n"
                f"Temp RT/LT final: {fmt(temp['temp_rt_c_final_max_5s'],1)} / {fmt(temp['temp_lt_c_final_max_5s'],1)} C | canal {st.temp_primary_channel} {fmt(temp['temp_c_final_max_5s'],1)} C\n"
                f"{self.temp_monitor_status_line()}\n"
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
            self.update_live_temperature_plot()
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
        self.update_live_temperature_plot()

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

    def return_to_menu(self):
        if self.state.capturing:
            self.stop_capture("VOLVER_MENU")
        self.back_to_menu.emit()

    def open_statistics_window(self):
        if self.state.capturing:
            self.stop_capture("ABRIR_ESTADISTICAS")
        self.open_statistics_requested.emit()

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

