"""Bland-Altman / method-agreement analysis.

Pure functions and dataclasses, no Qt/pyqtgraph dependency, so the whole
statistical core can be unit-tested without starting the app. Callers (the
GUI layer in ``ppg_suite/windows/agreement_window.py``) are responsible for
turning ``CaptureRecord``/``AnimalMeasurement`` objects into plain
``Mapping[str, object]`` rows (their existing ``.row`` dict is already in the
right shape) before calling into this module.

Methodological note (see also the module docstring in
``ppg_suite/processing.py::compute_blind_and_assisted_stable``): the "blind"
stable BPM (``bpm_estable_ciego_5s``) is selected without ever looking at the
manual reference, so it is the value that must be used to validate against
that reference. The "assisted" value additionally rejects windows far from
the reference and is a diagnostic aid only - never the primary agreement
estimator, or the comparison would be circular.

Repeated-measures handling uses a classic method-of-moments (ANOVA/Searle)
one-way random-intercept variance-components estimator rather than
``statsmodels.MixedLM`` - the spec this module implements explicitly allows
"a statistically equivalent implementation", and this keeps the project free
of a scipy/statsmodels/patsy dependency in a PyInstaller-packaged desktop app.
Confidence intervals use percentile bootstrap throughout instead of
parametric (t/normal) formulas, for the same reason.
"""

from __future__ import annotations

import csv
import math
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Mapping, Sequence

import numpy as np

from .io_utils import atomic_csv_dict_writer, atomic_write_json
from .models import AnalysisConfig, SensorConfig
from .processing import stable_bpm_segment
from .utils import mean_valid_reference

# Small bootstrap resamples (e.g. one animal dominating a cluster resample)
# routinely produce a numerically degenerate but still well-defined polyfit;
# this is expected and already handled (see _sample_size_warning), not a bug
# to surface on every one of a few thousand iterations.
warnings.filterwarnings("ignore", message="Polyfit may be poorly conditioned")

BpmMethod = Literal["final", "ciego", "asistido", "peaks", "fft", "autocorr"]
ReferenceSource = Literal["previo", "pulsio", "fonendo", "media"]
AnalysisMode = Literal["classic", "repeated", "auto"]
Scale = Literal["absolute", "percent", "log"]

BPM_METHOD_FIELDS: dict[str, str] = {
    "final": "bpm",
    "ciego": "bpm_estable_ciego_5s",
    "asistido": "bpm_estable_asistido_5s",
    "peaks": "bpm_peak",
    "fft": "bpm_fft",
    "autocorr": "bpm_autocorr",
}

BPM_METHOD_LABELS: dict[str, str] = {
    "final": "BPM final combinado",
    "ciego": "BPM estable ciego",
    "asistido": "BPM estable asistido",
    "peaks": "BPM por picos",
    "fft": "BPM por FFT",
    "autocorr": "BPM por autocorrelación",
}

REFERENCE_SOURCE_LABELS: dict[str, str] = {
    "previo": "Pulso previo",
    "pulsio": "Pulsioxímetro final",
    "fonendo": "Fonendoscopio final",
    "media": "Media de referencias disponibles",
}

EXCLUSION_REASONS: dict[str, str] = {
    "sin_referencia": "Sin referencia válida",
    "sin_bpm": "Sin BPM del método seleccionado",
    "sin_estable_ciego": "Sin segmento estable ciego",
    "calidad_insuficiente": "Calidad insuficiente",
    "pi_insuficiente": "PI insuficiente",
    "exceso_artefactos": "Exceso de artefactos",
    "saturacion": "Saturación",
    "estimadores_discrepantes": "Estimadores discrepantes",
    "duplicado": "Duplicado",
    "archivo_incompleto": "Archivo incompleto",
    "escala_no_positiva": "Valor no positivo incompatible con escala logarítmica",
}

