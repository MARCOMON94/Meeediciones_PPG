from __future__ import annotations

import math

import numpy as np

from .autocorr import autocorr_rr, method_agreement
from .config import RespirationConfig
from .models import CandidateEstimate
from .spectral import spectral_peak_metrics, welch_psd


def build_candidate(name: str, signal: np.ndarray, hz: float, cfg: RespirationConfig) -> CandidateEstimate:
    signal = np.asarray(signal, dtype=float)
    if signal.size < 8 or not np.isfinite(hz) or hz <= 0:
        return CandidateEstimate(name=name, reason="señal insuficiente")

    freqs, psd = welch_psd(signal, hz)
    peak = spectral_peak_metrics(freqs, psd, cfg.respiratory_low_hz, cfg.respiratory_high_hz)
    ac = autocorr_rr(signal, hz, cfg.rr_min, cfg.rr_max)
    agreement = method_agreement(peak.rr, ac.rr)

    spectral_quality = 0.0
    if np.isfinite(peak.rr):
        # A single periodogram bin can look "prominent" vs. the band median by pure chance
        # under noise, so require the peak to also (a) hold a real share of total band
        # power and (b) clearly beat the runner-up bin, not just the median.
        ratio_term = float(np.clip((peak.peak_band_ratio - 0.15) / 0.35, 0.0, 1.0))
        prominence_term = float(np.clip((peak.prominence - 1.5) / 4.5, 0.0, 1.0))
        dominance_term = float(np.clip(peak.second_peak_diff / max(peak.peak_power, 1e-9), 0.0, 1.0))
        spectral_quality = 100.0 * (0.45 * ratio_term + 0.25 * prominence_term + 0.30 * dominance_term)

    autocorr_quality = ac.strength

    if np.isfinite(peak.rr) and np.isfinite(ac.rr):
        rr = float((peak.rr + ac.rr) / 2.0) if (np.isfinite(agreement) and agreement >= 60.0) else (
            peak.rr if spectral_quality >= autocorr_quality else ac.rr
        )
    elif np.isfinite(peak.rr):
        rr = peak.rr
    elif np.isfinite(ac.rr):
        rr = ac.rr
    else:
        return CandidateEstimate(
            name=name,
            spectral_quality=spectral_quality,
            autocorr_quality=autocorr_quality,
            reason="sin pico espectral ni autocorrelación en banda respiratoria",
        )

    if not (cfg.rr_min <= rr <= cfg.rr_max):
        return CandidateEstimate(
            name=name,
            rr_fft=peak.rr,
            rr_autocorr=ac.rr,
            spectral_quality=spectral_quality,
            autocorr_quality=autocorr_quality,
            spectral_power=peak.peak_power,
            spectral_prominence=peak.prominence,
            reason="fuera de rango fisiológico configurado",
        )

    agreement_term = agreement if np.isfinite(agreement) else 50.0
    confidence = float(np.clip(0.40 * spectral_quality + 0.30 * autocorr_quality + 0.30 * agreement_term, 0.0, 100.0))

    return CandidateEstimate(
        name=name,
        rr=rr,
        confidence=confidence,
        spectral_quality=spectral_quality,
        autocorr_quality=autocorr_quality,
        method_agreement=agreement,
        rr_fft=peak.rr,
        rr_autocorr=ac.rr,
        spectral_power=peak.peak_power,
        spectral_prominence=peak.prominence,
    )


def robust_consensus(candidates: dict[str, CandidateEstimate]) -> tuple[float, float, list[str], list[str]]:
    """Confidence-weighted median across valid candidates, with MAD-based outlier rejection.

    Returns (final_rr, agreement_estimators, used_names, outlier_names).
    """
    valid = {name: c for name, c in candidates.items() if np.isfinite(c.rr) and c.confidence > 0}
    if not valid:
        return math.nan, math.nan, [], list(candidates.keys())

    names = list(valid.keys())
    rrs = np.asarray([valid[n].rr for n in names], dtype=float)
    weights = np.asarray([max(valid[n].confidence, 1.0) for n in names], dtype=float)

    rejected: list[str] = []
    if rrs.size >= 3:
        med = float(np.median(rrs))
        mad = max(float(np.median(np.abs(rrs - med))), 2.0)
        keep = np.abs(rrs - med) <= 3.5 * mad
        if int(np.sum(keep)) >= 2:
            rejected = [n for n, k in zip(names, keep) if not k]
            names = [n for n, k in zip(names, keep) if k]
            rrs = rrs[keep]
            weights = weights[keep]

    order = np.argsort(rrs)
    sorted_rrs = rrs[order]
    sorted_weights = weights[order]
    cum = np.cumsum(sorted_weights)
    cutoff = cum[-1] / 2.0
    median_idx = int(min(np.searchsorted(cum, cutoff), sorted_rrs.size - 1))
    final_rr = float(sorted_rrs[median_idx])

    spread = float(np.max(rrs) - np.min(rrs)) if rrs.size > 1 else 0.0
    agreement_estimators = float(np.clip(100.0 - spread * 3.0, 0.0, 100.0))

    outlier_names = rejected + [n for n in candidates if n not in valid]
    return final_rr, agreement_estimators, names, outlier_names
