from __future__ import annotations

import csv
import html
import json
import math
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
from PyQt6 import QtGui, QtWidgets

from ..models import Metrics
from ..paths import (
    VACUUM_AUDIO_DIR,
    VACUUM_CONFIG_DIR,
    VACUUM_DIR,
    VACUUM_FIGURES_DIR,
    VACUUM_PROCESSED_DIR,
    VACUUM_RAW_DIR,
    VACUUM_REPORT_DIR,
    VACUUM_SCREENSHOT_DIR,
    VACUUM_SESSION_DIR,
    log,
)
from ..processing import estimate_hz, processed_ppg, score_and_merge_metrics, uniform_resample
from ..utils import fmt, now_stamp, safe_float_text, sanitize_id
from .measurement_window import PPGSuite


class VacuumExperimentWindow(PPGSuite):
    """PPG + microphone capture for post-run vacuum/notch analysis."""

    def __init__(self):
        self.results_dir = VACUUM_DIR
        self.raw_dir = VACUUM_RAW_DIR
        self.processed_dir = VACUUM_PROCESSED_DIR
        self.session_dir = VACUUM_SESSION_DIR
        self.figures_dir = VACUUM_FIGURES_DIR
        self.screenshot_dir = VACUUM_SCREENSHOT_DIR
        self.config_dir = VACUUM_CONFIG_DIR
        self.report_dir = VACUUM_REPORT_DIR
        self.audio_dir = VACUUM_AUDIO_DIR

        self.audio_stream = None
        self.audio_frames: list[np.ndarray] = []
        self.audio_lock = threading.Lock()
        self.audio_samplerate = 44100
        self.audio_device_index: int | None = None
        self.audio_device_name = ""
        self.audio_path: Path | None = None
        self.audio_status = "audio no iniciado"
        self.audio_start_perf = math.nan
        self.audio_stop_perf = math.nan
        self.ppg_start_perf = math.nan
        self.ppg_stop_perf = math.nan
        self.vacuum_report_file: Path | None = None
        self.vacuum_report_pdf: Path | None = None
        self.vacuum_analysis_file: Path | None = None

        super().__init__("experimento_vacio")
        self.setWindowTitle("PPG Suite v8 | Experimento con vacio")
        self.duration_spin.setValue(90.0)
        self.condition_edit.setText("Experimento con vacio: PPG + audio sincronizado")
        self.btn_start.setText("Iniciar experimento con vacio")
        self.btn_open_base.clicked.disconnect()
        self.btn_open_base.clicked.connect(lambda: self.open_results_dir())
        self.refresh_audio_devices()

    def build_ui(self):
        super().build_ui()
        central = self.centralWidget()
        root_layout = central.layout() if central is not None else None
        if root_layout is None or root_layout.count() == 0:
            return
        left_scroll = root_layout.itemAt(0).widget()
        left_widget = left_scroll.widget() if hasattr(left_scroll, "widget") else None
        left_layout = left_widget.layout() if left_widget is not None else None
        if left_layout is None:
            return

        audio_group = QtWidgets.QGroupBox("Microfono")
        audio_layout = QtWidgets.QGridLayout(audio_group)
        self.audio_device_combo = QtWidgets.QComboBox()
        self.btn_refresh_audio = QtWidgets.QPushButton("Refrescar")
        self.audio_status_label = QtWidgets.QLabel("Microfono no comprobado")
        self.audio_status_label.setWordWrap(True)
        audio_layout.addWidget(self.audio_device_combo, 0, 0, 1, 2)
        audio_layout.addWidget(self.btn_refresh_audio, 1, 0, 1, 2)
        audio_layout.addWidget(self.audio_status_label, 2, 0, 1, 2)
        self.btn_refresh_audio.clicked.connect(self.refresh_audio_devices)
        self.audio_device_combo.currentIndexChanged.connect(self.select_audio_device)
        left_layout.insertWidget(1, audio_group)

    def open_results_dir(self):
        from ..utils import open_folder

        open_folder(self.results_dir)

    def refresh_audio_devices(self):
        if not hasattr(self, "audio_device_combo"):
            return
        self.audio_device_combo.blockSignals(True)
        self.audio_device_combo.clear()
        self.audio_device_index = None
        self.audio_device_name = ""
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            default_input = sd.default.device[0] if sd.default.device else None
            first_input_row = -1
            for idx, dev in enumerate(devices):
                max_inputs = int(dev.get("max_input_channels", 0))
                if max_inputs <= 0:
                    continue
                name = " ".join(str(dev.get("name", f"Microfono {idx}")).split())
                rate = int(float(dev.get("default_samplerate", self.audio_samplerate) or self.audio_samplerate))
                label = f"{idx} | {name} ({max_inputs} canal/es, {rate} Hz)"
                self.audio_device_combo.addItem(label, {"index": idx, "samplerate": rate, "name": name})
                if first_input_row < 0:
                    first_input_row = self.audio_device_combo.count() - 1
                if default_input == idx:
                    self.audio_device_combo.setCurrentIndex(self.audio_device_combo.count() - 1)
            if self.audio_device_combo.count() == 0:
                self.audio_device_combo.addItem("Sin microfonos de entrada", None)
                self.audio_status_label.setText("No se han encontrado microfonos de entrada.")
            elif self.audio_device_combo.currentIndex() < 0 and first_input_row >= 0:
                self.audio_device_combo.setCurrentIndex(first_input_row)
                self.audio_status_label.setText("Microfono listo.")
            else:
                self.audio_status_label.setText("Microfono listo.")
        except Exception as exc:
            self.audio_device_combo.addItem("Audio no disponible", None)
            self.audio_status_label.setText(f"No se pudo listar microfonos: {exc}")
            self.audio_status = f"audio no disponible: {exc}"
            log.warning("No se pudieron listar microfonos: %s", exc)
        finally:
            self.audio_device_combo.blockSignals(False)
            self.select_audio_device()

    def select_audio_device(self):
        if not hasattr(self, "audio_device_combo"):
            return
        data = self.audio_device_combo.currentData()
        if isinstance(data, dict):
            self.audio_device_index = int(data.get("index")) if data.get("index") is not None else None
            self.audio_samplerate = int(data.get("samplerate") or 44100)
            self.audio_device_name = str(data.get("name") or self.audio_device_combo.currentText())
        else:
            self.audio_device_index = None
            self.audio_samplerate = 44100
            self.audio_device_name = ""

    def _audio_callback(self, indata, frames, time_info, status):
        del frames, time_info
        if status:
            self.audio_status = f"audio con aviso: {status}"
        with self.audio_lock:
            self.audio_frames.append(np.asarray(indata[:, 0], dtype=np.float32).copy())

    def start_audio_recording(self):
        self.audio_frames = []
        self.audio_path = None
        self.audio_status = "audio pendiente"
        self.audio_start_perf = math.nan
        self.audio_stop_perf = math.nan
        try:
            import sounddevice as sd

            self.audio_stream = sd.InputStream(
                samplerate=self.audio_samplerate,
                channels=1,
                dtype="float32",
                device=self.audio_device_index,
                callback=self._audio_callback,
            )
            self.audio_stream.start()
            self.audio_start_perf = time.perf_counter()
            device_text = self.audio_device_name or "microfono predeterminado"
            self.audio_status = f"audio grabando: {device_text}"
            if hasattr(self, "audio_status_label"):
                self.audio_status_label.setText(self.audio_status)
        except Exception as exc:
            self.audio_stream = None
            self.audio_status = f"audio no disponible: {exc}"
            if hasattr(self, "audio_status_label"):
                self.audio_status_label.setText(self.audio_status)
            log.warning("No se pudo iniciar audio en experimento con vacio: %s", exc)

    def stop_audio_recording(self):
        self.audio_stop_perf = time.perf_counter()
        stream = self.audio_stream
        self.audio_stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:
                self.audio_status = f"audio detenido con error: {exc}"
                log.warning("Error deteniendo audio: %s", exc)

        with self.audio_lock:
            frames = list(self.audio_frames)
        if not frames:
            if not self.audio_status.startswith("audio no disponible"):
                self.audio_status = "audio sin muestras"
            return

        samples = np.concatenate(frames).astype(np.float32, copy=False)
        if samples.size == 0:
            self.audio_status = "audio sin muestras"
            return
        peak = float(np.max(np.abs(samples)))
        if peak > 0:
            samples = samples / max(peak, 1.0)
        pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
        self.audio_path = self.audio_dir / f"audio_{self.state.base_name}.wav"
        with wave.open(str(self.audio_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.audio_samplerate)
            wf.writeframes(pcm.tobytes())
        self.audio_status = f"audio guardado ({samples.size / self.audio_samplerate:.1f} s)"
        if hasattr(self, "audio_status_label"):
            self.audio_status_label.setText(self.audio_status)

    def start_normal_capture(self):
        if not self.serial_port or not self.serial_port.is_open:
            QtWidgets.QMessageBox.warning(self, "Serial", "No hay puerto serie abierto.")
            return
        if self.state.capturing:
            return
        self.reset_capture_state(keep_identity=False)
        st = self.state
        st.mode = "experimento_vacio"
        st.requested_duration_s = float(self.duration_spin.value())
        st.crotal_id = sanitize_id(self.crotal_edit.text())
        pulse_prev = self.ensure_initial_pulse_or_confirm()
        if pulse_prev is None:
            return
        st.pulse_prev = pulse_prev
        st.measurement_condition = self.current_condition_text()
        st.config_label = "vacio_audio_notch"
        st.base_name = f"VACIO_{st.crotal_id}_{now_stamp()}"
        st.session_id = st.base_name
        st.capture_start_wall = time.time()
        st.capturing = True
        st.finished = False
        try:
            self.serial_port.reset_input_buffer()
            self.serial_port.reset_output_buffer()
        except Exception:
            pass
        cfg = self.sensor_widget.get_config()
        if not self.confirm_config_before_start(cfg):
            st.capturing = False
            return
        self.open_raw_file()
        self.save_current_config_json(prefix=f"config_{st.base_name}")
        self.start_audio_recording()
        self.ppg_start_perf = time.perf_counter()
        self.send_command("START_CONTINUOUS")
        log.info("Inicio experimento con vacio: %s duracion %.1fs", st.base_name, st.requested_duration_s)

    def stop_capture(self, reason: str):
        st = self.state
        if not st.capturing:
            return
        st.capturing = False
        st.finished = True
        self.ppg_stop_perf = time.perf_counter()
        self.send_command("STOP")
        if st.raw_handle:
            st.raw_handle.flush()
            st.raw_handle.close()
            st.raw_handle = None
            st.raw_writer = None
        self.stop_audio_recording()
        self.finalize_capture(reason)

    def finalize_capture(self, reason: str):
        super().finalize_capture(reason)
        self.generate_vacuum_report(reason)

    def closeEvent(self, event: QtGui.QCloseEvent):
        if self.audio_stream is not None:
            self.stop_audio_recording()
        super().closeEvent(event)

    def update_info(self):
        super().update_info()
        if self.state.capturing or self.audio_status:
            self.info.setText(self.info.text() + f"\nAudio/vacio: {self.audio_status}\n")

    def _load_audio_samples(self) -> tuple[np.ndarray, int]:
        if self.audio_path is None or not self.audio_path.exists():
            return np.asarray([], dtype=float), self.audio_samplerate
        with wave.open(str(self.audio_path), "rb") as wf:
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        data = np.frombuffer(raw, dtype=np.int16).astype(float)
        if data.size:
            data = data / 32768.0
        return data, sr

    def _dominant_band(self, y: np.ndarray, hz: float, low_hz: float, high_hz: float) -> dict[str, float]:
        y = np.asarray(y, dtype=float)
        y = y[np.isfinite(y)]
        if y.size < 32 or not np.isfinite(hz) or hz <= 0:
            return {"hz": math.nan, "bpm": math.nan, "dominance": math.nan, "power_ratio": math.nan}
        y = y - float(np.mean(y))
        sd = float(np.std(y))
        if sd <= 1e-12:
            return {"hz": math.nan, "bpm": math.nan, "dominance": math.nan, "power_ratio": math.nan}
        spec = np.abs(np.fft.rfft(y * np.hanning(y.size)))
        freqs = np.fft.rfftfreq(y.size, d=1.0 / hz)
        band_mask = (freqs >= low_hz) & (freqs <= high_hz)
        if not np.any(band_mask):
            return {"hz": math.nan, "bpm": math.nan, "dominance": math.nan, "power_ratio": math.nan}
        band = spec[band_mask]
        fband = freqs[band_mask]
        if band.size < 2 or float(np.max(band)) <= 0:
            return {"hz": math.nan, "bpm": math.nan, "dominance": math.nan, "power_ratio": math.nan}
        order = np.argsort(band)
        peak = float(band[order[-1]])
        second = float(band[order[-2]]) if band.size > 1 else 0.0
        idx = int(order[-1])
        band_power = float(np.sum(band**2))
        total_power = float(np.sum(spec[(freqs >= low_hz * 0.5) & (freqs <= high_hz * 1.5)] ** 2))
        return {
            "hz": float(fband[idx]),
            "bpm": float(fband[idx] * 60.0),
            "dominance": float(peak / (second + 1e-9)),
            "power_ratio": float(band_power / (total_power + 1e-9)),
        }

    def analyze_audio_vacuum(self) -> dict[str, float | str]:
        audio, sr = self._load_audio_samples()
        if audio.size < sr * 3:
            return {"status": self.audio_status, "vacuum_hz": math.nan, "vacuum_bpm": math.nan, "dominance": math.nan}
        frame = max(1, int(round(0.05 * sr)))
        count = audio.size // frame
        if count < 32:
            return {"status": self.audio_status, "vacuum_hz": math.nan, "vacuum_bpm": math.nan, "dominance": math.nan}
        trimmed = audio[: count * frame].reshape(count, frame)
        envelope = np.sqrt(np.mean(trimmed * trimmed, axis=1))
        env_hz = sr / frame
        dom = self._dominant_band(envelope, env_hz, 0.20, 5.00)
        return {
            "status": self.audio_status,
            "audio_duration_s": float(audio.size / sr),
            "vacuum_hz": dom["hz"],
            "vacuum_bpm": dom["bpm"],
            "dominance": dom["dominance"],
            "power_ratio": dom["power_ratio"],
        }

    def _ppg_fft_bpm(self, t: np.ndarray, ir: np.ndarray, exclude_hz: list[float] | None = None) -> dict[str, float]:
        if t.size < 100:
            return {"bpm": math.nan, "hz": math.nan, "dominance": math.nan}
        cfg = self.analysis_widget.get_config()
        hz = estimate_hz(t)
        sig = processed_ppg(ir, hz, cfg)
        tt, yy, hz_u = uniform_resample(t, sig, hz)
        del tt
        if yy.size < 128:
            return {"bpm": math.nan, "hz": math.nan, "dominance": math.nan}
        yy = yy - float(np.mean(yy))
        spec = np.abs(np.fft.rfft(yy * np.hanning(yy.size)))
        freqs = np.fft.rfftfreq(yy.size, d=1.0 / hz_u)
        mask = (freqs * 60.0 >= cfg.bpm_min) & (freqs * 60.0 <= cfg.bpm_max)
        if exclude_hz:
            for freq in exclude_hz:
                if np.isfinite(freq) and freq > 0:
                    mask &= np.abs(freqs - freq) > 0.08
        if not np.any(mask):
            return {"bpm": math.nan, "hz": math.nan, "dominance": math.nan}
        band = spec[mask]
        fband = freqs[mask]
        order = np.argsort(band)
        peak = float(band[order[-1]])
        second = float(band[order[-2]]) if band.size > 1 else 0.0
        idx = int(order[-1])
        return {"bpm": float(fband[idx] * 60.0), "hz": float(fband[idx]), "dominance": float(peak / (second + 1e-9))}

    def _notch_processed_ir(self, t: np.ndarray, ir: np.ndarray, notch_hz: list[float]) -> tuple[np.ndarray, np.ndarray, float]:
        cfg = self.analysis_widget.get_config()
        hz = estimate_hz(t)
        sig = processed_ppg(ir, hz, cfg)
        tt, yy, hz_u = uniform_resample(t, sig, hz)
        if yy.size < 128:
            return tt, yy, hz_u
        spec = np.fft.rfft((yy - float(np.mean(yy))))
        freqs = np.fft.rfftfreq(yy.size, d=1.0 / hz_u)
        width = max(0.035, 1.5 / max(float(tt[-1] - tt[0]), 1.0))
        for base in notch_hz:
            if not np.isfinite(base) or base <= 0:
                continue
            harmonic = 1
            while base * harmonic <= 6.0:
                spec[np.abs(freqs - base * harmonic) <= width] = 0
                harmonic += 1
        filtered = np.fft.irfft(spec, n=yy.size)
        return tt, filtered, hz_u

    def analyze_ppg_with_notch(self, audio_analysis: dict[str, float | str]) -> dict[str, float | str]:
        t, _red, ir = self.arrays()
        cfg = self.analysis_widget.get_config()
        vacuum_hz = float(audio_analysis.get("vacuum_hz", math.nan))
        before = self._ppg_fft_bpm(t, ir)
        exclude = [vacuum_hz * i for i in range(1, 4) if np.isfinite(vacuum_hz)]
        spectral_excluding_vacuum = self._ppg_fft_bpm(t, ir, exclude_hz=exclude)
        tt, filtered, hz_u = self._notch_processed_ir(t, ir, [vacuum_hz] if np.isfinite(vacuum_hz) else [])
        after = self._dominant_band(filtered, hz_u, cfg.bpm_min / 60.0, cfg.bpm_max / 60.0)
        coincidence_bpm = math.nan
        if np.isfinite(vacuum_hz) and np.isfinite(before["hz"]):
            coincidence_bpm = abs(float(before["hz"]) - vacuum_hz) * 60.0
        return {
            "ppg_bpm_final": float(self.state.metrics.bpm) if np.isfinite(self.state.metrics.bpm) else math.nan,
            "ppg_fft_bpm_before_notch": before["bpm"],
            "ppg_fft_dominance_before_notch": before["dominance"],
            "ppg_fft_bpm_excluding_audio_peak": spectral_excluding_vacuum["bpm"],
            "ppg_fft_bpm_after_notch": after["bpm"],
            "ppg_fft_dominance_after_notch": after["dominance"],
            "audio_ppg_peak_distance_bpm": coincidence_bpm,
        }

    def _sync_metadata(self) -> dict[str, float | str]:
        return {
            "ppg_start_perf": self.ppg_start_perf,
            "ppg_stop_perf": self.ppg_stop_perf,
            "audio_start_perf": self.audio_start_perf,
            "audio_stop_perf": self.audio_stop_perf,
            "audio_minus_ppg_start_s": (
                float(self.audio_start_perf - self.ppg_start_perf)
                if np.isfinite(self.audio_start_perf) and np.isfinite(self.ppg_start_perf)
                else math.nan
            ),
            "created": datetime.now().isoformat(),
        }

    def generate_vacuum_report(self, reason: str):
        st = self.state
        if not st.base_name:
            return
        audio_analysis = self.analyze_audio_vacuum()
        ppg_analysis = self.analyze_ppg_with_notch(audio_analysis)
        payload = {
            "session_id": st.session_id or st.base_name,
            "base_name": st.base_name,
            "id": st.crotal_id,
            "reason": reason,
            "sync": self._sync_metadata(),
            "audio": audio_analysis,
            "audio_device": {
                "index": self.audio_device_index,
                "name": self.audio_device_name,
                "samplerate": self.audio_samplerate,
            },
            "ppg_notch": ppg_analysis,
            "metrics": self._metrics_dict(st.metrics),
            "files": {
                "raw_ppg": str(st.raw_file) if st.raw_file else "",
                "audio_wav": str(self.audio_path) if self.audio_path else "",
                "processed_ppg": str(st.processed_file) if st.processed_file else "",
                "summary": str(st.summary_file) if st.summary_file else "",
            },
        }
        self.vacuum_analysis_file = self.report_dir / f"analisis_vacio_{st.base_name}.json"
        with open(self.vacuum_analysis_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        self.vacuum_report_file = self.report_dir / f"informe_vacio_{st.base_name}.html"
        html_text = self._report_html(payload)
        self.vacuum_report_file.write_text(html_text, encoding="utf-8")
        self.vacuum_report_pdf = self.report_dir / f"informe_vacio_{st.base_name}.pdf"
        self._write_pdf(html_text, self.vacuum_report_pdf)

    def _metrics_dict(self, m: Metrics) -> dict[str, float | str | int]:
        return {
            "bpm": float(m.bpm) if np.isfinite(m.bpm) else math.nan,
            "bpm_peak": float(m.bpm_peak) if np.isfinite(m.bpm_peak) else math.nan,
            "bpm_fft": float(m.bpm_fft) if np.isfinite(m.bpm_fft) else math.nan,
            "bpm_autocorr": float(m.bpm_autocorr) if np.isfinite(m.bpm_autocorr) else math.nan,
            "quality": float(m.quality),
            "quality_label": m.quality_label,
            "spo2": float(m.spo2) if np.isfinite(m.spo2) else math.nan,
            "ratio_r": float(m.ratio_r) if np.isfinite(m.ratio_r) else math.nan,
            "pi_ir_pct": float(m.pi_ir_pct) if np.isfinite(m.pi_ir_pct) else math.nan,
            "pi_red_pct": float(m.pi_red_pct) if np.isfinite(m.pi_red_pct) else math.nan,
            "artifact_ir_pct": float(m.artifact_ir_pct) if np.isfinite(m.artifact_ir_pct) else math.nan,
            "contact_label": m.contact_label,
            "reason": m.reason,
        }

    def _report_html(self, payload: dict) -> str:
        audio = payload["audio"]
        audio_device = payload.get("audio_device") or {}
        ppg = payload["ppg_notch"]
        sync = payload["sync"]
        metrics = payload["metrics"]
        files = payload["files"]
        conclusion = self._report_conclusion(audio, ppg, metrics)
        rows = [
            ("BPM final PPG", fmt(metrics.get("bpm"), 1, "-")),
            ("Calidad PPG", f"{fmt(metrics.get('quality'), 1, '-')} ({html.escape(str(metrics.get('quality_label', '-')))})"),
            ("SpO2", fmt(metrics.get("spo2"), 1, "-")),
            ("Frecuencia vacio audio", f"{fmt(audio.get('vacuum_bpm'), 1, '-')} ciclos/min"),
            ("Microfono", str(audio_device.get("name") or "-")),
            ("Dominancia audio", fmt(audio.get("dominance"), 2, "-")),
            ("FFT PPG antes de notch", fmt(ppg.get("ppg_fft_bpm_before_notch"), 1, "-")),
            ("FFT PPG excluyendo pico audio", fmt(ppg.get("ppg_fft_bpm_excluding_audio_peak"), 1, "-")),
            ("FFT PPG despues de notch", fmt(ppg.get("ppg_fft_bpm_after_notch"), 1, "-")),
            ("Desfase inicio audio-PPG", f"{fmt(sync.get('audio_minus_ppg_start_s'), 4, '-')} s"),
        ]
        table = "\n".join(f"<tr><th>{html.escape(k)}</th><td>{v}</td></tr>" for k, v in rows)
        file_rows = "\n".join(
            f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>" for k, v in files.items()
        )
        return f"""<!doctype html>
<html><head><meta charset="utf-8">
<style>
body {{ font-family: Arial, sans-serif; margin: 28px; color: #222; }}
h1 {{ font-size: 22px; margin-bottom: 4px; }}
h2 {{ font-size: 16px; margin-top: 22px; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
th, td {{ border: 1px solid #ccc; padding: 7px; text-align: left; font-size: 12px; }}
th {{ width: 34%; background: #f2f2f2; }}
p, li {{ font-size: 12px; line-height: 1.45; }}
.note {{ border-left: 4px solid #777; padding-left: 10px; }}
</style></head><body>
<h1>Informe experimento con vacio</h1>
<p><b>Sesion:</b> {html.escape(str(payload["base_name"]))}</p>
<p><b>Animal:</b> {html.escape(str(payload["id"]))} | <b>Fin:</b> {html.escape(str(payload["reason"]))}</p>
<h2>Lectura resumida</h2>
<p class="note">{html.escape(conclusion)}</p>
<h2>Resultados</h2>
<table>{table}</table>
<h2>Metodo aplicado</h2>
<p>El microfono se graba en paralelo con la toma PPG. Se guarda un sello de tiempo de inicio de audio y de PPG para estimar el desfase.</p>
<p>El audio se analiza al final con Fourier sobre la envolvente RMS para buscar la frecuencia dominante del vacio. El notch se aplica solo en post-proceso sobre la senal IR procesada y sus armonicos; no modifica el raw PPG.</p>
<p>La prioridad de decision sigue siendo BPM. SpO2 queda como dato secundario y solo se interpreta si hay apoyo suficiente, PI RED/IR estable y ratio RED/IR razonable.</p>
<h2>Archivos</h2>
<table>{file_rows}</table>
</body></html>"""

    def _report_conclusion(self, audio: dict, ppg: dict, metrics: dict) -> str:
        bpm = metrics.get("bpm", math.nan)
        before = ppg.get("ppg_fft_bpm_before_notch", math.nan)
        after = ppg.get("ppg_fft_bpm_after_notch", math.nan)
        vacuum = audio.get("vacuum_bpm", math.nan)
        distance = ppg.get("audio_ppg_peak_distance_bpm", math.nan)
        if not np.isfinite(vacuum):
            return "No se pudo estimar una frecuencia de vacio con audio; usa el BPM PPG final y revisa colocacion/senal."
        if np.isfinite(distance) and distance <= 6:
            return (
                "El pico dominante PPG esta muy cerca de la frecuencia detectada en audio. "
                "El BPM despues de notch debe revisarse como candidato, pero no sustituye automaticamente el BPM final."
            )
        if np.isfinite(after) and np.isfinite(before) and abs(after - before) > 8:
            return (
                "El notch cambia de forma apreciable el pico FFT PPG; revisar el BPM post-notch como posible pulso animal "
                "si coincide con picos/autocorrelacion y referencia manual."
            )
        if np.isfinite(bpm):
            return "El BPM PPG final parece usable; el notch no detecta una interferencia dominante que desplace claramente el pulso."
        return "No hay BPM PPG final fiable; revisar apoyo del sensor y repetir con mas estabilidad."

    def _write_pdf(self, html_text: str, path: Path):
        try:
            document = QtGui.QTextDocument()
            document.setHtml(html_text)
            writer = QtGui.QPdfWriter(str(path))
            writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.PageSizeId.A4))
            writer.setResolution(96)
            document.print(writer)
        except Exception as exc:
            log.warning("No se pudo generar PDF experimento con vacio %s: %s", path, exc)
