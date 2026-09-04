from __future__ import annotations

import numpy as np

from .config import RespirationConfig


def duration_score(duration_s: float, cfg: RespirationConfig) -> float:
    if not np.isfinite(duration_s) or duration_s <= 0:
        return 0.0
    if duration_s < 20.0:
        return float(np.clip(duration_s, 0.0, 20.0))
    if duration_s < cfg.min_duration_s:
        span = max(cfg.min_duration_s - 20.0, 1e-6)
        return float(20.0 + (duration_s - 20.0) / span * 20.0)
    if duration_s < cfg.recommended_duration_s:
        span = max(cfg.recommended_duration_s - cfg.min_duration_s, 1e-6)
        return float(40.0 + (duration_s - cfg.min_duration_s) / span * 30.0)
    if duration_s < cfg.recommended_duration_s * 2.0:
        return float(70.0 + (duration_s - cfg.recommended_duration_s) / cfg.recommended_duration_s * 20.0)
    return 100.0


def cycle_score(cycles: float, cfg: RespirationConfig) -> float:
    if not np.isfinite(cycles) or cycles <= 0:
        return 0.0
    if cycles < cfg.min_cycles:
        return float(np.clip(cycles / cfg.min_cycles * 40.0, 0.0, 40.0))
    return float(np.clip(40.0 + (cycles - cfg.min_cycles) / cfg.min_cycles * 60.0, 40.0, 100.0))


def artifact_penalty(artifact_ir_pct: float, artifact_red_pct: float) -> float:
    penalty = 0.0
    if np.isfinite(artifact_ir_pct):
        penalty += min(30.0, artifact_ir_pct * 1.2)
    if np.isfinite(artifact_red_pct):
        penalty += min(15.0, artifact_red_pct * 0.6)
    return penalty


def compute_confidence(
    *,
    duration_s: float,
    cycles: float,
    best_spectral_quality: float,
    best_autocorr_quality: float,
    agreement_methods: float,
    agreement_red_ir: float,
    agreement_estimators: float,
    stability: float,
    artifact_ir_pct: float,
    artifact_red_pct: float,
    cfg: RespirationConfig,
) -> float:
    components: list[tuple[float, float]] = [
        (duration_score(duration_s, cfg), 15.0),
        (cycle_score(cycles, cfg), 15.0),
        (float(np.clip(best_spectral_quality, 0.0, 100.0)), 15.0),
        (float(np.clip(best_autocorr_quality, 0.0, 100.0)), 10.0),
    ]
    if np.isfinite(agreement_methods):
        components.append((float(np.clip(agreement_methods, 0.0, 100.0)), 10.0))
    if np.isfinite(agreement_red_ir):
        components.append((float(np.clip(agreement_red_ir, 0.0, 100.0)), 10.0))
    if np.isfinite(agreement_estimators):
        components.append((float(np.clip(agreement_estimators, 0.0, 100.0)), 15.0))
    if np.isfinite(stability):
        components.append((float(np.clip(stability, 0.0, 100.0)), 10.0))

    total_weight = sum(w for _, w in components)
    score = sum(v * w for v, w in components) / total_weight if total_weight > 0 else 0.0
    score -= artifact_penalty(artifact_ir_pct, artifact_red_pct)

    # Duration/cycle counts alone must never carry a case over the line: gate the whole
    # score by how convincing the actual periodicity evidence (spectral + autocorr) is,
    # so a long, artifact-free recording with no real respiratory signal still gets
    # rejected instead of "confident" purely because it had plenty of data.
    evidence = 0.5 * float(np.clip(best_spectral_quality, 0.0, 100.0)) + 0.5 * float(np.clip(best_autocorr_quality, 0.0, 100.0))
    evidence_gate = float(np.clip(evidence / 45.0, 0.0, 1.0))
    score *= evidence_gate

    return float(np.clip(score, 0.0, 100.0))


def rejection_reason(
    *,
    duration_s: float,
    cycles: float,
    has_spectral_peak: bool,
    agreement_red_ir: float,
    agreement_estimators: float,
    artifact_ir_pct: float,
    artifact_red_pct: float,
    cfg: RespirationConfig,
) -> str:
    if not np.isfinite(duration_s) or duration_s < cfg.min_duration_s:
        return "duración insuficiente"
    if not has_spectral_peak:
        return "sin periodicidad respiratoria dominante"
    high_ir_artifacts = np.isfinite(artifact_ir_pct) and artifact_ir_pct > 25.0
    high_red_artifacts = np.isfinite(artifact_red_pct) and artifact_red_pct > 25.0
    if high_ir_artifacts or high_red_artifacts:
        return "movimiento/contacto excesivo"
    if not np.isfinite(cycles) or cycles < cfg.min_cycles:
        return "demasiados pocos ciclos"
    if np.isfinite(agreement_red_ir) and agreement_red_ir < 30.0:
        return "alta discordancia RED/IR"
    if np.isfinite(agreement_estimators) and agreement_estimators < 30.0:
        return "estimadores respiratorios inconsistentes"
    return "confianza insuficiente"
