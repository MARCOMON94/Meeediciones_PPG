from __future__ import annotations

import shutil
import stat
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .io_utils import atomic_write_json
from .paths import RESULTS_DIR, TRASH_DIR, log


TRASH_RETENTION_DAYS = 30


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _relative_trash_path(path: Path, base_dir: Path = RESULTS_DIR) -> Path:
    resolved = path.resolve()
    try:
        return resolved.relative_to(base_dir.resolve())
    except ValueError:
        safe_parts = [part.replace(":", "") for part in resolved.parts if part not in (resolved.anchor, "\\")]
        return Path("external").joinpath(*safe_parts[-4:])


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _batch_created_at(batch_dir: Path) -> datetime:
    manifest = batch_dir / "manifest.json"
    if manifest.exists():
        try:
            import json

            data = json.loads(manifest.read_text(encoding="utf-8"))
            created = _parse_datetime(data.get("created"))
            if created is not None:
                return created
            moved_dates = [
                parsed
                for item in data.get("items", [])
                if isinstance(item, dict)
                for parsed in [_parse_datetime(item.get("moved_at"))]
                if parsed is not None
            ]
            if moved_dates:
                return min(moved_dates)
        except (OSError, ValueError, TypeError):
            pass
    return datetime.fromtimestamp(batch_dir.stat().st_mtime)


def purge_expired_trash(root: Path = TRASH_DIR, *, retention_days: int = TRASH_RETENTION_DAYS, now: datetime | None = None) -> list[Path]:
    if retention_days <= 0 or not root.exists():
        return []
    cutoff = (now or datetime.now()) - timedelta(days=retention_days)
    removed: list[Path] = []
    for batch_dir in root.iterdir():
        if not batch_dir.is_dir():
            continue
        try:
            if _batch_created_at(batch_dir) > cutoff:
                continue
            shutil.rmtree(batch_dir)
            removed.append(batch_dir)
        except OSError as exc:
            log.warning("No se pudo limpiar papelera antigua %s: %s", batch_dir, exc)
    return removed


@dataclass
class TrashBatch:
    source: str = "mtestv2"
    root: Path = TRASH_DIR
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    batch_name: str = field(default_factory=_timestamp)
    items: list[dict[str, object]] = field(default_factory=list)

    @property
    def batch_dir(self) -> Path:
        return self.root / self.batch_name

    def move(self, path: Path) -> tuple[bool, str]:
        original = Path(path)
        try:
            if not original.exists():
                return False, "el archivo ya no existe"
            try:
                original.chmod(original.stat().st_mode | stat.S_IWRITE)
            except OSError:
                pass
            destination = _unique_path(self.batch_dir / _relative_trash_path(original))
            destination.parent.mkdir(parents=True, exist_ok=True)
            size = original.stat().st_size
            shutil.move(str(original), str(destination))
            self.items.append({
                "original_path": str(original),
                "trash_path": str(destination),
                "size_bytes": size,
                "moved_at": datetime.now().isoformat(timespec="seconds"),
            })
            return True, ""
        except PermissionError as exc:
            return False, f"permiso denegado o archivo en uso ({exc})"
        except OSError as exc:
            return False, str(exc)

    def write_manifest(self) -> tuple[bool, str]:
        if not self.items:
            return True, ""
        try:
            atomic_write_json(
                self.batch_dir / "manifest.json",
                {
                    "source": self.source,
                    "created": self.created,
                    "batch": self.batch_name,
                    "items": self.items,
                },
            )
            return True, ""
        except OSError as exc:
            return False, str(exc)