CIRCULARITY_WARNING = (
    "Para evitar circularidad, el análisis de validación utiliza el BPM estable ciego. "
    "El BPM estable asistido no se utiliza como estimador principal en Bland-Altman."
)
CORRELATION_WARNING = "Una correlación elevada no demuestra acuerdo entre métodos."


def _as_float(value: object) -> float:
    if value is None:
        return math.nan
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else math.nan
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return math.nan


def _sample_size_warning(n: int) -> str | None:
    if n < 10:
        return "Muestra insuficiente (n<10). Resultado meramente exploratorio."
    if n < 30:
        return "Muestra reducida (10<=n<30). Interpretar los límites con precaución."
    return None


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AgreementAnalysisConfig:
    bpm_method: BpmMethod = "ciego"
    reference_source: ReferenceSource = "media"
    mode: AnalysisMode = "auto"
    scale: Scale = "absolute"
    min_quality: float = 0.0
    min_pi_ir_pct: float = 0.0
    max_artifact_pct: float = 100.0
    max_saturation_pct: float = 100.0
    min_estimators_valid: int = 0
    max_estimator_spread: float = math.inf
    species: str = ""
    config_label: str = ""
    mode_filter: str = ""
    measurement_condition: str = ""
    bootstrap_iterations: int = 2000
    bootstrap_seed: int | None = 12345

    def __post_init__(self):
        self.bootstrap_iterations = int(np.clip(int(self.bootstrap_iterations), 500, 10000))


@dataclass
class AgreementPair:
    capture_id: str
    animal_id: str
    species: str
    fecha: str
    config_label: str
    bpm_method: str
    software_bpm: float
    reference_bpm: float
    reference_source: str
    reference_count: int
    mean_value: float
    difference: float
    abs_difference: float
    pct_difference: float
    quality: float
    stable_start_s: float
    stable_end_s: float
    pi_ir_pct: float
    artifact_pct: float
    notes: str = ""


@dataclass
class AgreementExclusion:
    capture_id: str
    animal_id: str
    reason: str
    secondary_reasons: list[str] = field(default_factory=list)


@dataclass
class BlandAltmanResult:
    mode: Literal["classic", "repeated"]
    n: int
    n_animals: int
    bias: float
    sd_diff: float
    loa_low: float
    loa_high: float
    bias_ci: tuple[float, float] | None = None
    loa_low_ci: tuple[float, float] | None = None
    loa_high_ci: tuple[float, float] | None = None
    mae_ci: tuple[float, float] | None = None
    rmse_ci: tuple[float, float] | None = None
    between_animal_sd: float | None = None
    within_animal_sd: float | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class ErrorMetrics:
    n: int
    mae: float
    rmse: float
    median_abs_error: float
    min_error: float
    max_error: float
    p90_abs_error: float
    pct_within_5: float
    pct_within_10: float
    pct_within_20: float


@dataclass
class ProportionalBiasResult:
    slope: float
    intercept: float
    slope_ci: tuple[float, float] | None
    p_value: float | None
    n: int
    warning: str | None = None


@dataclass
class HeteroscedasticityResult:
    slope: float
    slope_ci: tuple[float, float] | None
    likely: bool
    warning: str | None = None


# ---------------------------------------------------------------------------
# Reference resolution and pair construction (sections 4, 11)
# ---------------------------------------------------------------------------


def _resolve_reference(row: Mapping[str, object], source: ReferenceSource) -> tuple[float, int, str]:
    if source == "previo":
        bpm, count = mean_valid_reference(row.get("pulso_previo"))
        return bpm, count, "previo"
    if source == "pulsio":
        bpm, count = mean_valid_reference(row.get("pulso_final_pulsio"))
        return bpm, count, "pulsio"
    if source == "fonendo":
        bpm, count = mean_valid_reference(row.get("pulso_final_fonendo"))
        return bpm, count, "fonendo"
    bpm, count = mean_valid_reference(
        row.get("pulso_previo"), row.get("pulso_final_pulsio"), row.get("pulso_final_fonendo")
    )
    return bpm, count, "media"


