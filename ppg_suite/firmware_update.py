from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from serial.tools import list_ports


NANO_33_IOT_FQBN = "arduino:samd:nano_33_iot"


@dataclass(frozen=True)
class FirmwarePort:
    device: str
    label: str
    score: int


@dataclass
class FirmwareUploadResult:
    ok: bool
    command: str
    stdout: str = ""
    stderr: str = ""

    @property
    def detail(self) -> str:
        parts = [f"Comando: {self.command}"]
        if self.stdout.strip():
            parts.append(f"Salida:\n{self.stdout.strip()}")
        if self.stderr.strip():
            parts.append(f"Errores:\n{self.stderr.strip()}")
        return "\n\n".join(parts)


def arduino_cli_path() -> str | None:
    return shutil.which("arduino-cli")


def available_firmware_ports() -> list[FirmwarePort]:
    ports: list[FirmwarePort] = []
    for port in list_ports.comports():
        device = str(getattr(port, "device", "") or "")
        if not device:
            continue
        description = str(getattr(port, "description", "") or "")
        hwid = str(getattr(port, "hwid", "") or "")
        text = f"{device} {description} {hwid}".upper()
        score = 0
        if any(token in text for token in ("ARDUINO", "GENUINO", "NANO 33", "NANO33", "MKR")):
            score += 100
        if any(token in text for token in ("VID:2341", "VID_2341", "VID:2A03", "VID_2A03")):
            score += 80
        if any(token in text for token in ("CH340", "CH341", "CP210", "FTDI", "USB SERIAL", "USB-SERIAL")):
            score += 40
        if "BLUETOOTH" in text:
            score -= 100
        label = f"{device} | {description}".strip()
        ports.append(FirmwarePort(device=device, label=label, score=score))
    return sorted(ports, key=lambda item: (item.score, item.device), reverse=True)


def run_arduino_cli(args: list[str], timeout_s: int = 240) -> FirmwareUploadResult:
    cli = arduino_cli_path()
    if not cli:
        return FirmwareUploadResult(False, "arduino-cli", stderr="No se encontró arduino-cli en este ordenador.")
    command = [cli, *args]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        return FirmwareUploadResult(
            False,
            " ".join(command),
            stdout=str(exc.stdout or ""),
            stderr=f"Tiempo agotado tras {timeout_s} segundos.",
        )
    return FirmwareUploadResult(
        ok=completed.returncode == 0,
        command=" ".join(command),
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def compile_firmware(sketch_dir: Path, fqbn: str = NANO_33_IOT_FQBN) -> FirmwareUploadResult:
    return run_arduino_cli(["compile", "--fqbn", fqbn, str(sketch_dir)])


def upload_firmware(sketch_dir: Path, port: str, fqbn: str = NANO_33_IOT_FQBN) -> FirmwareUploadResult:
    return run_arduino_cli(["upload", "-p", port, "--fqbn", fqbn, str(sketch_dir)])
