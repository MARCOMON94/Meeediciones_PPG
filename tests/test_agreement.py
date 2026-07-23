from __future__ import annotations

import math

import numpy as np

from ppg_suite.agreement import (
    AgreementAnalysisConfig,
    AgreementExclusion,
    AgreementPair,
    bland_altman_auto,
    bland_altman_classic,
    bland_altman_repeated,
    build_agreement_pairs,
    calculate_error_metrics,
    cluster_bootstrap_bland_altman,
    export_agreement_results,
)


def _row(capture_id: str, animal_id: str, software_bpm: float, reference_bpm: float, **extra) -> dict:
    base = {
        "capture_id": capture_id,
        "animal_id": animal_id,
        "animal_type": "oveja",
        "bpm_estable_ciego_5s": software_bpm,
        "pulso_final_pulsio": reference_bpm,
        "pulso_final_fonendo": reference_bpm,
        "calidad": 80.0,
        "pi_ir_pct": 1.0,
        "artefactos_ir_pct": 0.0,
        "saturation_pct": 0.0,
    }
    base.update(extra)
    return base


def test_known_bias_bland_altman():
    reference = [80.0, 90.0, 100.0, 110.0]
    rows = [_row(f"c{i}", f"a{i}", r + 5.0, r) for i, r in enumerate(reference)]

    pairs, exclusions = build_agreement_pairs(rows, config=AgreementAnalysisConfig())
    assert len(pairs) == 4
    assert not exclusions

    result = bland_altman_classic(pairs)
    assert result is not None
    assert abs(result.bias - 5.0) < 1e-9
    assert abs(result.sd_diff - 0.0) < 1e-9
    assert abs(result.loa_low - 5.0) < 1e-9
    assert abs(result.loa_high - 5.0) < 1e-9


def test_known_dispersion_bland_altman():
    reference = [80.0, 90.0, 100.0, 110.0, 120.0]
    diffs = [2.0, -2.0, 6.0, -6.0, 0.0]
    rows = [_row(f"c{i}", f"a{i}", r + d, r) for i, (r, d) in enumerate(zip(reference, diffs))]

    pairs, _exclusions = build_agreement_pairs(rows, config=AgreementAnalysisConfig())
    result = bland_altman_classic(pairs)

    expected_bias = float(np.mean(diffs))
    expected_sd = float(np.std(diffs, ddof=1))
    assert abs(result.bias - expected_bias) < 1e-9
    assert abs(result.sd_diff - expected_sd) < 1e-9
    assert abs(result.loa_low - (expected_bias - 1.96 * expected_sd)) < 1e-9
    assert abs(result.loa_high - (expected_bias + 1.96 * expected_sd)) < 1e-9


def test_missing_values_are_excluded_without_blocking_analysis():
    rows = [
        _row("c0", "a0", 85.0, 80.0),
        _row("c1", "a1", math.nan, 90.0),  # no software bpm
        _row("c2", "a2", 95.0, math.nan, pulso_final_pulsio="", pulso_final_fonendo=""),  # no reference
        _row("c3", "a3", 100.0, 95.0),
    ]
    pairs, exclusions = build_agreement_pairs(rows, config=AgreementAnalysisConfig())

    assert len(pairs) == 2
    assert len(exclusions) == 2
    reasons = {e.reason for e in exclusions}
    assert "Sin segmento estable ciego" in reasons
    assert "Sin referencia válida" in reasons

    # n=2 is enough to compute a result, just with an exploratory-sample warning.
    result = bland_altman_classic(pairs)
    assert result is not None
    assert any("insuficiente" in w for w in result.warnings)


def test_duplicate_capture_is_only_counted_once():
    rows = [
        _row("c0", "a0", 85.0, 80.0),
        _row("c0", "a0", 999.0, 1.0),  # same capture_id again, should be excluded as duplicate
    ]
    pairs, exclusions = build_agreement_pairs(rows, config=AgreementAnalysisConfig())

    assert len(pairs) == 1
    assert len(exclusions) == 1
    assert exclusions[0].reason == "Duplicado"


def test_inclusion_criteria_apply_before_reference_distance():
    # A row far from its reference must still be includable - filtering by
    # closeness to the reference would bias the analysis (section 11).
    rows = [_row("c0", "a0", 150.0, 80.0)]
    pairs, exclusions = build_agreement_pairs(rows, config=AgreementAnalysisConfig())
    assert len(pairs) == 1
    assert not exclusions
    assert abs(pairs[0].difference - 70.0) < 1e-9

    # Quality-based criteria, on the other hand, DO apply and are independent
    # of the reference value.
    low_quality_rows = [_row("c0", "a0", 150.0, 80.0, calidad=10.0)]
    cfg = AgreementAnalysisConfig(min_quality=50.0)
    pairs2, exclusions2 = build_agreement_pairs(low_quality_rows, config=cfg)
    assert not pairs2
    assert exclusions2[0].reason == "Calidad insuficiente"


