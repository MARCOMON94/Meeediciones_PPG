from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(r"C:\Users\julia\OneDrive\Desktop\tesis\mtest")
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
