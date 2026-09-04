from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RespirationConfig:
    rr_min: float = 6.0
    rr_max: float = 72.0

    min_duration_s: float = 30.0
    recommended_duration_s: float = 60.0

    respiratory_low_hz: float = 0.10
    respiratory_high_hz: float = 1.20

    resample_hz: float = 25.0

    min_cycles: int = 6

    min_confidence: float = 60.0

    window_seconds: float = 30.0
    window_overlap: float = 0.5

    beat_min_bpm: float = 30.0
    beat_max_bpm: float = 220.0
