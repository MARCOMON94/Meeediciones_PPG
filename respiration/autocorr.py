from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class AutocorrPeak:
    rr: float = math.nan
    strength: float = 0.0


def autocorr_rr(x: np.ndarray, hz: float, rr_min: float, rr_max: float) -> AutocorrPeak:
    x = np.asarray(x, dtype=float)
    if x.size < 8 or not np.isfinite(hz) or hz <= 0 or rr_min <= 0 or rr_max <= 0:
        return AutocorrPeak()
    x = x - float(np.mean(x))
    sd = float(np.std(x))
    if sd <= 1e-9:
        return AutocorrPeak()
    x = x / sd

    ac = np.correlate(x, x, mode="full")[x.size - 1:]
    if ac[0] <= 0:
        return AutocorrPeak()
    ac = ac / ac[0]

    min_lag = int(round((60.0 / rr_max) * hz))
    max_lag = int(round((60.0 / rr_min) * hz))
    max_lag = min(max_lag, ac.size - 1)
    if max_lag <= min_lag + 1:
        return AutocorrPeak()

    seg = ac[min_lag:max_lag + 1]
    idx = int(np.argmax(seg)) + min_lag
    peak = float(ac[idx])
    if peak < 0.05 or idx <= 0:
        return AutocorrPeak()

    # Edge guard: autocorrelation is highest near lag 0 for almost any signal and decays
    # from there. If the chosen lag sits right at the search boundary (rr_max) and
    # correlation was already at least as strong just before entering the window, that's
    # leftover near-zero-lag correlation (e.g. residual cardiac-band content), not genuine
    # periodicity at rr_max - reject it instead of reporting the boundary lag.
    if idx == min_lag and min_lag > 0 and ac[min_lag - 1] >= peak:
        return AutocorrPeak()
    if idx == max_lag and max_lag < ac.size - 1 and ac[max_lag + 1] >= peak:
        return AutocorrPeak()

    rr = float(60.0 * hz / idx)
    strength = float(np.clip(peak * 100.0, 0.0, 100.0))
    return AutocorrPeak(rr=rr, strength=strength)


def method_agreement(rr_a: float, rr_b: float) -> float:
    if not (np.isfinite(rr_a) and np.isfinite(rr_b)):
        return math.nan
    diff = abs(rr_a - rr_b)
    return float(np.clip(100.0 - diff * 4.0, 0.0, 100.0))
