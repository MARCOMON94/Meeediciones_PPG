"""Bland-Altman / method-agreement analysis panel.

Reusable ``AgreementAnalysisPanel`` widget: embedded as a tab in
``RelationExplorerWindow`` (Estadísticas) and opened as a dialog from
``AnimalsWindow`` ("Analizar concordancia"). All the statistics live in
``ppg_suite/agreement.py`` (pure, Qt-free); this module is the Qt view over
that data - controls, the Bland-Altman plot, and the result/pairs/exclusions/
comparison tables.
"""

from __future__ import annotations

import html
import math
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Sequence

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from ..agreement import (
    BPM_METHOD_FIELDS,
    BPM_METHOD_LABELS,
    CIRCULARITY_WARNING,
    CORRELATION_WARNING,
    REFERENCE_SOURCE_LABELS,
    AgreementAnalysisConfig,
    AgreementExclusion,
    AgreementPair,
    BlandAltmanResult,
    build_agreement_pairs,
    calculate_error_metrics,
    cluster_bootstrap_bland_altman,
    detect_heteroscedasticity,
    export_agreement_results,
    log_scale_ratios,
    proportional_bias_analysis,
    resolve_blind_stable_bpm,
)
from ..models import AnalysisConfig, SensorConfig
from ..paths import AGREEMENT_REPORT_DIR
from ..utils import sanitize_id

ScopeProvider = Callable[[str], Sequence[Mapping[str, object]]]

_METHOD_ITEMS = [(key, BPM_METHOD_LABELS[key]) for key in ("ciego", "final", "asistido", "peaks", "fft", "autocorr")]
_REFERENCE_ITEMS = [(key, REFERENCE_SOURCE_LABELS[key]) for key in ("media", "previo", "pulsio", "fonendo")]
_MODE_ITEMS = [("auto", "Automático"), ("classic", "Clásico"), ("repeated", "Medidas repetidas")]
_SCALE_ITEMS = [("absolute", "Absoluta (BPM)"), ("percent", "Porcentual (%)"), ("log", "Logarítmica")]

_TOOLTIP_FIELDS = ["animal_id", "fecha", "bpm_method", "software_bpm", "reference_bpm", "difference", "quality", "config_label"]


def _combo(items: list[tuple[str, str]]) -> QtWidgets.QComboBox:
    combo = QtWidgets.QComboBox()
    for key, label in items:
        combo.addItem(label, key)
    return combo


