from __future__ import annotations

import math
import time

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from ..models import CaptureState, Metrics
from ..paths import FIGURES_DIR, SCREENSHOT_DIR
from ..processing import estimate_hz, processed_for_plot, score_and_merge_metrics, spo2_support_message
from ..utils import fmt, now_stamp
from ..widgets import AnalysisConfigWidget, NoWheelDoubleSpinBox, NoWheelSpinBox, SensorConfigWidget
from .measurement_window import PPGSuite


class ReajustesWindow(PPGSuite):
    """Pantalla independiente para reajustes/larga duración.

    Importante: ya no se abre como ventana secundaria encima de PPGSuite.
    Es una ventana única, con un único QTimer principal heredado de PPGSuite.
    Bendita sea la RAM liberada.
    """

    def __init__(self):
        super().__init__("reajustes")
        self.setWindowTitle("PPG Suite v8 | Reajustes / larga duración")
        self.resize(1150, 760)

    def build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)

        left_scroll = QtWidgets.QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFixedWidth(430)
        left_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_widget = QtWidgets.QWidget()
        left_widget.setMinimumWidth(400)
        left_widget.setMaximumWidth(400)
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

        identity_group = QtWidgets.QGroupBox("Identificación")
        identity = QtWidgets.QFormLayout(identity_group)
        self.crotal_edit = QtWidgets.QLineEdit("SIN_CROTAL")
        self.prev_pulse_edit = QtWidgets.QLineEdit()
        self.temp_manual_initial_edit = QtWidgets.QLineEdit()
        self.temp_manual_initial_edit.setPlaceholderText("Opcional. Ej.: 38.6")
        self.animal_combo = QtWidgets.QComboBox()
        self.configure_animal_combo(self.animal_combo)
        self.udder_combo = QtWidgets.QComboBox()
        self.configure_udder_combo(self.udder_combo)
        self.temp_mapping_widget = self.create_temp_mapping_widget()
        self.vacuum_combo = QtWidgets.QComboBox()
        self.vacuum_combo.addItems(["", "con vacio", "sin vacio"])
        self.condition_edit = QtWidgets.QLineEdit()
        self.condition_edit.setPlaceholderText("Ej.: reajuste con sensor fijo, ordeño activo, prueba de LEDs...")
        self.duration_spin = NoWheelDoubleSpinBox()
        self.duration_spin.setRange(2, 3600)
        self.duration_spin.setDecimals(1)
        self.duration_spin.setValue(20.0)
        self.duration_spin.setSuffix(" s")
        self.duration_spin.setVisible(False)
        self.animal_combo.currentIndexChanged.connect(self.refresh_animal_dependent_controls)
        identity.addRow("Crotal:", self.crotal_edit)
        identity.addRow("Animal:", self.animal_combo)
        identity.addRow("Pulso previo ref.:", self.prev_pulse_edit)
        identity.addRow("Temp. manual inicio (C):", self.temp_manual_initial_edit)
        identity.addRow("Sensor:", self.udder_combo)
        identity.addRow("Termometros:", self.temp_mapping_widget)
        identity.addRow("Medicion:", self.vacuum_combo)
        identity.addRow("Anotaciones inicio:", self.condition_edit)
        left.addWidget(identity_group)
        self.refresh_animal_dependent_controls()

        live_group = QtWidgets.QGroupBox("Ventanas de reajuste")
        form = QtWidgets.QFormLayout(live_group)
        self.window_s = NoWheelSpinBox()
        self.window_s.setRange(3, 60)
        self.window_s.setValue(5)
        self.graph_s = NoWheelSpinBox()
        self.graph_s.setRange(5, 600)
        self.graph_s.setValue(30)
        form.addRow("Ventana cálculo vivo (s):", self.window_s)
        form.addRow("Ventana gráfica (s):", self.graph_s)
        left.addWidget(live_group)

        self.sensor_widget = SensorConfigWidget("Sensor en vivo")
        left.addWidget(self.sensor_widget)
        self.btn_save_animal_config = QtWidgets.QPushButton("Guardar configuracion especie")
        left.addWidget(self.btn_save_animal_config)
        self.btn_save_animal_config.clicked.connect(self.save_animal_profile_clicked)
        self.analysis_widget = AnalysisConfigWidget("Análisis en vivo")
        left.addWidget(self.analysis_widget)

        self.btn_apply_config = QtWidgets.QPushButton("Aplicar configuración al Arduino")
        self.btn_start = QtWidgets.QPushButton("Iniciar larga duración")
        self.btn_diagnostic = QtWidgets.QPushButton("Diagnóstico Arduino")
        self.btn_stop = QtWidgets.QPushButton("Parar")
        self.btn_snapshot = QtWidgets.QPushButton("Guardar snapshot")
        self.btn_back_menu = QtWidgets.QPushButton("Volver al menú inicial")
        self.btn_open_base = QtWidgets.QPushButton("Mostrar resultados")

        for b in [self.btn_apply_config, self.btn_start, self.btn_diagnostic, self.btn_stop, self.btn_snapshot, self.btn_open_base, self.btn_back_menu]:
            left.addWidget(b)

        self.btn_apply_config.clicked.connect(lambda: self.apply_sensor_config(self.sensor_widget.get_config()))
        self.btn_start.clicked.connect(self.start_long_capture)
        self.btn_diagnostic.clicked.connect(self.send_diagnostic_command)
        self.btn_stop.clicked.connect(lambda: self.stop_capture("STOP_LONG_MANUAL"))
        self.btn_snapshot.clicked.connect(self.save_snapshot)
        self.btn_back_menu.clicked.connect(self.return_to_menu)
        self.btn_open_base.clicked.connect(self.open_statistics_window)

        self.info = QtWidgets.QLabel()
        self.info.setFont(QtGui.QFont("Consolas", 9))
        self.info.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.info.setWordWrap(True)
        left.addWidget(self.info, stretch=1)

        right = QtWidgets.QVBoxLayout()
        root.addLayout(right, stretch=1)

        self.plot = pg.PlotWidget(title="Señal viva reciente | IR azul, RED rojo")
        self.plot.setBackground("w")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "Tiempo", units="s")
        self.ir_curve = self.plot.plot([], [], pen=pg.mkPen((0, 80, 220), width=2))
        self.red_curve = self.plot.plot([], [], pen=pg.mkPen((220, 30, 30), width=1))
        right.addWidget(self.plot, stretch=1)

        self.trend = pg.PlotWidget(title="Tendencia vivo | BPM y SpO2 estimada")
        self.trend.setBackground("w")
        self.trend.showGrid(x=True, y=True, alpha=0.25)
        self.trend.setLabel("bottom", "Tiempo", units="s")
        self.bpm_curve = self.trend.plot([], [], pen=pg.mkPen((30, 140, 40), width=2), name="BPM")
        self.spo2_curve = self.trend.plot([], [], pen=pg.mkPen((160, 60, 160), width=2), name="SpO2")
        self.trend.setMaximumHeight(230)
        right.addWidget(self.trend, stretch=0)

    def save_snapshot(self):
        st = self.state
        if not st.base_name:
            base = f"long_snapshot_{now_stamp()}"
            folder = SCREENSHOT_DIR
        else:
            base = f"long_snapshot_{st.base_name}_{now_stamp()}"
            folder = SCREENSHOT_DIR
        path = folder / f"{base}.png"
        self.grab().save(str(path), "PNG")
        QtWidgets.QMessageBox.information(self, "Snapshot", f"Guardado:\n{path}")

    def save_images(self):
        st = self.state
        if not st.base_name:
            return
        st.plot_file = FIGURES_DIR / f"plot_{st.base_name}.png"
        st.screenshot_file = SCREENSHOT_DIR / f"screen_{st.base_name}.png"
        self.plot.grab().save(str(st.plot_file), "PNG")
        self.grab().save(str(st.screenshot_file), "PNG")

    def update_info(self):
        st = self.state
        temp = self.temperature_summary()
        sensor_cfg = self.sensor_widget.get_config()
        cfg = self.analysis_widget.get_config()
        t, red, ir = self.arrays()
        if t.size < 2:
            self.info.setText(
                f"MODO REAJUSTES / LARGA DURACIÓN\n"
                f"Puerto: {self.port_name}\n"
                f"Estado: {'READY | preparado' if st.sensor_ready else 'esperando READY'}\n"
                f"Config Arduino: {self.last_config_ack}\n"
                f"Sin datos todavía.\n"
            )
            return

        start = max(0.0, float(t[-1]) - self.window_s.value())
        mask = t >= start
        met = score_and_merge_metrics(t[mask] - t[mask][0], red[mask], ir[mask], sensor_cfg, cfg) if int(np.sum(mask)) > 20 else Metrics()
        spo2_warning = spo2_support_message(met)
        spo2_warning_line = f"{spo2_warning}\n" if spo2_warning else ""
        status = f"CAPTURANDO {st.mode}" if st.capturing else ("READY | preparado" if st.sensor_ready else "PARADO")
        elapsed = time.time() - st.capture_start_wall if st.capturing else 0.0
        self.info.setText(
            f"MODO REAJUSTES / LARGA DURACIÓN\n"
            f"Puerto: {self.port_name}\n"
            f"Estado: {status} | {elapsed:.1f} s\n"
            f"Crotal: {st.crotal_id}\n"
            f"Ventana: últimos {self.window_s.value()} s | muestras={met.n}\n"
            f"Hz real: {fmt(met.hz, 2)}\n"
            f"BPM final: {fmt(met.bpm, 0)} | calidad {fmt(met.quality, 0)} ({met.quality_label})\n"
            f"BPM picos: {fmt(met.bpm_peak, 0)} | FFT: {fmt(met.bpm_fft, 0)} | autocorr: {fmt(met.bpm_autocorr, 0)}\n"
            f"Picos: {met.peaks_count} | polaridad: {met.polarity}\n"
            f"SpO2 estimada: {fmt(met.spo2, 1)} % | R={fmt(met.ratio_r, 4)}\n"
            f"{spo2_warning_line}"
            f"Respiraciones (experimental): {fmt(met.resp_rate_rpm, 1)} resp/min | calidad {fmt(met.resp_quality, 0)}\n"
            f"Temp RT/LT final: {fmt(temp['temp_rt_c_final_max_5s'], 1)} / {fmt(temp['temp_lt_c_final_max_5s'], 1)} C | canal {st.temp_primary_channel} {fmt(temp['temp_c_final_max_5s'], 1)} C\n"
            f"AC/DC IR: {fmt(met.ac_ir, 2)} / {fmt(met.dc_ir, 0)} | PI IR={fmt(met.pi_ir_pct, 3)} %\n"
            f"AC/DC RED: {fmt(met.ac_red, 2)} / {fmt(met.dc_red, 0)} | PI RED={fmt(met.pi_red_pct, 3)} %\n"
            f"Artefactos IR/RED: {fmt(met.artifact_ir_pct, 1)} / {fmt(met.artifact_red_pct, 1)} %\n"
            f"Saturación ADC aprox: {fmt(met.saturation_pct, 1)} %\n"
            f"Contacto: {met.contact_label}\n"
            f"Config Arduino: {self.last_config_ack} | {self.last_config_line[:80]}\n"
            f"Diagnóstico: {met.reason[:280]}\n\n"
            f"Datos totales sesión: {len(st.t)}\n"
            f"Última línea: {st.last_line[:90]}"
        )

    def update_plots(self):
        t, red, ir = self.arrays()
        if t.size < 2:
            self.ir_curve.setData([], [])
            self.red_curve.setData([], [])
            return
        cfg = self.analysis_widget.get_config()
        graph_start = max(0.0, float(t[-1]) - self.graph_s.value())
        mask_graph = t >= graph_start
        hz = estimate_hz(t[mask_graph])
        self.ir_curve.setData(t[mask_graph], processed_for_plot(ir[mask_graph], hz, cfg))
        self.red_curve.setData(t[mask_graph], processed_for_plot(red[mask_graph], hz, cfg))
        self.plot.setXRange(graph_start, max(graph_start + 1, float(t[-1])), padding=0.01)

        if self.state.rolling_t:
            self.bpm_curve.setData(self.state.rolling_t, self.state.rolling_bpm)
            self.spo2_curve.setData(self.state.rolling_t, self.state.rolling_spo2)
            self.trend.setXRange(max(0, self.state.rolling_t[-1] - self.graph_s.value()), max(1, self.state.rolling_t[-1]), padding=0.01)

    def open_long_window(self):
        # Ya estamos en reajustes. Pulsar L no debe abrir otra ventana como una muñeca rusa electrónica.
        return
