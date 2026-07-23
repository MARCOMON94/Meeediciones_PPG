from __future__ import annotations

import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def sanitize_id(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "SIN_CROTAL"
    return re.sub(r"[^a-zA-Z0-9_-]", "_", text)


def safe_float_text(text: str) -> str:
    return (text or "").strip().replace(",", ".")


def finite_or_nan(value: float) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) else math.nan
    except Exception:
        return math.nan


def mean_valid_reference(*values: object) -> tuple[float, int]:
    """Mean of manually-entered reference BPM values, ignoring blanks, non-numeric
    text and values <= 0 (e.g. an unfilled pulsioximeter/fonendo reading).

    Single source of truth for this rule - used wherever a manual reference
    (pulso previo / pulsioximetro / fonendo) needs to be averaged, so the
    inclusion criteria stay identical across capture, display and analysis code.
    """
    valid: list[float] = []
    for value in values:
        try:
            bpm = float(str(value if value is not None else "").replace(",", "."))
        except (TypeError, ValueError):
            continue
        if math.isfinite(bpm) and bpm > 0:
            valid.append(bpm)
    if not valid:
        return math.nan, 0
    return float(np.mean(valid)), len(valid)


def fmt(value: object, decimals: int = 2, dash: str = "-") -> str:
    if value is None:
        return dash
    if isinstance(value, (float, np.floating)):
        if not np.isfinite(value):
            return dash
        return f"{float(value):.{decimals}f}"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    text = str(value)
    return text if text else dash


def open_folder(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        os.startfile(str(path))
    elif sys.platform == "darwin":
        os.system(f'open "{path}"')
    else:
        os.system(f'xdg-open "{path}"')
