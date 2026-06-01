from __future__ import annotations

import csv
import html
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from ..models import AnalysisConfig
from ..paths import RAW_DIR
from ..processing import (
    detect_artifacts,
    estimate_bpm_autocorr,
    estimate_hz,
    processed_ppg,
    replace_nan_with_last,
    uniform_resample,
)
from ..utils import fmt


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    return [{str(k or "").strip(): str(v or "").strip() for k, v in row.items()} for row in csv.DictReader(text.splitlines(), dialect=dialect)]


def _as_float(value: str) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return math.nan


def _as_int(value: str) -> int:
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return 0


@dataclass
class RawFileInfo:
    path: Path
    rows: int
    animal: str = ""
    date: str = ""
    config_summary: str = ""


@dataclass
class SpectrumResult:
    file: Path
    animal: str
    base_name: str
    config_label: str
    cfg_red: int
    cfg_ir: int
    cfg_avg: int
    cfg_rate: int
    cfg_width: int
    cfg_adc: int
    n: int
    duration_s: float
    hz: float
    hz_jitter_pct: float
    bpm_fft_ir: float
    bpm_fft_red: float
    bpm_autocorr: float
    dominance_ir: float
    band_ratio_ir: float
    peak_snr_db: float
    entropy_ir: float
    pi_ir_pct: float
    pi_red_pct: float
    artifact_ir_pct: float
    artifact_red_pct: float
    saturation_pct: float
    agreement_bpm: float
    score: float
    verdict: str
    reasons: list[str] = field(default_factory=list)
    freqs_bpm: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    spectrum_ir: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))


def _safe_percent(mask: np.ndarray) -> float:
    return float(np.mean(mask) * 100.0) if mask.size else math.nan


def _fft_details(t: np.ndarray, y: np.ndarray, cfg: AnalysisConfig) -> dict[str, float | np.ndarray]:
    hz = estimate_hz(t)
    sig = processed_ppg(y, hz, cfg)
    tt, yy, hz_u = uniform_resample(t, sig, hz)
    if yy.size < max(128, int(4 * hz_u)):
        return {"bpm": math.nan, "quality": 0.0, "reason": "ventana corta", "freqs_bpm": np.asarray([]), "spectrum": np.asarray([])}
    yy = yy - float(np.mean(yy))
    sd = float(np.std(yy))
    if sd <= 1e-9:
        return {"bpm": math.nan, "quality": 0.0, "reason": "sin variabilidad", "freqs_bpm": np.asarray([]), "spectrum": np.asarray([])}
    win = np.hanning(yy.size)
    spectrum = np.abs(np.fft.rfft(yy * win))
    freqs = np.fft.rfftfreq(yy.size, d=1.0 / hz_u)
    fmin = cfg.bpm_min / 60.0
    fmax = cfg.bpm_max / 60.0
    physiologic = (freqs >= fmin) & (freqs <= fmax)
    useful = (freqs >= 0.2) & (freqs <= 8.0)
    if not np.any(physiologic):
        return {"bpm": math.nan, "quality": 0.0, "reason": "sin banda cardiaca", "freqs_bpm": freqs * 60.0, "spectrum": spectrum}

    band = spectrum[physiologic]
    fband = freqs[physiologic]
    if band.size < 3 or float(np.max(band)) <= 0:
        return {"bpm": math.nan, "quality": 0.0, "reason": "sin pico FFT", "freqs_bpm": freqs * 60.0, "spectrum": spectrum}

    idx = int(np.argmax(band))
    bpm = float(fband[idx] * 60.0)
    peak = float(band[idx])
    sorted_band = np.sort(band)
    second = float(sorted_band[-2]) if sorted_band.size > 1 else 0.0
    dominance = peak / (second + 1e-9)

    band_power = float(np.sum(band**2))
    useful_power = float(np.sum(spectrum[useful] ** 2)) if np.any(useful) else band_power
    band_ratio = band_power / (useful_power + 1e-9)

    lo = max(0, idx - 2)
    hi = min(band.size, idx + 3)
    noise = np.concatenate([band[:lo], band[hi:]])
    noise_floor = float(np.median(noise)) if noise.size else 0.0
    snr_db = float(20.0 * math.log10((peak + 1e-9) / (noise_floor + 1e-9)))

    power = band**2
    prob = power / (float(np.sum(power)) + 1e-12)
    entropy = float(-np.sum(prob * np.log(prob + 1e-12)) / math.log(max(2, prob.size)))

    quality = float(np.clip(25.0 * min(dominance, 4.0) / 4.0 + 35.0 * band_ratio + 25.0 * np.clip(snr_db / 18.0, 0, 1) + 15.0 * (1.0 - entropy), 0.0, 100.0))
    return {
        "bpm": bpm,
        "quality": quality,
        "reason": f"dominancia={dominance:.2f}; banda={band_ratio:.2f}; snr={snr_db:.1f} dB",
        "dominance": dominance,
        "band_ratio": band_ratio,
        "snr_db": snr_db,
        "entropy": entropy,
        "freqs_bpm": freqs * 60.0,
        "spectrum": spectrum,
    }


