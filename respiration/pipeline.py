from __future__ import annotations

import math

import numpy as np

from .autocorr import method_agreement
from .config import RespirationConfig
from .fusion import build_candidate, robust_consensus
from .models import CandidateEstimate
from .preprocessing import PreparedSignal
from .riav import compute_riav, detect_cardiac_beats
from .rifv import compute_rifv
from .riiv import compute_riiv


def _rr_of(candidates: dict[str, CandidateEstimate], name: str) -> float:
    candidate = candidates.get(name)
    return candidate.rr if candidate is not None else math.nan


def compute_rr_estimate(prepared: PreparedSignal, cfg: RespirationConfig) -> dict:
    """Run the full RIIV/RIAV/RIFV -> spectral/autocorr -> fusion stack once.

    Shared by the single-shot analysis and by windowed sub-segment analysis, so both use
    the exact same estimator logic.
    """
    riiv_ir = compute_riiv(prepared.resp_ir, prepared.resp_hz, cfg)
    riiv_red = compute_riiv(prepared.resp_red, prepared.resp_hz, cfg) if prepared.has_red else np.asarray([], dtype=float)

    peak_times_ir, peak_values_ir, trough_values_ir = detect_cardiac_beats(
        prepared.fine_t, prepared.fine_ir, prepared.fine_hz, cfg
    )
    riav_ir = compute_riav(peak_times_ir, peak_values_ir, trough_values_ir, prepared.resp_t, prepared.resp_hz, cfg)
    rifv = compute_rifv(peak_times_ir, prepared.resp_t, prepared.resp_hz, cfg)

    riav_red = np.asarray([], dtype=float)
    if prepared.has_red:
        peak_times_red, peak_values_red, trough_values_red = detect_cardiac_beats(
            prepared.fine_t, prepared.fine_red, prepared.fine_hz, cfg
        )
        riav_red = compute_riav(peak_times_red, peak_values_red, trough_values_red, prepared.resp_t, prepared.resp_hz, cfg)

    candidates: dict[str, CandidateEstimate] = {
        "riiv_ir": build_candidate("riiv_ir", riiv_ir, prepared.resp_hz, cfg),
        "riav_ir": build_candidate("riav_ir", riav_ir, prepared.resp_hz, cfg),
        "rifv": build_candidate("rifv", rifv, prepared.resp_hz, cfg),
    }
    if prepared.has_red:
        candidates["riiv_red"] = build_candidate("riiv_red", riiv_red, prepared.resp_hz, cfg)
        candidates["riav_red"] = build_candidate("riav_red", riav_red, prepared.resp_hz, cfg)

    final_rr, agreement_estimators, used_names, outlier_names = robust_consensus(candidates)

    agreement_red_ir = math.nan
    if prepared.has_red:
        pairs = [
            method_agreement(_rr_of(candidates, "riiv_ir"), _rr_of(candidates, "riiv_red")),
            method_agreement(_rr_of(candidates, "riav_ir"), _rr_of(candidates, "riav_red")),
        ]
        pairs = [p for p in pairs if np.isfinite(p)]
        if pairs:
            agreement_red_ir = float(np.mean(pairs))

    used = [candidates[n] for n in used_names]
    best_spectral_quality = float(np.max([c.spectral_quality for c in used])) if used else 0.0
    best_autocorr_quality = float(np.max([c.autocorr_quality for c in used])) if used else 0.0
    agreement_values = [c.method_agreement for c in used if np.isfinite(c.method_agreement)]
    agreement_methods = float(np.mean(agreement_values)) if agreement_values else math.nan

    cycles = math.nan
    if np.isfinite(final_rr) and np.isfinite(prepared.duration_s):
        cycles = final_rr * prepared.duration_s / 60.0

    return {
        "rr": final_rr,
        "candidates": candidates,
        "used_names": used_names,
        "outlier_names": outlier_names,
        "agreement_red_ir": agreement_red_ir,
        "agreement_estimators": agreement_estimators,
        "agreement_methods": agreement_methods,
        "best_spectral_quality": best_spectral_quality,
        "best_autocorr_quality": best_autocorr_quality,
        "cycles": cycles,
    }
