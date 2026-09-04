from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class RespirationMetrics:
    rr: float = math.nan
    confidence: float = 0.0
    valid: bool = False

    rr_riiv_ir: float = math.nan
    rr_riiv_red: float = math.nan

    rr_riav_ir: float = math.nan
    rr_riav_red: float = math.nan

    rr_rifv: float = math.nan

    rr_fft: float = math.nan
    rr_autocorr: float = math.nan

    spectral_power: float = math.nan
    spectral_prominence: float = math.nan

    agreement_methods: float = math.nan
    agreement_red_ir: float = math.nan
    agreement_estimators: float = math.nan
    stability: float = math.nan

    usable_duration_s: float = math.nan
    respiratory_cycles_estimated: float = math.nan

    artifact_red_pct: float = math.nan
    artifact_ir_pct: float = math.nan

    final_source: str = ""
    reason: str = ""


@dataclass
class CandidateEstimate:
    name: str
    rr: float = math.nan
    confidence: float = 0.0
    spectral_quality: float = 0.0
    autocorr_quality: float = 0.0
    method_agreement: float = math.nan
    stability: float = math.nan
    rr_fft: float = math.nan
    rr_autocorr: float = math.nan
    spectral_power: float = math.nan
    spectral_prominence: float = math.nan
    reason: str = ""


@dataclass
class WindowRR:
    start_s: float
    end_s: float
    rr: float = math.nan
    confidence: float = 0.0


@dataclass
class WindowedResult:
    windows: list[WindowRR] = field(default_factory=list)
    median_rr: float = math.nan
    mad_rr: float = math.nan
    cv_rr: float = math.nan
    stability: float = math.nan
