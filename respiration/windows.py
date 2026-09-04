from __future__ import annotations

import math

import numpy as np

from .config import RespirationConfig
from .models import WindowedResult, WindowRR
from .pipeline import compute_rr_estimate
from .preprocessing import PreparedSignal


def analyze_windows(prepared: PreparedSignal, cfg: RespirationConfig) -> WindowedResult:
    result = WindowedResult()
    duration = prepared.duration_s
    if not np.isfinite(duration) or duration < cfg.window_seconds * 1.5 or prepared.resp_t.size == 0:
        return result

    t0 = float(prepared.resp_t[0])
    step = max(cfg.window_seconds * (1.0 - cfg.window_overlap), 1.0)
    starts = t0 + np.arange(0.0, duration - cfg.window_seconds + 1e-9, step)
    if starts.size < 2:
        return result

    windows: list[WindowRR] = []
    for start in starts:
        end = start + cfg.window_seconds
        sub = prepared.slice_resp(float(start), float(end))
        if not sub.valid or sub.resp_t.size < 20:
            continue
        estimate = compute_rr_estimate(sub, cfg)
        windows.append(
            WindowRR(
                start_s=float(start - t0),
                end_s=float(end - t0),
                rr=estimate["rr"],
                confidence=estimate["best_spectral_quality"],
            )
        )

    result.windows = windows
    finite_rrs = np.asarray([w.rr for w in windows if np.isfinite(w.rr)], dtype=float)
    if finite_rrs.size < 2:
        return result

    median_rr = float(np.median(finite_rrs))
    mad_rr = float(np.median(np.abs(finite_rrs - median_rr)))
    cv_rr = float(mad_rr / median_rr) if median_rr > 1e-9 else math.nan
    stability = float(np.clip(100.0 - cv_rr * 250.0, 0.0, 100.0)) if np.isfinite(cv_rr) else math.nan

    result.median_rr = median_rr
    result.mad_rr = mad_rr
    result.cv_rr = cv_rr
    result.stability = stability
    return result
