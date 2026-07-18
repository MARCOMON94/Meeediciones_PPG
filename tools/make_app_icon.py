from __future__ import annotations

import struct
from pathlib import Path

from PyQt6 import QtCore, QtGui


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "ppg_suite" / "assets" / "rumiando" / "rumiando-sheep-tech-app-colors.png"
TARGET = ROOT / "packaging" / "assets" / "app.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(image: QtGui.QImage) -> bytes:
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Qt no pudo convertir el icono a PNG")
    return bytes(buffer.data())


def build_icon(source: Path = SOURCE, target: Path = TARGET) -> None:
    image = QtGui.QImage(str(source))
    if image.isNull():
        raise FileNotFoundError(f"No se pudo leer la imagen de icono: {source}")

    frames: list[tuple[int, bytes]] = []
    for size in SIZES:
        scaled = image.scaled(
            size,
            size,
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        canvas = QtGui.QImage(size, size, QtGui.QImage.Format.Format_ARGB32)
        canvas.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(canvas)
        painter.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
        painter.end()
        frames.append((size, png_bytes(canvas)))

    header_size = 6 + 16 * len(frames)
    offset = header_size
    entries = []
    payload = bytearray()
    for size, data in frames:
        width = 0 if size == 256 else size
        entries.append(struct.pack("<BBBBHHII", width, width, 0, 0, 1, 32, len(data), offset))
        payload.extend(data)
        offset += len(data)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(struct.pack("<HHH", 0, 1, len(frames)) + b"".join(entries) + payload)


def main() -> int:
    build_icon()
    print(f"Icono generado: {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
