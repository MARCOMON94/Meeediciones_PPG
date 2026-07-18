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


def test_menu_has_bottom_import_and_firmware_actions():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = ModeSelectDialog()
    dialog.resize(dialog.minimumSize())
    dialog.show()
    app.processEvents()

    assert dialog.btn_import_data.text() == "IMPORTAR DATOS"
    assert dialog.btn_update_firmware.text() == "ACTUALIZAR FIRMWARE"
    assert dialog.btn_import_data.objectName() == "bottomToolButton"
    assert dialog.btn_update_firmware.objectName() == "bottomToolButton"
    assert dialog.btn_import_data.y() > dialog.btn_fourier.y()

    dialog.close()


def test_menu_header_keeps_only_own_logo_and_uppercase_title():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = ModeSelectDialog()
    dialog.resize(dialog.minimumSize())
    dialog.show()
    app.processEvents()

    title = dialog.findChild(QtWidgets.QLabel, "title")
    brand = dialog.findChild(QtWidgets.QLabel, "brandFooter")
    assert title is not None
    assert title.text() == "MEEEDICIONES"
    assert brand is None
    assert dialog.minimumHeight() == 590
    assert dialog.hero_image.pixmap() is not None
    assert not dialog.hero_image.pixmap().isNull()
    assert not hasattr(dialog, "header_fv_logo")
    assert not hasattr(dialog, "ulpgc_logo")
    assert not hasattr(dialog, "fv_logo")

    dialog.close()
