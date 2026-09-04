from __future__ import annotations

from pathlib import Path

import numpy as np

from .models import WindowRR
from .spectral import welch_psd


def build_diagnostic_layout(
    *,
    title: str,
    fine_t: np.ndarray,
    fine_ir: np.ndarray,
    fine_red: np.ndarray,
    riiv_ir: np.ndarray,
    riiv_red: np.ndarray,
    riav_ir: np.ndarray,
    riav_red: np.ndarray,
    rifv: np.ndarray,
    resp_t: np.ndarray,
    resp_hz: float,
    windows: list[WindowRR],
    respiratory_low_hz: float,
    respiratory_high_hz: float,
):
    """Build the 6-panel respiratory diagnostic (spec section 25) as a pyqtgraph widget.

    Shared by the offscreen batch PNG exporter and the live GUI window, so both always
    show exactly the same panels.
    """
    import pyqtgraph as pg

    pg.setConfigOption("background", "w")
    pg.setConfigOption("foreground", "k")

    layout = pg.GraphicsLayoutWidget(title=title)
    layout.resize(1100, 1500)

    p1 = layout.addPlot(row=0, col=0, title="RAW IR / RED")
    if fine_ir.size:
        p1.plot(fine_t, fine_ir, pen=pg.mkPen("r", width=1))
    if fine_red.size:
        p1.plot(fine_t, fine_red, pen=pg.mkPen("b", width=1))
    p1.setLabel("bottom", "t", units="s")

    p2 = layout.addPlot(row=1, col=0, title="RIIV (0.10-1.20 Hz band)")
    if riiv_ir.size:
        p2.plot(resp_t, riiv_ir, pen=pg.mkPen("r", width=1), name="IR")
    if riiv_red.size:
        p2.plot(resp_t, riiv_red, pen=pg.mkPen("b", width=1), name="RED")

    p3 = layout.addPlot(row=2, col=0, title="RIAV")
    if riav_ir.size:
        p3.plot(resp_t, riav_ir, pen=pg.mkPen("r", width=1))
    if riav_red.size:
        p3.plot(resp_t, riav_red, pen=pg.mkPen("b", width=1))

    p4 = layout.addPlot(row=3, col=0, title="RIFV")
    if rifv.size:
        p4.plot(resp_t, rifv, pen=pg.mkPen("g", width=1))

    p5 = layout.addPlot(row=4, col=0, title="PSD respiratoria (mejor candidato IR)")
    best_signal = riiv_ir if riiv_ir.size else (riav_ir if riav_ir.size else rifv)
    if best_signal is not None and best_signal.size:
        freqs, psd = welch_psd(best_signal, resp_hz)
        if freqs.size:
            band = (freqs >= respiratory_low_hz) & (freqs <= respiratory_high_hz)
            p5.plot(freqs[band] * 60.0, psd[band], pen=pg.mkPen("k", width=1))
    p5.setLabel("bottom", "rpm")

    p6 = layout.addPlot(row=5, col=0, title="RR por ventana")
    if windows:
        mid = np.asarray([(w.start_s + w.end_s) / 2.0 for w in windows], dtype=float)
        rr = np.asarray([w.rr for w in windows], dtype=float)
        finite = np.isfinite(rr)
        if np.any(finite):
            p6.plot(mid[finite], rr[finite], pen=None, symbol="o", symbolSize=6, symbolBrush="k")
    p6.setLabel("bottom", "t", units="s")
    p6.setLabel("left", "rpm")

    return layout


def render_diagnostic_png(out_path: Path, **kwargs) -> None:
    """Render the diagnostic panels to a PNG file, offscreen (no display required)."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtWidgets
    import pyqtgraph.exporters

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    layout = build_diagnostic_layout(**kwargs)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    exporter = pyqtgraph.exporters.ImageExporter(layout.scene())
    exporter.export(str(out_path))
    layout.close()
