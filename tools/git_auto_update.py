from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_TIMEOUT_SECONDS = 15
MIN_TIMEOUT_SECONDS = 3
MAX_TIMEOUT_SECONDS = 120

GIT_BASE_ARGS = [
    "git",
    "-c",
    "gc.auto=0",
    "-c",
    "maintenance.auto=false",
    "-c",
    "http.lowSpeedLimit=1",
    "-c",
    "http.lowSpeedTime=5",
]


def update_timeout_seconds() -> int:
    raw = os.environ.get("AUTO_UPDATE_TIMEOUT_SEC", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        print(
            f"[WARN] AUTO_UPDATE_TIMEOUT_SEC no es valido ({raw!r}). "
            f"Se usa {DEFAULT_TIMEOUT_SECONDS} s."
        )
        return DEFAULT_TIMEOUT_SECONDS
    return max(MIN_TIMEOUT_SECONDS, min(value, MAX_TIMEOUT_SECONDS))


def git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"
    env["GCM_INTERACTIVE"] = "never"
    return env


def run_git(
    args: list[str],
    project_dir: Path,
    timeout_seconds: int,
    *,
    quiet: bool = False,
) -> subprocess.CompletedProcess[str] | None:
    stdout = subprocess.DEVNULL if quiet else None
    stderr = subprocess.DEVNULL if quiet else None
    try:
        return subprocess.run(
            [*GIT_BASE_ARGS, *args],
            cwd=project_dir,
            env=git_env(),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            timeout=timeout_seconds,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[WARN] Git no respondio en {timeout_seconds} s. "
            "Se omite la actualizacion automatica."
        )
    except OSError as exc:
        print(f"[WARN] No se pudo ejecutar Git: {exc}")
    return None


def is_dirty(result: subprocess.CompletedProcess[str] | None, label: str) -> bool | None:
    if result is None:
        print(f"[WARN] No se pudo comprobar {label}. Se omite actualizacion automatica.")
        return None
    if result.returncode == 0:
        return False
    if result.returncode == 1:
        return True
    print(f"[WARN] Git devolvio error al comprobar {label}. Se omite actualizacion automatica.")
    return None


def main() -> int:
    project_dir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    if not (project_dir / ".git").exists():
        print("[WARN] Esta carpeta no parece ser un repositorio Git. Se omite git pull.")
        return 0

    if shutil.which("git") is None:
        print("[WARN] Git no esta disponible en PATH. Se omite actualizacion automatica.")
        return 0

    timeout_seconds = update_timeout_seconds()

    print("[INFO] Comprobando si hay cambios locales antes de actualizar...")
    worktree = is_dirty(
        run_git(["diff", "--quiet"], project_dir, timeout_seconds, quiet=True),
        "cambios locales",
    )
    if worktree is None:
        return 0

    index = is_dirty(
        run_git(["diff", "--cached", "--quiet"], project_dir, timeout_seconds, quiet=True),
        "cambios preparados",
    )
    if index is None:
        return 0

    if worktree:
        print()
        print("[WARN] Hay cambios locales en el repositorio. Se omite actualizacion automatica.")
        print("[WARN] El programa arrancara con la version local para no tocar archivos de este ordenador.")
        return 0

    if index:
        print()
        print("[WARN] Hay cambios preparados en Git. Se omite actualizacion automatica.")
        print("[WARN] El programa arrancara con la version local.")
        return 0

    print(
        "[INFO] Actualizando repositorio sin preguntas interactivas "
        f"(maximo {timeout_seconds} s)..."
    )
    result = run_git(
        ["pull", "--ff-only", "--no-edit"],
        project_dir,
        timeout_seconds,
        quiet=False,
    )
    if result is None or result.returncode != 0:
        print()
        print("[WARN] No se pudo actualizar automaticamente con Git.")
        print("[WARN] El programa continuara con la version local.")
        print("[WARN] Si no hay internet, esto es normal y no debe bloquear el arranque.")
        return 0

    print("[OK] Repositorio actualizado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
