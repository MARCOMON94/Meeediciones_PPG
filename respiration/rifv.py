from __future__ import annotations

import numpy as np

from .config import RespirationConfig
from .filters import fft_bandpass


def compute_rifv(peak_times: np.ndarray, resp_t: np.ndarray, resp_hz: float, cfg: RespirationConfig) -> np.ndarray:
    """Respiratory-Induced Frequency Variation, from cardiac inter-beat intervals.

    RIFV is expected to be the weakest estimator in some species/animals; it must be able
    to return an empty result (never raise) so it can be dropped from fusion without
    invalidating RIIV/RIAV.
    """
    peak_times = np.asarray(peak_times, dtype=float)
    if peak_times.size < 5 or resp_t.size < 8:
        return np.asarray([], dtype=float)

    order = np.argsort(peak_times)
    peak_times = peak_times[order]
    ibi = np.diff(peak_times)
    valid = np.isfinite(ibi) & (ibi > 0)
    if int(np.sum(valid)) < 4:
        return np.asarray([], dtype=float)
    ibi = ibi[valid]
    mid_times = peak_times[:-1][valid] + ibi / 2.0
    instantaneous_hr = 60.0 / ibi

    med = float(np.median(instantaneous_hr))
    mad = float(np.median(np.abs(instantaneous_hr - med)))
    if mad > 1e-9:
        z = np.abs(instantaneous_hr - med) / (1.4826 * mad)
        instantaneous_hr = np.where(z > 6.0, med, instantaneous_hr)

    interpolated = np.interp(resp_t, mid_times, instantaneous_hr, left=instantaneous_hr[0], right=instantaneous_hr[-1])
    return fft_bandpass(interpolated, resp_hz, cfg.respiratory_low_hz, cfg.respiratory_high_hz)
