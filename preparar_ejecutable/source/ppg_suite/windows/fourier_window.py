from __future__ import annotations

import csv
import html
import json
import math
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from ..models import AnalysisConfig
from ..paths import DOCUMENTS_DIR, RAW_DIR, REPORT_DIR
from ..processing import (
    detect_artifacts,
    estimate_bpm_autocorr,
    estimate_respiration,
    estimate_spo2,
    estimate_hz,
    processed_ppg,
    replace_nan_with_last,
    saturation_percent,
    uniform_resample,
)
from ..utils import fmt, now_stamp, open_folder


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


def _as_ref_pulse(value: object) -> float:
    bpm = _as_float(str(value if value is not None else ""))
    if np.isfinite(bpm) and bpm > 0:
        return bpm
    return math.nan


def _mean_ref_pulse(*values: object) -> tuple[float, int]:
    valid = [_as_ref_pulse(value) for value in values]
    valid = [value for value in valid if np.isfinite(value)]
    if not valid:
        return math.nan, 0
    return float(np.mean(valid)), len(valid)


def _as_int(value: str) -> int:
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return 0


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _load_manual_refs(path: Path, row0: dict[str, str]) -> tuple[float, float, float, float, int]:
    pulse_prev = _as_ref_pulse(row0.get("pulso_previo", ""))
    pulse_pulsio = _as_ref_pulse(row0.get("pulso_final_pulsio", ""))
    pulse_fonendo = _as_ref_pulse(row0.get("pulso_final_fonendo", ""))
    if not (np.isfinite(pulse_prev) or np.isfinite(pulse_pulsio) or np.isfinite(pulse_fonendo)):
        base_name = row0.get("base_name", path.stem.removeprefix("raw_"))
        candidates = [
            REPORT_DIR / f"summary_{base_name}.json",
            path.parent.parent / "reports" / f"summary_{base_name}.json",
            path.parent / f"summary_{base_name}.json",
        ]
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            manual = data.get("manual_reference", {}) if isinstance(data, dict) else {}
            pulse_prev = _as_ref_pulse(manual.get("pulso_previo"))
            pulse_pulsio = _as_ref_pulse(manual.get("pulso_final_pulsio"))
            pulse_fonendo = _as_ref_pulse(manual.get("pulso_final_fonendo"))
            break
    avg, count = _mean_ref_pulse(pulse_prev, pulse_pulsio, pulse_fonendo)
    return pulse_prev, pulse_pulsio, pulse_fonendo, avg, count


def _ref_diff(value: float, reference: float) -> float:
    if np.isfinite(value) and np.isfinite(reference):
        return float(abs(value - reference))
    return math.nan


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
    bpm_hilbert_ir: float
    pulse_prev_ref: float
    pulse_final_pulsio_ref: float
    pulse_final_fonendo_ref: float
    pulse_ref_avg: float
    pulse_ref_count: int
    diff_fft_ref_bpm: float
    diff_autocorr_ref_bpm: float
    diff_hilbert_ref_bpm: float
    hilbert_envelope_cv_pct: float
    hilbert_phase_iqr_bpm: float
    hilbert_quality: float
    hilbert_reason: str
    dominance_ir: float
    band_ratio_ir: float
    peak_snr_db: float
    entropy_ir: float
    spo2_est_pct: float
    spo2_ratio_r: float
    spo2_quality: float
    spo2_reason: str
    resp_rate_rpm: float
    resp_quality: float
    resp_reason: str
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

    @property
    def best_bpm_estimate(self) -> float:
        for value in (self.bpm_fft_ir, self.bpm_autocorr, self.bpm_hilbert_ir, self.bpm_fft_red):
            if np.isfinite(value):
                return value
        return math.nan


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


