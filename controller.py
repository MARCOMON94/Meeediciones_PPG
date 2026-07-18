from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from ppg_suite.menu import ModeSelectDialog
from ppg_suite.windows.real_window import RealWindow
from ppg_suite.windows.test_window import TestWindow
from ppg_suite.windows.reajustes_window import ReajustesWindow
from ppg_suite.windows.scheduled_window import ConfigurationsWindow, Experiment3MWindow
from ppg_suite.windows.temperature_window import TemperatureWindow
from ppg_suite.windows.relations_window import RelationExplorerWindow
from ppg_suite.windows.fourier_window import FourierAnalysisWindow
from ppg_suite.windows.vacuum_window import VacuumExperimentWindow
from ppg_suite.windows.animals_window import AnimalsWindow


class LoadingDialog(QtWidgets.QDialog):
    def __init__(self, message: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cargando")
        self.setModal(False)
        self.setFixedSize(320, 110)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)
        label = QtWidgets.QLabel(message)
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        progress = QtWidgets.QProgressBar()
        progress.setRange(0, 0)
        layout.addWidget(progress)


class AppController(QtCore.QObject):
    def __init__(self, app: QtWidgets.QApplication):
        super().__init__()
        self.app = app
        self.current_window: QtWidgets.QMainWindow | None = None
        self.loading_dialog: LoadingDialog | None = None

    def close_current_window(self):
        if self.current_window is None:
            return
        win = self.current_window
        self.current_window = None
        if hasattr(win, "timer") and win.timer.isActive():
            win.timer.stop()
        win.close()
        win.deleteLater()

    def _process_events(self):
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.processEvents()

    def show_loading(self, message: str = "Cargando..."):
        if self.loading_dialog is not None:
            return
        self.loading_dialog = LoadingDialog(message)
        self.loading_dialog.show()
        self._process_events()

    def hide_loading(self):
        if self.loading_dialog is None:
            return
        dialog = self.loading_dialog
        self.loading_dialog = None
        dialog.close()
        dialog.deleteLater()
        self._process_events()

    def _on_workspace_window_destroyed(self, win):
        if self.current_window is not win:
            return
        self.current_window = None
        self.app.quit()

    def show_menu(self):
        self.close_current_window()
        dialog = ModeSelectDialog()
        result = dialog.exec()
        if result != QtWidgets.QDialog.DialogCode.Accepted:
            self.app.quit()
            return
        if dialog.selected_mode == "real":
            self.show_real()
        elif dialog.selected_mode == "test":
            self.show_test()
        elif dialog.selected_mode == "reajustes":
            self.show_reajustes()
        elif dialog.selected_mode == "configurations":
            self.show_configurations()
        elif dialog.selected_mode == "experimento_3m":
            self.show_experiment_3m()
        elif dialog.selected_mode == "experimento_vacio":
            self.show_vacuum_experiment()
        elif dialog.selected_mode == "temp":
            self.show_temperature()
        elif dialog.selected_mode == "relations":
            self.show_relations()
        elif dialog.selected_mode == "fourier":
            self.show_fourier()
        elif dialog.selected_mode == "animals":
            self.show_animals()

    def _wire_common_signals(self, win):
        win.back_to_menu.connect(self.show_menu)
        if hasattr(win, "open_statistics_requested"):
            win.open_statistics_requested.connect(self.show_relations)

    def _show_workspace_window(self, win: QtWidgets.QMainWindow):
        self._wire_common_signals(win)
        self.current_window = win
        win.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        win.destroyed.connect(lambda _obj=None, watched=win: self._on_workspace_window_destroyed(watched))
        win.showMaximized()

    def _open_workspace(self, factory, message: str = "Cargando..."):
        self.show_loading(message)
        try:
            self.close_current_window()
            self._process_events()
            win = factory()
            self._show_workspace_window(win)
            self._process_events()
        finally:
            self.hide_loading()

    def show_real(self):
        self._open_workspace(RealWindow)

    def show_test(self):
        self._open_workspace(TestWindow)

    def show_reajustes(self):
        self._open_workspace(ReajustesWindow)

    def show_configurations(self):
        self._open_workspace(ConfigurationsWindow)

    def show_experiment_3m(self):
        self._open_workspace(Experiment3MWindow)

    def show_vacuum_experiment(self):
        self._open_workspace(VacuumExperimentWindow)

    def show_temperature(self):
        self._open_workspace(TemperatureWindow)

    def show_relations(self):
        self._open_workspace(RelationExplorerWindow)

    def show_fourier(self):
        self._open_workspace(FourierAnalysisWindow)

    def show_animals(self):
        self._open_workspace(AnimalsWindow)
