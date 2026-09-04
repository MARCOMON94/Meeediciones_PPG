from __future__ import annotations

import numpy as np

from .config import RespirationConfig
from .filters import fft_bandpass


def compute_riiv(signal: np.ndarray, hz: float, cfg: RespirationConfig) -> np.ndarray:
    """Respiratory-Induced Intensity Variation: band-pass the (already anti-aliased,
    down-sampled) intensity signal to the respiratory band. The band-pass alone removes
    both the extremely slow baseline drift (below respiratory_low_hz) and anything faster
    than respiratory_high_hz, so no separate detrend step is needed here.
    """
    signal = np.asarray(signal, dtype=float)
    if signal.size < 8 or not np.isfinite(hz) or hz <= 0:
        return np.asarray([], dtype=float)
    return fft_bandpass(signal, hz, cfg.respiratory_low_hz, cfg.respiratory_high_hz)
