from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from ppg_suite.menu import ModeSelectDialog


def test_primary_menu_button_text_fits_minimum_dialog_width():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = ModeSelectDialog()
    dialog.resize(dialog.minimumSize())
    dialog.show()
    app.processEvents()

    button = dialog.btn_real
    text_width = button.fontMetrics().horizontalAdvance(button.text())

    assert "padding: 14px 10px;" in dialog.styleSheet()
    assert "text-align: center;" in dialog.styleSheet()
    assert text_width < button.width()
    dialog.close()


def test_other_modes_toggle_rebalances_dialog_size():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = ModeSelectDialog()
    dialog.resize(dialog.minimumSize())
    dialog.show()
    app.processEvents()
    before_height = dialog.height()

    dialog.btn_other_toggle.setChecked(True)
    app.processEvents()
    app.processEvents()

    assert dialog.other_modes_widget.isVisible()
    assert dialog.height() >= before_height
    dialog.close()
