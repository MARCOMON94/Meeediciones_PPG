from __future__ import annotations

import csv
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4


@contextmanager
def atomic_text_file(path: Path, *, encoding: str = "utf-8", newline: str | None = None) -> Iterator[object]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    handle = None
    try:
        handle = open(tmp, "w", encoding=encoding, newline=newline)
        yield handle
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        tmp.replace(target)
    except Exception:
        if handle is not None:
            handle.close()
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    with atomic_text_file(path, encoding=encoding) as handle:
        handle.write(text)


def atomic_write_json(path: Path, data: object, *, indent: int = 2, ensure_ascii: bool = False) -> None:
    with atomic_text_file(path, encoding="utf-8") as handle:
        json.dump(data, handle, indent=indent, ensure_ascii=ensure_ascii)


@contextmanager
def atomic_csv_writer(path: Path, *, delimiter: str = ";", encoding: str = "utf-8") -> Iterator[csv.writer]:
    with atomic_text_file(path, encoding=encoding, newline="") as handle:
        yield csv.writer(handle, delimiter=delimiter)


@contextmanager
def atomic_csv_dict_writer(
    path: Path,
    fieldnames: list[str],
    *,
    delimiter: str = ";",
    encoding: str = "utf-8",
    extrasaction: str = "ignore",
) -> Iterator[csv.DictWriter]:
    with atomic_text_file(path, encoding=encoding, newline="") as handle:
        yield csv.DictWriter(handle, fieldnames=fieldnames, delimiter=delimiter, extrasaction=extrasaction)
