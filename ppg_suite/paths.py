from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


BASE_DIR = _read_project_dir_from_env()
RAW_DIR = BASE_DIR / "raw"
PROCESSED_DIR = BASE_DIR / "processed"
SESSION_DIR = BASE_DIR / "sessions"
FIGURES_DIR = BASE_DIR / "figures"
SCREENSHOT_DIR = BASE_DIR / "screenshots"
LOG_DIR = BASE_DIR / "logs"
CONFIG_DIR = BASE_DIR / "configs"
REPORT_DIR = BASE_DIR / "reports"

RESULT_FOLDERS = (RAW_DIR, PROCESSED_DIR, SESSION_DIR, FIGURES_DIR, SCREENSHOT_DIR, LOG_DIR, CONFIG_DIR, REPORT_DIR)

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