def _scaled_difference(software_bpm: float, reference_bpm: float, scale: Scale) -> tuple[float, float]:
    """Return (difference, mean) in the requested Bland-Altman scale."""
    if scale == "percent":
        mean_value = (software_bpm + reference_bpm) / 2.0
        difference = 100.0 * (software_bpm - reference_bpm) / mean_value if mean_value else math.nan
        return difference, mean_value
    if scale == "log":
        log_sw = math.log(software_bpm)
        log_ref = math.log(reference_bpm)
        return log_sw - log_ref, (log_sw + log_ref) / 2.0
    mean_value = (software_bpm + reference_bpm) / 2.0
    return software_bpm - reference_bpm, mean_value


def log_scale_ratios(result: BlandAltmanResult) -> dict[str, float]:
    """Back-transform a log-scale BlandAltmanResult into software/reference ratios."""
    return {
        "ratio_bias": math.exp(result.bias),
        "ratio_loa_low": math.exp(result.loa_low),
        "ratio_loa_high": math.exp(result.loa_high),
    }


def build_agreement_pairs(
    measurements: Sequence[Mapping[str, object]],
    *,
    config: AgreementAnalysisConfig,
) -> tuple[list[AgreementPair], list[AgreementExclusion]]:
    """Build agreement pairs, applying inclusion criteria BEFORE looking at
    the difference to the reference (section 11: never filter by closeness
    to the reference, that would bias the analysis).
    """
    pairs: list[AgreementPair] = []
    exclusions: list[AgreementExclusion] = []
    seen_capture_ids: set[str] = set()
    bpm_field = BPM_METHOD_FIELDS[config.bpm_method]

    for row in measurements:
        capture_id = str(row.get("capture_id") or row.get("base_name") or "")
        animal_id = str(row.get("animal_id") or row.get("id") or "")

        if not capture_id:
            exclusions.append(AgreementExclusion("(sin id)", animal_id, EXCLUSION_REASONS["archivo_incompleto"]))
            continue
        if capture_id in seen_capture_ids:
            exclusions.append(AgreementExclusion(capture_id, animal_id, EXCLUSION_REASONS["duplicado"]))
            continue

        species = str(row.get("animal_type") or "")
        config_label = str(row.get("config_label") or "")
        modo = str(row.get("modo") or "")
        condicion = str(row.get("condiciones_medida") or "")

        if config.species and species != config.species:
            continue
        if config.config_label and config_label != config.config_label:
            continue
        if config.mode_filter and modo != config.mode_filter:
            continue
        if config.measurement_condition and condicion != config.measurement_condition:
            continue

        quality = _as_float(row.get("calidad"))
        pi_ir = _as_float(row.get("pi_ir_pct"))
        artifacts = _as_float(row.get("artefactos_ir_pct"))
        saturation = _as_float(row.get("saturation_pct"))
        estimators_valid_raw = _as_float(row.get("bpm_estimators_valid"))
        estimators_valid = int(estimators_valid_raw) if np.isfinite(estimators_valid_raw) else 0
        estimator_spread = _as_float(row.get("bpm_estimators_spread"))

        reason = ""
        secondary: list[str] = []

        def flag(condition: bool, key: str):
            nonlocal reason
            if not condition:
                return
            label = EXCLUSION_REASONS[key]
            if not reason:
                reason = label
            elif label not in secondary:
                secondary.append(label)

        flag(np.isfinite(quality) and quality < config.min_quality, "calidad_insuficiente")
        flag(np.isfinite(pi_ir) and pi_ir < config.min_pi_ir_pct, "pi_insuficiente")
        flag(np.isfinite(artifacts) and artifacts > config.max_artifact_pct, "exceso_artefactos")
        flag(np.isfinite(saturation) and saturation > config.max_saturation_pct, "saturacion")
        flag(bool(estimators_valid) and estimators_valid < config.min_estimators_valid, "estimadores_discrepantes")
        flag(np.isfinite(estimator_spread) and estimator_spread > config.max_estimator_spread, "estimadores_discrepantes")

        if reason:
            seen_capture_ids.add(capture_id)
            exclusions.append(AgreementExclusion(capture_id, animal_id, reason, secondary))
            continue

        software_bpm = _as_float(row.get(bpm_field))
        if not np.isfinite(software_bpm):
            seen_capture_ids.add(capture_id)
            key = "sin_estable_ciego" if config.bpm_method == "ciego" else "sin_bpm"
            exclusions.append(AgreementExclusion(capture_id, animal_id, EXCLUSION_REASONS[key]))
            continue

        ref_bpm, ref_count, ref_label = _resolve_reference(row, config.reference_source)
        if not np.isfinite(ref_bpm):
            seen_capture_ids.add(capture_id)
            exclusions.append(AgreementExclusion(capture_id, animal_id, EXCLUSION_REASONS["sin_referencia"]))
            continue

        if config.scale == "log" and (software_bpm <= 0 or ref_bpm <= 0):
            seen_capture_ids.add(capture_id)
            exclusions.append(AgreementExclusion(capture_id, animal_id, EXCLUSION_REASONS["escala_no_positiva"]))
            continue

        seen_capture_ids.add(capture_id)
        difference, mean_value = _scaled_difference(software_bpm, ref_bpm, config.scale)
        pairs.append(
            AgreementPair(
                capture_id=capture_id,
                animal_id=animal_id,
                species=species,
                fecha=str(row.get("fecha") or ""),
                config_label=config_label,
                bpm_method=config.bpm_method,
                software_bpm=software_bpm,
                reference_bpm=ref_bpm,
                reference_source=ref_label,
                reference_count=ref_count,
                mean_value=mean_value,
                difference=difference,
                abs_difference=abs(software_bpm - ref_bpm),
                pct_difference=(100.0 * (software_bpm - ref_bpm) / ref_bpm) if ref_bpm else math.nan,
                quality=quality,
                stable_start_s=_as_float(row.get("bpm_estable_ciego_inicio_s")),
                stable_end_s=_as_float(row.get("bpm_estable_ciego_fin_s")),
                pi_ir_pct=pi_ir,
                artifact_pct=artifacts,
            )
        )
    return pairs, exclusions


