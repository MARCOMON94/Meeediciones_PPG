from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_packaging_excludes_long_unused_data_paths():
    spec = (ROOT / "packaging" / "MedicionPPG.spec").read_text(encoding="utf-8")

    assert 'target.startswith("pyqtgraph/colors/maps/")' in spec
    assert 'target.startswith("pyqtgraph/icons/peegee/")' in spec
    assert '".dist-info/licenses/" in target' in spec


def test_portable_zip_uses_short_filename():
    script = (ROOT / "tools" / "build_windows_release.ps1").read_text(encoding="utf-8")

    assert '"MEE_$Version.zip"' in script
    assert "MEEEDICIONES_Portable_$Version.zip" not in script
