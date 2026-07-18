from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from controller import AppController


class FakeApp:
    def __init__(self):
        self.quit_called = False

    def quit(self):
        self.quit_called = True


class FakeWindow(QtWidgets.QMainWindow):
    back_to_menu = QtCore.pyqtSignal()


def test_workspace_window_close_quits_application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    fake_app = FakeApp()
    controller = AppController(fake_app)
    win = FakeWindow()

    controller._show_workspace_window(win)
    app.processEvents()
    win.close()
    app.processEvents()

    assert fake_app.quit_called
    assert controller.current_window is None


def test_controller_window_switch_does_not_quit_application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    fake_app = FakeApp()
    controller = AppController(fake_app)
    win = FakeWindow()

    controller._show_workspace_window(win)
    app.processEvents()
    controller.close_current_window()
    app.processEvents()

    assert not fake_app.quit_called
    assert controller.current_window is None


def test_late_destroy_from_previous_window_does_not_quit_new_window():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    fake_app = FakeApp()
    controller = AppController(fake_app)
    old_win = FakeWindow()
    new_win = FakeWindow()

    controller._show_workspace_window(old_win)
    app.processEvents()
    controller.close_current_window()
    controller._show_workspace_window(new_win)
    controller._on_workspace_window_destroyed(old_win)
    app.processEvents()

    assert not fake_app.quit_called
    assert controller.current_window is new_win
