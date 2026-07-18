from __future__ import annotations

import sys

from PyQt6 import QtWidgets
import pyqtgraph as pg

from controller import AppController
from ppg_suite.paths import log
from ppg_suite.trash import purge_expired_trash


def main():
    try:
        purge_expired_trash()
    except Exception as exc:
        log.warning("No se pudo limpiar la papelera interna: %s", exc)

    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    pg.setConfigOptions(antialias=False)

    controller = AppController(app)
    controller.show_menu()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