class AgreementAnalysisPanel(QtWidgets.QWidget):
    def __init__(
        self,
        scope_provider: ScopeProvider,
        *,
        scope_options: list[tuple[str, str]] | None = None,
        default_config_label: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.scope_provider = scope_provider
        self.default_config_label = default_config_label
        self.pairs: list[AgreementPair] = []
        self.exclusions: list[AgreementExclusion] = []
        self.result: BlandAltmanResult | None = None
        self.error_metrics = None
        self._reconstruction_cache: dict[str, tuple[float, str]] = {}
        self._build_ui(scope_options or [("current", "Ámbito actual")])

    # -- UI construction ---------------------------------------------------

    def _build_ui(self, scope_options: list[tuple[str, str]]):
        root = QtWidgets.QVBoxLayout(self)

        controls_group = QtWidgets.QGroupBox("Concordancia / Bland-Altman")
        controls = QtWidgets.QGridLayout(controls_group)

        self.scope_combo = _combo(scope_options)
        self.method_combo = _combo(_METHOD_ITEMS)
        self.reference_combo = _combo(_REFERENCE_ITEMS)
        self.mode_combo = _combo(_MODE_ITEMS)
        self.scale_combo = _combo(_SCALE_ITEMS)

        self.min_quality_spin = QtWidgets.QDoubleSpinBox(); self.min_quality_spin.setRange(0, 100); self.min_quality_spin.setValue(0)
        self.min_pi_spin = QtWidgets.QDoubleSpinBox(); self.min_pi_spin.setRange(0, 100); self.min_pi_spin.setDecimals(3); self.min_pi_spin.setValue(0)
        self.max_artifact_spin = QtWidgets.QDoubleSpinBox(); self.max_artifact_spin.setRange(0, 100); self.max_artifact_spin.setValue(100)
        self.max_saturation_spin = QtWidgets.QDoubleSpinBox(); self.max_saturation_spin.setRange(0, 100); self.max_saturation_spin.setValue(100)
        self.min_estimators_spin = QtWidgets.QSpinBox(); self.min_estimators_spin.setRange(0, 3); self.min_estimators_spin.setValue(0)
        self.max_spread_spin = QtWidgets.QDoubleSpinBox(); self.max_spread_spin.setRange(0, 999); self.max_spread_spin.setValue(999)

        self.iterations_spin = QtWidgets.QSpinBox(); self.iterations_spin.setRange(500, 10000); self.iterations_spin.setSingleStep(500); self.iterations_spin.setValue(2000)
        self.seed_spin = QtWidgets.QSpinBox(); self.seed_spin.setRange(0, 999999); self.seed_spin.setValue(12345)

        row = 0
        for label, widget in (
            ("Ámbito", self.scope_combo),
            ("Método BPM", self.method_combo),
            ("Referencia", self.reference_combo),
            ("Análisis", self.mode_combo),
            ("Escala", self.scale_combo),
        ):
            controls.addWidget(QtWidgets.QLabel(label), row, 0)
            controls.addWidget(widget, row, 1)
            row += 1

        row = 0
        for label, widget in (
            ("Calidad mín.", self.min_quality_spin),
            ("PI IR mín. (%)", self.min_pi_spin),
            ("Artefactos IR máx. (%)", self.max_artifact_spin),
            ("Saturación máx. (%)", self.max_saturation_spin),
            ("Estimadores válidos mín.", self.min_estimators_spin),
            ("Spread estimadores máx.", self.max_spread_spin),
            ("Iteraciones bootstrap", self.iterations_spin),
            ("Semilla bootstrap", self.seed_spin),
        ):
            controls.addWidget(QtWidgets.QLabel(label), row, 2)
            controls.addWidget(widget, row, 3)
            row += 1

        buttons = QtWidgets.QHBoxLayout()
        self.btn_calculate = QtWidgets.QPushButton("Calcular")
        self.btn_export = QtWidgets.QPushButton("Exportar")
        self.btn_reset = QtWidgets.QPushButton("Restablecer filtros")
        buttons.addWidget(self.btn_calculate)
        buttons.addWidget(self.btn_export)
        buttons.addWidget(self.btn_reset)
        buttons.addStretch(1)
        controls.addLayout(buttons, row, 0, 1, 4)

        root.addWidget(controls_group)

        self.scope_label = QtWidgets.QLabel("")
        root.addWidget(self.scope_label)
        if len(scope_options) <= 1:
            self.scope_combo.setVisible(False)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        root.addWidget(splitter, stretch=1)

        top = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.summary_text = QtWidgets.QTextEdit(); self.summary_text.setReadOnly(True)
        self.summary_text.setMinimumWidth(280); self.summary_text.setMaximumWidth(420)
        top.addWidget(self.summary_text)

        self.plot = pg.PlotWidget(title="Bland-Altman")
        self.plot.setBackground("w")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "Media (software y referencia)")
        self.plot.setLabel("left", "Diferencia (software - referencia)")
        top.addWidget(self.plot)
        top.setSizes([320, 640])
        splitter.addWidget(top)

        self.tabs = QtWidgets.QTabWidget()
        self.pairs_table = QtWidgets.QTableWidget(0, len(_PAIR_COLUMNS))
        self.pairs_table.setHorizontalHeaderLabels([label for _key, label in _PAIR_COLUMNS])
        self.pairs_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pairs_table.setAlternatingRowColors(True)
        self.tabs.addTab(self.pairs_table, "Pares")

        self.exclusions_table = QtWidgets.QTableWidget(0, 3)
        self.exclusions_table.setHorizontalHeaderLabels(["Toma", "Animal", "Motivo"])
        self.exclusions_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.exclusions_table.setAlternatingRowColors(True)
        self.tabs.addTab(self.exclusions_table, "Exclusiones")

        self.comparison_table = QtWidgets.QTableWidget(0, len(_COMPARISON_COLUMNS))
        self.comparison_table.setHorizontalHeaderLabels(_COMPARISON_COLUMNS)
        self.comparison_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.comparison_table.setAlternatingRowColors(True)
        self.tabs.addTab(self.comparison_table, "Comparación de estimadores")

        self.warnings_text = QtWidgets.QTextEdit(); self.warnings_text.setReadOnly(True)
        self.tabs.addTab(self.warnings_text, "Avisos metodológicos")

        splitter.addWidget(self.tabs)
        splitter.setSizes([360, 360])

        self.btn_calculate.clicked.connect(self.calculate)
        self.btn_export.clicked.connect(self.export_results)
        self.btn_reset.clicked.connect(self.reset_filters)

        self._proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved, rateLimit=30, slot=self._on_mouse_moved)
        self._scatter: pg.ScatterPlotItem | None = None
        self._scatter_pairs: list[AgreementPair] = []

        self.reset_filters()

    # -- Filters -------------------------------------------------------

    def reset_filters(self):
        self.method_combo.setCurrentIndex(0)  # "ciego"
        self.reference_combo.setCurrentIndex(0)  # "media"
        self.mode_combo.setCurrentIndex(0)  # "auto"
        self.scale_combo.setCurrentIndex(0)  # "absolute"
        self.min_quality_spin.setValue(0)
        self.min_pi_spin.setValue(0)
        self.max_artifact_spin.setValue(100)
        self.max_saturation_spin.setValue(100)
        self.min_estimators_spin.setValue(0)
        self.max_spread_spin.setValue(999)
        self.iterations_spin.setValue(2000)
        self.seed_spin.setValue(12345)

    def current_config(self) -> AgreementAnalysisConfig:
        return AgreementAnalysisConfig(
            bpm_method=self.method_combo.currentData(),
            reference_source=self.reference_combo.currentData(),
            mode=self.mode_combo.currentData(),
            scale=self.scale_combo.currentData(),
            min_quality=self.min_quality_spin.value(),
            min_pi_ir_pct=self.min_pi_spin.value(),
            max_artifact_pct=self.max_artifact_spin.value(),
            max_saturation_pct=self.max_saturation_spin.value(),
            min_estimators_valid=self.min_estimators_spin.value(),
            max_estimator_spread=self.max_spread_spin.value(),
            bootstrap_iterations=self.iterations_spin.value(),
            bootstrap_seed=self.seed_spin.value(),
        )

    # -- Calculation -----------------------------------------------------

    def _reconstruct_missing_blind(self, rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
        """Fill in bpm_estable_ciego_5s for old summaries that predate the
        blind/assisted split, recomputing from raw/processed when possible
        (section 17). Never mutates the caller's data or writes to disk."""
        out: list[dict[str, object]] = []
        for row in rows:
            row_dict = dict(row)
            stored = row_dict.get("bpm_estable_ciego_5s")
            if stored not in (None, "") and math.isfinite(_safe_float(stored)):
                out.append(row_dict)
                continue
            raw_path = row_dict.get("_raw_path")
            processed_path = row_dict.get("_processed_path")
            value, status, _reason = resolve_blind_stable_bpm(
                row_dict,
                raw_path=Path(raw_path) if raw_path else None,
                processed_path=Path(processed_path) if processed_path else None,
                sensor_cfg=SensorConfig(),
                analysis_cfg=AnalysisConfig(),
                cache=self._reconstruction_cache,
            )
            if math.isfinite(value):
                row_dict["bpm_estable_ciego_5s"] = value
                row_dict["notes"] = "recalculado" if status == "recalculado" else row_dict.get("notes", "")
            out.append(row_dict)
        return out

    def calculate(self):
        config = self.current_config()
        rows = list(self.scope_provider(self.scope_combo.currentData() or "current"))
        rows = self._reconstruct_missing_blind(rows)
        self.pairs, self.exclusions = build_agreement_pairs(rows, config=config)

        mode = config.mode
        base_mode = ("repeated" if _has_repeats(self.pairs) else "classic") if mode == "auto" else mode
        self.result = cluster_bootstrap_bland_altman(
            self.pairs, mode=base_mode, iterations=config.bootstrap_iterations, seed=config.bootstrap_seed
        )
        self.error_metrics = calculate_error_metrics(self.pairs)
        self._prop_bias = proportional_bias_analysis(
            self.pairs, mode=base_mode, iterations=config.bootstrap_iterations, seed=config.bootstrap_seed
        )
        self._heteroscedasticity = detect_heteroscedasticity(
            self.pairs, iterations=config.bootstrap_iterations, seed=config.bootstrap_seed
        )

        self.scope_label.setText(f"{len(self.pairs)} pares incluidos, {len(self.exclusions)} excluidos.")
        self._refresh_plot()
        self._refresh_summary(config)
        self._refresh_pairs_table()
        self._refresh_exclusions_table()
        self._refresh_comparison_table(rows, config)
        self._refresh_warnings()

    # -- Rendering ---------------------------------------------------------

    def _refresh_plot(self):
        self.plot.clear()
        self._scatter = None
        self._scatter_pairs = list(self.pairs)
        if not self.pairs or self.result is None:
            self.plot.setTitle("Sin pares válidos con los filtros actuales")
            return
        means = np.asarray([p.mean_value for p in self.pairs], dtype=float)
        diffs = np.asarray([p.difference for p in self.pairs], dtype=float)
        colors = [
            (200, 60, 60, 200) if not (self.result.loa_low <= p.difference <= self.result.loa_high) else (30, 100, 200, 160)
            for p in self.pairs
        ]
        scatter = pg.ScatterPlotItem(size=9, pen=pg.mkPen(None))
        scatter.setData(means.tolist(), diffs.tolist(), brush=[pg.mkBrush(c) for c in colors], data=list(range(len(self.pairs))))
        self.plot.addItem(scatter)
        self._scatter = scatter

        self.plot.addItem(pg.InfiniteLine(pos=0.0, angle=0, pen=pg.mkPen((150, 150, 150), width=1, style=QtCore.Qt.PenStyle.DotLine)))
        self.plot.addItem(pg.InfiniteLine(pos=self.result.bias, angle=0, pen=pg.mkPen((20, 140, 70), width=2)))
        for loa_pos in (self.result.loa_low, self.result.loa_high):
            self.plot.addItem(pg.InfiniteLine(pos=loa_pos, angle=0, pen=pg.mkPen((200, 60, 60), width=2, style=QtCore.Qt.PenStyle.DashLine)))
        if self.result.loa_low_ci and self.result.loa_high_ci:
            for ci in (self.result.loa_low_ci, self.result.loa_high_ci):
                band = pg.LinearRegionItem(values=list(ci), orientation="horizontal", movable=False, brush=pg.mkBrush(200, 60, 60, 35))
                band.setZValue(-10)
                self.plot.addItem(band)

        method_label = BPM_METHOD_LABELS.get(self.pairs[0].bpm_method, self.pairs[0].bpm_method)
        ref_label = REFERENCE_SOURCE_LABELS.get(self.pairs[0].reference_source, self.pairs[0].reference_source)
        self.plot.setTitle(
            f"Bland-Altman ({self.result.mode}) - {method_label} vs. {ref_label} - n={self.result.n}"
        )

    def _on_mouse_moved(self, event):
        if self._scatter is None or not self._scatter_pairs:
            return
        pos = event[0]
        if not self.plot.plotItem.vb.sceneBoundingRect().contains(pos):
            QtWidgets.QToolTip.hideText()
            return
        view_pos = self.plot.plotItem.vb.mapSceneToView(pos)
        points = self._scatter.pointsAt(view_pos)
        if not len(points):
            QtWidgets.QToolTip.hideText()
            return
        index = points[0].data()
        if index is None or not (0 <= index < len(self._scatter_pairs)):
            return
        pair = self._scatter_pairs[index]
        text = "<br>".join(
            f"<b>{label}:</b> {_format_tooltip_value(getattr(pair, field))}"
            for field, label in zip(_TOOLTIP_FIELDS, ["Animal", "Fecha", "Método", "BPM software", "BPM referencia", "Diferencia", "Calidad", "Configuración"])
        )
        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), text)

    def _refresh_summary(self, config: AgreementAnalysisConfig):
        result = self.result
        parts: list[str] = [f"<p><b>{html.escape(CIRCULARITY_WARNING)}</b></p>"]
        if result is None:
            parts.append("<p>No hay suficientes pares (se necesitan al menos 2) para calcular Bland-Altman.</p>")
            self.summary_text.setHtml("".join(parts))
            return

        def row(name: str, value: str) -> str:
            return f"<tr><td><b>{html.escape(name)}</b></td><td>{html.escape(value)}</td></tr>"

        def ci_text(ci: tuple[float, float] | None) -> str:
            return f" (IC95%: {ci[0]:.1f} a {ci[1]:.1f})" if ci else ""

        fields = [
            ("Modo", "Medidas repetidas" if result.mode == "repeated" else "Clásico"),
            ("N pares / N animales", f"{result.n} / {result.n_animals}"),
            ("Sesgo", f"{result.bias:.2f}{ci_text(result.bias_ci)}"),
            ("SD diferencias", f"{result.sd_diff:.2f}"),
            ("Límite inferior (95%)", f"{result.loa_low:.2f}{ci_text(result.loa_low_ci)}"),
            ("Límite superior (95%)", f"{result.loa_high:.2f}{ci_text(result.loa_high_ci)}"),
        ]
        if result.between_animal_sd is not None:
            fields.append(("SD entre animales / residual", f"{result.between_animal_sd:.2f} / {result.within_animal_sd:.2f}"))
        if self.error_metrics:
            em = self.error_metrics
            fields.extend(
                [
                    ("MAE", f"{em.mae:.2f}{ci_text(result.mae_ci)}"),
                    ("RMSE", f"{em.rmse:.2f}{ci_text(result.rmse_ci)}"),
                    ("Mediana error absoluto", f"{em.median_abs_error:.2f}"),
                    ("Error min / max", f"{em.min_error:.2f} / {em.max_error:.2f}"),
                    ("Percentil 90 error absoluto", f"{em.p90_abs_error:.2f}"),
                    ("Dentro de ±5 / ±10 / ±20 BPM", f"{em.pct_within_5:.0f}% / {em.pct_within_10:.0f}% / {em.pct_within_20:.0f}%"),
                ]
            )
        if config.scale == "log":
            ratios = log_scale_ratios(result)
            fields.append(("Razón software/referencia (retrotransformada)", f"{ratios['ratio_bias']:.3f} ({ratios['ratio_loa_low']:.3f} a {ratios['ratio_loa_high']:.3f})"))
        prop = getattr(self, "_prop_bias", None)
        if prop:
            ci = ci_text(prop.slope_ci)
            fields.append(("Pendiente sesgo proporcional", f"{prop.slope:.3f}{ci} (p={prop.p_value:.3f})" if prop.p_value is not None else f"{prop.slope:.3f}{ci}"))

        html_rows = "".join(row(name, value) for name, value in fields)
        parts.append(f"<table cellspacing='4'>{html_rows}</table>")
        parts.append(f"<p>{html.escape(CORRELATION_WARNING)}</p>")
        self.summary_text.setHtml("".join(parts))

    def _refresh_pairs_table(self):
        self.pairs_table.setRowCount(len(self.pairs))
        for r, pair in enumerate(self.pairs):
            data = asdict(pair)
            for c, (key, _label) in enumerate(_PAIR_COLUMNS):
                value = data.get(key, "")
                text = f"{value:.2f}" if isinstance(value, float) else str(value)
                self.pairs_table.setItem(r, c, QtWidgets.QTableWidgetItem(text))
        self.pairs_table.resizeColumnsToContents()

    def _refresh_exclusions_table(self):
        self.exclusions_table.setRowCount(len(self.exclusions))
        for r, exclusion in enumerate(self.exclusions):
            values = [exclusion.capture_id, exclusion.animal_id, exclusion.reason]
            for c, value in enumerate(values):
                self.exclusions_table.setItem(r, c, QtWidgets.QTableWidgetItem(str(value)))
        self.exclusions_table.resizeColumnsToContents()

    def _refresh_comparison_table(self, rows: list[dict[str, object]], base_config: AgreementAnalysisConfig):
        methods = list(BPM_METHOD_FIELDS.keys())
        self.comparison_table.setRowCount(len(methods))
        total_rows = len(rows) or 1
        for r, method in enumerate(methods):
            cfg = AgreementAnalysisConfig(
                bpm_method=method,
                reference_source=base_config.reference_source,
                mode=base_config.mode,
                scale=base_config.scale,
                min_quality=base_config.min_quality,
                min_pi_ir_pct=base_config.min_pi_ir_pct,
                max_artifact_pct=base_config.max_artifact_pct,
                max_saturation_pct=base_config.max_saturation_pct,
                min_estimators_valid=base_config.min_estimators_valid,
                max_estimator_spread=base_config.max_estimator_spread,
            )
            pairs, _exclusions = build_agreement_pairs(rows, config=cfg)
            from ..agreement import bland_altman_auto

            result = bland_altman_auto(pairs) if pairs else None
            metrics = calculate_error_metrics(pairs) if pairs else None
            prop = proportional_bias_analysis(pairs, mode="classic", iterations=200, seed=1) if len(pairs) >= 3 else None
            availability = 100.0 * len(pairs) / total_rows
            values = [
                BPM_METHOD_LABELS[method],
                str(len(pairs)),
                f"{availability:.0f}%",
                f"{result.bias:.2f}" if result else "-",
                f"{result.loa_low:.2f}" if result else "-",
                f"{result.loa_high:.2f}" if result else "-",
                f"{(result.loa_high - result.loa_low):.2f}" if result else "-",
                f"{metrics.mae:.2f}" if metrics else "-",
                f"{metrics.rmse:.2f}" if metrics else "-",
                f"{metrics.pct_within_5:.0f}%" if metrics else "-",
                f"{metrics.pct_within_10:.0f}%" if metrics else "-",
                f"{metrics.pct_within_20:.0f}%" if metrics else "-",
                f"{prop.slope:.3f}" if prop else "-",
            ]
            for c, value in enumerate(values):
                self.comparison_table.setItem(r, c, QtWidgets.QTableWidgetItem(value))
        self.comparison_table.resizeColumnsToContents()

    def _refresh_warnings(self):
        lines = [CIRCULARITY_WARNING, CORRELATION_WARNING]
        if self.result:
            lines.extend(self.result.warnings)
        prop = getattr(self, "_prop_bias", None)
        if prop and prop.warning:
            lines.append(prop.warning)
        het = getattr(self, "_heteroscedasticity", None)
        if het and het.warning:
            lines.append(het.warning)
        if not self.pairs:
            lines.append("Sin pares válidos con los filtros y el ámbito seleccionados.")
        self.warnings_text.setHtml("<ul>" + "".join(f"<li>{html.escape(line)}</li>" for line in lines) + "</ul>")

    # -- Export --------------------------------------------------------

    def export_results(self):
        if not self.pairs and not self.exclusions:
            QtWidgets.QMessageBox.information(self, "Exportar", "Calcula primero un análisis para poder exportarlo.")
            return
        config = self.current_config()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        method = sanitize_id(config.bpm_method)
        reference = sanitize_id(config.reference_source)
        mode = sanitize_id(self.result.mode if self.result else config.mode)
        name_prefix = f"bland_altman_{method}_{reference}_{mode}_{stamp}"

        written = export_agreement_results(
            self.pairs, self.exclusions, self.result, self.error_metrics, config, AGREEMENT_REPORT_DIR, name_prefix=name_prefix
        )
        png_path = AGREEMENT_REPORT_DIR / f"{name_prefix}.png"
        self.plot.grab().save(str(png_path), "PNG")
        written["plot_png"] = png_path

        report_path = AGREEMENT_REPORT_DIR / f"{name_prefix}.html"
        report_path.write_text(self._build_html_report(config), encoding="utf-8")
        written["report_html"] = report_path

        QtWidgets.QMessageBox.information(
            self,
            "Exportar",
            "Resultados exportados a:\n\n" + "\n".join(str(path) for path in written.values()),
        )

    def _build_html_report(self, config: AgreementAnalysisConfig) -> str:
        result = self.result
        rows = [
            ("Método", BPM_METHOD_LABELS.get(config.bpm_method, config.bpm_method)),
            ("Referencia", REFERENCE_SOURCE_LABELS.get(config.reference_source, config.reference_source)),
            ("Escala", config.scale),
            ("Criterios de inclusión", f"calidad>={config.min_quality}, PI>={config.min_pi_ir_pct}, artefactos<={config.max_artifact_pct}%, saturación<={config.max_saturation_pct}%"),
            ("N pares / N exclusiones", f"{len(self.pairs)} / {len(self.exclusions)}"),
        ]
        if result:
            rows.extend(
                [
                    ("N animales", str(result.n_animals)),
                    ("Modo", result.mode),
                    ("Sesgo", f"{result.bias:.2f}"),
                    ("Límites de acuerdo", f"{result.loa_low:.2f} a {result.loa_high:.2f}"),
                    ("Avisos", "; ".join(result.warnings) or "-"),
                ]
            )
        if self.error_metrics:
            em = self.error_metrics
            rows.append(("MAE / RMSE", f"{em.mae:.2f} / {em.rmse:.2f}"))
        body_rows = "".join(f"<tr><td><b>{html.escape(k)}</b></td><td>{html.escape(str(v))}</td></tr>" for k, v in rows)
        exclusion_rows = "".join(
            f"<tr><td>{html.escape(e.capture_id)}</td><td>{html.escape(e.animal_id)}</td><td>{html.escape(e.reason)}</td></tr>"
            for e in self.exclusions
        )
        return (
            "<html><head><meta charset='utf-8'><title>Bland-Altman</title></head><body>"
            f"<h1>Informe de concordancia Bland-Altman</h1>"
            f"<p>{html.escape(CIRCULARITY_WARNING)}</p>"
            f"<table border='1' cellspacing='0' cellpadding='4'>{body_rows}</table>"
            f"<h2>Exclusiones</h2>"
            f"<table border='1' cellspacing='0' cellpadding='4'><tr><th>Toma</th><th>Animal</th><th>Motivo</th></tr>{exclusion_rows}</table>"
            "</body></html>"
        )


