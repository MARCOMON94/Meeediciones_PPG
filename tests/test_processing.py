from __future__ import annotations

import numpy as np

from ppg_suite.models import AnalysisConfig, SensorConfig
from ppg_suite.processing import detect_artifacts, estimate_hz, robust_normalize, score_and_merge_metrics


def test_estimate_hz_regular_samples():
    t = np.arange(0.0, 10.0, 0.01)

    assert abs(estimate_hz(t) - 100.0) < 0.05


def test_robust_normalize_handles_nan_and_clips_outlier():
    values = np.asarray([1.0, 1.1, np.nan, 1.2, 30.0], dtype=float)
    normalized = robust_normalize(values)

    assert normalized.shape == values.shape
    assert np.all(np.isfinite(normalized))
    assert float(np.max(normalized)) <= 5.0


def test_detect_artifacts_marks_large_jump():
    values = np.ones(80, dtype=float) * 100.0
    values[40] = 5000.0

    artifacts = detect_artifacts(values, strict=True)

    assert artifacts[40]
    assert int(np.sum(artifacts)) >= 1


def test_score_and_merge_metrics_tracks_synthetic_90_bpm_signal():
    hz = 100.0
    t = np.arange(0.0, 20.0, 1.0 / hz)
    pulse_hz = 1.5
    ir = 50000.0 + 1500.0 * np.sin(2 * np.pi * pulse_hz * t)
    red = 45000.0 + 900.0 * np.sin(2 * np.pi * pulse_hz * t + 0.1)
    cfg = AnalysisConfig(ignore_initial_seconds=0.0, bpm_min=45, bpm_max=180)

    metrics = score_and_merge_metrics(t, red, ir, SensorConfig(), cfg)

    assert metrics.n >= 100
    assert np.isfinite(metrics.bpm)
    assert abs(metrics.bpm - 90.0) <= 8.0
