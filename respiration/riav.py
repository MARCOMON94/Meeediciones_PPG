from __future__ import annotations

import numpy as np

from ppg_suite.models import AnalysisConfig
from ppg_suite.processing import bpm_from_peak_indices, find_local_peaks, processed_ppg

from .config import RespirationConfig
from .filters import fft_bandpass


def _beat_analysis_config(cfg: RespirationConfig) -> AnalysisConfig:
    # A moving-average detrend window has spectral nulls at k/window Hz; the cardiac
    # default (2.0 s -> first null at 0.5 Hz = 30 rpm) sits inside the respiratory band
    # and can suppress genuine cardiac-beat timing whenever RR is near a null. Keep the
    # window's first null safely above rr_max so beat detection never resonates with RR.
    safe_detrend = float(min(1.0, 30.0 / max(cfg.rr_max, 1.0)))
    return AnalysisConfig(
        bpm_min=cfg.beat_min_bpm, bpm_max=cfg.beat_max_bpm, detrend_seconds=safe_detrend, ignore_initial_seconds=0.0
    )


def detect_cardiac_beats(
    t: np.ndarray, y: np.ndarray, hz: float, cfg: RespirationConfig
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Detect cardiac beat peak times/values and their preceding local trough values.

    Uses ppg_suite.processing's cardiac peak finder purely for beat *timing* (it applies
    the short cardiac detrend internally). That detrend is fine as a timing reference for
    individual pulses, but is never used for the RIIV band itself.
    """
    y = np.asarray(y, dtype=float)
    t = np.asarray(t, dtype=float)
    n = min(t.size, y.size)
    t, y = t[:n], y[:n]
    empty = (np.array([], dtype=float), np.array([], dtype=float), np.array([], dtype=float))
    if n < 20 or not np.isfinite(hz) or hz <= 0:
        return empty

    a_cfg = _beat_analysis_config(cfg)
    proc = processed_ppg(y, hz, a_cfg)

    peaks_pos, _ = find_local_peaks(proc, hz, a_cfg)
    _bpm_pos, q_pos, _ = bpm_from_peak_indices(t, peaks_pos, a_cfg)
    peaks_neg, _ = find_local_peaks(-proc, hz, a_cfg)
    _bpm_neg, q_neg, _ = bpm_from_peak_indices(t, peaks_neg, a_cfg)

    # find_local_peaks on -proc marks the RAW signal's local minima (troughs), not its
    # peaks - when that polarity wins, the roles of "marker" and "opposite extremum"
    # below must be swapped, or amplitude collapses to ~0 (marker minus itself).
    use_inverted = q_neg > q_pos
    peaks = peaks_neg if use_inverted else peaks_pos
    if peaks.size < 4:
        return empty

    peak_times = t[peaks]
    marker_values = y[peaks]
    opposite_values = np.empty(peaks.size, dtype=float)
    prev_idx = 0
    for i, p_idx in enumerate(peaks):
        p_idx = int(p_idx)
        window = y[prev_idx:p_idx + 1]
        if window.size:
            opposite_values[i] = float(np.max(window)) if use_inverted else float(np.min(window))
        else:
            opposite_values[i] = float(y[p_idx])
        prev_idx = p_idx

    if use_inverted:
        trough_values, peak_values = marker_values, opposite_values
    else:
        peak_values, trough_values = marker_values, opposite_values
    return peak_times, peak_values, trough_values


def compute_riav(
    peak_times: np.ndarray,
    peak_values: np.ndarray,
    trough_values: np.ndarray,
    resp_t: np.ndarray,
    resp_hz: float,
    cfg: RespirationConfig,
) -> np.ndarray:
    if peak_times.size < 4 or resp_t.size < 8:
        return np.asarray([], dtype=float)
    amplitude = peak_values - trough_values
    finite = np.isfinite(amplitude) & np.isfinite(peak_times)
    if int(np.sum(finite)) < 4:
        return np.asarray([], dtype=float)
    pt = peak_times[finite]
    amp = amplitude[finite]
    order = np.argsort(pt)
    pt, amp = pt[order], amp[order]

    med = float(np.median(amp))
    mad = float(np.median(np.abs(amp - med)))
    if mad > 1e-9:
        z = np.abs(amp - med) / (1.4826 * mad)
        amp = np.where(z > 6.0, med, amp)

    interpolated = np.interp(resp_t, pt, amp, left=amp[0], right=amp[-1])
    return fft_bandpass(interpolated, resp_hz, cfg.respiratory_low_hz, cfg.respiratory_high_hz)
