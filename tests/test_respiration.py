from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ppg_suite.paths import RAW_DIR
from respiration import RespirationConfig, analyze_respiration


def _synthetic_ppg(
    duration_s: float = 90.0,
    hr_bpm: float = 80.0,
    rr_rpm: float = 30.0,
    baseline_amp: float = 3000.0,
    pulse_amp: float = 1500.0,
    pulse_mod_depth: float = 0.3,
    noise_std: float = 20.0,
    hz: float = 100.0,
    seed: int = 1,
):
    """Synthetic RED/IR pair: a raised-cosine cardiac pulse riding on a DC baseline,
    with independently controllable slow-baseline (RIIV-style) and pulse-amplitude
    (RIAV-style) modulation at the respiratory rate.
    """
    t = np.arange(0.0, duration_s, 1.0 / hz)
    hr_hz = hr_bpm / 60.0
    rr_hz = rr_rpm / 60.0
    baseline = 50000.0 + baseline_amp * np.sin(2 * np.pi * rr_hz * t)
    pulse = pulse_amp * (1.0 + pulse_mod_depth * np.sin(2 * np.pi * rr_hz * t)) * (0.5 - 0.5 * np.cos(2 * np.pi * hr_hz * t))
    ir = baseline + pulse
    red = 0.9 * baseline + 0.8 * pulse
    rng = np.random.default_rng(seed)
    ir = ir + rng.normal(0.0, noise_std, ir.size)
    red = red + rng.normal(0.0, noise_std, red.size)
    return t, red, ir


def test_riiv_recovers_baseline_modulated_respiratory_rate():
    # Spec test 1: HR=80 bpm, RR=30 rpm baseline modulation -> overall RR ~= 30.
    t, red, ir = _synthetic_ppg(hr_bpm=80.0, rr_rpm=30.0)
    metrics = analyze_respiration(t, red, ir, RespirationConfig())

    assert metrics.valid
    assert abs(metrics.rr - 30.0) <= 5.0
    assert abs(metrics.rr_riiv_ir - 30.0) <= 2.0


def test_riav_recovers_pulse_amplitude_modulated_respiratory_rate():
    # Spec test 2: pulse-amplitude modulation at RR=24 rpm, flat baseline -> RIAV ~= 24.
    t, red, ir = _synthetic_ppg(hr_bpm=80.0, rr_rpm=24.0, baseline_amp=0.0, pulse_mod_depth=0.5)
    metrics = analyze_respiration(t, red, ir, RespirationConfig())

    assert metrics.valid
    assert abs(metrics.rr - 24.0) <= 3.0
    assert abs(metrics.rr_riav_ir - 24.0) <= 3.0


def test_confidence_degrades_with_noise():
    # Spec test 3: increasing noise must reduce confidence.
    cfg = RespirationConfig()
    t, red, ir = _synthetic_ppg(noise_std=20.0)
    low_noise = analyze_respiration(t, red, ir, cfg)

    t, red, ir = _synthetic_ppg(noise_std=5000.0)
    high_noise = analyze_respiration(t, red, ir, cfg)

    assert low_noise.confidence > high_noise.confidence + 20.0


def test_strong_motion_artifact_does_not_hijack_consensus():
    # Spec test 4: a strong periodic artifact at a different rate must not be reported
    # instead of the true respiratory rate.
    cfg = RespirationConfig()
    t, red, ir = _synthetic_ppg(rr_rpm=30.0)
    motion = 8000.0 * np.sin(2 * np.pi * (60.0 / 60.0) * t)
    metrics = analyze_respiration(t, red + 0.9 * motion, ir + motion, cfg)

    assert metrics.valid
    assert abs(metrics.rr - 30.0) <= 5.0
    assert abs(metrics.rr - 60.0) > 10.0


def test_no_respiratory_modulation_is_rejected():
    # Spec test 5: pure cardiac signal, no respiratory modulation at all -> must not
    # invent a frequency. HR is kept well clear of rr_max (72 rpm = 1.2 Hz) so the
    # cardiac fundamental can't leak into the respiratory band through filter edges.
    hz = 100.0
    t = np.arange(0.0, 90.0, 1.0 / hz)
    hr_hz = 100.0 / 60.0
    ir = 50000.0 + 1500.0 * (0.5 - 0.5 * np.cos(2 * np.pi * hr_hz * t))
    red = 45000.0 + 1200.0 * (0.5 - 0.5 * np.cos(2 * np.pi * hr_hz * t))
    rng = np.random.default_rng(0)
    ir = ir + rng.normal(0.0, 20.0, ir.size)
    red = red + rng.normal(0.0, 20.0, red.size)

    metrics = analyze_respiration(t, red, ir, RespirationConfig())

    assert not metrics.valid
    assert not np.isfinite(metrics.rr)


def test_too_short_recording_gets_reduced_confidence():
    # Spec test 6: a too-short recording must be flagged, not treated as a normal one.
    cfg = RespirationConfig()
    t_long, red_long, ir_long = _synthetic_ppg(duration_s=90.0)
    long_metrics = analyze_respiration(t_long, red_long, ir_long, cfg)

    t_short, red_short, ir_short = _synthetic_ppg(duration_s=18.0)
    short_metrics = analyze_respiration(t_short, red_short, ir_short, cfg)

    assert short_metrics.confidence < long_metrics.confidence

    t_tiny, red_tiny, ir_tiny = _synthetic_ppg(duration_s=12.0)
    tiny_metrics = analyze_respiration(t_tiny, red_tiny, ir_tiny, cfg)
    assert not tiny_metrics.valid
    assert not np.isfinite(tiny_metrics.rr)


def _first_long_raw_csv() -> Path | None:
    if not RAW_DIR.exists():
        return None
    candidates = sorted(RAW_DIR.glob("raw_LONG_*.csv"))
    return candidates[0] if candidates else None


@pytest.mark.skipif(_first_long_raw_csv() is None, reason="no raw_LONG_*.csv available under resultados/raw")
def test_pipeline_runs_end_to_end_on_a_real_long_recording():
    import csv

    path = _first_long_raw_csv()
    assert path is not None
    with path.open(encoding="utf-8-sig", errors="replace") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    t = np.asarray([float(r["tiempo_s"].replace(",", ".")) for r in rows], dtype=float)
    red = np.asarray([float((r.get("red_raw") or "nan").replace(",", ".")) for r in rows], dtype=float)
    ir = np.asarray([float((r.get("ir_raw") or "nan").replace(",", ".")) for r in rows], dtype=float)

    # No ground truth exists for these recordings (spec section 34): only assert the
    # pipeline runs end-to-end without raising and returns a well-formed result.
    metrics = analyze_respiration(t, red, ir, RespirationConfig())

    assert 0.0 <= metrics.confidence <= 100.0
    if metrics.valid:
        assert np.isfinite(metrics.rr)
        assert RespirationConfig().rr_min <= metrics.rr <= RespirationConfig().rr_max
    else:
        assert metrics.reason
