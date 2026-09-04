from __future__ import annotations

import math

import numpy as np

from .config import RespirationConfig
from .models import CandidateEstimate, RespirationMetrics
from .pipeline import compute_rr_estimate
from .preprocessing import prepare_respiration_signal
from .quality import compute_confidence, rejection_reason
from .windows import analyze_windows

__all__ = ["RespirationConfig", "RespirationMetrics", "analyze_respiration"]


def _rr_field(candidates: dict[str, CandidateEstimate], name: str) -> float:
    candidate = candidates.get(name)
    return candidate.rr if candidate is not None else math.nan


def analyze_respiration(
    t: np.ndarray, red: np.ndarray, ir: np.ndarray, cfg: RespirationConfig | None = None
) -> RespirationMetrics:
    """Estimate respiratory rate from raw RED/IR PPG using RIIV/RIAV/RIFV consensus.

    Single public entrypoint used by both the batch CLI and the GUI. Never uses
    ppg_suite.processing.processed_ppg's respiratory-band content directly for RIIV -
    the cardiac 2s detrend removes exactly the slow modulation this module is after.
    """
    cfg = cfg or RespirationConfig()
    prepared = prepare_respiration_signal(t, red, ir, cfg)
    if not prepared.valid:
        return RespirationMetrics(
            valid=False,
            reason=prepared.reason,
            usable_duration_s=prepared.duration_s,
            artifact_ir_pct=prepared.artifact_ir_pct,
            artifact_red_pct=prepared.artifact_red_pct,
        )

    estimate = compute_rr_estimate(prepared, cfg)
    windowed = analyze_windows(prepared, cfg)

    final_rr = windowed.median_rr if np.isfinite(windowed.median_rr) else estimate["rr"]
    candidates: dict[str, CandidateEstimate] = estimate["candidates"]
    used_names: list[str] = estimate["used_names"]

    cycles = math.nan
    if np.isfinite(final_rr) and np.isfinite(prepared.duration_s):
        cycles = final_rr * prepared.duration_s / 60.0

    used_candidates = [candidates[n] for n in used_names]
    best_candidate = max(used_candidates, key=lambda c: c.confidence) if used_candidates else None
    has_spectral_peak = best_candidate is not None and np.isfinite(best_candidate.rr_fft)

    confidence = compute_confidence(
        duration_s=prepared.duration_s,
        cycles=cycles,
        best_spectral_quality=estimate["best_spectral_quality"],
        best_autocorr_quality=estimate["best_autocorr_quality"],
        agreement_methods=estimate["agreement_methods"],
        agreement_red_ir=estimate["agreement_red_ir"],
        agreement_estimators=estimate["agreement_estimators"],
        stability=windowed.stability,
        artifact_ir_pct=prepared.artifact_ir_pct,
        artifact_red_pct=prepared.artifact_red_pct,
        cfg=cfg,
    )

    valid = bool(np.isfinite(final_rr) and confidence >= cfg.min_confidence and cfg.rr_min <= final_rr <= cfg.rr_max)

    if valid:
        reason = f"consenso: {', '.join(used_names)}" + (f"; outliers: {', '.join(estimate['outlier_names'])}" if estimate["outlier_names"] else "")
        rr_out = final_rr
        final_source = "+".join(used_names)
    else:
        reason = rejection_reason(
            duration_s=prepared.duration_s,
            cycles=cycles,
            has_spectral_peak=has_spectral_peak,
            agreement_red_ir=estimate["agreement_red_ir"],
            agreement_estimators=estimate["agreement_estimators"],
            artifact_ir_pct=prepared.artifact_ir_pct,
            artifact_red_pct=prepared.artifact_red_pct,
            cfg=cfg,
        )
        rr_out = math.nan
        final_source = ""

    return RespirationMetrics(
        rr=rr_out,
        confidence=confidence,
        valid=valid,
        rr_riiv_ir=_rr_field(candidates, "riiv_ir"),
        rr_riiv_red=_rr_field(candidates, "riiv_red"),
        rr_riav_ir=_rr_field(candidates, "riav_ir"),
        rr_riav_red=_rr_field(candidates, "riav_red"),
        rr_rifv=_rr_field(candidates, "rifv"),
        rr_fft=best_candidate.rr_fft if best_candidate else math.nan,
        rr_autocorr=best_candidate.rr_autocorr if best_candidate else math.nan,
        spectral_power=best_candidate.spectral_power if best_candidate else math.nan,
        spectral_prominence=best_candidate.spectral_prominence if best_candidate else math.nan,
        agreement_methods=estimate["agreement_methods"],
        agreement_red_ir=estimate["agreement_red_ir"],
        agreement_estimators=estimate["agreement_estimators"],
        stability=windowed.stability,
        usable_duration_s=prepared.duration_s,
        respiratory_cycles_estimated=cycles,
        artifact_red_pct=prepared.artifact_red_pct,
        artifact_ir_pct=prepared.artifact_ir_pct,
        final_source=final_source,
        reason=reason,
    )
