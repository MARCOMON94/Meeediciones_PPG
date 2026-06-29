from __future__ import annotations

import sys

from PyQt6 import QtWidgets
import pyqtgraph as pg

from controller import AppController


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    pg.setConfigOptions(antialias=False)

    controller = AppController(app)
    # TEMP 2026-06-30: no abrir la primera ventana con opciones.
    # Para revertir: descomentar controller.show_menu() y quitar este return.
    # controller.show_menu()
    return

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
