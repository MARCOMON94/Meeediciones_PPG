from __future__ import annotations

import sys

from PyQt6 import QtGui, QtWidgets
import pyqtgraph as pg

from controller import AppController
from ppg_suite.app_info import APP_DISPLAY_NAME, APP_PUBLISHER, APP_VERSION
from ppg_suite.paths import APP_ICON_PATH, log
from ppg_suite.trash import purge_expired_trash


def main():
    try:
        purge_expired_trash()
    except Exception as exc:
        log.warning("No se pudo limpiar la papelera interna: %s", exc)

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_PUBLISHER)
    icon = QtGui.QIcon(str(APP_ICON_PATH))
    if not icon.isNull():
        app.setWindowIcon(icon)
    app.setQuitOnLastWindowClosed(False)
    pg.setConfigOptions(antialias=False)

    controller = AppController(app)
    controller.show_menu()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
