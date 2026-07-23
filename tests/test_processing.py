from __future__ import annotations

import math
from dataclasses import asdict

import numpy as np

from ppg_suite.models import AnalysisConfig, Metrics, SensorConfig
from ppg_suite.processing import (
    compute_blind_and_assisted_stable,
    detect_artifacts,
    estimate_hz,
    robust_normalize,
    score_and_merge_metrics,
    stable_bpm_segment,
)


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


def test_stable_bpm_segment_accepts_clean_five_second_window():
    hz = 100.0
    t = np.arange(0.0, 20.0, 1.0 / hz)
    pulse_hz = 1.2
    ir = 50000.0 + 1200.0 * np.sin(2 * np.pi * pulse_hz * t)
    red = 45000.0 + 800.0 * np.sin(2 * np.pi * pulse_hz * t + 0.05)
    cfg = AnalysisConfig(ignore_initial_seconds=0.0, bpm_min=45, bpm_max=180)

    metrics = stable_bpm_segment(t, red, ir, SensorConfig(), cfg, window_s=5.0)

    assert np.isfinite(metrics.bpm_estable_5s)
    assert abs(metrics.bpm_estable_5s - 72.0) <= 8.0
    assert metrics.bpm_estable_muestras > 0
    assert "ventana elegida" in metrics.bpm_estable_motivo


def test_stable_bpm_segment_accepts_low_pi_when_estimators_agree():
    hz = 100.0
    t = np.arange(0.0, 20.0, 1.0 / hz)
    pulse_hz = 1.2
    ir = 100000.0 + 70.0 * np.sin(2 * np.pi * pulse_hz * t)
    red = 90000.0 + 50.0 * np.sin(2 * np.pi * pulse_hz * t + 0.05)
    cfg = AnalysisConfig(ignore_initial_seconds=0.0, bpm_min=45, bpm_max=180)

    metrics = stable_bpm_segment(t, red, ir, SensorConfig(), cfg, window_s=5.0, reference_bpm=80.0)

    assert np.isfinite(metrics.bpm_estable_5s)
    assert abs(metrics.bpm_estable_5s - 72.0) <= 8.0
    assert "PI IR bajo" in metrics.bpm_estable_motivo


def test_stable_bpm_segment_rejects_bpm_far_from_manual_reference():
    hz = 100.0
    t = np.arange(0.0, 20.0, 1.0 / hz)
    pulse_hz = 2.4
    ir = 50000.0 + 1200.0 * np.sin(2 * np.pi * pulse_hz * t)
    red = 45000.0 + 800.0 * np.sin(2 * np.pi * pulse_hz * t + 0.05)
    cfg = AnalysisConfig(ignore_initial_seconds=0.0, bpm_min=45, bpm_max=180)

    metrics = stable_bpm_segment(t, red, ir, SensorConfig(), cfg, window_s=5.0, reference_bpm=80.0)

    assert not np.isfinite(metrics.bpm_estable_5s)
    assert "BPM fuera de referencia manual" in metrics.bpm_estable_motivo


def test_stable_bpm_segment_rejects_strong_baseline_drift():
    hz = 100.0
    t = np.arange(0.0, 20.0, 1.0 / hz)
    pulse_hz = 1.2
    drift = np.linspace(0.0, 80000.0, t.size)
    ir = 50000.0 + drift + 1200.0 * np.sin(2 * np.pi * pulse_hz * t)
    red = 45000.0 + drift * 0.8 + 800.0 * np.sin(2 * np.pi * pulse_hz * t + 0.05)
    cfg = AnalysisConfig(ignore_initial_seconds=0.0, bpm_min=45, bpm_max=180)

    metrics = stable_bpm_segment(t, red, ir, SensorConfig(), cfg, window_s=5.0)

    assert not np.isfinite(metrics.bpm_estable_5s)
    assert metrics.bpm_estable_muestras == 0
    assert "deriva" in metrics.bpm_estable_motivo


def test_blind_stable_segment_ignores_manual_reference():
    hz = 100.0
    t = np.arange(0.0, 20.0, 1.0 / hz)
    pulse_hz = 1.2
    ir = 50000.0 + 1200.0 * np.sin(2 * np.pi * pulse_hz * t)
    red = 45000.0 + 800.0 * np.sin(2 * np.pi * pulse_hz * t + 0.05)
    cfg = AnalysisConfig(ignore_initial_seconds=0.0, bpm_min=45, bpm_max=180)

    blind_close, assisted_close = compute_blind_and_assisted_stable(
        t, red, ir, SensorConfig(), cfg, window_s=5.0, reference_bpm=80.0
    )
    blind_far, assisted_far = compute_blind_and_assisted_stable(
        t, red, ir, SensorConfig(), cfg, window_s=5.0, reference_bpm=200.0
    )

    # Changing the manual reference must not change the blind segment at all.
    assert np.isfinite(blind_close.bpm_estable_5s)
    assert blind_close.bpm_estable_5s == blind_far.bpm_estable_5s
    assert blind_close.bpm_estable_inicio_s == blind_far.bpm_estable_inicio_s
    assert blind_close.bpm_estable_fin_s == blind_far.bpm_estable_fin_s
    assert blind_close.bpm_estable_calidad == blind_far.bpm_estable_calidad

    # The assisted segment CAN change: a reference close to the true ~72 BPM
    # accepts the window, a reference far from it rejects it.
    assert np.isfinite(assisted_close.bpm_estable_5s)
    assert not np.isfinite(assisted_far.bpm_estable_5s)


def test_metrics_defaults_are_backward_compatible():
    m = Metrics()

    assert math.isnan(m.bpm_estable_ciego_5s)
    assert math.isnan(m.bpm_estable_asistido_5s)
    assert m.bpm_estimators_valid == 0
    assert m.bpm_final_source == ""

    # asdict() is what measurement_window.py::save_summary() serializes into
    # summary_*.json - it must never fail, and old readers that only know
    # about bpm_estable_5s must still find that key present.
    data = asdict(m)
    assert "bpm_estable_5s" in data
    assert "bpm_estable_ciego_5s" in data
    assert "bpm_estable_asistido_5s" in data
