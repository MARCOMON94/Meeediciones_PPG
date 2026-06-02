from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

if getattr(sys, "frozen", False):
    APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    APP_ROOT = Path(__file__).resolve().parents[1]

PROJECT_ROOT = APP_ROOT


def _read_project_dir_from_env() -> Path:
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return PROJECT_ROOT

    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip().upper() == "PROJECT_DIR":
                value = value.strip().strip('"')
                if value:
                    path = Path(value)
                    if path.exists():
                        return path
    except OSError:
        return PROJECT_ROOT

    return PROJECT_ROOT


def _installed_data_dir() -> Path:
    documents = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents"
    return documents / "mtestv2"


BASE_DIR = _installed_data_dir() if getattr(sys, "frozen", False) else _read_project_dir_from_env()
RESULTS_DIR = BASE_DIR / "resultados"
RAW_DIR = RESULTS_DIR / "raw"
PROCESSED_DIR = RESULTS_DIR / "processed"
SESSION_DIR = RESULTS_DIR / "sessions"
FIGURES_DIR = RESULTS_DIR / "figures"
SCREENSHOT_DIR = RESULTS_DIR / "screenshots"
LOG_DIR = RESULTS_DIR / "logs"
CONFIG_DIR = RESULTS_DIR / "configs"
REPORT_DIR = RESULTS_DIR / "reports"

RESULT_FOLDERS = (RESULTS_DIR, RAW_DIR, PROCESSED_DIR, SESSION_DIR, FIGURES_DIR, SCREENSHOT_DIR, LOG_DIR, CONFIG_DIR, REPORT_DIR)

for folder in RESULT_FOLDERS:
    folder.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / f"ppg_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
log = logging.getLogger("ppg_suite")