# ---------------------------------------------------------------------------
# Bland-Altman core (sections 6, 7)
# ---------------------------------------------------------------------------


def bland_altman_classic(pairs: Sequence[AgreementPair]) -> BlandAltmanResult | None:
    if len(pairs) < 2:
        return None
    diffs = np.asarray([p.difference for p in pairs], dtype=float)
    n = int(diffs.size)
    bias = float(np.mean(diffs))
    sd = float(np.std(diffs, ddof=1))
    warnings: list[str] = []
    size_warning = _sample_size_warning(n)
    if size_warning:
        warnings.append(size_warning)
    n_animals = len({p.animal_id for p in pairs if p.animal_id})
    return BlandAltmanResult(
        mode="classic",
        n=n,
        n_animals=n_animals,
        bias=bias,
        sd_diff=sd,
        loa_low=bias - 1.96 * sd,
        loa_high=bias + 1.96 * sd,
        warnings=warnings,
    )


def bland_altman_repeated(pairs: Sequence[AgreementPair]) -> BlandAltmanResult | None:
    """One-way random-intercept variance components (ANOVA/Searle method of
    moments) by animal_id - see module docstring for why not statsmodels.
    """
    if len(pairs) < 2:
        return None
    if any(not p.animal_id for p in pairs):
        result = bland_altman_classic(pairs)
        if result is not None:
            result.warnings.append(
                "Faltaba animal_id en algún par; no se puede asumir diseño repetido, se usa análisis clásico."
            )
        return result

    groups: dict[str, list[float]] = {}
    for p in pairs:
        groups.setdefault(p.animal_id, []).append(p.difference)

    diffs = np.asarray([p.difference for p in pairs], dtype=float)
    n = int(diffs.size)
    bias = float(np.mean(diffs))
    k = len(groups)

    if k < 2 or all(len(values) == 1 for values in groups.values()):
        result = bland_altman_classic(pairs)
        if result is not None:
            result.warnings.append(
                "Cada animal aporta una sola pareja; no hay repeticiones para estimar varianza residual, se usa análisis clásico."
            )
        return result

    group_sizes = np.asarray([len(v) for v in groups.values()], dtype=float)
    group_means = np.asarray([float(np.mean(v)) for v in groups.values()], dtype=float)
    total_n = float(n)
    df_between = k - 1
    df_within = total_n - k

    ss_between = float(np.sum(group_sizes * (group_means - bias) ** 2))
    ss_within = float(sum(float(np.sum((np.asarray(v) - np.mean(v)) ** 2)) for v in groups.values()))
    ms_between = ss_between / df_between if df_between > 0 else math.nan
    ms_within = ss_within / df_within if df_within > 0 else 0.0
    n0 = (total_n - float(np.sum(group_sizes**2)) / total_n) / df_between if df_between > 0 else math.nan

    between_var = max(0.0, (ms_between - ms_within) / n0) if np.isfinite(n0) and n0 > 0 else 0.0
    within_var = max(0.0, ms_within)
    total_var = between_var + within_var
    sd_total = math.sqrt(total_var)

    warnings: list[str] = []
    size_warning = _sample_size_warning(n)
    if size_warning:
        warnings.append(size_warning)

    return BlandAltmanResult(
        mode="repeated",
        n=n,
        n_animals=k,
        bias=bias,
        sd_diff=sd_total,
        loa_low=bias - 1.96 * sd_total,
        loa_high=bias + 1.96 * sd_total,
        between_animal_sd=math.sqrt(between_var),
        within_animal_sd=math.sqrt(within_var),
        warnings=warnings,
    )