def _ac_dc_pi(t: np.ndarray, y: np.ndarray, cfg: AnalysisConfig) -> tuple[float, float, float]:
    if y.size < 20:
        return math.nan, math.nan, math.nan
    yy = replace_nan_with_last(y)
    dc = float(np.mean(yy))
    ac_sig = processed_ppg(yy, estimate_hz(t), cfg)
    ac = float(np.sqrt(np.mean(ac_sig**2)))
    pi = float((ac / dc) * 100.0) if dc > 0 else math.nan
    return ac, dc, pi


def _group_rows(rows: list[dict[str, str]]) -> list[list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        key = "|".join([
            row.get("base_name", ""),
            row.get("config_label", ""),
            row.get("cfg_red", ""),
            row.get("cfg_ir", ""),
            row.get("cfg_avg", ""),
            row.get("cfg_adc", ""),
        ])
        groups.setdefault(key, []).append(row)
    return list(groups.values())


def analyze_raw_file(path: Path, cfg: AnalysisConfig) -> list[SpectrumResult]:
    rows = _read_csv(path)
    results: list[SpectrumResult] = []
    for group in _group_rows(rows):
        if len(group) < 40:
            continue
        t = np.asarray([_as_float(r.get("tiempo_s", "")) for r in group], dtype=float)
        red = np.asarray([_as_float(r.get("red_raw", "")) for r in group], dtype=float)
        ir = np.asarray([_as_float(r.get("ir_raw", "")) for r in group], dtype=float)
        mask = np.isfinite(t) & np.isfinite(ir) & np.isfinite(red)
        t, red, ir = t[mask], red[mask], ir[mask]
        if t.size < 40:
            continue
        t = t - float(t[0])
        row0 = group[0]
        hz = estimate_hz(t)
        duration = float(t[-1] - t[0]) if t.size > 1 else math.nan
        diffs = np.diff(t)
        diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
        hz_jitter = math.nan
        if diffs.size > 3:
            hz_jitter = float(np.std(diffs) / np.mean(diffs) * 100.0)

        fft_ir = _fft_details(t, ir, cfg)
        fft_red = _fft_details(t, red, cfg)
        bpm_ac, q_ac, reason_ac = estimate_bpm_autocorr(t, ir, cfg)
        _ac_ir, dc_ir, pi_ir = _ac_dc_pi(t, ir, cfg)
        _ac_red, dc_red, pi_red = _ac_dc_pi(t, red, cfg)
        art_ir = detect_artifacts(ir)
        art_red = detect_artifacts(red)
        adc = _as_int(row0.get("cfg_adc", "0"))
        raw_digital_ceiling = 262143.0
        limit = raw_digital_ceiling * 0.98
        saturation = _safe_percent(np.concatenate([ir >= limit, red >= limit]))

        bpm_ir = float(fft_ir.get("bpm", math.nan))
        bpm_red = float(fft_red.get("bpm", math.nan))
        candidates = [v for v in [bpm_ir, bpm_red, bpm_ac] if np.isfinite(v)]
        agreement = float(np.max(candidates) - np.min(candidates)) if len(candidates) >= 2 else math.nan

        score = float(fft_ir.get("quality", 0.0))
        reasons: list[str] = []
        if np.isfinite(bpm_ir):
            reasons.append(f"FFT IR detecta {bpm_ir:.1f} BPM")
        else:
            reasons.append(str(fft_ir.get("reason", "FFT IR no valido")))
        if np.isfinite(bpm_red):
            reasons.append(f"FFT RED detecta {bpm_red:.1f} BPM")
        if np.isfinite(bpm_ac):
            reasons.append(f"autocorrelacion IR {bpm_ac:.1f} BPM ({reason_ac})")

        if np.isfinite(agreement):
            if agreement <= 8:
                score += 12
                reasons.append(f"buen acuerdo entre estimadores: diferencia maxima {agreement:.1f} BPM")
            elif agreement <= 18:
                score += 4
                reasons.append(f"acuerdo moderado entre estimadores: diferencia {agreement:.1f} BPM")
            else:
                score -= 18
                reasons.append(f"estimadores discrepantes: diferencia {agreement:.1f} BPM")

        if np.isfinite(pi_ir):
            if pi_ir >= 0.20:
                score += 8
                reasons.append(f"PI IR util ({pi_ir:.3f} %): hay componente pulsatile medible")
            elif pi_ir >= 0.08:
                score += 2
                reasons.append(f"PI IR bajo pero aprovechable ({pi_ir:.3f} %)")
            else:
                score -= 12
                reasons.append(f"PI IR muy bajo ({pi_ir:.3f} %): pulso poco visible")

        artifact = _safe_percent(art_ir)
        if np.isfinite(artifact):
            score -= min(25.0, artifact * 1.8)
            reasons.append(f"artefactos IR {artifact:.1f} %")
        if np.isfinite(saturation) and saturation > 0:
            score -= min(35.0, saturation * 2.0)
            reasons.append(f"saturacion cerca del techo ADC {saturation:.1f} %")
        if np.isfinite(hz_jitter):
            if hz_jitter <= 10:
                score += 5
                reasons.append(f"muestreo estable (jitter {hz_jitter:.1f} %)")
            else:
                score -= min(15.0, (hz_jitter - 10.0) * 0.8)
                reasons.append(f"muestreo irregular (jitter {hz_jitter:.1f} %)")
        if np.isfinite(duration) and duration < 8:
            score -= 15
            reasons.append(f"duracion corta ({duration:.1f} s): peor resolucion en frecuencia")

        score = float(np.clip(score, 0.0, 100.0))
        if score >= 75:
            verdict = "mejor candidata"
        elif score >= 58:
            verdict = "buena"
        elif score >= 42:
            verdict = "usable con cautela"
        else:
            verdict = "no recomendable"

        results.append(SpectrumResult(
            file=path,
            animal=row0.get("id", ""),
            base_name=row0.get("base_name", path.stem),
            config_label=row0.get("config_label", ""),
            cfg_red=_as_int(row0.get("cfg_red", "0")),
            cfg_ir=_as_int(row0.get("cfg_ir", "0")),
            cfg_avg=_as_int(row0.get("cfg_avg", "0")),
            cfg_rate=_as_int(row0.get("cfg_rate", "0")),
            cfg_width=_as_int(row0.get("cfg_width", "0")),
            cfg_adc=adc,
            n=int(t.size),
            duration_s=duration,
            hz=hz,
            hz_jitter_pct=hz_jitter,
            bpm_fft_ir=bpm_ir,
            bpm_fft_red=bpm_red,
            bpm_autocorr=bpm_ac,
            dominance_ir=float(fft_ir.get("dominance", math.nan)),
            band_ratio_ir=float(fft_ir.get("band_ratio", math.nan)),
            peak_snr_db=float(fft_ir.get("snr_db", math.nan)),
            entropy_ir=float(fft_ir.get("entropy", math.nan)),
            pi_ir_pct=pi_ir,
            pi_red_pct=pi_red,
            artifact_ir_pct=artifact,
            artifact_red_pct=_safe_percent(art_red),
            saturation_pct=saturation,
            agreement_bpm=agreement,
            score=score,
            verdict=verdict,
            reasons=reasons,
            freqs_bpm=np.asarray(fft_ir.get("freqs_bpm", np.asarray([])), dtype=float),
            spectrum_ir=np.asarray(fft_ir.get("spectrum", np.asarray([])), dtype=float),
        ))
    return results


class FourierAnalysisWindow(QtWidgets.QMainWindow):
    back_to_menu = QtCore.pyqtSignal()

    result_headers = [
        "Puntuacion", "Veredicto", "Animal", "Configuracion", "BPM FFT IR", "BPM FFT RED",
        "BPM autocorr", "Dominancia", "Banda", "SNR dB", "PI IR %", "Artefactos %",
        "Saturacion %", "Jitter %", "Duracion", "Muestras", "Archivo",
    ]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PPG Suite v8 | Analisis experimental de Fourier")
        self.resize(1420, 900)
        self.raw_files: list[RawFileInfo] = []
        self.results: list[SpectrumResult] = []
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
        title = QtWidgets.QLabel("Analisis experimental de Fourier")
        title.setStyleSheet("font-size: 14pt; font-weight: bold;")
        top.addWidget(title)
        top.addStretch(1)
        self.btn_reload = QtWidgets.QPushButton("Recargar raws")
        self.btn_reload.clicked.connect(self.reload_raws)
        top.addWidget(self.btn_reload)

        controls = QtWidgets.QGroupBox("Seleccion de raws")
        cl = QtWidgets.QGridLayout(controls)
        self.animal_filter = QtWidgets.QComboBox()
        self.text_filter = QtWidgets.QLineEdit()
        self.text_filter.setPlaceholderText("Filtrar por archivo, sesion o configuracion")
        self.btn_select_visible = QtWidgets.QPushButton("Marcar visibles")
        self.btn_clear = QtWidgets.QPushButton("Desmarcar")
        self.btn_analyze = QtWidgets.QPushButton("Analizar seleccionados")
        self.btn_analyze.setMinimumHeight(36)
        self.btn_analyze.setStyleSheet("font-weight: bold;")
        cl.addWidget(QtWidgets.QLabel("Animal"), 0, 0)
        cl.addWidget(self.animal_filter, 0, 1)
        cl.addWidget(QtWidgets.QLabel("Texto"), 0, 2)
        cl.addWidget(self.text_filter, 0, 3)
        cl.addWidget(self.btn_select_visible, 0, 4)
        cl.addWidget(self.btn_clear, 0, 5)
        cl.addWidget(self.btn_analyze, 0, 6)
        root.addWidget(controls)
        self.animal_filter.currentTextChanged.connect(self.apply_filters)
        self.text_filter.textChanged.connect(self.apply_filters)
        self.btn_select_visible.clicked.connect(self.select_visible)
        self.btn_clear.clicked.connect(self.clear_selection)
        self.btn_analyze.clicked.connect(self.analyze_selected)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        root.addWidget(splitter, stretch=1)

        self.raw_table = QtWidgets.QTableWidget(0, 6)
        self.raw_table.setHorizontalHeaderLabels(["Usar", "Animal", "Fecha", "Filas", "Configuraciones", "Archivo"])
        self.raw_table.verticalHeader().setVisible(False)
        self.raw_table.setAlternatingRowColors(True)
        self.raw_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        splitter.addWidget(self.raw_table)

        results_page = QtWidgets.QWidget()
        results_layout = QtWidgets.QVBoxLayout(results_page)
        self.results_table = QtWidgets.QTableWidget(0, len(self.result_headers))
        self.results_table.setHorizontalHeaderLabels(self.result_headers)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.currentCellChanged.connect(self.plot_current_result)
        results_layout.addWidget(self.results_table, stretch=2)

        bottom_split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        results_layout.addWidget(bottom_split, stretch=3)
        self.details = QtWidgets.QTextEdit()
        self.details.setReadOnly(True)
        bottom_split.addWidget(self.details)
        self.plot = pg.PlotWidget(title="Espectro IR normalizado")
        self.plot.setBackground("w")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "Frecuencia", units="BPM")
        self.plot.setLabel("left", "Magnitud normalizada")
        bottom_split.addWidget(self.plot)
        bottom_split.setSizes([620, 760])
        splitter.addWidget(results_page)
        splitter.setSizes([260, 600])

    def reload_raws(self):
        infos: list[RawFileInfo] = []
        for path in sorted(RAW_DIR.glob("raw_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True):
            rows = _read_csv(path)
            if not rows:
                continue
            configs = sorted({r.get("config_label", "") for r in rows if r.get("config_label", "")})
            animals = sorted({r.get("id", "") for r in rows if r.get("id", "")})
            date = rows[0].get("system_time", "")[:19].replace("T", " ")
            infos.append(RawFileInfo(
                path=path,
                rows=len(rows),
                animal=", ".join(animals) or "-",
                date=date,
                config_summary=f"{len(configs)} config." if len(configs) > 3 else ", ".join(configs),
            ))
        self.raw_files = infos
        current = self.animal_filter.currentText()
        animals = sorted({animal for info in infos for animal in [a.strip() for a in info.animal.split(",")] if animal and animal != "-"})
        self.animal_filter.blockSignals(True)
        self.animal_filter.clear()
        self.animal_filter.addItem("Todos")
        self.animal_filter.addItems(animals)
        self.animal_filter.setCurrentText(current if current in ["Todos", *animals] else "Todos")
        self.animal_filter.blockSignals(False)
        self.apply_filters()

    def apply_filters(self):
        animal = self.animal_filter.currentText()
        text = self.text_filter.text().strip().lower()
        self.raw_table.setRowCount(0)
        for info in self.raw_files:
            haystack = f"{info.path.name} {info.animal} {info.date} {info.config_summary}".lower()
            if animal != "Todos" and animal not in info.animal:
                continue
            if text and text not in haystack:
                continue
            row = self.raw_table.rowCount()
            self.raw_table.insertRow(row)
            check = QtWidgets.QTableWidgetItem("")
            check.setFlags(check.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(QtCore.Qt.CheckState.Unchecked)
            check.setData(QtCore.Qt.ItemDataRole.UserRole, str(info.path))
            self.raw_table.setItem(row, 0, check)
            values = [info.animal, info.date, str(info.rows), info.config_summary, info.path.name]
            for col, value in enumerate(values, start=1):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(str(info.path))
                self.raw_table.setItem(row, col, item)
        self.raw_table.resizeColumnsToContents()

    def selected_paths(self) -> list[Path]:
        paths: list[Path] = []
        for row in range(self.raw_table.rowCount()):
            item = self.raw_table.item(row, 0)
            if item and item.checkState() == QtCore.Qt.CheckState.Checked:
                paths.append(Path(item.data(QtCore.Qt.ItemDataRole.UserRole)))
        return paths

    def select_visible(self):
        for row in range(self.raw_table.rowCount()):
            item = self.raw_table.item(row, 0)
            if item:
                item.setCheckState(QtCore.Qt.CheckState.Checked)

    def clear_selection(self):
        for row in range(self.raw_table.rowCount()):
            item = self.raw_table.item(row, 0)
            if item:
                item.setCheckState(QtCore.Qt.CheckState.Unchecked)

    def analyze_selected(self):
        paths = self.selected_paths()
        if not paths:
            QtWidgets.QMessageBox.information(self, "Analisis Fourier", "Marca uno o varios raw antes de analizar.")
            return
        cfg = AnalysisConfig()
        results: list[SpectrumResult] = []
        for path in paths:
            results.extend(analyze_raw_file(path, cfg))
        results.sort(key=lambda r: r.score, reverse=True)
        self.results = results
        self.populate_results()
        if not results:
            self.details.setHtml("<h2>Sin resultados</h2><p>No se encontraron tramos suficientes para analizar.</p>")
            self.plot.clear()

    def populate_results(self):
        self.results_table.setRowCount(0)
        for result in self.results:
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            values = [
                fmt(result.score, 1, ""), result.verdict, result.animal, result.config_label,
                fmt(result.bpm_fft_ir, 1, ""), fmt(result.bpm_fft_red, 1, ""), fmt(result.bpm_autocorr, 1, ""),
                fmt(result.dominance_ir, 2, ""), fmt(result.band_ratio_ir, 3, ""), fmt(result.peak_snr_db, 1, ""),
                fmt(result.pi_ir_pct, 3, ""), fmt(result.artifact_ir_pct, 1, ""), fmt(result.saturation_pct, 1, ""),
                fmt(result.hz_jitter_pct, 1, ""), fmt(result.duration_s, 1, ""), str(result.n), result.file.name,
            ]
            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if col == 0:
                    item.setData(QtCore.Qt.ItemDataRole.UserRole, row)
                if result.score >= 75:
                    item.setBackground(QtGui.QColor("#d8f3dc"))
                elif result.score < 42:
                    item.setBackground(QtGui.QColor("#ffd6d6"))
                self.results_table.setItem(row, col, item)
        self.results_table.resizeColumnsToContents()
        if self.results:
            self.results_table.selectRow(0)
            self.show_details(self.results[0])
            self.plot_result(self.results[0])

    def plot_current_result(self, current_row: int, _current_col: int, _previous_row: int, _previous_col: int):
        if 0 <= current_row < len(self.results):
            result = self.results[current_row]
            self.show_details(result)
            self.plot_result(result)

    def plot_result(self, result: SpectrumResult):
        self.plot.clear()
        if result.freqs_bpm.size == 0 or result.spectrum_ir.size == 0:
            return
        mask = (result.freqs_bpm >= 20) & (result.freqs_bpm <= 240)
        x = result.freqs_bpm[mask]
        y = result.spectrum_ir[mask]
        if y.size and float(np.max(y)) > 0:
            y = y / float(np.max(y))
        self.plot.plot(x, y, pen=pg.mkPen((0, 80, 220), width=2))
        if np.isfinite(result.bpm_fft_ir):
            line = pg.InfiniteLine(pos=result.bpm_fft_ir, angle=90, pen=pg.mkPen((220, 40, 35), width=2))
            self.plot.addItem(line)

    def show_details(self, result: SpectrumResult):
        reasons = "".join(f"<li>{html.escape(reason)}</li>" for reason in result.reasons)
        best = self.results[0] if self.results else None
        comparison = ""
        if best and best is not result:
            comparison = (
                f"<p><b>Comparacion con la mejor:</b> esta configuracion queda "
                f"{best.score - result.score:.1f} puntos por debajo de "
                f"{html.escape(best.config_label)}.</p>"
            )
        elif best is result:
            comparison = "<p><b>Resultado:</b> es la mejor candidata dentro de los raw seleccionados.</p>"
        html_text = f"""
        <h2>{html.escape(result.config_label or result.base_name)}</h2>
        <p><b>Puntuacion experimental:</b> {fmt(result.score, 1, '-')} / 100 | <b>{html.escape(result.verdict)}</b></p>
        {comparison}
        <table cellspacing="7">
        <tr><td><b>Archivo</b></td><td>{html.escape(result.file.name)}</td></tr>
        <tr><td><b>Animal</b></td><td>{html.escape(result.animal)}</td></tr>
        <tr><td><b>Sensor</b></td><td>RED {result.cfg_red} | IR {result.cfg_ir} | AVG {result.cfg_avg} | RATE {result.cfg_rate} | WIDTH {result.cfg_width} | ADC {result.cfg_adc}</td></tr>
        <tr><td><b>Muestras</b></td><td>{result.n} en {fmt(result.duration_s, 2, '-')} s; Hz real {fmt(result.hz, 2, '-')}</td></tr>
        <tr><td><b>Fourier IR</b></td><td>{fmt(result.bpm_fft_ir, 1, '-')} BPM; dominancia {fmt(result.dominance_ir, 2, '-')}; banda cardiaca {fmt(result.band_ratio_ir, 3, '-')}; SNR {fmt(result.peak_snr_db, 1, '-')} dB; entropia {fmt(result.entropy_ir, 3, '-')}</td></tr>
        <tr><td><b>Acuerdo</b></td><td>FFT RED {fmt(result.bpm_fft_red, 1, '-')} BPM; autocorrelacion {fmt(result.bpm_autocorr, 1, '-')} BPM; diferencia maxima {fmt(result.agreement_bpm, 1, '-')} BPM</td></tr>
        <tr><td><b>Senal</b></td><td>PI IR {fmt(result.pi_ir_pct, 3, '-')} %; PI RED {fmt(result.pi_red_pct, 3, '-')} %; artefactos IR {fmt(result.artifact_ir_pct, 1, '-')} %; saturacion {fmt(result.saturation_pct, 1, '-')} %</td></tr>
        </table>
        <h3>Por que puntua asi</h3>
        <ul>{reasons}</ul>
        <p><b>Lectura rigurosa:</b> Fourier solo mide periodicidad espectral. Una configuracion es preferible si concentra energia en la banda cardiaca esperada, tiene un pico dominante y estrecho, coincide con RED/autocorrelacion, evita saturacion ADC y mantiene suficiente componente pulsatile. No sustituye validacion con pulso de referencia.</p>
        """
        self.details.setHtml(html_text)

    def closeEvent(self, event: QtGui.QCloseEvent):
        event.accept()
