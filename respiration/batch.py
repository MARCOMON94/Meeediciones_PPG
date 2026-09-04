from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np

from ppg_suite.paths import RESULTS_DIR, RAW_DIR

from .config import RespirationConfig
from .models import RespirationMetrics
from .pipeline import compute_rr_estimate
from .preprocessing import prepare_respiration_signal
from .windows import analyze_windows
from . import analyze_respiration

OUTPUT_DIR = RESULTS_DIR / "analisis" / "respiracion"
SUMMARY_CSV = OUTPUT_DIR / "rr_summary.csv"
JSON_DIR = OUTPUT_DIR / "json"
DIAGNOSTICS_DIR = OUTPUT_DIR / "diagnostics"

SUMMARY_COLUMNS = [
    "file", "id", "mode", "duration_s", "duration_band", "hz",
    "rr", "confidence", "valid",
    "rr_riiv_ir", "rr_riiv_red", "rr_riav_ir", "rr_riav_red", "rr_rifv",
    "rr_fft", "rr_autocorr",
    "agreement_methods", "agreement_red_ir", "agreement_estimators", "stability",
    "artifact_red_pct", "artifact_ir_pct",
    "cycles_estimated",
    "reason",
]


def _as_float(value: object) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return math.nan


def load_raw_csv(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    rows = list(csv.DictReader(text.splitlines(), dialect=dialect))
    if not rows:
        return np.asarray([]), np.asarray([]), np.asarray([]), {}

    t = np.asarray([_as_float(r.get("tiempo_s")) for r in rows], dtype=float)
    red = np.asarray([_as_float(r.get("red_raw")) for r in rows], dtype=float)
    ir = np.asarray([_as_float(r.get("ir_raw")) for r in rows], dtype=float)
    row0 = rows[0]
    meta = {"id": row0.get("id", ""), "mode": row0.get("modo", "")}
    return t, red, ir, meta


def duration_band(duration_s: float) -> str:
    if not np.isfinite(duration_s):
        return "desconocida"
    if duration_s >= 60.0:
        return ">=60s"
    if duration_s >= 30.0:
        return "30-59s"
    if duration_s >= 20.0:
        return "20-29s"
    return "<20s"


def discover_raw_files() -> list[Path]:
    if not RAW_DIR.exists():
        return []
    long_files = sorted(RAW_DIR.glob("raw_LONG_*.csv"))
    other_files = sorted(p for p in RAW_DIR.glob("raw_*.csv") if not p.name.startswith("raw_LONG_"))
    return long_files + other_files


def _hz_from_t(t: np.ndarray) -> float:
    if t.size < 2:
        return math.nan
    duration = float(t[-1] - t[0])
    return float((t.size - 1) / duration) if duration > 0 else math.nan


def analyze_file(path: Path, cfg: RespirationConfig) -> tuple[dict, RespirationMetrics]:
    t, red, ir, meta = load_raw_csv(path)
    duration_s = float(t[-1] - t[0]) if t.size > 1 else math.nan
    metrics = analyze_respiration(t, red, ir, cfg)

    row = {
        "file": path.name,
        "id": meta.get("id", ""),
        "mode": meta.get("mode", ""),
        "duration_s": round(duration_s, 2) if np.isfinite(duration_s) else "",
        "duration_band": duration_band(duration_s),
        "hz": round(_hz_from_t(t), 2) if np.isfinite(_hz_from_t(t)) else "",
        "rr": round(metrics.rr, 2) if np.isfinite(metrics.rr) else "",
        "confidence": round(metrics.confidence, 1),
        "valid": metrics.valid,
        "rr_riiv_ir": round(metrics.rr_riiv_ir, 2) if np.isfinite(metrics.rr_riiv_ir) else "",
        "rr_riiv_red": round(metrics.rr_riiv_red, 2) if np.isfinite(metrics.rr_riiv_red) else "",
        "rr_riav_ir": round(metrics.rr_riav_ir, 2) if np.isfinite(metrics.rr_riav_ir) else "",
        "rr_riav_red": round(metrics.rr_riav_red, 2) if np.isfinite(metrics.rr_riav_red) else "",
        "rr_rifv": round(metrics.rr_rifv, 2) if np.isfinite(metrics.rr_rifv) else "",
        "rr_fft": round(metrics.rr_fft, 2) if np.isfinite(metrics.rr_fft) else "",
        "rr_autocorr": round(metrics.rr_autocorr, 2) if np.isfinite(metrics.rr_autocorr) else "",
        "agreement_methods": round(metrics.agreement_methods, 1) if np.isfinite(metrics.agreement_methods) else "",
        "agreement_red_ir": round(metrics.agreement_red_ir, 1) if np.isfinite(metrics.agreement_red_ir) else "",
        "agreement_estimators": round(metrics.agreement_estimators, 1) if np.isfinite(metrics.agreement_estimators) else "",
        "stability": round(metrics.stability, 1) if np.isfinite(metrics.stability) else "",
        "artifact_red_pct": round(metrics.artifact_red_pct, 1) if np.isfinite(metrics.artifact_red_pct) else "",
        "artifact_ir_pct": round(metrics.artifact_ir_pct, 1) if np.isfinite(metrics.artifact_ir_pct) else "",
        "cycles_estimated": round(metrics.respiratory_cycles_estimated, 1) if np.isfinite(metrics.respiratory_cycles_estimated) else "",
        "reason": metrics.reason,
    }
    return row, metrics


def write_json(path: Path, row: dict, metrics: RespirationMetrics) -> None:
    payload = {
        "file": row["file"],
        "duration_s": row["duration_s"],
        "duration_band": row["duration_band"],
        "rr": row["rr"],
        "confidence": row["confidence"],
        "valid": row["valid"],
        "estimators": {
            "riiv_ir": row["rr_riiv_ir"],
            "riiv_red": row["rr_riiv_red"],
            "riav_ir": row["rr_riav_ir"],
            "riav_red": row["rr_riav_red"],
            "rifv": row["rr_rifv"],
        },
        "quality": {
            "rr_fft": row["rr_fft"],
            "rr_autocorr": row["rr_autocorr"],
            "agreement_methods": row["agreement_methods"],
            "agreement_red_ir": row["agreement_red_ir"],
            "agreement_estimators": row["agreement_estimators"],
            "stability": row["stability"],
            "artifact_red_pct": row["artifact_red_pct"],
            "artifact_ir_pct": row["artifact_ir_pct"],
            "cycles_estimated": row["cycles_estimated"],
        },
        "reason": row["reason"],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def render_plot(path: Path, cfg: RespirationConfig, out_png: Path) -> None:
    from .plotting import render_diagnostic_png

    t, red, ir, _meta = load_raw_csv(path)
    prepared = prepare_respiration_signal(t, red, ir, cfg)
    if not prepared.valid:
        return
    estimate = compute_rr_estimate(prepared, cfg)
    windowed = analyze_windows(prepared, cfg)

    from .riiv import compute_riiv
    from .riav import compute_riav, detect_cardiac_beats
    from .rifv import compute_rifv

    riiv_ir = compute_riiv(prepared.resp_ir, prepared.resp_hz, cfg)
    riiv_red = compute_riiv(prepared.resp_red, prepared.resp_hz, cfg) if prepared.has_red else np.asarray([])
    pt, pv, tv = detect_cardiac_beats(prepared.fine_t, prepared.fine_ir, prepared.fine_hz, cfg)
    riav_ir = compute_riav(pt, pv, tv, prepared.resp_t, prepared.resp_hz, cfg)
    rifv = compute_rifv(pt, prepared.resp_t, prepared.resp_hz, cfg)
    riav_red = np.asarray([])
    if prepared.has_red:
        pt_r, pv_r, tv_r = detect_cardiac_beats(prepared.fine_t, prepared.fine_red, prepared.fine_hz, cfg)
        riav_red = compute_riav(pt_r, pv_r, tv_r, prepared.resp_t, prepared.resp_hz, cfg)

    render_diagnostic_png(
        out_png,
        title=path.name,
        fine_t=prepared.fine_t,
        fine_ir=prepared.fine_ir,
        fine_red=prepared.fine_red,
        riiv_ir=riiv_ir,
        riiv_red=riiv_red,
        riav_ir=riav_ir,
        riav_red=riav_red,
        rifv=rifv,
        resp_t=prepared.resp_t,
        resp_hz=prepared.resp_hz,
        windows=windowed.windows,
        respiratory_low_hz=cfg.respiratory_low_hz,
        respiratory_high_hz=cfg.respiratory_high_hz,
    )
    del estimate


def run_batch(limit: int | None = None, plots: bool = False, plots_all: bool = False, plot_top_n: int = 5) -> list[dict]:
    cfg = RespirationConfig()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_DIR.mkdir(parents=True, exist_ok=True)

    files = discover_raw_files()
    if limit is not None:
        files = files[:limit]

    rows: list[dict] = []
    metrics_by_file: dict[str, RespirationMetrics] = {}
    for path in files:
        try:
            row, metrics = analyze_file(path, cfg)
        except Exception as exc:  # noqa: BLE001 - a single malformed raw file must not abort the batch
            row = {col: "" for col in SUMMARY_COLUMNS}
            row["file"] = path.name
            row["valid"] = False
            row["reason"] = f"error de procesamiento: {exc}"
            metrics = RespirationMetrics(valid=False, reason=row["reason"])
        rows.append(row)
        metrics_by_file[path.name] = metrics
        write_json(JSON_DIR / f"{path.stem}.json", row, metrics)

    with SUMMARY_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    if plots or plots_all:
        DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
        valid_rows = [r for r in rows if r.get("valid")]
        by_confidence = sorted(valid_rows, key=lambda r: r["confidence"], reverse=True)
        if plots_all:
            selected = rows
        else:
            best = by_confidence[:plot_top_n]
            worst = sorted(rows, key=lambda r: r["confidence"])[:plot_top_n]
            selected = list({r["file"]: r for r in (best + worst)}.values())
        for row in selected:
            path = RAW_DIR / row["file"]
            if not path.exists():
                continue
            try:
                render_plot(path, cfg, DIAGNOSTICS_DIR / f"{path.stem}.png")
            except Exception:  # noqa: BLE001 - plotting must never abort the batch run
                continue

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrospective respiratory-rate batch analysis over resultados/raw/*.csv")
    parser.add_argument("--limit", type=int, default=None, help="process only the first N files (LONG-first order)")
    parser.add_argument("--plots", action="store_true", help="render diagnostic PNGs for the best/worst confidence files")
    parser.add_argument("--plots-all", action="store_true", help="render diagnostic PNGs for every processed file")
    parser.add_argument("--plot-top-n", type=int, default=5, help="how many best/worst files to plot with --plots")
    args = parser.parse_args()

    rows = run_batch(limit=args.limit, plots=args.plots, plots_all=args.plots_all, plot_top_n=args.plot_top_n)

    total = len(rows)
    valid = sum(1 for r in rows if r.get("valid"))
    print(f"Procesados: {total} archivos")
    print(f"RR válida: {valid} ({(100.0 * valid / total) if total else 0.0:.1f} %)")
    print(f"Resumen: {SUMMARY_CSV}")


if __name__ == "__main__":
    main()
