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


class AppController(QtCore.QObject):
    def __init__(self, app: QtWidgets.QApplication):
        super().__init__()
        self.app = app
        self.current_window: QtWidgets.QMainWindow | None = None

    def close_current_window(self):
        if self.current_window is None:
            return
        win = self.current_window
        self.current_window = None
        if hasattr(win, "timer") and win.timer.isActive():
            win.timer.stop()
        win.close()
        win.deleteLater()

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

    def show_real(self):
        self.close_current_window()
        win = RealWindow()
        self._wire_common_signals(win)
        self.current_window = win
        win.show()

    def show_test(self):
        self.close_current_window()
        win = TestWindow()
        self._wire_common_signals(win)
        self.current_window = win
        win.show()

    def show_reajustes(self):
        self.close_current_window()
        win = ReajustesWindow()
        self._wire_common_signals(win)
        self.current_window = win
        win.show()

    def show_configurations(self):
        self.close_current_window()
        win = ConfigurationsWindow()
        self._wire_common_signals(win)
        self.current_window = win
        win.show()

    def show_experiment_3m(self):
        self.close_current_window()
        win = Experiment3MWindow()
        self._wire_common_signals(win)
        self.current_window = win
        win.show()

    def show_vacuum_experiment(self):
        self.close_current_window()
        win = VacuumExperimentWindow()
        self._wire_common_signals(win)
        self.current_window = win
        win.show()

    def show_temperature(self):
        self.close_current_window()
        win = TemperatureWindow()
        self._wire_common_signals(win)
        self.current_window = win
        win.show()

    def show_relations(self):
        self.close_current_window()
        win = RelationExplorerWindow()
        self._wire_common_signals(win)
        self.current_window = win
        win.show()

    def show_fourier(self):
        self.close_current_window()
        win = FourierAnalysisWindow()
        self._wire_common_signals(win)
        self.current_window = win
        win.show()

    def show_animals(self):
        self.close_current_window()
        win = AnimalsWindow()
        self._wire_common_signals(win)
        self.current_window = win
        win.show()
