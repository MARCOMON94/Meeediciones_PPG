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
    controller.show_menu()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