def bland_altman_auto(pairs: Sequence[AgreementPair]) -> BlandAltmanResult | None:
    counts: dict[str, int] = {}
    for p in pairs:
        if p.animal_id:
            counts[p.animal_id] = counts.get(p.animal_id, 0) + 1
    if counts and any(c > 1 for c in counts.values()):
        return bland_altman_repeated(pairs)
    return bland_altman_classic(pairs)


_MODE_FUNCS = {"classic": bland_altman_classic, "repeated": bland_altman_repeated, "auto": bland_altman_auto}


def run_bland_altman(pairs: Sequence[AgreementPair], mode: AnalysisMode) -> BlandAltmanResult | None:
    return _MODE_FUNCS[mode](pairs)


# ---------------------------------------------------------------------------
# Bootstrap confidence intervals (section 8)
# ---------------------------------------------------------------------------


def cluster_bootstrap_bland_altman(
    pairs: Sequence[AgreementPair],
    *,
    mode: Literal["classic", "repeated"],
    iterations: int = 2000,
    seed: int | None = None,
) -> BlandAltmanResult | None:
    """95% percentile bootstrap CIs for bias/LoA/MAE/RMSE.

    Classic mode resamples whole pairs; repeated mode resamples whole animals
    (with all of their measurements), so within-animal correlation is
    respected.
    """
    base = bland_altman_repeated(pairs) if mode == "repeated" else bland_altman_classic(pairs)
    if base is None:
        return None
    rng = np.random.default_rng(seed)

    biases: list[float] = []
    loa_lows: list[float] = []
    loa_highs: list[float] = []
    maes: list[float] = []
    rmses: list[float] = []

    def record(sample: list[AgreementPair], result_fn):
        result = result_fn(sample)
        if result is None:
            return
        biases.append(result.bias)
        loa_lows.append(result.loa_low)
        loa_highs.append(result.loa_high)
        diffs = np.asarray([p.software_bpm - p.reference_bpm for p in sample], dtype=float)
        maes.append(float(np.mean(np.abs(diffs))))
        rmses.append(float(np.sqrt(np.mean(diffs**2))))

    if mode == "repeated" and base.mode == "repeated":
        groups: dict[str, list[AgreementPair]] = {}
        for p in pairs:
            groups.setdefault(p.animal_id, []).append(p)
        animal_ids = list(groups.keys())
        for _ in range(iterations):
            chosen = rng.choice(len(animal_ids), size=len(animal_ids), replace=True)
            sample: list[AgreementPair] = []
            for idx in chosen:
                sample.extend(groups[animal_ids[idx]])
            record(sample, bland_altman_repeated)
    else:
        n = len(pairs)
        for _ in range(iterations):
            chosen = rng.integers(0, n, size=n)
            sample = [pairs[i] for i in chosen]
            record(sample, bland_altman_classic)

    def ci(values: list[float]) -> tuple[float, float] | None:
        if not values:
            return None
        return float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5))

    base.bias_ci = ci(biases)
    base.loa_low_ci = ci(loa_lows)
    base.loa_high_ci = ci(loa_highs)
    base.mae_ci = ci(maes)
    base.rmse_ci = ci(rmses)
    return base


