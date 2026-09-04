from __future__ import annotations

import numpy as np


def _rolloff_mask(freqs: np.ndarray, low_hz: float, high_hz: float, roll_hz: float) -> np.ndarray:
    """Raised-cosine transition mask, 1.0 inside [low_hz, high_hz], 0.0 beyond +/- roll_hz.

    An ideal brick-wall cutoff has poor time-domain behaviour (Gibbs ringing) whenever a
    strong out-of-band tone sits close to the edge - e.g. a cardiac harmonic just above the
    respiratory band's high cutoff. That ringing can masquerade as a spurious respiratory
    peak right at the edge, so band edges are tapered instead of cut sharply.
    """
    mask = np.zeros_like(freqs)
    roll_hz = max(roll_hz, 1e-9)
    mask[(freqs >= low_hz) & (freqs <= high_hz)] = 1.0

    left = (freqs >= low_hz - roll_hz) & (freqs < low_hz)
    mask[left] = 0.5 * (1.0 + np.cos(np.pi * (low_hz - freqs[left]) / roll_hz))

    right = (freqs > high_hz) & (freqs <= high_hz + roll_hz)
    mask[right] = 0.5 * (1.0 + np.cos(np.pi * (freqs[right] - high_hz) / roll_hz))

    return mask


def fft_lowpass(y: np.ndarray, hz: float, cutoff_hz: float) -> np.ndarray:
    """Zero-phase low-pass via FFT-domain masking with a tapered edge."""
    y = np.asarray(y, dtype=float)
    n = y.size
    if n < 4 or not np.isfinite(hz) or hz <= 0:
        return y.copy()
    freqs = np.fft.rfftfreq(n, d=1.0 / hz)
    spectrum = np.fft.rfft(y)
    roll_hz = max(0.1 * cutoff_hz, hz / n * 3.0)
    mask = _rolloff_mask(freqs, 0.0, cutoff_hz, roll_hz)
    return np.fft.irfft(spectrum * mask, n=n)


def fft_bandpass(y: np.ndarray, hz: float, low_hz: float, high_hz: float) -> np.ndarray:
    """Zero-phase band-pass via FFT-domain masking with tapered edges (see _rolloff_mask)."""
    y = np.asarray(y, dtype=float)
    n = y.size
    if n < 4 or not np.isfinite(hz) or hz <= 0:
        return y.copy()
    freqs = np.fft.rfftfreq(n, d=1.0 / hz)
    spectrum = np.fft.rfft(y)
    bandwidth = max(high_hz - low_hz, 1e-9)
    # Keep the taper narrow: a wide roll-off would leak a nearby strong out-of-band tone
    # (e.g. a low cardiac fundamental just above the respiratory band's high edge) straight
    # into the "pass" region, which is worse than the ringing it was meant to suppress.
    min_roll = hz / n * 3.0
    roll_hz = float(np.clip(0.08 * bandwidth, min_roll, max(min_roll, 0.05)))
    mask = _rolloff_mask(freqs, low_hz, high_hz, roll_hz)
    return np.fft.irfft(spectrum * mask, n=n)
