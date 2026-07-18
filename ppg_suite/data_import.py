from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DataImportResult:
    source: Path
    destination: Path
    copied_files: int = 0
    skipped_existing: int = 0
    created_dirs: int = 0
    bytes_copied: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_resultados_folder(path: Path) -> Path:
    source = path.expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise ValueError("Selecciona una carpeta existente llamada resultados.")
    if source.name.lower() != "resultados":
        raise ValueError("La carpeta seleccionada debe llamarse exactamente resultados.")
    return source


def import_resultados_folder(source: Path, destination: Path) -> DataImportResult:
    source_dir = validate_resultados_folder(source)
    destination_dir = destination.expanduser().resolve()
    if source_dir == destination_dir:
        raise ValueError("La carpeta seleccionada ya es la carpeta de datos actual.")
    if destination_dir.is_relative_to(source_dir):
        raise ValueError("La carpeta de destino no puede estar dentro de la carpeta importada.")

    result = DataImportResult(source=source_dir, destination=destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)

    for path in source_dir.rglob("*"):
        rel_path = path.relative_to(source_dir)
        target = destination_dir / rel_path
        try:
            if path.is_dir():
                if not target.exists():
                    target.mkdir(parents=True, exist_ok=True)
                    result.created_dirs += 1
                continue
            if not path.is_file():
                continue
            if target.exists():
                result.skipped_existing += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            size = path.stat().st_size
            shutil.copy2(path, target)
            result.copied_files += 1
            result.bytes_copied += size
        except OSError as exc:
            result.errors.append(f"{rel_path}: {exc}")

    return result