def _analytic_signal(y: np.ndarray) -> np.ndarray:
    n = y.size
    if n < 2:
        return y.astype(complex)
    spectrum = np.fft.fft(y)
    h = np.zeros(n)
    if n % 2 == 0:
        h[0] = 1.0
        h[n // 2] = 1.0
        h[1:n // 2] = 2.0
    else:
        h[0] = 1.0
        h[1:(n + 1) // 2] = 2.0
    return np.fft.ifft(spectrum * h)


def _hilbert_details(t: np.ndarray, y: np.ndarray, cfg: AnalysisConfig) -> dict[str, float | str]:
    hz = estimate_hz(t)
    sig = processed_ppg(y, hz, cfg)
    _tt, yy, hz_u = uniform_resample(t, sig, hz)
    if yy.size < max(128, int(4 * hz_u)):
        return {"bpm": math.nan, "envelope_cv_pct": math.nan, "phase_iqr_bpm": math.nan, "quality": 0.0, "reason": "ventana corta"}
    yy = yy - float(np.mean(yy))
    sd = float(np.std(yy))
    if sd <= 1e-9:
        return {"bpm": math.nan, "envelope_cv_pct": math.nan, "phase_iqr_bpm": math.nan, "quality": 0.0, "reason": "sin variabilidad"}

    analytic = _analytic_signal(yy)
    envelope = np.abs(analytic)
    env_mean = float(np.mean(envelope))
    envelope_cv = float(np.std(envelope) / (env_mean + 1e-9) * 100.0)

    phase = np.unwrap(np.angle(analytic))
    inst_bpm = np.diff(phase) * hz_u * 60.0 / (2.0 * math.pi)
    valid = inst_bpm[np.isfinite(inst_bpm) & (inst_bpm >= cfg.bpm_min) & (inst_bpm <= cfg.bpm_max)]
    valid_ratio = float(valid.size / max(1, inst_bpm.size))
    if valid.size >= max(12, int(0.20 * inst_bpm.size)):
        bpm = float(np.median(valid))
        phase_iqr = float(np.percentile(valid, 75) - np.percentile(valid, 25))
    else:
        bpm = math.nan
        phase_iqr = math.nan

    env_score = float(np.clip(100.0 - envelope_cv * 1.25, 0.0, 100.0))
    phase_score = 0.0 if not np.isfinite(phase_iqr) else float(np.clip(100.0 - phase_iqr * 2.0, 0.0, 100.0))
    quality = float(np.clip(0.45 * env_score + 0.40 * phase_score + 15.0 * valid_ratio, 0.0, 100.0))
    reason = (
        f"envolvente CV={envelope_cv:.1f} %; "
        f"fase IQR={phase_iqr:.1f} BPM; "
        f"muestras de fase validas={valid_ratio * 100.0:.0f} %"
    )
    return {"bpm": bpm, "envelope_cv_pct": envelope_cv, "phase_iqr_bpm": phase_iqr, "quality": quality, "reason": reason}


def _ac_dc_pi(t: np.ndarray, y: np.ndarray, cfg: AnalysisConfig) -> tuple[float, float, float]:
    if y.size < 20:
        return math.nan, math.nan, math.nan
    yy = replace_nan_with_last(y)
    dc = float(np.mean(yy))
    ac_sig = processed_ppg(yy, estimate_hz(t), cfg)
    ac = float(np.sqrt(np.mean(ac_sig**2)))
    pi = float((ac / dc) * 100.0) if dc > 0 else math.nan
    return ac, dc, pi


def _estimate_spo2_quality(
    spo2: float,
    ratio_r: float,
    pi_red: float,
    pi_ir: float,
    artifact_red_pct: float,
    artifact_ir_pct: float,
    saturation_pct: float,
    duration_s: float,
) -> tuple[float, str]:
    if not np.isfinite(spo2) or not np.isfinite(ratio_r):
        return 0.0, "SpO2 no estimable: falta ratio RED/IR valido"
    quality = 45.0
    reasons: list[str] = ["SpO2 estimada no calibrada desde ratio RED/IR"]
    if 0.35 <= ratio_r <= 1.8:
        quality += 12
        reasons.append(f"ratio R plausible ({ratio_r:.3f})")
    else:
        quality -= 18
        reasons.append(f"ratio R extremo ({ratio_r:.3f})")
    if np.isfinite(pi_red) and np.isfinite(pi_ir):
        min_pi = min(pi_red, pi_ir)
        if min_pi >= 0.15:
            quality += 18
            reasons.append(f"PI RED/IR suficiente (min {min_pi:.3f} %)")
        elif min_pi >= 0.06:
            quality += 6
            reasons.append(f"PI RED/IR bajo pero usable (min {min_pi:.3f} %)")
        else:
            quality -= 18
            reasons.append(f"PI RED/IR muy bajo (min {min_pi:.3f} %)")
    artifact_penalty = 0.0
    for value in (artifact_red_pct, artifact_ir_pct):
        if np.isfinite(value):
            artifact_penalty += min(14.0, value * 0.9)
    if artifact_penalty > 0:
        quality -= artifact_penalty
        reasons.append(f"penalizacion por artefactos RED/IR {artifact_penalty:.1f} puntos")
    if np.isfinite(saturation_pct) and saturation_pct > 0:
        penalty = min(25.0, saturation_pct * 2.0)
        quality -= penalty
        reasons.append(f"saturacion penaliza SpO2 ({saturation_pct:.1f} %)")
    if np.isfinite(duration_s) and duration_s < 12.0:
        quality -= 10
        reasons.append("tramo corto para ratio RED/IR estable")
    if np.isfinite(spo2) and (spo2 <= 70.0 or spo2 >= 100.0):
        quality -= 6
        reasons.append(f"valor SpO2 en borde de formula ({spo2:.1f} %)")
    return float(np.clip(quality, 0.0, 100.0)), "; ".join(reasons)


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


def _drop_leading_timestamp_gap(t: np.ndarray, red: np.ndarray, ir: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
    if t.size < 5:
        return t, red, ir, ""
    diffs = np.diff(t)
    positive = diffs[np.isfinite(diffs) & (diffs > 0)]
    if positive.size < 3:
        return t, red, ir, ""
    baseline = positive[1:] if positive.size > 3 else positive
    median_dt = float(np.median(baseline))
    first_dt = float(diffs[0])
    if median_dt > 0 and first_dt > max(1.0, median_dt * 8.0):
        return t[1:] - float(t[1]), red[1:], ir[1:], f"se descarta primera muestra aislada por gap inicial {first_dt:.2f} s"
    return t, red, ir, ""


def _keep_longest_contiguous_chunk(t: np.ndarray, red: np.ndarray, ir: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
    if t.size < 5:
        return t, red, ir, ""
    diffs = np.diff(t)
    positive = diffs[np.isfinite(diffs) & (diffs > 0)]
    if positive.size < 3:
        return t, red, ir, ""
    median_dt = float(np.median(positive))
    if median_dt <= 0:
        return t, red, ir, ""
    gap_limit = max(1.0, median_dt * 8.0)
    gap_indices = np.where(diffs > gap_limit)[0]
    if gap_indices.size == 0:
        return t, red, ir, ""
    starts = [0, *[int(i + 1) for i in gap_indices]]
    ends = [*[int(i + 1) for i in gap_indices], int(t.size)]
    chunks = [(end - start, start, end) for start, end in zip(starts, ends) if end > start]
    if not chunks:
        return t, red, ir, ""
    _length, start, end = max(chunks, key=lambda item: item[0])
    if start == 0 and end == t.size:
        return t, red, ir, ""
    dropped = int(t.size - (end - start))
    max_gap = float(np.max(diffs[gap_indices]))
    return (
        t[start:end] - float(t[start]),
        red[start:end],
        ir[start:end],
        f"analizado tramo continuo mas largo; descartadas {dropped} muestras separadas por gaps (max {max_gap:.2f} s)",
    )


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
        t, red, ir, leading_gap_reason = _drop_leading_timestamp_gap(t, red, ir)
        t, red, ir, chunk_reason = _keep_longest_contiguous_chunk(t, red, ir)
        if t.size < 40:
            continue
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
        hilbert_ir = _hilbert_details(t, ir, cfg)
        bpm_ac, q_ac, reason_ac = estimate_bpm_autocorr(t, ir, cfg)
        _ac_ir, dc_ir, pi_ir = _ac_dc_pi(t, ir, cfg)
        _ac_red, dc_red, pi_red = _ac_dc_pi(t, red, cfg)
        art_ir = detect_artifacts(ir)
        art_red = detect_artifacts(red)
        adc = _as_int(row0.get("cfg_adc", "0"))
        saturation = saturation_percent(red, ir, adc)
        spo2, ratio_r, spo2_reason, ac_red, dc_red, ac_ir, dc_ir, pi_red_spo2, pi_ir_spo2 = estimate_spo2(t, red, ir, cfg)
        if np.isfinite(pi_red_spo2):
            pi_red = pi_red_spo2
        if np.isfinite(pi_ir_spo2):
            pi_ir = pi_ir_spo2
        artifact_red = _safe_percent(art_red)
        artifact_ir = _safe_percent(art_ir)
        spo2_quality, spo2_quality_reason = _estimate_spo2_quality(
            spo2,
            ratio_r,
            pi_red,
            pi_ir,
            artifact_red,
            artifact_ir,
            saturation,
            duration,
        )
        resp_rate, resp_quality, resp_reason = estimate_respiration(t, ir, hz)

        bpm_ir = float(fft_ir.get("bpm", math.nan))
        bpm_red = float(fft_red.get("bpm", math.nan))
        bpm_hilbert = float(hilbert_ir.get("bpm", math.nan))
        pulse_prev, pulse_pulsio, pulse_fonendo, pulse_ref_avg, pulse_ref_count = _load_manual_refs(path, row0)
        diff_fft_ref = _ref_diff(bpm_ir, pulse_ref_avg)
        diff_ac_ref = _ref_diff(bpm_ac, pulse_ref_avg)
        diff_hilbert_ref = _ref_diff(bpm_hilbert, pulse_ref_avg)
        hilbert_quality = float(hilbert_ir.get("quality", 0.0))
        envelope_cv = float(hilbert_ir.get("envelope_cv_pct", math.nan))
        phase_iqr = float(hilbert_ir.get("phase_iqr_bpm", math.nan))
        candidates = [v for v in [bpm_ir, bpm_red] if np.isfinite(v)]
        ac_reliable = np.isfinite(bpm_ac) and cfg.bpm_min <= bpm_ac <= cfg.bpm_max and q_ac >= 20.0
        hilbert_reliable = np.isfinite(bpm_hilbert) and hilbert_quality >= 35.0
        if ac_reliable:
            candidates.append(bpm_ac)
        if hilbert_reliable:
            candidates.append(bpm_hilbert)
        agreement = float(np.max(candidates) - np.min(candidates)) if len(candidates) >= 2 else math.nan

        score = float(fft_ir.get("quality", 0.0))
        reasons: list[str] = []
        if leading_gap_reason:
            reasons.append(leading_gap_reason)
        if chunk_reason:
            reasons.append(chunk_reason)
        if np.isfinite(bpm_ir):
            reasons.append(f"FFT IR detecta {bpm_ir:.1f} BPM")
        else:
            reasons.append(str(fft_ir.get("reason", "FFT IR no valido")))
        if np.isfinite(bpm_red):
            reasons.append(f"FFT RED detecta {bpm_red:.1f} BPM")
        if ac_reliable:
            reasons.append(f"autocorrelacion IR {bpm_ac:.1f} BPM ({reason_ac})")
        elif np.isfinite(bpm_ac):
            reasons.append(f"autocorrelacion IR no se usa en acuerdo ({bpm_ac:.1f} BPM; {reason_ac})")
        if hilbert_reliable:
            reasons.append(f"Hilbert IR estima {bpm_hilbert:.1f} BPM por fase instantanea ({hilbert_ir.get('reason', '')})")
        elif np.isfinite(bpm_hilbert):
            reasons.append(f"Hilbert IR no se usa en acuerdo por calidad baja ({bpm_hilbert:.1f} BPM; {hilbert_ir.get('reason', '')})")
        else:
            reasons.append(f"Hilbert IR no estima BPM fiable ({hilbert_ir.get('reason', 'sin motivo')})")

        if np.isfinite(pulse_ref_avg):
            reasons.append(
                f"referencia manual media {pulse_ref_avg:.1f} BPM "
                f"({pulse_ref_count} lectura(s), ignorando ceros/vacios)"
            )
            if np.isfinite(diff_fft_ref):
                if diff_fft_ref <= 5:
                    bonus = max(13.0, 28.0 - 3.0 * diff_fft_ref)
                    score += bonus
                    reasons.append(f"FFT IR coincide con referencia: diferencia {diff_fft_ref:.1f} BPM; bonus cercania {bonus:.1f}")
                elif diff_fft_ref <= 10:
                    bonus = max(7.0, 13.0 - 1.2 * (diff_fft_ref - 5.0))
                    score += bonus
                    reasons.append(f"FFT IR cerca de referencia: diferencia {diff_fft_ref:.1f} BPM; bonus cercania {bonus:.1f}")
                elif diff_fft_ref <= 18:
                    bonus = max(0.0, 5.0 - 0.6 * (diff_fft_ref - 10.0))
                    score += bonus
                    reasons.append(f"FFT IR algo separada de referencia: diferencia {diff_fft_ref:.1f} BPM; bonus cercania {bonus:.1f}")
                else:
                    score -= min(28.0, diff_fft_ref * 0.9)
                    reasons.append(f"FFT IR lejos de referencia: diferencia {diff_fft_ref:.1f} BPM")
        else:
            reasons.append("sin referencia manual valida: no se puntua cercania a pulsioximetro/fonendo")

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

        if hilbert_quality >= 70:
            score += 7
            reasons.append(f"Hilbert estable ({hilbert_quality:.0f}/100): envolvente limpia y fase coherente")
        elif hilbert_quality >= 45:
            score += 2
            reasons.append(f"Hilbert aceptable ({hilbert_quality:.0f}/100): util como apoyo, no como veredicto unico")
        else:
            score -= 6
            reasons.append(f"Hilbert debil ({hilbert_quality:.0f}/100): envolvente/fase inestable")

        artifact = artifact_ir
        if np.isfinite(artifact):
            score -= min(25.0, artifact * 1.8)
            reasons.append(f"artefactos IR {artifact:.1f} %")
        if np.isfinite(saturation) and saturation > 0:
            score -= min(35.0, saturation * 2.0)
            reasons.append(f"saturacion cerca del techo digital raw {saturation:.1f} %")
        if np.isfinite(spo2_quality):
            if spo2_quality >= 70:
                score += 6
                reasons.append(f"calidad SpO2 buena ({spo2_quality:.0f}/100): ratio RED/IR coherente")
            elif spo2_quality >= 45:
                score += 2
                reasons.append(f"calidad SpO2 aceptable ({spo2_quality:.0f}/100)")
            else:
                score -= 10
                reasons.append(f"calidad SpO2 baja ({spo2_quality:.0f}/100): revisar RED/IR")
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
            bpm_hilbert_ir=bpm_hilbert,
            pulse_prev_ref=pulse_prev,
            pulse_final_pulsio_ref=pulse_pulsio,
            pulse_final_fonendo_ref=pulse_fonendo,
            pulse_ref_avg=pulse_ref_avg,
            pulse_ref_count=pulse_ref_count,
            diff_fft_ref_bpm=diff_fft_ref,
            diff_autocorr_ref_bpm=diff_ac_ref,
            diff_hilbert_ref_bpm=diff_hilbert_ref,
            hilbert_envelope_cv_pct=envelope_cv,
            hilbert_phase_iqr_bpm=phase_iqr,
            hilbert_quality=hilbert_quality,
            hilbert_reason=str(hilbert_ir.get("reason", "")),
            dominance_ir=float(fft_ir.get("dominance", math.nan)),
            band_ratio_ir=float(fft_ir.get("band_ratio", math.nan)),
            peak_snr_db=float(fft_ir.get("snr_db", math.nan)),
            entropy_ir=float(fft_ir.get("entropy", math.nan)),
            spo2_est_pct=spo2,
            spo2_ratio_r=ratio_r,
            spo2_quality=spo2_quality,
            spo2_reason=f"{spo2_reason}; {spo2_quality_reason}",
            resp_rate_rpm=resp_rate,
            resp_quality=resp_quality,
            resp_reason=resp_reason,
            pi_ir_pct=pi_ir,
            pi_red_pct=pi_red,
            artifact_ir_pct=artifact,
            artifact_red_pct=artifact_red,
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
        "Correo", "Puntuacion", "Veredicto", "Animal", "Configuracion", "BPM ref.", "Dif FFT-ref",
        "BPM FFT IR", "BPM autocorr", "BPM Hilbert", "BPM FFT RED", "Hilbert env. CV %", "Hilbert calidad",
        "Dominancia", "Banda", "SNR dB", "PI IR %", "Artefactos %",
        "SpO2 est.", "Calidad SpO2", "Resp/min (experimental)", "Calidad Resp.", "Saturacion %",
        "Jitter %", "Duracion", "Muestras", "Archivo",
    ]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PPG Suite v8 | Analisis experimental de Fourier")
        self.resize(1420, 900)
        self.raw_files: list[RawFileInfo] = []
        self.results: list[SpectrumResult] = []
        self.mail_paths: dict[str, Path] = {}
        self._updating_raw_table = False
        self._build_ui()
        self.reload_raws()
        self.update_mail_status()

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
        self.btn_export = QtWidgets.QPushButton("Exportar informe PDF")
        self.mail_status = QtWidgets.QLabel("0 archivos seleccionados")
        self.btn_prepare_mail = QtWidgets.QPushButton("Preparar correo")
        self.btn_clear_mail = QtWidgets.QPushButton("Limpiar correo")
        self.btn_export.setEnabled(False)
        self.btn_analyze.setMinimumHeight(36)
        self.btn_analyze.setStyleSheet("font-weight: bold;")
        self.btn_export.setMinimumHeight(36)
        self.btn_prepare_mail.setMinimumHeight(36)
        self.btn_clear_mail.setMinimumHeight(36)
        cl.addWidget(QtWidgets.QLabel("Animal"), 0, 0)
        cl.addWidget(self.animal_filter, 0, 1)
        cl.addWidget(QtWidgets.QLabel("Texto"), 0, 2)
        cl.addWidget(self.text_filter, 0, 3)
        cl.addWidget(self.btn_select_visible, 0, 4)
        cl.addWidget(self.btn_clear, 0, 5)
        cl.addWidget(self.btn_analyze, 0, 6)
        cl.addWidget(self.btn_export, 0, 7)
        cl.addWidget(self.mail_status, 1, 0, 1, 2)
        cl.addWidget(self.btn_prepare_mail, 1, 4)
        cl.addWidget(self.btn_clear_mail, 1, 5)
        root.addWidget(controls)
        self.animal_filter.currentTextChanged.connect(self.apply_filters)
        self.text_filter.textChanged.connect(self.apply_filters)
        self.btn_select_visible.clicked.connect(self.select_visible)
        self.btn_clear.clicked.connect(self.clear_selection)
        self.btn_analyze.clicked.connect(self.analyze_selected)
        self.btn_export.clicked.connect(self.export_report)
        self.btn_prepare_mail.clicked.connect(self.prepare_mail_zip)
        self.btn_clear_mail.clicked.connect(self.clear_mail_selection)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        root.addWidget(splitter, stretch=1)

        self.raw_table = QtWidgets.QTableWidget(0, 7)
        self.raw_table.setHorizontalHeaderLabels(["Usar", "Correo", "Animal", "Fecha", "Filas", "Configuraciones", "Archivo"])
        self.raw_table.verticalHeader().setVisible(False)
        self.raw_table.setAlternatingRowColors(True)
        self.raw_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.raw_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.raw_table.itemChanged.connect(self.on_raw_table_item_changed)
        self.raw_table.doubleClicked.connect(self.open_selected_raw_file)
        splitter.addWidget(self.raw_table)

        results_page = QtWidgets.QWidget()
        results_layout = QtWidgets.QVBoxLayout(results_page)
        self.results_table = QtWidgets.QTableWidget(0, len(self.result_headers))
        self.results_table.setHorizontalHeaderLabels(self.result_headers)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results_table.itemChanged.connect(self.on_results_table_item_changed)
        self.results_table.currentCellChanged.connect(self.plot_current_result)
        self.results_table.doubleClicked.connect(self.open_selected_result_file)
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
        self._updating_raw_table = True
        try:
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
                check.setToolTip("Marcar raw para incluirlo en el analisis comparativo")
                self.raw_table.setItem(row, 0, check)
                mail_check = QtWidgets.QTableWidgetItem("")
                mail_check.setFlags(mail_check.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
                mail_check.setCheckState(QtCore.Qt.CheckState.Checked if self.mail_key(info.path) in self.mail_paths else QtCore.Qt.CheckState.Unchecked)
                mail_check.setData(QtCore.Qt.ItemDataRole.UserRole, str(info.path))
                mail_check.setToolTip("Marcar raw para incluirlo en el ZIP de correo")
                self.raw_table.setItem(row, 1, mail_check)
                values = [info.animal, info.date, str(info.rows), info.config_summary, info.path.name]
                for col, value in enumerate(values, start=2):
                    item = QtWidgets.QTableWidgetItem(value)
                    item.setToolTip(str(info.path))
                    self.raw_table.setItem(row, col, item)
        finally:
            self._updating_raw_table = False
        self.raw_table.resizeColumnsToContents()

    def mail_key(self, path: Path | None) -> str:
        if path is None:
            return ""
        try:
            return str(path.resolve())
        except OSError:
            return str(path)

    def on_raw_table_item_changed(self, item: QtWidgets.QTableWidgetItem):
        if self._updating_raw_table or item.column() != 1:
            return
        self.set_mail_checked_from_item(item)

    def on_results_table_item_changed(self, item: QtWidgets.QTableWidgetItem):
        if self._updating_raw_table or item.column() != 0:
            return
        self.set_mail_checked_from_item(item)

    def set_mail_checked_from_item(self, item: QtWidgets.QTableWidgetItem):
        path_text = item.data(QtCore.Qt.ItemDataRole.UserRole) or ""
        if not path_text:
            return
        path = Path(path_text)
        key = self.mail_key(path)
        if item.checkState() == QtCore.Qt.CheckState.Checked:
            self.mail_paths[key] = path
        else:
            self.mail_paths.pop(key, None)
        self.update_mail_status()
        self.sync_mail_check_states()

    def update_mail_status(self):
        count = len(self.mail_paths)
        self.mail_status.setText(f"{count} archivo{'s' if count != 1 else ''} seleccionado{'s' if count != 1 else ''}")

    def sync_mail_check_states(self):
        self._updating_raw_table = True
        try:
            for table, column in ((self.raw_table, 1), (self.results_table, 0)):
                for row in range(table.rowCount()):
                    item = table.item(row, column)
                    if not item:
                        continue
                    path_text = item.data(QtCore.Qt.ItemDataRole.UserRole) or ""
                    checked = self.mail_key(Path(path_text)) in self.mail_paths if path_text else False
                    item.setCheckState(QtCore.Qt.CheckState.Checked if checked else QtCore.Qt.CheckState.Unchecked)
        finally:
            self._updating_raw_table = False

    def clear_mail_selection(self):
        if not self.mail_paths:
            return
        self.mail_paths.clear()
        self.sync_mail_check_states()
        self.update_mail_status()

    def desktop_dir(self) -> Path:
        desktop = Path.home() / "Desktop"
        if not desktop.exists():
            desktop = Path.home() / "Escritorio"
        return desktop if desktop.exists() else Path.home()

    def prepare_mail_zip(self):
        paths = [path for path in self.mail_paths.values() if path.exists()]
        if not paths:
            QtWidgets.QMessageBox.information(self, "Preparar correo", "Marca primero uno o varios raws en la columna Correo.")
            return
        desktop = self.desktop_dir()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_path = desktop / f"mtestv2_archivos_para_correo_{stamp}.zip"
        used_names: dict[str, int] = {}
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in paths:
                name = path.name
                if name in used_names:
                    used_names[name] += 1
                    name = f"{path.stem}_{used_names[path.name]}{path.suffix}"
                else:
                    used_names[name] = 1
                zf.write(path, arcname=name)
        QtWidgets.QApplication.clipboard().setText(str(zip_path))
        QtWidgets.QMessageBox.information(
            self,
            "Preparar correo",
            f"Se ha creado un ZIP en el Escritorio con {len(paths)} raw(s):\n\n{zip_path}\n\nLa ruta queda copiada al portapapeles.",
        )

    def selected_paths(self) -> list[Path]:
        paths: list[Path] = []
        for row in range(self.raw_table.rowCount()):
            item = self.raw_table.item(row, 0)
            if item and item.checkState() == QtCore.Qt.CheckState.Checked:
                paths.append(Path(item.data(QtCore.Qt.ItemDataRole.UserRole)))
        return paths

    def open_path(self, path: Path | None):
        if path is None:
            return
        if not path.exists():
            QtWidgets.QMessageBox.warning(self, "Abrir raw", f"No se encontro el archivo:\n{path}")
            return
        ok = QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))
        if not ok:
            QtWidgets.QMessageBox.warning(self, "Abrir raw", f"No se pudo abrir:\n{path}")

    def open_selected_raw_file(self, index: QtCore.QModelIndex):
        if not index.isValid():
            return
        item = self.raw_table.item(index.row(), 0)
        path_text = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else ""
        self.open_path(Path(path_text) if path_text else None)

    def open_selected_result_file(self, index: QtCore.QModelIndex):
        if not index.isValid():
            return
        row = index.row()
        self.open_path(self.results[row].file if 0 <= row < len(self.results) else None)

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
        self.btn_export.setEnabled(bool(results))
        if not results:
            self.details.setHtml("<h2>Sin resultados</h2><p>No se encontraron tramos suficientes para analizar.</p>")
            self.plot.clear()

    def populate_results(self):
        self._updating_raw_table = True
        self.results_table.setRowCount(0)
        try:
            for result in self.results:
                row = self.results_table.rowCount()
                self.results_table.insertRow(row)
                mail_check = QtWidgets.QTableWidgetItem("")
                mail_check.setFlags(mail_check.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
                mail_check.setCheckState(QtCore.Qt.CheckState.Checked if self.mail_key(result.file) in self.mail_paths else QtCore.Qt.CheckState.Unchecked)
                mail_check.setData(QtCore.Qt.ItemDataRole.UserRole, str(result.file))
                mail_check.setToolTip("Marcar raw para incluirlo en el ZIP de correo")
                self.results_table.setItem(row, 0, mail_check)
                values = [
                    fmt(result.score, 1, ""), result.verdict, result.animal, result.config_label,
                    fmt(result.pulse_ref_avg, 1, ""), fmt(result.diff_fft_ref_bpm, 1, ""),
                    fmt(result.bpm_fft_ir, 1, ""), fmt(result.bpm_autocorr, 1, ""),
                    fmt(result.bpm_hilbert_ir, 1, ""), fmt(result.bpm_fft_red, 1, ""),
                    fmt(result.hilbert_envelope_cv_pct, 1, ""), fmt(result.hilbert_quality, 0, ""),
                    fmt(result.dominance_ir, 2, ""), fmt(result.band_ratio_ir, 3, ""), fmt(result.peak_snr_db, 1, ""),
                    fmt(result.pi_ir_pct, 3, ""), fmt(result.artifact_ir_pct, 1, ""),
                    fmt(result.spo2_est_pct, 1, ""), fmt(result.spo2_quality, 0, ""),
                    fmt(result.resp_rate_rpm, 1, ""), fmt(result.resp_quality, 0, ""),
                    fmt(result.saturation_pct, 1, ""),
                    fmt(result.hz_jitter_pct, 1, ""), fmt(result.duration_s, 1, ""), str(result.n), result.file.name,
                ]
                for col, value in enumerate(values, start=1):
                    item = QtWidgets.QTableWidgetItem(value)
                    if col == 1:
                        item.setData(QtCore.Qt.ItemDataRole.UserRole, row)
                    if result.score >= 75:
                        item.setBackground(QtGui.QColor("#dff3e4"))
                        item.setForeground(QtGui.QBrush(QtGui.QColor("#17202a")))
                    elif result.score < 42:
                        item.setBackground(QtGui.QColor("#f8d7da"))
                        item.setForeground(QtGui.QBrush(QtGui.QColor("#17202a")))
                    self.results_table.setItem(row, col, item)
        finally:
            self._updating_raw_table = False
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
        if np.isfinite(result.pulse_ref_avg):
            ref_line = pg.InfiniteLine(pos=result.pulse_ref_avg, angle=90, pen=pg.mkPen((20, 140, 70), width=2, style=QtCore.Qt.PenStyle.DashLine))
            self.plot.addItem(ref_line)

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
        <tr><td><b>Referencia manual</b></td><td>media {fmt(result.pulse_ref_avg, 1, '-')} BPM ({result.pulse_ref_count} lectura(s) validas; 0/vacio se ignora). Previo {fmt(result.pulse_prev_ref, 1, '-')} | pulsio final {fmt(result.pulse_final_pulsio_ref, 1, '-')} | fonendo final {fmt(result.pulse_final_fonendo_ref, 1, '-')}</td></tr>
        <tr><td><b>Diferencia vs referencia</b></td><td>FFT IR {fmt(result.diff_fft_ref_bpm, 1, '-')} BPM | autocorrelacion {fmt(result.diff_autocorr_ref_bpm, 1, '-')} BPM | Hilbert {fmt(result.diff_hilbert_ref_bpm, 1, '-')} BPM</td></tr>
        <tr><td><b>Fourier IR</b></td><td>{fmt(result.bpm_fft_ir, 1, '-')} BPM; dominancia {fmt(result.dominance_ir, 2, '-')}; banda cardiaca {fmt(result.band_ratio_ir, 3, '-')}; SNR {fmt(result.peak_snr_db, 1, '-')} dB; entropia {fmt(result.entropy_ir, 3, '-')}</td></tr>
        <tr><td><b>Hilbert IR</b></td><td>{fmt(result.bpm_hilbert_ir, 1, '-')} BPM por fase instantanea; envolvente CV {fmt(result.hilbert_envelope_cv_pct, 1, '-')} %; fase IQR {fmt(result.hilbert_phase_iqr_bpm, 1, '-')} BPM; calidad {fmt(result.hilbert_quality, 0, '-')} / 100; {html.escape(result.hilbert_reason)}</td></tr>
        <tr><td><b>Acuerdo</b></td><td>FFT RED {fmt(result.bpm_fft_red, 1, '-')} BPM; autocorrelacion {fmt(result.bpm_autocorr, 1, '-')} BPM; Hilbert {fmt(result.bpm_hilbert_ir, 1, '-')} BPM; diferencia maxima {fmt(result.agreement_bpm, 1, '-')} BPM</td></tr>
        <tr><td><b>Senal</b></td><td>PI IR {fmt(result.pi_ir_pct, 3, '-')} %; PI RED {fmt(result.pi_red_pct, 3, '-')} %; artefactos IR {fmt(result.artifact_ir_pct, 1, '-')} %; saturacion {fmt(result.saturation_pct, 1, '-')} %</td></tr>
        <tr><td><b>SpO2 experimental</b></td><td>{fmt(result.spo2_est_pct, 1, '-')} %; ratio R {fmt(result.spo2_ratio_r, 4, '-')}; calidad {fmt(result.spo2_quality, 0, '-')} / 100; {html.escape(result.spo2_reason)}</td></tr>
        <tr><td><b>Respiraciones (experimental)</b></td><td>{fmt(result.resp_rate_rpm, 1, '-')} resp/min; calidad {fmt(result.resp_quality, 0, '-')} / 100; {html.escape(result.resp_reason)}</td></tr>
        </table>
        <h3>Por que puntua asi</h3>
        <ul>{reasons}</ul>
        """
        self.details.setHtml(html_text)

    def _write_report_pdf(self, path: Path, generated_at: datetime):
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
            "red": QtGui.QColor("#f8d7da"),
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
            bottom_limit = page.y() + margin + height
            if y > page.y() + margin and y + block_h > bottom_limit:
                new_page()

        def draw_text(text: str, size: int = 10, bold: bool = False, color: str = "ink", gap: int = 8, max_width: float | None = None):
            nonlocal y
            painter.setFont(font(size, bold))
            painter.setPen(colors[color])
            w = int(max_width or width)
            fm = QtGui.QFontMetrics(painter.font())
            rect = fm.boundingRect(QtCore.QRect(0, 0, w, 10000), int(QtCore.Qt.TextFlag.TextWordWrap), text)
            ensure(rect.height() + gap)
            painter.drawText(QtCore.QRectF(x0, y, w, rect.height() + 4), int(QtCore.Qt.TextFlag.TextWordWrap), text)
            y += rect.height() + gap

        def draw_rule(gap: int = 14):
            nonlocal y
            ensure(gap + 2)
            painter.setPen(QtGui.QPen(colors["line"], 1))
            painter.drawLine(int(x0), int(y), int(x0 + width), int(y))
            y += gap

        def draw_metric_boxes(items: list[tuple[str, str]]):
            nonlocal y
            box_gap = 8
            box_w = (width - box_gap * (len(items) - 1)) / len(items)
            ensure(74)
            for i, (label, value) in enumerate(items):
                x = x0 + i * (box_w + box_gap)
                painter.fillRect(QtCore.QRectF(x, y, box_w, 62), colors["soft"])
                painter.setPen(QtGui.QPen(colors["line"], 1))
                painter.drawRect(QtCore.QRectF(x, y, box_w, 62))
                painter.setFont(font(8, True))
                painter.setPen(colors["muted"])
                painter.drawText(QtCore.QRectF(x + 8, y + 8, box_w - 16, 14), label)
                painter.setFont(font(13, True))
                painter.setPen(colors["ink"])
                painter.drawText(QtCore.QRectF(x + 8, y + 27, box_w - 16, 24), int(QtCore.Qt.TextFlag.TextWordWrap), value)
            y += 74

        def draw_table(headers: list[str], rows: list[list[str]], col_widths: list[float], row_h: int = 28):
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

        def draw_spectrum(result: SpectrumResult, chart_h: int = 190):
            nonlocal y
            ensure(chart_h + 42)
            painter.setFont(font(10, True))
            painter.setPen(colors["ink"])
            painter.drawText(QtCore.QRectF(x0, y, width, 20), f"Espectro IR normalizado - {result.config_label or result.base_name}")
            y += 24
            rect = QtCore.QRectF(x0, y, width, chart_h)
            painter.fillRect(rect, QtGui.QColor("#ffffff"))
            painter.setPen(QtGui.QPen(colors["line"], 1))
            painter.drawRect(rect)
            mask = (result.freqs_bpm >= 20) & (result.freqs_bpm <= 240)
            xs = result.freqs_bpm[mask]
            ys = result.spectrum_ir[mask]
            if xs.size > 1 and ys.size > 1 and float(np.max(ys)) > 0:
                ys = ys / float(np.max(ys))
                left, right = rect.left() + 36, rect.right() - 10
                top, bottom = rect.top() + 12, rect.bottom() - 28
                painter.setPen(QtGui.QPen(QtGui.QColor("#edf1f5"), 1))
                for tick in (40, 80, 120, 160, 200, 240):
                    tx = left + (tick - 20.0) / 220.0 * (right - left)
                    painter.drawLine(int(tx), int(top), int(tx), int(bottom))
                poly = QtGui.QPolygonF()
                step = max(1, int(math.ceil(xs.size / 450)))
                for xv, yv in zip(xs[::step], ys[::step]):
                    px = left + (float(xv) - 20.0) / 220.0 * (right - left)
                    py = bottom - float(np.clip(yv, 0.0, 1.0)) * (bottom - top)
                    poly.append(QtCore.QPointF(px, py))
                painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
                painter.setPen(QtGui.QPen(QtGui.QColor("#0b63ce"), 2))
                painter.drawPolyline(poly)
                if np.isfinite(result.bpm_fft_ir):
                    peak_x = left + (result.bpm_fft_ir - 20.0) / 220.0 * (right - left)
                    if left <= peak_x <= right:
                        painter.setPen(QtGui.QPen(QtGui.QColor("#c0392b"), 2))
                        painter.drawLine(int(peak_x), int(top), int(peak_x), int(bottom))
                if np.isfinite(result.pulse_ref_avg):
                    ref_x = left + (result.pulse_ref_avg - 20.0) / 220.0 * (right - left)
                    if left <= ref_x <= right:
                        pen = QtGui.QPen(QtGui.QColor("#168a45"), 2)
                        pen.setStyle(QtCore.Qt.PenStyle.DashLine)
                        painter.setPen(pen)
                        painter.drawLine(int(ref_x), int(top), int(ref_x), int(bottom))
                painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)
                painter.setFont(font(8))
                painter.setPen(colors["muted"])
                painter.drawText(QtCore.QRectF(left, bottom + 6, right - left, 16), QtCore.Qt.AlignmentFlag.AlignCenter, "Frecuencia cardiaca estimada (BPM). Rojo=FFT IR; verde=referencia manual")
            else:
                painter.setFont(font(9))
                painter.setPen(colors["muted"])
                painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, "Espectro insuficiente para graficar.")
            y += chart_h + 18

        results = self.results
        best = results[0]
        raw_names = sorted({r.file.name for r in results})
        raw_summary = ", ".join(raw_names[:10])
        if len(raw_names) > 10:
            raw_summary += f" ... y {len(raw_names) - 10} archivo(s) mas"

        painter.fillRect(QtCore.QRectF(page.x(), page.y(), page.width(), 112), colors["blue"])
        painter.setPen(QtGui.QColor("#ffffff"))
        painter.setFont(font(22, True))
        painter.drawText(QtCore.QRectF(x0, page.y() + 30, width, 34), "Informe comparativo de pulso PPG")
        painter.setFont(font(10))
        painter.drawText(QtCore.QRectF(x0, page.y() + 68, width, 20), f"mtestv2 | generado el {generated_at.strftime('%d/%m/%Y %H:%M:%S')}")
        y = page.y() + 138

        draw_text("Resumen ejecutivo", 16, True, gap=10)
        draw_metric_boxes([
            ("Mejor configuracion", best.config_label or best.base_name),
            ("Puntuacion", f"{fmt(best.score, 1, '-')} / 100"),
            ("Pulso ref.", f"{fmt(best.pulse_ref_avg, 1, '-')} BPM"),
            ("BPM FFT IR", fmt(best.bpm_fft_ir, 1, "-")),
            ("Dif. vs ref.", f"{fmt(best.diff_fft_ref_bpm, 1, '-')} BPM"),
        ])
        draw_text(
            f"La configuracion seleccionada como mejor candidata es {best.config_label or best.base_name}. "
            "El criterio principal es que la BPM estimada por PPG se acerque al pulso de referencia anotado con pulsioximetro/fonendo. "
            "Como apoyo tecnico se revisa que la senal tenga pulso visible, poca saturacion, pocos artefactos y estimadores internos coherentes.",
            10,
        )
        draw_text(
            "Pulso ref. es la media de pulso previo, pulsioximetro final y fonendo final. Las lecturas 0 o vacias se ignoran porque se interpretan como no anotadas.",
            9,
            color="muted",
        )
        draw_text(f"Archivos raw analizados: {raw_summary}", 9, color="muted")
        draw_rule()

        draw_text("Procedimiento comparativo", 15, True)
        ranking_rows = []
        for idx, result in enumerate(results[:18], start=1):
            ranking_rows.append([
                str(idx),
                fmt(result.score, 1, "-"),
                result.verdict,
                result.config_label or result.base_name,
                fmt(result.pulse_ref_avg, 1, "-"),
                fmt(result.bpm_fft_ir, 1, "-"),
                fmt(result.bpm_autocorr, 1, "-"),
                fmt(result.bpm_hilbert_ir, 1, "-"),
                fmt(result.diff_fft_ref_bpm, 1, "-"),
                fmt(result.pi_ir_pct, 3, "-"),
            ])
        draw_table(
            ["#", "Punt.", "Veredicto", "Configuracion", "Ref.", "FFT", "Autoc.", "Hilbert", "Dif.", "PI IR"],
            ranking_rows,
            [24, 38, 74, 128, 43, 43, 45, 45, 40, 40],
        )

        draw_text("Por que gana la mejor configuracion", 15, True)
        for reason in best.reasons[:12]:
            draw_text(f"- {reason}", 9, gap=3)

        for result in results[:4]:
            draw_spectrum(result)
            draw_table(
                ["Metrica", "Valor", "Lectura"],
                [
                    ["Fourier IR", f"{fmt(result.bpm_fft_ir, 1, '-')} BPM", f"Dominancia {fmt(result.dominance_ir, 2, '-')} | SNR {fmt(result.peak_snr_db, 1, '-')} dB"],
                    ["Referencia", f"{fmt(result.pulse_ref_avg, 1, '-')} BPM", f"Dif. FFT-ref {fmt(result.diff_fft_ref_bpm, 1, '-')} BPM | validas {result.pulse_ref_count}"],
                    ["Autocorrelacion", f"{fmt(result.bpm_autocorr, 1, '-')} BPM", f"Diferencia maxima estimadores {fmt(result.agreement_bpm, 1, '-')} BPM"],
                    ["Hilbert", f"{fmt(result.bpm_hilbert_ir, 1, '-')} BPM", f"Envolvente CV {fmt(result.hilbert_envelope_cv_pct, 1, '-')} % | calidad {fmt(result.hilbert_quality, 0, '-')}"],
                    ["Senal", f"PI IR {fmt(result.pi_ir_pct, 3, '-')} %", f"Artefactos {fmt(result.artifact_ir_pct, 1, '-')} % | saturacion {fmt(result.saturation_pct, 1, '-')} %"],
                ],
                [112, 120, width - 232],
                row_h=34,
            )

        draw_rule()
        if y > page.y() + margin + height - 260:
            new_page()
        draw_text("Anexo tecnico: como leer el analisis", 18, True)
        explanations = [
            ("Referencia manual",
             "La referencia manual es la media de las pulsaciones anotadas antes y al final de la toma con pulsioximetro/fonendo. Si una lectura esta a 0 o vacia no se usa en la media. Esta referencia es el criterio externo mas defendible del informe y se usa para penalizar configuraciones que estiman BPM muy alejadas."),
            ("Fourier",
             "La transformada de Fourier descompone la senal PPG en frecuencias. En este proyecto se mira si hay un pico claro dentro de la banda cardiaca esperada. Un pico dominante, con buena energia de banda y buen SNR, indica que la configuracion esta separando bien una periodicidad compatible con pulso. Fourier responde sobre todo a: que frecuencia domina en esta toma."),
            ("Autocorrelacion",
             "La autocorrelacion compara la senal consigo misma desplazada en el tiempo. Si el pulso se repite de forma regular, aparece un retardo dominante que puede convertirse a BPM. Sirve como comprobacion temporal independiente de Fourier: no busca energia en frecuencia, busca repeticion del patron."),
            ("Como leer Hilbert",
             "La transformada de Hilbert construye una senal analitica a partir del PPG filtrado. De ella salen dos lecturas complementarias: la envolvente, que resume como cambia la amplitud del pulso, y la fase instantanea, que permite estimar un BPM segundo a segundo. En este proyecto se usa como apoyo para detectar si una configuracion mantiene un pulso estable o si solo parece buena por un pico aislado en Fourier."),
            ("Interpretacion de Hilbert",
             "Una envolvente con CV bajo indica amplitud mas regular. Una fase con IQR bajo indica ritmo mas coherente. Si Hilbert, Fourier y autocorrelacion coinciden, la configuracion gana confianza. Si Hilbert se vuelve inestable, suele apuntar a movimiento, mal contacto, saturacion, poca componente pulsatile o una senal demasiado ruidosa."),
            ("PI, artefactos y saturacion",
             "El PI estima cuanto componente pulsatile hay respecto al nivel DC. Un PI bajo sugiere que el pulso esta poco visible. Los artefactos penalizan cambios bruscos incompatibles con una senal estable. La saturacion avisa de muestras cerca del techo digital del ADC; si hay saturacion, el sensor puede estar perdiendo informacion real."),
            ("SpO2 experimental",
             "La SpO2 se estima desde el ratio RED/IR, pero no esta calibrada clinicamente. Por eso se informa con calidad experimental: ratio plausible, PI suficiente, poca saturacion, pocos artefactos y duracion adecuada aumentan confianza. Debe validarse con referencia externa antes de usarla como criterio definitivo."),
            ("Respiracion experimental",
             "La respiracion se estima a partir de modulaciones lentas de la PPG. Necesita tomas mas largas que el pulso para ser estable. En tomas cortas puede salir como no estimable o con calidad baja."),
            ("Puntuacion final",
             "La puntuacion combina cercania a la referencia manual cuando existe, calidad Fourier IR, acuerdo entre FFT/autocorrelacion/Hilbert, estabilidad de Hilbert, PI, artefactos, saturacion, calidad SpO2, jitter de muestreo y duracion. Es un criterio comparativo interno para elegir configuraciones."),
            ("Lectura rigurosa",
             "Fourier mide periodicidad espectral; Hilbert mira evolucion temporal de amplitud y fase; autocorrelacion comprueba repeticion del patron. Una configuracion es preferible si concentra energia en la banda cardiaca esperada, tiene un pico dominante y estrecho, coincide con RED/autocorrelacion/Hilbert, evita saturacion ADC y mantiene suficiente componente pulsatile. No sustituye validacion con pulso de referencia."),
        ]
        for title, body in explanations:
            if y > page.y() + margin + height - 95:
                new_page()
            draw_text(title, 13, True, gap=6)
            draw_text(body, 10, gap=12)

        draw_rule()
        if y > page.y() + margin + height - 260:
            new_page()
        draw_text("Leyenda de escalas e interpretacion", 18, True)
        draw_table(
            ["Metrica", "Escala / unidad", "Interpretacion practica"],
            [
                ["Puntuacion", "0-100", "Mas alto es mejor. >=75 mejor candidata; 58-74 buena; 42-57 usable con cautela; <42 no recomendable."],
                ["Pulso ref.", "BPM", "Media de lecturas manuales validas. Se ignoran 0/vacios. Es la referencia externa principal si esta disponible."],
                ["Dif. FFT-ref", "BPM", "Diferencia absoluta entre BPM por Fourier IR y pulso de referencia. Menor es mejor."],
                ["Calidad Hilbert", "0-100", "Mas alto indica envolvente mas regular y fase mas coherente. Usar como apoyo, no como criterio unico."],
                ["Calidad SpO2", "0-100", "Confianza interna en el calculo experimental RED/IR. No equivale a validacion clinica."],
                ["Calidad Resp.", "0-100", "Confianza interna en la respiracion estimada desde modulaciones lentas. Requiere tomas largas."],
                ["BPM", "latidos/min", "Estimacion de frecuencia cardiaca por FFT, autocorrelacion o Hilbert."],
                ["Dominancia", "ratio", "Relacion entre pico principal y segundo pico en banda cardiaca. Mayor suele indicar pico mas claro."],
                ["Banda cardiaca", "0-1", "Proporcion de energia util concentrada en la banda fisiologica esperada."],
                ["SNR", "dB", "Separacion del pico frente al ruido de fondo espectral. Mayor suele ser mejor."],
                ["PI IR/RED", "%", "Indice de perfusion aproximado: componente pulsatile frente a nivel DC. Bajo PI implica pulso poco visible."],
                ["Artefactos", "%", "Porcentaje de muestras o cambios sospechosos. Menor es mejor."],
                ["Saturacion", "%", "Porcentaje cerca del techo digital ADC. Menor es mejor; saturacion alta indica perdida de informacion."],
                ["Jitter", "%", "Irregularidad temporal del muestreo. Menor es mejor."],
            ],
            [92, 92, width - 184],
            row_h=42,
        )

        draw_text("Mini nota de formulas y criterios usados", 18, True)
        draw_table(
            ["Bloque", "Formula / criterio resumido"],
            [
                ["FFT", "Se filtra/procesa la PPG, se remuestrea de forma uniforme y se calcula abs(rFFT). El BPM FFT es el pico dominante dentro de la banda cardiaca configurada."],
                ["Dominancia", "pico_principal / segundo_pico en la banda cardiaca. Evita elegir configuraciones con varios picos parecidos."],
                ["Energia de banda", "energia_en_banda_cardiaca / energia_util. Favorece configuraciones que concentran energia donde se espera pulso."],
                ["SNR", "20*log10(pico / suelo_de_ruido). El suelo se aproxima con la mediana del espectro alrededor de la banda sin el pico principal."],
                ["Autocorrelacion", "Se busca el retardo de repeticion mas fuerte dentro de los retardos compatibles con BPM minimo/maximo."],
                ["Hilbert", "La senal analitica se obtiene anulando frecuencias negativas y duplicando positivas mediante FFT. Envolvente=abs(senal_analitica); fase=unwrap(angle())."],
                ["BPM Hilbert", "Derivada temporal de la fase instantanea: diff(fase) * Hz * 60 / (2*pi), usando valores dentro de banda fisiologica."],
                ["Pulso ref.", "mean(pulso_previo, pulso_final_pulsio, pulso_final_fonendo), descartando valores no numericos, vacios o iguales a 0."],
                ["Dif. ref.", "abs(BPM_estimado - pulso_ref). Se usa como criterio externo de comparacion cuando hay referencia manual."],
                ["CV envolvente", "std(envolvente) / media(envolvente) * 100. CV menor implica amplitud mas estable."],
                ["PI", "AC/DC * 100, donde AC se estima como energia RMS de la senal pulsatile procesada y DC como media raw."],
                ["SpO2", "Estimacion experimental desde ratio R=(AC_RED/DC_RED)/(AC_IR/DC_IR). No calibrada para uso clinico."],
                ["Respiracion", "Estimacion experimental desde modulaciones lentas de la PPG; solo orientativa sin referencia externa."],
                ["Puntuacion", "Suma ponderada experimental con bonus por cercania a referencia y acuerdo entre estimadores, y penalizaciones por artefactos, saturacion, jitter y duracion corta."],
            ],
            [110, width - 110],
            row_h=48,
        )

        draw_text("Recomendacion para documentacion", 13, True, gap=6)
        draw_text(
            "En memoria o tesis, describir este informe como un analisis comparativo interno de calidad de senal PPG. "
            "La mejor configuracion no debe presentarse como verdad fisiologica absoluta, sino como la opcion que, dentro de las tomas seleccionadas, "
            "maximiza coherencia espectral, estabilidad temporal y visibilidad del pulso con menor presencia de artefactos o saturacion. "
            "Para conclusiones fisiologicas, contrastar con pulsioximetro o referencia externa.",
            10,
            gap=12,
        )

        footer()
        painter.end()

    def export_report(self):
        if not self.results:
            QtWidgets.QMessageBox.information(self, "Exportar informe", "Primero analiza uno o varios raw.")
            return
        DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
        path = DOCUMENTS_DIR / f"informe_fourier_hilbert_{now_stamp()}.pdf"
        try:
            self._write_report_pdf(path, datetime.now())
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Exportar informe", f"No se pudo guardar el informe:\n{path}\n\n{exc}")
            return
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Exportar informe", f"No se pudo generar el PDF:\n{path}\n\n{exc}")
            return
        msg = QtWidgets.QMessageBox(self)
        msg.setIcon(QtWidgets.QMessageBox.Icon.Information)
        msg.setWindowTitle("Informe exportado")
        msg.setText("Informe PDF Fourier + Hilbert guardado correctamente.")
        msg.setInformativeText(str(path))
        open_btn = msg.addButton("Abrir carpeta", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("Cerrar", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if msg.clickedButton() is open_btn:
            open_folder(DOCUMENTS_DIR)

    def closeEvent(self, event: QtGui.QCloseEvent):
        event.accept()