_PAIR_COLUMNS = [
    ("capture_id", "Toma"),
    ("animal_id", "Animal"),
    ("species", "Especie"),
    ("fecha", "Fecha"),
    ("config_label", "Configuración"),
    ("software_bpm", "BPM software"),
    ("reference_bpm", "BPM referencia"),
    ("difference", "Diferencia"),
    ("abs_difference", "Dif. absoluta"),
    ("quality", "Calidad"),
    ("stable_start_s", "Inicio estable (s)"),
    ("stable_end_s", "Fin estable (s)"),
    ("pi_ir_pct", "PI IR %"),
    ("artifact_pct", "Artefactos %"),
]

_COMPARISON_COLUMNS = [
    "Método", "N", "Disponibilidad", "Sesgo", "Límite inf.", "Límite sup.", "Anchura LoA",
    "MAE", "RMSE", "±5 BPM", "±10 BPM", "±20 BPM", "Pendiente sesgo prop.",
]


def _has_repeats(pairs: Sequence[AgreementPair]) -> bool:
    counts: dict[str, int] = {}
    for p in pairs:
        if p.animal_id:
            counts[p.animal_id] = counts.get(p.animal_id, 0) + 1
    return any(c > 1 for c in counts.values())


def _safe_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


def _format_tooltip_value(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.2f}" if math.isfinite(value) else "-"
    return str(value) if value not in (None, "") else "-"
