from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


def welch_psd(x: np.ndarray, hz: float, nperseg: int | None = None, overlap: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """Hand-rolled Welch-style PSD (segment-averaged, Hanning-windowed periodogram).

    scipy is not a project dependency; this mirrors scipy.signal.welch's default
    behaviour closely enough for peak-picking and band-power comparisons.
    """
    x = np.asarray(x, dtype=float)
    n = x.size
    if n < 8 or not np.isfinite(hz) or hz <= 0:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)
    x = x - float(np.mean(x))

    if nperseg is None:
        # Shorter segments -> more averaged segments -> lower variance per PSD bin, so a
        # single noisy bin is less likely to masquerade as a dominant respiratory peak.
        nperseg = min(n, max(64, int(round(hz * 15.0))))
    nperseg = int(np.clip(nperseg, 8, n))
    step = max(1, int(round(nperseg * (1.0 - overlap))))

    window = np.hanning(nperseg)
    win_power = float(np.sum(window ** 2))
    freqs = np.fft.rfftfreq(nperseg, d=1.0 / hz)
    accum = np.zeros(freqs.size, dtype=float)
    segments = 0
    start = 0
    while start + nperseg <= n:
        seg = x[start:start + nperseg] * window
        spectrum = np.fft.rfft(seg)
        psd = (np.abs(spectrum) ** 2) / (hz * win_power)
        psd[1:-1] *= 2.0
        accum += psd
        segments += 1
        start += step

    if segments == 0:
        seg = x * np.hanning(n)
        spectrum = np.fft.rfft(seg)
        win_power = float(np.sum(np.hanning(n) ** 2))
        psd = (np.abs(spectrum) ** 2) / (hz * max(win_power, 1e-9))
        psd[1:-1] *= 2.0
        return np.fft.rfftfreq(n, d=1.0 / hz), psd

    return freqs, accum / segments


@dataclass
class SpectralPeak:
    rr: float = math.nan
    peak_power: float = math.nan
    band_power: float = math.nan
    peak_band_ratio: float = math.nan
    prominence: float = math.nan
    second_peak_diff: float = math.nan
    peak_width_hz: float = math.nan


def spectral_peak_metrics(freqs: np.ndarray, psd: np.ndarray, low_hz: float, high_hz: float) -> SpectralPeak:
    if freqs.size == 0 or psd.size == 0:
        return SpectralPeak()
    band_mask = (freqs >= low_hz) & (freqs <= high_hz)
    if not np.any(band_mask):
        return SpectralPeak()
    band_freqs = freqs[band_mask]
    band_psd = psd[band_mask]
    if band_psd.size < 2 or float(np.max(band_psd)) <= 0:
        return SpectralPeak()

    idx = int(np.argmax(band_psd))
    peak_power = float(band_psd[idx])
    peak_freq = float(band_freqs[idx])

    # Edge-leakage guard: slow drift just below low_hz (thermal/contact settling, far
    # stronger than any real respiratory modulation) has a spectral tail that decays but
    # doesn't vanish at the cutoff. If the "peak" sits at the very edge of the search band
    # and the spectrum was already comparably strong just outside it, this is that tail,
    # not a genuine localized peak - reject it rather than reporting the tail's frequency.
    band_indices = np.flatnonzero(band_mask)
    if idx == 0 and band_indices[0] > 0:
        guard_power = float(psd[band_indices[0] - 1])
        if guard_power >= 0.5 * peak_power:
            return SpectralPeak()
    if idx == band_psd.size - 1 and band_indices[-1] < psd.size - 1:
        guard_power = float(psd[band_indices[-1] + 1])
        if guard_power >= 0.5 * peak_power:
            return SpectralPeak()
    band_power = float(np.sum(band_psd))
    peak_band_ratio = float(peak_power / band_power) if band_power > 0 else math.nan

    exclude = max(1, band_psd.size // 20)
    lo = max(0, idx - exclude)
    hi = min(band_psd.size, idx + exclude + 1)
    other = np.concatenate([band_psd[:lo], band_psd[hi:]])
    second_peak = float(np.max(other)) if other.size else 0.0
    second_peak_diff = float(peak_power - second_peak)

    median_band = float(np.median(band_psd))
    prominence = float(peak_power / max(median_band, 1e-12))

    half_power = peak_power / 2.0
    left = idx
    while left > 0 and band_psd[left] > half_power:
        left -= 1
    right = idx
    while right < band_psd.size - 1 and band_psd[right] > half_power:
        right += 1
    peak_width_hz = float(band_freqs[right] - band_freqs[left]) if right > left else math.nan

    return SpectralPeak(
        rr=peak_freq * 60.0,
        peak_power=peak_power,
        band_power=band_power,
        peak_band_ratio=peak_band_ratio,
        prominence=prominence,
        second_peak_diff=second_peak_diff,
        peak_width_hz=peak_width_hz,
    )
