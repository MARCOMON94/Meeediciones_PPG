from __future__ import annotations

import pytest

from ppg_suite.data_import import import_resultados_folder, validate_resultados_folder


def test_validate_resultados_folder_requires_exact_name(tmp_path):
    wrong = tmp_path / "backup"
    wrong.mkdir()

    with pytest.raises(ValueError, match="resultados"):
        validate_resultados_folder(wrong)


def test_import_resultados_folder_copies_each_file_to_matching_destination(tmp_path):
    source = tmp_path / "resultados"
    destination = tmp_path / "destino" / "resultados"
    (source / "raw").mkdir(parents=True)
    (source / "sessions").mkdir()
    (source / "raw" / "raw_demo.csv").write_text("a;b\n1;2\n", encoding="utf-8")
    (source / "sessions" / "session_demo.csv").write_text("x;y\n3;4\n", encoding="utf-8")
    (destination / "raw").mkdir(parents=True)
    (destination / "raw" / "raw_existente.csv").write_text("old", encoding="utf-8")

    result = import_resultados_folder(source, destination)

    assert result.errors == []
    assert result.copied_files == 2
    assert (destination / "raw" / "raw_demo.csv").read_text(encoding="utf-8") == "a;b\n1;2\n"
    assert (destination / "sessions" / "session_demo.csv").exists()
    assert (destination / "raw" / "raw_existente.csv").read_text(encoding="utf-8") == "old"


def test_import_resultados_folder_skips_existing_files(tmp_path):
    source = tmp_path / "resultados"
    destination = tmp_path / "destino" / "resultados"
    (source / "raw").mkdir(parents=True)
    (destination / "raw").mkdir(parents=True)
    (source / "raw" / "raw_demo.csv").write_text("new", encoding="utf-8")
    (destination / "raw" / "raw_demo.csv").write_text("old", encoding="utf-8")

    result = import_resultados_folder(source, destination)

    assert result.copied_files == 0
    assert result.skipped_existing == 1
    assert (destination / "raw" / "raw_demo.csv").read_text(encoding="utf-8") == "old"

