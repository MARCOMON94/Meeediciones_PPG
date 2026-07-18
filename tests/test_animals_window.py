from __future__ import annotations

import math
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from ppg_suite.windows.animals_window import AnimalMeasurement, AnimalSelectionRecord, AnimalsWindow
from ppg_suite.windows.relations_window import _read_csv


def make_window() -> AnimalsWindow:
    window = AnimalsWindow.__new__(AnimalsWindow)
    window.profiles = {
        "oveja:123": {"animal_type": "oveja", "id": "123", "baseline_settings": {"enabled": False}},
    }
    window.measurements_by_animal = {}
    window.stable_bpm_cache = {}
    window.selected_items = {}
    return window


def test_recommended_alerts_exclude_measurements_without_stable_bpm():
    window = make_window()
    measurements = [
        AnimalMeasurement(
            "oveja:123",
            {
                "animal_type": "oveja",
                "bpm_estable_5s": "70",
                "bpm": "140",
                "temp_rt_c_final_max_5s": "37.0",
                "temp_lt_c_final_max_5s": "36.0",
            },
        ),
        AnimalMeasurement(
            "oveja:123",
            {
                "animal_type": "oveja",
                "bpm_estable_5s": "",
                "bpm": "160",
                "temp_rt_c_final_max_5s": "38.0",
                "temp_lt_c_final_max_5s": "37.0",
            },
        ),
        AnimalMeasurement(
            "oveja:123",
            {
                "animal_type": "oveja",
                "bpm_estable_5s": "90",
                "bpm": "180",
                "temp_rt_c_final_max_5s": "39.0",
                "temp_lt_c_final_max_5s": "38.0",
            },
        ),
    ]

    recommendation = window.recommended_alerts_for_measurements("oveja:123", measurements)

    assert recommendation["bpm_count"] == 2
    assert recommendation["bpm_excluded"] == 1
    assert math.isclose(float(recommendation["bpm_mean"]), 80.0)
    temps = recommendation["temp_by_position"]
    assert math.isclose(float(temps["RT"]["mean"]), 38.0)
    assert math.isclose(float(temps["LT"]["mean"]), 37.0)


def test_selected_animal_mail_paths_use_raws(tmp_path: Path):
    window = make_window()
    raw1 = tmp_path / "raw1.csv"
    raw2 = tmp_path / "raw2.csv"
    raw1.write_text("tiempo_s;ir_raw\n0;1\n", encoding="utf-8")
    raw2.write_text("tiempo_s;ir_raw\n0;2\n", encoding="utf-8")
    window.measurements_by_animal = {
        "oveja:123": [
            AnimalMeasurement("oveja:123", {"raw": raw1.name}, {"raw": raw1}),
            AnimalMeasurement("oveja:123", {"raw": raw2.name}, {"raw": raw2}),
        ]
    }
    window.selected_items = {
        "animal:oveja:123": AnimalSelectionRecord(kind="animal", key="animal:oveja:123", animal_key="oveja:123"),
    }

    paths = window.selected_paths_for_mail()

    assert paths == [raw1, raw2]


def test_animal_table_row_selection_uses_animal_key_from_checkbox_metadata():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert app is not None
    window = make_window()
    window._loading_form = False
    window._updating_tables = False
    window.animals_table = QtWidgets.QTableWidget(1, 2)
    selected_keys: list[str] = []
    window.select_animal = selected_keys.append

    selection_item = QtWidgets.QTableWidgetItem("")
    selection_item.setData(QtCore.Qt.ItemDataRole.UserRole, "animal:oveja:123")
    selection_item.setData(QtCore.Qt.ItemDataRole.UserRole.value + 3, "oveja:123")
    window.animals_table.setItem(0, 0, selection_item)

    label_item = QtWidgets.QTableWidgetItem("Oveja 123")
    label_item.setData(QtCore.Qt.ItemDataRole.UserRole, "oveja:123")
    window.animals_table.setItem(0, 1, label_item)

    AnimalsWindow.select_animal_from_table(window, 0, 0, -1, -1)

    assert selected_keys == ["oveja:123"]


def test_selection_column_header_is_compact_like_statistics():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert app is not None
    window = make_window()
    table = QtWidgets.QTableWidget(0, len(window.file_headers))

    AnimalsWindow.configure_table(window, table, window.file_headers)

    assert table.horizontalHeaderItem(0).text() == ""
    assert table.horizontalHeaderItem(0).toolTip()
    assert table.columnWidth(0) == 34


def test_remove_capture_rows_from_sessions_uses_atomic_rewrite(tmp_path: Path):
    session_path = tmp_path / "session_demo.csv"
    session_path.write_text(
        "session_id;base_name;id\n"
        "cap1;base1;animal1\n"
        "cap2;base2;animal2\n",
        encoding="utf-8",
    )
    measurement = AnimalMeasurement(
        "oveja:123",
        {"session_id": "cap1", "base_name": "base1"},
        {"session": session_path},
    )
    window = make_window()

    errors, failed = window.remove_capture_rows_from_sessions([measurement])

    assert errors == []
    assert failed == set()
    rows = _read_csv(session_path)
    assert [row["session_id"] for row in rows] == ["cap2"]
    assert not list(tmp_path.glob("*.tmp"))
