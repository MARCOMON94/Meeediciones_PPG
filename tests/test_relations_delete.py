from __future__ import annotations

from pathlib import Path

from ppg_suite.windows.relations_window import CaptureRecord, RelationExplorerWindow, SelectionRecord, SessionGroup, _read_csv


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


def test_selected_captures_for_compare_uses_checked_items(tmp_path: Path):
    raw1 = tmp_path / "raw1.csv"
    raw2 = tmp_path / "raw2.csv"
    raw1.write_text("tiempo_s;ir_raw\n0;1\n", encoding="utf-8")
    raw2.write_text("tiempo_s;ir_raw\n0;2\n", encoding="utf-8")
    cap1 = CaptureRecord("session_a", "cap1", "base1", files={"raw": raw1})
    cap2 = CaptureRecord("session_a", "cap2", "base2", files={"raw": raw2})
    window = RelationExplorerWindow.__new__(RelationExplorerWindow)
    window.sessions = [SessionGroup("session_a", None, [cap1, cap2])]
    window.current_capture = None
    window.selected_items = {
        "session:session_a": SelectionRecord(kind="session", key="session:session_a", session_key="session_a"),
        "capture:session_a|cap1|base1": SelectionRecord(
            kind="capture",
            key="capture:session_a|cap1|base1",
            capture_key="session_a|cap1|base1",
        ),
    }

    captures = window.selected_captures_for_compare()

    assert {cap.capture_id for cap in captures} == {"cap1", "cap2"}
