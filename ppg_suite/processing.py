from __future__ import annotations

from collections import Counter
from dataclasses import replace
import math
from typing import Optional

import numpy as np

from .models import AnalysisConfig, Metrics, SensorConfig


RAW_DIGITAL_CEILING = 262143.0


def estimate_hz(t: np.ndarray) -> float:
    if t.size < 2:
        return math.nan
    duration = float(t[-1] - t[0])
    if duration <= 0:
        return math.nan
    return float((t.size - 1) / duration)


def finite_arrays(t: np.ndarray, red: np.ndarray, ir: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = min(t.size, red.size, ir.size)
    t, red, ir = t[:n], red[:n], ir[:n]
    mask = np.isfinite(t) & np.isfinite(ir)
    return t[mask], red[mask], ir[mask]


def replace_nan_with_last(y: np.ndarray) -> np.ndarray:
    y = np.asarray(y, dtype=float)
    if y.size == 0:
        return y.copy()
    out = np.empty_like(y)
    finite = y[np.isfinite(y)]
    last = float(finite[0]) if finite.size else 0.0
    for i, v in enumerate(y):
        if np.isfinite(v):
            last = float(v)
        out[i] = last
    return out


def moving_average_edge(y: np.ndarray, win: int) -> np.ndarray:
    y = replace_nan_with_last(y)
    if y.size == 0:
        return y.copy()
    win = max(1, int(win))
    if win <= 1:
        return y.copy()
    if win % 2 == 0:
        win += 1
    if y.size < win:
        return np.full_like(y, float(np.mean(y)))
    pad = win // 2
    padded = np.pad(y, (pad, pad), mode="edge")
    kernel = np.ones(win, dtype=float) / win
    return np.convolve(padded, kernel, mode="valid")


def robust_normalize(y: np.ndarray) -> np.ndarray:
    y = replace_nan_with_last(y)
    if y.size == 0:
        return y.copy()
    med = float(np.median(y))
    mad = float(np.median(np.abs(y - med)))
    if not np.isfinite(mad) or mad < 1e-9:
        sd = float(np.std(y))
        mad = sd / 1.4826 if sd > 1e-9 else 1.0
    z = (y - med) / (1.4826 * mad)
    return np.clip(z, -5.0, 5.0)


def processed_ppg(y: np.ndarray, hz: float, cfg: AnalysisConfig) -> np.ndarray:
    y = replace_nan_with_last(y)
    if y.size == 0:
        return y.copy()
    if not np.isfinite(hz) or hz <= 0:
        hz = 100.0
    detrend_win = max(3, int(round(cfg.detrend_seconds * hz)))
    smooth_win = max(1, int(round(cfg.smooth_seconds * hz)))
    baseline = moving_average_edge(y, detrend_win)
    hp = y - baseline
    sm = moving_average_edge(hp, smooth_win)
    return sm


def processed_for_plot(y: np.ndarray, hz: float, cfg: AnalysisConfig) -> np.ndarray:
    return np.clip(robust_normalize(processed_ppg(y, hz, cfg)), -3.5, 3.5)


def detect_artifacts(y: np.ndarray, strict: bool = False) -> np.ndarray:
    y = replace_nan_with_last(y)
    n = y.size
    out = np.zeros(n, dtype=bool)
    if n < 10:
        return out
    dif = np.zeros(n, dtype=float)
    dif[1:] = np.abs(np.diff(y))
    med_y = float(np.median(y))
    mad_y = float(np.median(np.abs(y - med_y)))
    med_d = float(np.median(dif))
    mad_d = float(np.median(np.abs(dif - med_d)))
    mad_y = max(mad_y, 1.0)
    mad_d = max(mad_d, 1.0)
    z_val = np.abs(y - med_y) / (1.4826 * mad_y)
    z_jump = np.abs(dif - med_d) / (1.4826 * mad_d)
    value_limit = 5.0 if strict else 8.0
    jump_limit = 6.0 if strict else 10.0
    expand = 3 if strict else 2
    out[(z_val > value_limit) | (z_jump > jump_limit)] = True
    expanded = out.copy()
    for idx in np.where(out)[0]:
        a = max(0, idx - expand)
        b = min(n, idx + expand + 1)
        expanded[a:b] = True
    return expanded


def percent_true(mask: np.ndarray) -> float:
    return float(np.mean(mask) * 100.0) if mask.size else math.nan


def robust_quality_screen(
    t: np.ndarray,
    red: np.ndarray,
    ir: np.ndarray,
    *,
    settle_seconds: float = 2.0,
    min_samples: int = 100,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, float, str]:
    """Return arrays after a conservative PPG screen, without mutating raw data."""
    t, red, ir = finite_arrays(t, red, ir)
    if t.size == 0:
        return t, red, ir, math.nan, math.nan, "cribado robusto: sin muestras"

    reasons: list[str] = []
    if settle_seconds > 0 and t.size > 0:
        settle_mask = t >= (t[0] + settle_seconds)
        if int(np.sum(settle_mask)) >= min_samples:
            dropped = int(t.size - np.sum(settle_mask))
            t, red, ir = t[settle_mask] - t[settle_mask][0], red[settle_mask], ir[settle_mask]
            if dropped > 0:
                reasons.append(f"primeros {settle_seconds:.1f} s ignorados")

    if t.size < 10:
        return t, red, ir, 100.0, 0.0, "; ".join(reasons)

    art_ir = detect_artifacts(ir, strict=True)
    art_red = detect_artifacts(red, strict=True) if np.any(np.isfinite(red)) else np.zeros_like(art_ir)
    discard = art_ir | art_red | ~np.isfinite(t) | ~np.isfinite(ir)
    if red.size == discard.size:
        discard = discard | ~np.isfinite(red)
    clean = ~discard
    retained_pct = float(np.mean(clean) * 100.0) if clean.size else math.nan
    discard_pct = float(np.mean(discard) * 100.0) if discard.size else math.nan

    if int(np.sum(clean)) >= min_samples:
        reasons.append(f"cribado robusto descarta {discard_pct:.1f} %")
        t2 = t[clean] - t[clean][0]
        red2 = red[clean] if red.size == clean.size else red
        ir2 = ir[clean]
        return t2, red2, ir2, retained_pct, discard_pct, "; ".join(reasons)

    reasons.append(f"cribado robusto insuficiente ({int(np.sum(clean))} muestras limpias); se conserva tramo")
    return t, red, ir, retained_pct, discard_pct, "; ".join(reasons)


def spo2_support_message(m: Metrics) -> str:
    """Short live warning for SpO2 reliability, while keeping BPM usable."""
    if m.n < 100:
        return ""

    low_ir = np.isfinite(m.pi_ir_pct) and m.pi_ir_pct < 0.15
    low_red = np.isfinite(m.pi_red_pct) and m.pi_red_pct < 0.08
    artifacts = np.isfinite(m.artifact_ir_pct) and m.artifact_ir_pct >= 12.0
    saturation = np.isfinite(m.saturation_pct) and m.saturation_pct >= 1.0
    ratio_bad = np.isfinite(m.ratio_r) and not (0.35 <= m.ratio_r <= 1.8)
    missing_spo2 = not (np.isfinite(m.spo2) and np.isfinite(m.ratio_r))

    if saturation:
        return "Aviso SpO2: saturación ADC; baja brillo o amplía rango para oxígeno fiable."
    if low_ir and low_red:
        return "Aviso SpO2: poco apoyo/contacto; PI IR y RED bajos. BPM puede seguir siendo util."
    if low_red:
        return "Aviso SpO2: señal RED pulsátil baja; oxígeno poco fiable."
    if low_ir:
        return "Aviso SpO2: señal IR pulsátil baja; revisa apoyo estable del sensor."
    if artifacts:
        return "Aviso SpO2: movimiento/contacto irregular; oxígeno poco fiable."
    if ratio_bad:
        return "Aviso SpO2: ratio RED/IR fuera de rango; revisa apoyo y luz ambiente."
    if missing_spo2 and np.isfinite(m.dc_ir) and m.dc_ir > 1000:
        return "Aviso SpO2: falta señal RED/IR suficiente para calcular oxígeno."
    return ""


def uniform_resample(t: np.ndarray, y: np.ndarray, hz: Optional[float] = None) -> tuple[np.ndarray, np.ndarray, float]:
    t = np.asarray(t, dtype=float)
    y = replace_nan_with_last(y)
    if t.size < 4:
        return t.copy(), y.copy(), math.nan
    if hz is None or not np.isfinite(hz) or hz <= 0:
        dt = np.median(np.diff(t))
        hz = 1.0 / dt if dt > 0 else estimate_hz(t)
    hz_value = float(hz) if hz is not None and np.isfinite(hz) else 100.0
    hz = float(np.clip(hz_value, 20.0, 500.0))
    grid = np.arange(t[0], t[-1], 1.0 / hz)
    if grid.size < 4:
        return t.copy(), y.copy(), hz
    yy = np.interp(grid, t, y)
    return grid, yy, hz


def find_local_peaks(y: np.ndarray, hz: float, cfg: AnalysisConfig) -> tuple[np.ndarray, float]:
    if y.size < 10 or not np.isfinite(hz) or hz <= 0:
        return np.array([], dtype=int), math.nan
    yy = robust_normalize(y)
    med = float(np.median(yy))
    sd = float(np.std(yy))
    if sd <= 1e-9:
        return np.array([], dtype=int), math.nan
    threshold = med + cfg.peak_threshold_sd * sd
    min_distance = max(1, int(round((60.0 / cfg.bpm_max) * hz * 0.75)))
    peaks: list[int] = []
    last = -10**9
    for i in range(1, yy.size - 1):
        if yy[i] > yy[i - 1] and yy[i] >= yy[i + 1] and yy[i] > threshold:
            if i - last >= min_distance:
                peaks.append(i)
                last = i
            elif peaks and yy[i] > yy[peaks[-1]]:
                peaks[-1] = i
                last = i
    return np.asarray(peaks, dtype=int), threshold


def bpm_from_peak_indices(t: np.ndarray, peaks: np.ndarray, cfg: AnalysisConfig) -> tuple[float, float, str]:
    if peaks.size < 3:
        return math.nan, 0.0, "pocos picos"
    intervals = np.diff(t[peaks])
    intervals = intervals[np.isfinite(intervals)]
    if intervals.size < 2:
        return math.nan, 0.0, "pocos intervalos"
    min_dt = 60.0 / cfg.bpm_max
    max_dt = 60.0 / cfg.bpm_min
    intervals = intervals[(intervals >= min_dt) & (intervals <= max_dt)]
    if intervals.size < 2:
        return math.nan, 0.0, "intervalos fuera de rango"
    bpm_values = 60.0 / intervals
    med_bpm = float(np.median(bpm_values))
    cv = float(np.std(intervals) / np.mean(intervals)) if np.mean(intervals) > 0 else 9.9
    quality = float(np.clip(100.0 * (1.0 - cv * 2.0), 0.0, 100.0))
    return med_bpm, quality, f"cv_intervalos={cv:.2f}"


def estimate_bpm_peaks(t: np.ndarray, ir: np.ndarray, cfg: AnalysisConfig) -> tuple[float, float, str, str, np.ndarray, np.ndarray]:
    hz = estimate_hz(t)
    sig = processed_ppg(ir, hz, cfg)
    tt, yy, hz_u = uniform_resample(t, sig, hz)
    if tt.size < int(max(3.0, 60.0 / cfg.bpm_min * 3) * hz_u):
        return math.nan, 0.0, "ventana corta", "-", np.array([], dtype=int), np.array([], dtype=float)

    peaks_pos, thr_pos = find_local_peaks(yy, hz_u, cfg)
    bpm_pos, q_pos, r_pos = bpm_from_peak_indices(tt, peaks_pos, cfg)

    peaks_neg, thr_neg = find_local_peaks(-yy, hz_u, cfg)
    bpm_neg, q_neg, r_neg = bpm_from_peak_indices(tt, peaks_neg, cfg)

    if q_neg > q_pos:
        return bpm_neg, q_neg, r_neg, "invertida", peaks_neg, tt
    return bpm_pos, q_pos, r_pos, "normal", peaks_pos, tt


def estimate_bpm_fft(t: np.ndarray, ir: np.ndarray, cfg: AnalysisConfig) -> tuple[float, float, str]:
    hz = estimate_hz(t)
    sig = processed_ppg(ir, hz, cfg)
    tt, yy, hz_u = uniform_resample(t, sig, hz)
    if yy.size < max(128, int(4 * hz_u)):
        return math.nan, 0.0, "ventana corta"
    yy = yy - np.mean(yy)
    sd = np.std(yy)
    if sd <= 1e-9:
        return math.nan, 0.0, "sin variabilidad"
    win = np.hanning(yy.size)
    spectrum = np.abs(np.fft.rfft(yy * win))
    freqs = np.fft.rfftfreq(yy.size, d=1.0 / hz_u)
    fmin = cfg.bpm_min / 60.0
    fmax = cfg.bpm_max / 60.0
    mask = (freqs >= fmin) & (freqs <= fmax)
    if not np.any(mask):
        return math.nan, 0.0, "sin banda"
    band = spectrum[mask]
    fband = freqs[mask]
    if band.size < 2 or np.max(band) <= 0:
        return math.nan, 0.0, "sin pico FFT"
    idx = int(np.argmax(band))
    bpm = float(fband[idx] * 60.0)
    sorted_band = np.sort(band)
    peak = float(sorted_band[-1])
    second = float(sorted_band[-2]) if sorted_band.size > 1 else 0.0
    dominance = peak / (second + 1e-9)
    quality = float(np.clip(35.0 + 25.0 * math.log1p(max(0.0, dominance - 1.0)), 0.0, 100.0))
    return bpm, quality, f"dominancia_fft={dominance:.2f}"


def estimate_bpm_autocorr(t: np.ndarray, ir: np.ndarray, cfg: AnalysisConfig) -> tuple[float, float, str]:
    hz = estimate_hz(t)
    sig = processed_ppg(ir, hz, cfg)
    tt, yy, hz_u = uniform_resample(t, sig, hz)
    if yy.size < max(150, int(5 * hz_u)):
        return math.nan, 0.0, "ventana corta"
    yy = yy - np.mean(yy)
    sd = np.std(yy)
    if sd <= 1e-9:
        return math.nan, 0.0, "sin variabilidad"
    yy = yy / sd
    ac = np.correlate(yy, yy, mode="full")[yy.size - 1:]
    ac = ac / (ac[0] + 1e-9)
    min_lag = int(round((60.0 / cfg.bpm_max) * hz_u))
    max_lag = int(round((60.0 / cfg.bpm_min) * hz_u))
    max_lag = min(max_lag, ac.size - 1)
    if max_lag <= min_lag + 2:
        return math.nan, 0.0, "rango lag inválido"
    seg = ac[min_lag:max_lag + 1]
    idx = int(np.argmax(seg)) + min_lag
    peak = float(ac[idx])
    if peak < 0.08:
        return math.nan, 0.0, f"autocorr baja={peak:.2f}"
    bpm = float(60.0 * hz_u / idx)
    quality = float(np.clip(peak * 100.0, 0.0, 100.0))
    return bpm, quality, f"autocorr={peak:.2f}"


def compute_ac_dc(t: np.ndarray, y: np.ndarray, cfg: AnalysisConfig) -> tuple[float, float, float]:
    if y.size < 20:
        return math.nan, math.nan, math.nan
    hz = estimate_hz(t)
    yy = replace_nan_with_last(y)
    dc = float(np.mean(yy))
    ac_sig = processed_ppg(yy, hz, cfg)
    ac = float(np.sqrt(np.mean(ac_sig ** 2)))
    pi = float((ac / dc) * 100.0) if dc > 0 else math.nan
    return ac, dc, pi


def estimate_spo2(t: np.ndarray, red: np.ndarray, ir: np.ndarray, cfg: AnalysisConfig) -> tuple[float, float, str, float, float, float, float, float, float]:
    if t.size < 100 or red.size < 100 or not np.all(np.isfinite(red[:min(10, red.size)])):
        return math.nan, math.nan, "sin RED suficiente", math.nan, math.nan, math.nan, math.nan, math.nan, math.nan
    red = replace_nan_with_last(red)
    ir = replace_nan_with_last(ir)
    ac_red, dc_red, pi_red = compute_ac_dc(t, red, cfg)
    ac_ir, dc_ir, pi_ir = compute_ac_dc(t, ir, cfg)
    if not all(np.isfinite(v) and v > 0 for v in [ac_red, dc_red, ac_ir, dc_ir]):
        return math.nan, math.nan, "AC/DC inválido", ac_red, dc_red, ac_ir, dc_ir, pi_red, pi_ir
    ratio = (ac_red / dc_red) / (ac_ir / dc_ir)
    if cfg.spo2_formula == "linear_104_17":
        spo2 = 104.0 - 17.0 * ratio
    elif cfg.spo2_formula == "linear_110_25":
        spo2 = 110.0 - 25.0 * ratio
    elif cfg.spo2_formula == "custom":
        spo2 = cfg.spo2_custom_a + cfg.spo2_custom_b * ratio + cfg.spo2_custom_c * ratio * ratio
    else:
        spo2 = -45.060 * ratio * ratio + 30.354 * ratio + 94.845
    spo2 = float(np.clip(spo2, 0.0, 100.0))
    return spo2, float(ratio), "estimada no calibrada", ac_red, dc_red, ac_ir, dc_ir, pi_red, pi_ir


def saturation_percent(red: np.ndarray, ir: np.ndarray, adc_range: int | None = None) -> float:
    if red.size == 0 or ir.size == 0:
        return math.nan
    limit = RAW_DIGITAL_CEILING * 0.98
    vals = np.concatenate([replace_nan_with_last(red), replace_nan_with_last(ir)])
    return float(np.mean(vals >= limit) * 100.0)


def estimate_respiration(t: np.ndarray, ir: np.ndarray, hz: float | None = None) -> tuple[float, float, str]:
    if t.size < 80:
        return math.nan, 0.0, "respiracion experimental no estimable: pocas muestras"
    hz_value = estimate_hz(t) if hz is None or not np.isfinite(hz) or hz <= 0 else float(hz)
    if not np.isfinite(hz_value) or hz_value <= 0:
        return math.nan, 0.0, "respiracion experimental no estimable: Hz invalido"
    duration = float(t[-1] - t[0]) if t.size > 1 else math.nan
    if not np.isfinite(duration) or duration < 20.0:
        return math.nan, 0.0, "respiracion experimental no estimable: toma menor de 20 s"

    yy = replace_nan_with_last(ir)
    _tt, yy, hz_u = uniform_resample(t, yy, hz_value)
    if yy.size < max(200, int(20 * hz_u)):
        return math.nan, 0.0, "respiracion experimental no estimable: ventana corta tras remuestreo"

    short_win = max(3, int(round(0.75 * hz_u)))
    long_win = max(short_win + 2, int(round(8.0 * hz_u)))
    envelope = moving_average_edge(yy, short_win)
    baseline = moving_average_edge(envelope, long_win)
    resp_sig = envelope - baseline
    resp_sig = resp_sig - float(np.mean(resp_sig))
    sd = float(np.std(resp_sig))
    if sd <= 1e-9:
        return math.nan, 0.0, "respiracion experimental no estimable: modulacion lenta plana"

    win = np.hanning(resp_sig.size)
    spectrum = np.abs(np.fft.rfft(resp_sig * win))
    freqs = np.fft.rfftfreq(resp_sig.size, d=1.0 / hz_u)
    resp_band = (freqs >= 0.10) & (freqs <= 1.00)
    useful = (freqs >= 0.05) & (freqs <= 1.50)
    if not np.any(resp_band):
        return math.nan, 0.0, "respiracion experimental no estimable: sin banda respiratoria"
    band = spectrum[resp_band]
    fband = freqs[resp_band]
    if band.size < 3 or float(np.max(band)) <= 0:
        return math.nan, 0.0, "respiracion experimental no estimable: sin pico lento"

    idx = int(np.argmax(band))
    rpm = float(fband[idx] * 60.0)
    sorted_band = np.sort(band)
    peak = float(sorted_band[-1])
    second = float(sorted_band[-2]) if sorted_band.size > 1 else 0.0
    dominance = peak / (second + 1e-9)
    band_power = float(np.sum(band**2))
    useful_power = float(np.sum(spectrum[useful] ** 2)) if np.any(useful) else band_power
    band_ratio = band_power / (useful_power + 1e-9)
    cycles = duration * (rpm / 60.0)
    quality = (
        20.0 * min(dominance, 3.0) / 3.0
        + 35.0 * band_ratio
        + 25.0 * np.clip((cycles - 2.0) / 6.0, 0.0, 1.0)
        + 20.0 * np.clip(duration / 45.0, 0.0, 1.0)
    )
    reason = f"experimental: pico lento {rpm:.1f} resp/min; dominancia={dominance:.2f}; banda={band_ratio:.2f}; ciclos~{cycles:.1f}"
    return rpm, float(np.clip(quality, 0.0, 100.0)), reason


def score_and_merge_metrics(t: np.ndarray, red: np.ndarray, ir: np.ndarray, sensor_cfg: SensorConfig, cfg: AnalysisConfig) -> Metrics:
    t, red, ir = finite_arrays(t, red, ir)
    if cfg.ignore_initial_seconds > 0 and t.size > 0:
        mask = t >= (t[0] + cfg.ignore_initial_seconds)
        if int(np.sum(mask)) >= 100:
            t, red, ir = t[mask] - t[mask][0], red[mask], ir[mask]
    screen_t, screen_red, screen_ir, retained_pct, discard_pct, screen_reason = robust_quality_screen(
        t,
        red,
        ir,
        settle_seconds=max(0.0, 2.0 - max(0.0, float(cfg.ignore_initial_seconds))),
        min_samples=100,
    )
    if screen_t.size >= 100:
        t, red, ir = screen_t, screen_red, screen_ir
    n = t.size
    m = Metrics(n=int(n))
    if n < 20:
        m.reason = "menos de 20 muestras"
        return m
    m.hz = estimate_hz(t)
    m.duration_s = float(t[-1] - t[0]) if n > 1 else math.nan
    art_ir = detect_artifacts(ir, strict=True)
    art_red = detect_artifacts(red, strict=True) if np.any(np.isfinite(red)) else np.zeros_like(art_ir)
    m.artifact_ir_pct = discard_pct if np.isfinite(discard_pct) else percent_true(art_ir)
    m.artifact_red_pct = percent_true(art_red)

    clean = ~art_ir & ~art_red & np.isfinite(ir)
    if int(np.sum(clean)) >= 100:
        tt = t[clean]
        rr = red[clean] if red.size == clean.size else red
        ii = ir[clean]
    else:
        tt, rr, ii = t, red, ir

    m.bpm_peak, q_peak, r_peak, m.polarity, peaks, _ = estimate_bpm_peaks(tt, ii, cfg)
    m.peaks_count = int(peaks.size)
    m.bpm_fft, q_fft, r_fft = estimate_bpm_fft(tt, ii, cfg)
    m.bpm_autocorr, q_acorr, r_acorr = estimate_bpm_autocorr(tt, ii, cfg)

    candidates: list[tuple[float, float, str]] = []
    for value, q, name in [(m.bpm_peak, q_peak, "picos"), (m.bpm_fft, q_fft, "FFT"), (m.bpm_autocorr, q_acorr, "autocorr")]:
        if np.isfinite(value) and cfg.bpm_min <= value <= cfg.bpm_max and q > 10:
            candidates.append((float(value), float(q), name))

    reasons = [screen_reason, r_peak, r_fft, r_acorr]
    if not candidates:
        m.bpm = math.nan
        bpm_quality = 0.0
        reasons.append("sin candidatos BPM válidos")
    elif len(candidates) == 1:
        m.bpm = candidates[0][0]
        bpm_quality = min(55.0, candidates[0][1])
        reasons.append(f"solo {candidates[0][2]}")
    else:
        values = np.asarray([c[0] for c in candidates], dtype=float)
        weights = np.asarray([max(c[1], 1.0) for c in candidates], dtype=float)
        spread = float(np.max(values) - np.min(values))
        if spread <= 12.0:
            m.bpm = float(np.average(values, weights=weights))
            bpm_quality = float(np.clip(np.mean(weights) + max(0.0, 20.0 - spread), 0.0, 100.0))
            reasons.append(f"estimadores coherentes spread={spread:.1f}")
        else:
            best = max(candidates, key=lambda c: (c[1] + (8.0 if c[2] == "FFT" else 0.0)))
            m.bpm = best[0]
            bpm_quality = float(np.clip(best[1] * 0.50, 0.0, 55.0))
            reasons.append(f"estimadores discrepantes; se usa {best[2]} spread={spread:.1f}")

    spo2, r, spo2_reason, ac_red, dc_red, ac_ir, dc_ir, pi_red, pi_ir = estimate_spo2(tt, rr, ii, cfg)
    m.spo2 = spo2
    m.ratio_r = r
    m.ac_red = ac_red
    m.dc_red = dc_red
    m.ac_ir = ac_ir
    m.dc_ir = dc_ir
    m.pi_red_pct = pi_red
    m.pi_ir_pct = pi_ir
    m.saturation_pct = saturation_percent(rr, ii, sensor_cfg.adc)
    m.resp_rate_rpm, m.resp_quality, m.resp_reason = estimate_respiration(tt, ii, m.hz)

    if not np.isfinite(m.dc_ir) or m.dc_ir <= 0:
        m.contact_label = "sin contacto"
    elif np.isfinite(m.pi_ir_pct) and m.pi_ir_pct >= 0.15 and m.dc_ir > 1000:
        m.contact_label = "contacto útil"
    elif m.dc_ir > 1000:
        m.contact_label = "contacto débil / baja perfusión"
    else:
        m.contact_label = "señal muy baja"

    artifact_penalty = 0.0 if not np.isfinite(m.artifact_ir_pct) else min(35.0, m.artifact_ir_pct * 2.0)
    pi_bonus = 0.0
    if np.isfinite(m.pi_ir_pct):
        pi_bonus = float(np.clip(m.pi_ir_pct * 12.0, 0.0, 20.0))
    sat_penalty = 0.0 if not np.isfinite(m.saturation_pct) else min(30.0, m.saturation_pct)
    m.quality = float(np.clip(bpm_quality + pi_bonus - artifact_penalty - sat_penalty, 0.0, 100.0))

    if not np.isfinite(m.bpm):
        m.quality_label = "BPM no fiable"
    elif m.quality >= 70:
        m.quality_label = "buena"
    elif m.quality >= cfg.min_quality_to_accept:
        m.quality_label = "aceptable"
    else:
        m.quality_label = "dudosa"

    if np.isfinite(m.bpm) and m.quality < cfg.min_quality_to_accept:
        reasons.append("BPM por debajo del umbral de calidad")

    reasons.append(spo2_reason)
    m.reason = " | ".join([r for r in reasons if r])
    return m


def block_bpm(t: np.ndarray, ir: np.ndarray, sensor_cfg: SensorConfig, cfg: AnalysisConfig, block_s: int = 2) -> list[float]:
    if t.size < 100:
        return []
    out: list[float] = []
    total = int(math.ceil(float(t[-1]) / block_s))
    for i in range(total):
        start = i * block_s
        end = start + block_s
        a = max(0.0, start - 3.0)
        b = min(float(t[-1]), end + 3.0)
        mask = (t >= a) & (t <= b)
        if int(np.sum(mask)) < 250:
            out.append(math.nan)
            continue
        met = score_and_merge_metrics(t[mask] - t[mask][0], np.full(int(np.sum(mask)), math.nan), ir[mask], sensor_cfg, cfg)
        out.append(met.bpm if met.quality >= 35 else math.nan)
    return out


def _stable_window_shape_score(
    local_t: np.ndarray,
    local_red: np.ndarray,
    local_ir: np.ndarray,
    sensor_cfg: SensorConfig,
    cfg: AnalysisConfig,
) -> tuple[bool, float, list[str], dict[str, float]]:
    local_cfg = replace(cfg, ignore_initial_seconds=0.0)
    met = score_and_merge_metrics(local_t, local_red, local_ir, sensor_cfg, local_cfg)
    reasons: list[str] = []
    values = {
        "artifact_ir_pct": met.artifact_ir_pct,
        "artifact_red_pct": met.artifact_red_pct,
        "saturation_pct": met.saturation_pct,
        "pi_ir_pct": met.pi_ir_pct,
        "ir_drift": math.nan,
        "red_drift": math.nan,
        "ir_baseline_shift_pct": math.nan,
        "red_baseline_shift_pct": math.nan,
    }
    if np.isfinite(met.artifact_ir_pct) and met.artifact_ir_pct > 12.0:
        return False, -25.0, [f"artefactos IR altos ({met.artifact_ir_pct:.1f} %)"], values
    if np.isfinite(met.artifact_red_pct) and met.artifact_red_pct > 14.0:
        return False, -20.0, [f"artefactos RED altos ({met.artifact_red_pct:.1f} %)"], values
    if np.isfinite(met.saturation_pct) and met.saturation_pct > 1.0:
        return False, -20.0, [f"saturación ADC ({met.saturation_pct:.1f} %)"], values
    if np.isfinite(met.pi_ir_pct):
        if met.pi_ir_pct < 0.04:
            return False, -15.0, [f"PI IR demasiado bajo ({met.pi_ir_pct:.3f} %)"], values
        if met.pi_ir_pct < 0.12:
            reasons.append(f"PI IR bajo ({met.pi_ir_pct:.3f} %)")

    def drift_span(values: np.ndarray) -> float:
        finite = np.isfinite(local_t) & np.isfinite(values)
        if int(np.sum(finite)) < 20:
            return math.inf
        x = local_t[finite] - float(local_t[finite][0])
        y = robust_normalize(values[finite])
        if x.size < 20 or float(x[-1] - x[0]) <= 0:
            return math.inf
        slope, _intercept = np.polyfit(x, y, 1)
        return float(abs(slope) * (x[-1] - x[0]))

    def baseline_shift_pct(values: np.ndarray) -> float:
        finite = np.isfinite(local_t) & np.isfinite(values)
        if int(np.sum(finite)) < 20:
            return math.inf
        x = local_t[finite] - float(local_t[finite][0])
        y = values[finite]
        if x.size < 20 or float(x[-1] - x[0]) <= 0:
            return math.inf
        slope, _intercept = np.polyfit(x, y, 1)
        shift = float(abs(slope) * (x[-1] - x[0]))
        dc = max(abs(float(np.median(y))), 1.0)
        return float((shift / dc) * 100.0)

    ir_drift = drift_span(local_ir)
    red_drift = drift_span(local_red) if np.any(np.isfinite(local_red)) else math.nan
    ir_shift_pct = baseline_shift_pct(local_ir)
    red_shift_pct = baseline_shift_pct(local_red) if np.any(np.isfinite(local_red)) else math.nan
    values["ir_drift"] = ir_drift
    values["red_drift"] = red_drift
    values["ir_baseline_shift_pct"] = ir_shift_pct
    values["red_baseline_shift_pct"] = red_shift_pct
    if np.isfinite(ir_shift_pct) and ir_shift_pct > 10.0:
        return False, -25.0, [f"deriva de línea base IR alta ({ir_shift_pct:.1f} %)"], values
    if np.isfinite(red_shift_pct) and red_shift_pct > 10.0:
        return False, -18.0, [f"deriva de línea base RED alta ({red_shift_pct:.1f} %)"], values
    if np.isfinite(ir_drift):
        if ir_drift > 3.2:
            return False, -25.0, [f"deriva IR alta ({ir_drift:.2f})"], values
        if ir_drift > 1.8:
            reasons.append(f"deriva IR moderada ({ir_drift:.2f})")
    if np.isfinite(red_drift):
        if red_drift > 3.6:
            return False, -18.0, [f"deriva RED alta ({red_drift:.2f})"], values
        if red_drift > 2.2:
            reasons.append(f"deriva RED moderada ({red_drift:.2f})")

    drift_penalty = 0.0
    if np.isfinite(ir_drift):
        drift_penalty += min(18.0, ir_drift * 6.0)
    if np.isfinite(red_drift):
        drift_penalty += min(12.0, red_drift * 4.0)
    artifact_penalty = 0.0
    if np.isfinite(met.artifact_ir_pct):
        if met.artifact_ir_pct > 6.0:
            reasons.append(f"artefactos IR moderados ({met.artifact_ir_pct:.1f} %)")
        artifact_penalty += met.artifact_ir_pct * 1.5
    if np.isfinite(met.artifact_red_pct):
        if met.artifact_red_pct > 8.0:
            reasons.append(f"artefactos RED moderados ({met.artifact_red_pct:.1f} %)")
        artifact_penalty += met.artifact_red_pct
    pi_bonus = min(12.0, max(0.0, met.pi_ir_pct * 3.0)) if np.isfinite(met.pi_ir_pct) else 0.0
    low_signal_penalty = max(0.0, (0.12 - met.pi_ir_pct) * 120.0) if np.isfinite(met.pi_ir_pct) else 0.0
    return True, float(70.0 + pi_bonus - low_signal_penalty - drift_penalty - artifact_penalty), reasons, values


def _stable_reason_summary(total_windows: int, accepted_windows: int, rejection_counts: Counter[str], extra: list[str] | None = None) -> str:
    parts: list[str] = []
    if accepted_windows:
        parts.append(f"{accepted_windows}/{total_windows} ventanas de 5 s pasaron el filtro estable")
    else:
        parts.append(f"0/{total_windows} ventanas de 5 s pasaron el filtro estable")
    if rejection_counts:
        grouped: Counter[str] = Counter()
        for reason, count in rejection_counts.items():
            label = reason
            if not reason.startswith("BPM fuera de referencia manual"):
                label = reason.split(" (", 1)[0]
            grouped[label] += count
        common = ", ".join(f"{reason} ({count})" for reason, count in grouped.most_common(4))
        parts.append(f"motivos principales: {common}")
    if extra:
        parts.extend(extra)
    return "; ".join(part for part in parts if part)


def _stable_reference_tolerance(reference_bpm: float) -> float:
    return float(max(24.0, min(32.0, abs(reference_bpm) * 0.38)))


def stable_bpm_segment(
    t: np.ndarray,
    red: np.ndarray,
    ir: np.ndarray,
    sensor_cfg: SensorConfig,
    cfg: AnalysisConfig,
    window_s: float = 5.0,
    reference_bpm: float | None = None,
) -> Metrics:
    out = Metrics()
    ref_bpm = math.nan
    if reference_bpm is not None:
        try:
            ref_bpm = float(reference_bpm)
        except (TypeError, ValueError):
            ref_bpm = math.nan
    ref_ok = np.isfinite(ref_bpm) and ref_bpm > 0
    ref_tolerance = _stable_reference_tolerance(ref_bpm) if ref_ok else math.nan
    n = min(t.size, red.size, ir.size)
    if n < 80:
        return out
    t = np.asarray(t[:n], dtype=float)
    red = np.asarray(red[:n], dtype=float)
    ir = np.asarray(ir[:n], dtype=float)
    valid = np.isfinite(t) & np.isfinite(ir)
    if int(np.sum(valid)) < 80:
        return out
    t = t[valid]
    red = red[valid]
    ir = ir[valid]
    t = t - float(t[0])
    duration = float(t[-1] - t[0]) if t.size > 1 else math.nan
    if not np.isfinite(duration) or duration < window_s:
        return out
    hz = estimate_hz(t)
    min_samples = 80
    if np.isfinite(hz) and hz > 0:
        min_samples = max(min_samples, int(round(hz * window_s * 0.45)))
    best: tuple[float, float, float, Metrics, int, list[str]] | None = None
    total_windows = 0
    accepted_windows = 0
    rejection_counts: Counter[str] = Counter()
    step_s = 0.5
    start = 0.0
    max_start = max(0.0, duration - window_s)
    while start <= max_start + 1e-9:
        total_windows += 1
        end = start + window_s
        mask = (t >= start) & (t <= end)
        samples = int(np.sum(mask))
        if samples >= min_samples:
            local_t = t[mask] - float(t[mask][0])
            local_red = red[mask]
            local_ir = ir[mask]
            shape_ok, shape_score, shape_reasons, shape_values = _stable_window_shape_score(local_t, local_red, local_ir, sensor_cfg, cfg)
            if not shape_ok:
                rejection_counts.update(shape_reasons or ["señal no limpia"])
                start += step_s
                continue
            bpm_peak, q_peak, _r_peak, _polarity, _peaks, _peak_t = estimate_bpm_peaks(local_t, local_ir, cfg)
            bpm_fft, q_fft, _r_fft = estimate_bpm_fft(local_t, local_ir, cfg)
            bpm_acorr, q_acorr, _r_acorr = estimate_bpm_autocorr(local_t, local_ir, cfg)
            candidates = [
                (float(value), float(quality))
                for value, quality in ((bpm_peak, q_peak), (bpm_fft, q_fft), (bpm_acorr, q_acorr))
                if np.isfinite(value) and cfg.bpm_min <= value <= cfg.bpm_max and quality > 10.0
            ]
            if not candidates:
                rejection_counts["sin candidatos BPM válidos"] += 1
                start += step_s
                continue
            values = [value for value, _quality in candidates]
            qualities = [quality for _value, quality in candidates]
            spread = (max(values) - min(values)) if len(values) >= 2 else 18.0
            needs_strong_agreement = bool(shape_reasons)
            if needs_strong_agreement and (len(candidates) < 2 or spread > 12.0):
                rejection_counts["señal débil/variable sin acuerdo suficiente"] += 1
                start += step_s
                continue
            if len(candidates) >= 2 and spread <= 18.0:
                weights = np.asarray(qualities, dtype=float)
                bpm = float(np.average(np.asarray(values, dtype=float), weights=weights))
                quality = float(np.clip(np.mean(weights) + max(0.0, 20.0 - spread), 0.0, 100.0))
            else:
                best_idx = int(np.argmax(qualities))
                bpm = values[best_idx]
                quality = float(np.clip(qualities[best_idx] * 0.55, 0.0, 60.0))
            quality_floor = max(35.0, cfg.min_quality_to_accept)
            if needs_strong_agreement:
                quality_floor = max(60.0, quality_floor)
            if quality < quality_floor:
                rejection_counts[f"calidad de ventana baja ({quality:.0f}/100)"] += 1
                start += step_s
                continue
            if ref_ok:
                ref_diff = abs(bpm - ref_bpm)
                if ref_diff > ref_tolerance:
                    rejection_counts[f"BPM fuera de referencia manual (> {ref_tolerance:.0f} BPM)"] += 1
                    start += step_s
                    continue
            accepted_windows += 1
            met = Metrics(
                n=samples,
                hz=estimate_hz(local_t),
                duration_s=float(local_t[-1] - local_t[0]) if local_t.size > 1 else math.nan,
                bpm=bpm,
                bpm_peak=bpm_peak,
                bpm_fft=bpm_fft,
                bpm_autocorr=bpm_acorr,
                quality=quality,
                quality_label="estable",
            )
            score = float(quality + shape_score - min(45.0, spread * 2.0) + min(10.0, samples / max(1, min_samples)))
            if best is None or score > best[0]:
                best_notes = list(shape_reasons)
                best_notes.append(f"acuerdo entre estimadores: spread {spread:.1f} BPM")
                if np.isfinite(shape_values.get("pi_ir_pct", math.nan)):
                    best_notes.append(f"PI IR {shape_values['pi_ir_pct']:.3f} %")
                if np.isfinite(shape_values.get("ir_drift", math.nan)):
                    best_notes.append(f"deriva IR {shape_values['ir_drift']:.2f}")
                if np.isfinite(shape_values.get("red_drift", math.nan)):
                    best_notes.append(f"deriva RED {shape_values['red_drift']:.2f}")
                if np.isfinite(shape_values.get("ir_baseline_shift_pct", math.nan)):
                    best_notes.append(f"deriva línea base IR {shape_values['ir_baseline_shift_pct']:.1f} %")
                if ref_ok:
                    best_notes.append(f"diferencia con referencia manual {abs(bpm - ref_bpm):.1f} BPM")
                best = (score, float(start), float(end), met, samples, best_notes)
        else:
            rejection_counts["pocas muestras en ventana"] += 1
        start += step_s
    if best is None:
        out.bpm_estable_motivo = _stable_reason_summary(total_windows, accepted_windows, rejection_counts)
        return out
    if total_windows > 1 and accepted_windows < 2:
        out.bpm_estable_motivo = _stable_reason_summary(
            total_windows,
            accepted_windows,
            rejection_counts,
            ["solo apareció una ventana aislada; se deja vacío para evitar un tramo frágil"],
        )
        return out
    _score, start, end, met, samples, best_notes = best
    out.bpm_estable_5s = float(met.bpm)
    out.bpm_estable_inicio_s = start
    out.bpm_estable_fin_s = end
    out.bpm_estable_calidad = float(met.quality)
    out.bpm_estable_muestras = int(samples)
    out.bpm_estable_motivo = _stable_reason_summary(total_windows, accepted_windows, rejection_counts, [f"ventana elegida {start:.2f}-{end:.2f} s", *best_notes])
    return out

