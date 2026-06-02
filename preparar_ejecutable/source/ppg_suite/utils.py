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
