from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from .app_info import APP_DATA_NAME, APP_DATA_VENDOR

IS_FROZEN = bool(getattr(sys, "frozen", False))
SOURCE_ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else SOURCE_ROOT
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT)).resolve()
PROJECT_ROOT = EXECUTABLE_DIR if IS_FROZEN else SOURCE_ROOT
UPDATES_DIR = RESOURCE_ROOT / "actualizaciones"
RUMIANDO_ASSET_DIR = RESOURCE_ROOT / "ppg_suite" / "assets" / "rumiando"
APP_ICON_PATH = RUMIANDO_ASSET_DIR / "rumiando-sheep-tech-app-colors.png"
ARDUINO_FIRMWARE_DIR = RESOURCE_ROOT / "arduino" / "ppg_max3010x_firmware"
ARDUINO_FIRMWARE_SKETCH = ARDUINO_FIRMWARE_DIR / "ppg_max3010x_firmware.ino"


def _default_user_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_DATA_VENDOR / APP_DATA_NAME
    return Path.home() / "AppData" / "Local" / APP_DATA_VENDOR / APP_DATA_NAME


def _env_files() -> tuple[Path, ...]:
    files = [PROJECT_ROOT / ".env"]
    if IS_FROZEN:
        files.append(_default_user_data_dir() / ".env")
    return tuple(dict.fromkeys(files))


def _read_data_dir_from_env() -> Path | None:
    env_override = os.environ.get("PPG_SUITE_DATA_DIR")
    if env_override:
        return Path(env_override).expanduser()

    for env_file in _env_files():
        if not env_file.exists():
            continue
        found = _read_project_dir_from_env_file(env_file)
        if found is not None:
            return found
    return None


def _read_project_dir_from_env_file(env_file: Path) -> Path | None:
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
        return None

    return None


BASE_DIR = _read_data_dir_from_env() or (_default_user_data_dir() if IS_FROZEN else PROJECT_ROOT)
RESULTS_DIR = BASE_DIR / "resultados"
RAW_DIR = RESULTS_DIR / "raw"
PROCESSED_DIR = RESULTS_DIR / "processed"
SESSION_DIR = RESULTS_DIR / "sessions"
FIGURES_DIR = RESULTS_DIR / "figures"
SCREENSHOT_DIR = RESULTS_DIR / "screenshots"
LOG_DIR = RESULTS_DIR / "logs"
CONFIG_DIR = RESULTS_DIR / "configs"
REPORT_DIR = RESULTS_DIR / "reports"
AGREEMENT_REPORT_DIR = REPORT_DIR / "bland_altman"
DOCUMENTS_DIR = RESULTS_DIR / "documentos_generados"
TRASH_DIR = RESULTS_DIR / ".trash"
ANIMALS_DIR = RESULTS_DIR / "animals"
ANIMAL_PHOTO_DIR = ANIMALS_DIR / "photos"
VACUUM_DIR = RESULTS_DIR / "experimento_con_vacio"
VACUUM_RAW_DIR = VACUUM_DIR / "raw_ppg"
VACUUM_AUDIO_DIR = VACUUM_DIR / "audio"
VACUUM_PROCESSED_DIR = VACUUM_DIR / "processed"
VACUUM_SESSION_DIR = VACUUM_DIR / "sessions"
VACUUM_FIGURES_DIR = VACUUM_DIR / "figures"
VACUUM_SCREENSHOT_DIR = VACUUM_DIR / "screenshots"
VACUUM_CONFIG_DIR = VACUUM_DIR / "configs"
VACUUM_REPORT_DIR = VACUUM_DIR / "reports"

RESULT_FOLDERS = (
    RESULTS_DIR,
    RAW_DIR,
    PROCESSED_DIR,
    SESSION_DIR,
    FIGURES_DIR,
    SCREENSHOT_DIR,
    LOG_DIR,
    CONFIG_DIR,
    REPORT_DIR,
    AGREEMENT_REPORT_DIR,
    DOCUMENTS_DIR,
    TRASH_DIR,
    ANIMALS_DIR,
    ANIMAL_PHOTO_DIR,
    VACUUM_DIR,
    VACUUM_RAW_DIR,
    VACUUM_AUDIO_DIR,
    VACUUM_PROCESSED_DIR,
    VACUUM_SESSION_DIR,
    VACUUM_FIGURES_DIR,
    VACUUM_SCREENSHOT_DIR,
    VACUUM_CONFIG_DIR,
    VACUUM_REPORT_DIR,
)

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
