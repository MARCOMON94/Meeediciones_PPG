from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ppg_suite.processing import (
    detect_artifacts,
    estimate_hz,
    finite_arrays,
    replace_nan_with_last,
    saturation_percent,
    uniform_resample,
)

from .config import RespirationConfig
from .filters import fft_lowpass

MIN_PROCESSABLE_DURATION_S = 15.0
MAX_SATURATION_PCT = 20.0
MAX_ARTIFACT_PCT = 40.0
MAX_NEGATIVE_DT_FRACTION = 0.05


@dataclass
class PreparedSignal:
    valid: bool
    reason: str
    duration_s: float = math.nan
    fine_t: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    fine_ir: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    fine_red: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    fine_hz: float = math.nan
    resp_t: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    resp_ir: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    resp_red: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    resp_hz: float = math.nan
    has_red: bool = False
    artifact_ir_pct: float = math.nan
    artifact_red_pct: float = math.nan
    saturation_pct: float = math.nan

    def slice_resp(self, start_s: float, end_s: float) -> "PreparedSignal":
        mask = (self.resp_t >= start_s) & (self.resp_t <= end_s)
        if int(np.sum(mask)) < 8:
            return PreparedSignal(valid=False, reason="ventana vacía")
        sub_resp_t = self.resp_t[mask] - float(self.resp_t[mask][0])
        sub_resp_ir = self.resp_ir[mask]
        sub_resp_red = self.resp_red[mask] if self.has_red and self.resp_red.size == self.resp_t.size else np.asarray([], dtype=float)

        fine_mask = (self.fine_t >= start_s) & (self.fine_t <= end_s) if self.fine_t.size else np.asarray([], dtype=bool)
        has_fine = bool(np.any(fine_mask))
        sub_fine_t = self.fine_t[fine_mask] - float(self.fine_t[fine_mask][0]) if has_fine else np.asarray([], dtype=float)
        sub_fine_ir = self.fine_ir[fine_mask] if has_fine else np.asarray([], dtype=float)
        sub_fine_red = self.fine_red[fine_mask] if (has_fine and self.has_red and self.fine_red.size == self.fine_t.size) else np.asarray([], dtype=float)

        return PreparedSignal(
            valid=True,
            reason="",
            duration_s=float(sub_resp_t[-1] - sub_resp_t[0]) if sub_resp_t.size > 1 else math.nan,
            fine_t=sub_fine_t,
            fine_ir=sub_fine_ir,
            fine_red=sub_fine_red,
            fine_hz=self.fine_hz,
            resp_t=sub_resp_t,
            resp_ir=sub_resp_ir,
            resp_red=sub_resp_red,
            resp_hz=self.resp_hz,
            has_red=self.has_red and sub_fine_red.size > 0,
            artifact_ir_pct=self.artifact_ir_pct,
            artifact_red_pct=self.artifact_red_pct,
            saturation_pct=self.saturation_pct,
        )


def _interpolate_short_artifacts(y: np.ndarray, artifact_mask: np.ndarray, max_gap_samples: int) -> np.ndarray:
    y = y.copy()
    n = y.size
    if n == 0 or not np.any(artifact_mask):
        return y
    idx = np.arange(n)
    i = 0
    while i < n:
        if not artifact_mask[i]:
            i += 1
            continue
        j = i
        while j < n and artifact_mask[j]:
            j += 1
        run_len = j - i
        if run_len <= max_gap_samples and i > 0 and j < n:
            y[i:j] = np.interp(idx[i:j], [i - 1, j], [y[i - 1], y[j]])
        i = j
    return y


