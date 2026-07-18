# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules


SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR if (SPEC_DIR / "main.py").exists() else SPEC_DIR.parent
sys.path.insert(0, str(ROOT))

from ppg_suite.app_info import APP_EXECUTABLE_NAME


datas = [
    (str(ROOT / "ppg_suite" / "assets"), "ppg_suite/assets"),
    (str(ROOT / "actualizaciones"), "actualizaciones"),
    (str(ROOT / "arduino" / "ppg_max3010x_firmware"), "arduino/ppg_max3010x_firmware"),
]

hiddenimports = []
hiddenimports += collect_submodules("bleak.backends.winrt")

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_EXECUTABLE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "packaging" / "assets" / "app.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=APP_EXECUTABLE_NAME,
)