# ---------------------------------------------------------------------------
# Proportional bias and heteroscedasticity (sections 9, 10)
# ---------------------------------------------------------------------------


def proportional_bias_analysis(
    pairs: Sequence[AgreementPair],
    *,
    mode: Literal["classic", "repeated"] = "classic",
    iterations: int = 2000,
    seed: int | None = None,
) -> ProportionalBiasResult | None:
    """OLS slope of difference ~ mean_bpm, with a bootstrap CI and a bootstrap
    (not t-distribution) p-value, so this module never needs scipy.

    In repeated mode, both variables are centered within each animal before
    fitting (equivalent to a fixed-effect-per-animal / LSDV regression),
    approximating "difference_ij = intercept + slope*mean_bpm_ij +
    random_intercept_animal_i" without a mixed-model solver.
    """
    if len(pairs) < 3:
        return None
    means = np.asarray([p.mean_value for p in pairs], dtype=float)
    diffs = np.asarray([p.difference for p in pairs], dtype=float)
    animal_ids = [p.animal_id for p in pairs]
    use_repeated = mode == "repeated" and all(animal_ids)

    def fit_slope(m: np.ndarray, d: np.ndarray, ids: list[str]) -> float | None:
        if use_repeated:
            m = m.copy()
            d = d.copy()
            groups: dict[str, list[int]] = {}
            for idx, aid in enumerate(ids):
                groups.setdefault(aid, []).append(idx)
            for idxs in groups.values():
                m[idxs] = m[idxs] - np.mean(m[idxs])
                d[idxs] = d[idxs] - np.mean(d[idxs])
            if np.allclose(m, 0.0):
                return None
        try:
            slope, _intercept = np.polyfit(m, d, 1)
        except (np.linalg.LinAlgError, ValueError):
            return None
        return float(slope)

    slope = fit_slope(means, diffs, animal_ids)
    if slope is None:
        return None
    intercept = float(np.polyfit(means, diffs, 1)[1])

    rng = np.random.default_rng(seed)
    n = means.size
    slopes: list[float] = []
    for _ in range(iterations):
        idx = rng.integers(0, n, size=n)
        s = fit_slope(means[idx], diffs[idx], [animal_ids[i] for i in idx])
        if s is not None:
            slopes.append(s)

    slope_ci = (float(np.percentile(slopes, 2.5)), float(np.percentile(slopes, 97.5))) if slopes else None
    p_value = None
    if slopes:
        arr = np.asarray(slopes)
        p_low = float(np.mean(arr <= 0))
        p_high = float(np.mean(arr >= 0))
        p_value = float(min(1.0, 2.0 * min(p_low, p_high)))

    warning = None
    if slope_ci and (slope_ci[0] > 0 or slope_ci[1] < 0):
        warning = "Posible sesgo proporcional: el error cambia según el nivel de BPM."
    return ProportionalBiasResult(slope=slope, intercept=intercept, slope_ci=slope_ci, p_value=p_value, n=n, warning=warning)


