from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

from ppg_suite.io_utils import atomic_csv_writer, atomic_write_json
from ppg_suite.trash import TrashBatch, purge_expired_trash


def test_atomic_write_json_replaces_target(tmp_path: Path):
    target = tmp_path / "data.json"
    atomic_write_json(target, {"value": 1})
    atomic_write_json(target, {"value": 2})

    assert json.loads(target.read_text(encoding="utf-8")) == {"value": 2}
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_csv_writer_keeps_semicolon_dialect(tmp_path: Path):
    target = tmp_path / "rows.csv"

    with atomic_csv_writer(target, delimiter=";") as writer:
        writer.writerow(["a", "b"])
        writer.writerow(["1", "2"])

    with open(target, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f, delimiter=";"))

    assert rows == [["a", "b"], ["1", "2"]]


def test_trash_batch_moves_file_and_writes_manifest(tmp_path: Path):
    source = tmp_path / "resultados" / "raw" / "raw_demo.csv"
    source.parent.mkdir(parents=True)
    source.write_text("id;value\n1;ok\n", encoding="utf-8")
    batch = TrashBatch(source="test", root=tmp_path / ".trash", batch_name="batch")

    ok, error = batch.move(source)
    manifest_ok, manifest_error = batch.write_manifest()

    assert ok, error
    assert manifest_ok, manifest_error
    assert not source.exists()
    manifest = json.loads((tmp_path / ".trash" / "batch" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"] == "test"
    assert manifest["items"][0]["original_path"].endswith("raw_demo.csv")
    assert Path(manifest["items"][0]["trash_path"]).exists()


def test_purge_expired_trash_removes_batches_older_than_retention(tmp_path: Path):
    root = tmp_path / ".trash"
    old_batch = root / "old"
    recent_batch = root / "recent"
    old_batch.mkdir(parents=True)
    recent_batch.mkdir(parents=True)
    old_batch.joinpath("manifest.json").write_text(
        json.dumps({"created": "2026-01-01T10:00:00", "items": []}),
        encoding="utf-8",
    )
    recent_batch.joinpath("manifest.json").write_text(
        json.dumps({"created": "2026-01-20T10:00:00", "items": []}),
        encoding="utf-8",
    )
    old_batch.joinpath("file.txt").write_text("old", encoding="utf-8")
    recent_batch.joinpath("file.txt").write_text("recent", encoding="utf-8")

    removed = purge_expired_trash(root, retention_days=30, now=datetime(2026, 2, 5, 10, 0, 0))

    assert removed == [old_batch]
    assert not old_batch.exists()
    assert recent_batch.exists()


def test_purge_expired_trash_uses_directory_mtime_without_manifest(tmp_path: Path):
    root = tmp_path / ".trash"
    batch = root / "no_manifest"
    batch.mkdir(parents=True)
    batch.joinpath("file.txt").write_text("old", encoding="utf-8")
    old_ts = (datetime.now() - timedelta(days=45)).timestamp()
    import os

    os.utime(batch, (old_ts, old_ts))

    removed = purge_expired_trash(root, retention_days=30)

    assert removed == [batch]
    assert not batch.exists()