def prepare_respiration_signal(t: np.ndarray, red: np.ndarray, ir: np.ndarray, cfg: RespirationConfig) -> PreparedSignal:
    t = np.asarray(t, dtype=float)
    red = np.asarray(red, dtype=float)
    ir = np.asarray(ir, dtype=float)
    t, red, ir = finite_arrays(t, red, ir)

    if t.size < 20:
        return PreparedSignal(valid=False, reason="sin muestras suficientes")

    diffs = np.diff(t)
    if diffs.size == 0 or np.median(diffs) <= 0:
        return PreparedSignal(valid=False, reason="timestamps no crecientes")
    negative_fraction = float(np.mean(diffs < 0))
    if negative_fraction > MAX_NEGATIVE_DT_FRACTION:
        return PreparedSignal(valid=False, reason="timestamps no monótonos")

    duration_s = float(t[-1] - t[0])
    if not np.isfinite(duration_s) or duration_s < MIN_PROCESSABLE_DURATION_S:
        return PreparedSignal(valid=False, reason="duración insuficiente para procesar", duration_s=duration_s)

    raw_hz = estimate_hz(t)
    if not np.isfinite(raw_hz) or raw_hz <= 0:
        return PreparedSignal(valid=False, reason="frecuencia de muestreo inválida", duration_s=duration_s)

    finite_red_frac = float(np.mean(np.isfinite(red))) if red.size == t.size else 0.0
    std_red = float(np.nanstd(red)) if finite_red_frac > 0.5 else 0.0
    has_red = finite_red_frac > 0.5 and np.isfinite(std_red) and std_red > 1e-6

    saturation_pct = saturation_percent(red if has_red else np.full_like(ir, np.nan), ir)
    if np.isfinite(saturation_pct) and saturation_pct > MAX_SATURATION_PCT:
        return PreparedSignal(valid=False, reason="saturación ADC excesiva", duration_s=duration_s, saturation_pct=saturation_pct)

    ir_std = float(np.std(ir))
    ir_mean = float(np.mean(ir)) if ir.size else math.nan
    if not np.isfinite(ir_std) or ir_std <= 1e-6 or (np.isfinite(ir_mean) and abs(ir_mean) > 1e-9 and ir_std / abs(ir_mean) < 1e-6):
        return PreparedSignal(valid=False, reason="señal IR prácticamente constante (sin contacto o sensor apagado)", duration_s=duration_s)

    art_ir_mask = detect_artifacts(ir, strict=False)
    artifact_ir_pct = float(np.mean(art_ir_mask) * 100.0)
    if has_red:
        art_red_mask = detect_artifacts(red, strict=False)
        artifact_red_pct = float(np.mean(art_red_mask) * 100.0)
    else:
        art_red_mask = np.zeros_like(art_ir_mask)
        artifact_red_pct = math.nan

    if artifact_ir_pct > MAX_ARTIFACT_PCT:
        return PreparedSignal(
            valid=False,
            reason="movimiento/contacto excesivo",
            duration_s=duration_s,
            artifact_ir_pct=artifact_ir_pct,
            artifact_red_pct=artifact_red_pct,
            saturation_pct=saturation_pct,
        )

    max_gap_samples = max(1, int(round(1.0 * raw_hz)))
    ir_clean = _interpolate_short_artifacts(replace_nan_with_last(ir), art_ir_mask, max_gap_samples)
    red_clean = _interpolate_short_artifacts(replace_nan_with_last(red), art_red_mask, max_gap_samples) if has_red else red

    fine_t, fine_ir, fine_hz = uniform_resample(t, ir_clean, None)
    if fine_t.size < 20 or not np.isfinite(fine_hz) or fine_hz <= 0:
        return PreparedSignal(valid=False, reason="remuestreo insuficiente", duration_s=duration_s)
    if has_red:
        fine_t_red, fine_red, _ = uniform_resample(t, red_clean, fine_hz)
        if fine_t_red.size != fine_t.size:
            has_red = False
            fine_red = np.asarray([], dtype=float)
    else:
        fine_red = np.asarray([], dtype=float)

    target_hz = float(np.clip(cfg.resample_hz, 5.0, fine_hz))
    cutoff_hz = min(0.9 * target_hz / 2.0, 0.45 * fine_hz)
    ir_filtered = fft_lowpass(fine_ir, fine_hz, cutoff_hz)
    resp_t = np.arange(fine_t[0], fine_t[-1], 1.0 / target_hz)
    if resp_t.size < 20:
        return PreparedSignal(valid=False, reason="ventana insuficiente tras remuestreo", duration_s=duration_s)
    resp_ir = np.interp(resp_t, fine_t, ir_filtered)

    if has_red:
        red_filtered = fft_lowpass(fine_red, fine_hz, cutoff_hz)
        resp_red = np.interp(resp_t, fine_t, red_filtered)
    else:
        resp_red = np.asarray([], dtype=float)

    return PreparedSignal(
        valid=True,
        reason="",
        duration_s=duration_s,
        fine_t=fine_t,
        fine_ir=fine_ir,
        fine_red=fine_red,
        fine_hz=fine_hz,
        resp_t=resp_t,
        resp_ir=resp_ir,
        resp_red=resp_red,
        resp_hz=target_hz,
        has_red=has_red,
        artifact_ir_pct=artifact_ir_pct,
        artifact_red_pct=artifact_red_pct,
        saturation_pct=saturation_pct,
    )