def detect_heteroscedasticity(
    pairs: Sequence[AgreementPair], *, iterations: int = 2000, seed: int | None = None
) -> HeteroscedasticityResult | None:
    """Bootstrap CI on the slope of |difference| ~ mean_bpm. Only warns -
    never switches scale automatically (section 10)."""
    if len(pairs) < 5:
        return None
    means = np.asarray([p.mean_value for p in pairs], dtype=float)
    abs_diffs = np.asarray([abs(p.difference) for p in pairs], dtype=float)
    try:
        slope = float(np.polyfit(means, abs_diffs, 1)[0])
    except (np.linalg.LinAlgError, ValueError):
        return None

    rng = np.random.default_rng(seed)
    n = means.size
    slopes: list[float] = []
    for _ in range(iterations):
        idx = rng.integers(0, n, size=n)
        try:
            s = float(np.polyfit(means[idx], abs_diffs[idx], 1)[0])
        except (np.linalg.LinAlgError, ValueError):
            continue
        slopes.append(s)

    slope_ci = (float(np.percentile(slopes, 2.5)), float(np.percentile(slopes, 97.5))) if slopes else None
    likely = bool(slope_ci and slope_ci[0] > 0)
    warning = None
    if likely:
        warning = (
            "Posible heterocedasticidad: el tamaño del error crece con el BPM medio. "
            "Revisa la escala porcentual o logarítmica."
        )
    return HeteroscedasticityResult(slope=slope, slope_ci=slope_ci, likely=likely, warning=warning)


# ---------------------------------------------------------------------------
# Complementary error metrics (section 15)
# ---------------------------------------------------------------------------


def calculate_error_metrics(pairs: Sequence[AgreementPair]) -> ErrorMetrics | None:
    if not pairs:
        return None
    abs_diffs = np.asarray([p.abs_difference for p in pairs], dtype=float)
    signed = np.asarray([p.software_bpm - p.reference_bpm for p in pairs], dtype=float)
    n = int(abs_diffs.size)
    return ErrorMetrics(
        n=n,
        mae=float(np.mean(abs_diffs)),
        rmse=float(np.sqrt(np.mean(signed**2))),
        median_abs_error=float(np.median(abs_diffs)),
        min_error=float(np.min(abs_diffs)),
        max_error=float(np.max(abs_diffs)),
        p90_abs_error=float(np.percentile(abs_diffs, 90)),
        pct_within_5=float(100.0 * np.mean(abs_diffs <= 5.0)),
        pct_within_10=float(100.0 * np.mean(abs_diffs <= 10.0)),
        pct_within_20=float(100.0 * np.mean(abs_diffs <= 20.0)),
    )


# ---------------------------------------------------------------------------
# Backward compatibility with old summaries (section 17)
# ---------------------------------------------------------------------------


