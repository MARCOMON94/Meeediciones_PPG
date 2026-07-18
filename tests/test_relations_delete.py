from __future__ import annotations

from pathlib import Path

from ppg_suite.windows.relations_window import CaptureRecord, RelationExplorerWindow, _read_csv


def test_remove_capture_rows_from_sessions_uses_atomic_rewrite(tmp_path: Path):
    session_path = tmp_path / "session_demo.csv"
    session_path.write_text(
        "session_id;base_name;id\n"
        "cap1;base1;animal1\n"
        "cap2;base2;animal2\n",
        encoding="utf-8",
    )
    cap = CaptureRecord(
        session_key="session_demo",
        capture_id="cap1",
        base_name="base1",
        row={"session_id": "cap1"},
        files={"session": session_path},
    )
    window = RelationExplorerWindow.__new__(RelationExplorerWindow)

    errors, failed = window._remove_capture_rows_from_sessions([cap])

    assert errors == []
    assert failed == set()
    rows = _read_csv(session_path)
    assert [row["session_id"] for row in rows] == ["cap2"]
    assert not list(tmp_path.glob("*.tmp"))