def test_repeated_measures_group_by_animal():
    rows = [
        _row("c0", "a0", 85.0, 80.0),
        _row("c1", "a0", 87.0, 82.0),
        _row("c2", "a1", 95.0, 90.0),
        _row("c3", "a2", 100.0, 100.0),
    ]
    pairs, _exclusions = build_agreement_pairs(rows, config=AgreementAnalysisConfig())

    # One pair per animal -> classic.
    single_per_animal = [pairs[0], pairs[2], pairs[3]]
    assert bland_altman_auto(single_per_animal).mode == "classic"

    # a0 contributes two pairs -> repeated.
    result = bland_altman_auto(pairs)
    assert result.mode == "repeated"
    assert result.n_animals == 3
    assert result.n == 4


def test_repeated_measures_without_animal_id_falls_back_to_classic():
    rows = [
        _row("c0", "", 85.0, 80.0),
        _row("c1", "a1", 95.0, 90.0),
    ]
    pairs, _exclusions = build_agreement_pairs(rows, config=AgreementAnalysisConfig())
    result = bland_altman_repeated(pairs)
    assert result is not None
    assert result.mode == "classic"
    assert any("animal_id" in w for w in result.warnings)


def test_bootstrap_is_reproducible_with_same_seed():
    rows = [
        _row("c0", "a0", 85.0, 80.0),
        _row("c1", "a0", 87.0, 82.0),
        _row("c2", "a1", 95.0, 90.0),
        _row("c3", "a2", 100.0, 100.0),
        _row("c4", "a2", 102.0, 101.0),
    ]
    pairs, _exclusions = build_agreement_pairs(rows, config=AgreementAnalysisConfig())

    first = cluster_bootstrap_bland_altman(pairs, mode="repeated", iterations=300, seed=123)
    second = cluster_bootstrap_bland_altman(pairs, mode="repeated", iterations=300, seed=123)
    assert first.bias_ci == second.bias_ci
    assert first.loa_low_ci == second.loa_low_ci
    assert first.loa_high_ci == second.loa_high_ci

    different = cluster_bootstrap_bland_altman(pairs, mode="repeated", iterations=300, seed=456)
    assert different.bias_ci != first.bias_ci or different.loa_low_ci != first.loa_low_ci


def test_log_scale_rejects_non_positive_values():
    rows = [
        _row("c0", "a0", -5.0, 80.0),
        _row("c1", "a1", 90.0, 0.0, pulso_final_pulsio=0, pulso_final_fonendo=0),
        _row("c2", "a2", 95.0, 90.0),
    ]
    cfg = AgreementAnalysisConfig(scale="log")
    pairs, exclusions = build_agreement_pairs(rows, config=cfg)

    assert len(pairs) == 1
    assert pairs[0].capture_id == "c2"
    reasons = {e.reason for e in exclusions}
    assert "Valor no positivo incompatible con escala logarítmica" in reasons or "Sin referencia válida" in reasons


def test_small_sample_warns_but_does_not_block():
    rows = [_row("c0", "a0", 85.0, 80.0), _row("c1", "a1", 95.0, 90.0), _row("c2", "a2", 78.0, 80.0)]
    pairs, _exclusions = build_agreement_pairs(rows, config=AgreementAnalysisConfig())
    result = bland_altman_classic(pairs)
    assert result is not None
    assert result.n == 3
    assert any("insuficiente" in w for w in result.warnings)


def test_export_writes_all_pairs_and_exclusion_reasons(tmp_path):
    rows = [
        _row("c0", "a0", 85.0, 80.0),
        _row("c1", "a1", math.nan, 90.0),
    ]
    cfg = AgreementAnalysisConfig()
    pairs, exclusions = build_agreement_pairs(rows, config=cfg)
    result = bland_altman_classic(pairs)
    metrics = calculate_error_metrics(pairs)

    written = export_agreement_results(pairs, exclusions, result, metrics, cfg, tmp_path, name_prefix="test_export")

    pairs_text = written["pairs_csv"].read_text(encoding="utf-8")
    for pair in pairs:
        assert pair.capture_id in pairs_text

    exclusions_text = written["exclusions_csv"].read_text(encoding="utf-8")
    for exclusion in exclusions:
        assert exclusion.capture_id in exclusions_text
        assert exclusion.reason in exclusions_text

    import json

    payload = json.loads(written["summary_json"].read_text(encoding="utf-8"))
    assert payload["n_pairs"] == len(pairs)
    assert payload["n_exclusions"] == len(exclusions)