def resolve_blind_stable_bpm(
    row: Mapping[str, object],
    *,
    raw_path: Path | None,
    processed_path: Path | None,
    sensor_cfg: SensorConfig,
    analysis_cfg: AnalysisConfig,
    cache: dict[str, tuple[float, str]],
) -> tuple[float, str, str]:
    """Resolve the blind stable BPM for a measurement, reconstructing it from
    raw/processed data when an old summary predates the ciego/asistido split.

    Never writes to the original summary/raw/processed file - only caches in
    memory for the current analysis session. Returns (value, status, reason)
    where status is "stored" / "recalculado" / "no_reconstruible".
    """
    stored = _as_float(row.get("bpm_estable_ciego_5s"))
    if np.isfinite(stored):
        return stored, "stored", ""

    capture_id = str(row.get("capture_id") or row.get("base_name") or "")
    cached = cache.get(capture_id) if capture_id else None
    if cached is not None:
        return cached[0], cached[1], "" if np.isfinite(cached[0]) else "No se puede reconstruir BPM estable ciego"

    source_path = raw_path if raw_path and raw_path.exists() else (processed_path if processed_path and processed_path.exists() else None)
    if source_path is None:
        return math.nan, "no_reconstruible", "No se puede reconstruir BPM estable ciego"

    try:
        with open(source_path, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            rows = list(reader)
    except OSError:
        return math.nan, "no_reconstruible", "No se puede reconstruir BPM estable ciego"
    if not rows:
        return math.nan, "no_reconstruible", "No se puede reconstruir BPM estable ciego"

    t = np.asarray([_as_float(r.get("tiempo_s")) for r in rows], dtype=float)
    red = np.asarray([_as_float(r.get("red_raw")) for r in rows], dtype=float)
    ir = np.asarray([_as_float(r.get("ir_raw")) for r in rows], dtype=float)
    stable = stable_bpm_segment(t, red, ir, sensor_cfg, analysis_cfg, window_s=5.0, reference_bpm=None)
    value = stable.bpm_estable_5s
    if capture_id:
        cache[capture_id] = (value, "recalculado")
    if not np.isfinite(value):
        return math.nan, "no_reconstruible", "No se puede reconstruir BPM estable ciego"
    return value, "recalculado", ""


# ---------------------------------------------------------------------------
# Export (section 16) - CSV pairs/exclusions + JSON config+results.
# PNG/SVG/HTML need the Qt plot widget, so those are produced by the GUI
# layer (ppg_suite/windows/agreement_window.py), reusing this module's data.
# ---------------------------------------------------------------------------

_PAIR_FIELDS = [
    "capture_id", "animal_id", "species", "fecha", "config_label", "bpm_method",
    "software_bpm", "reference_bpm", "reference_source", "reference_count",
    "mean_value", "difference", "abs_difference", "pct_difference", "quality",
    "stable_start_s", "stable_end_s", "pi_ir_pct", "artifact_pct", "notes",
]
_EXCLUSION_FIELDS = ["capture_id", "animal_id", "reason", "secondary_reasons"]


def export_agreement_results(
    pairs: Sequence[AgreementPair],
    exclusions: Sequence[AgreementExclusion],
    result: BlandAltmanResult | None,
    error_metrics: ErrorMetrics | None,
    config: AgreementAnalysisConfig,
    out_dir: Path,
    *,
    name_prefix: str,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    pairs_path = out_dir / f"{name_prefix}_pares.csv"
    with atomic_csv_dict_writer(pairs_path, _PAIR_FIELDS, delimiter=";") as writer:
        writer.writeheader()
        for p in pairs:
            writer.writerow(asdict(p))
    written["pairs_csv"] = pairs_path

    exclusions_path = out_dir / f"{name_prefix}_exclusiones.csv"
    with atomic_csv_dict_writer(exclusions_path, _EXCLUSION_FIELDS, delimiter=";") as writer:
        writer.writeheader()
        for e in exclusions:
            writer.writerow(
                {
                    "capture_id": e.capture_id,
                    "animal_id": e.animal_id,
                    "reason": e.reason,
                    "secondary_reasons": "; ".join(e.secondary_reasons),
                }
            )
    written["exclusions_csv"] = exclusions_path

    summary_path = out_dir / f"{name_prefix}.json"
    payload = {
        "config": asdict(config),
        "n_pairs": len(pairs),
        "n_exclusions": len(exclusions),
        "result": asdict(result) if result else None,
        "error_metrics": asdict(error_metrics) if error_metrics else None,
    }
    atomic_write_json(summary_path, payload)
    written["summary_json"] = summary_path
    return written
