from __future__ import annotations

from datetime import datetime
from typing import Literal

from PyQt6 import QtCore, QtGui, QtWidgets

from .paths import BASE_DIR


AppMode = Literal["reajustes", "test", "real", "configurations", "experimento_3m", "temp", "relations", "fourier"]


class ModeSelectDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Seleccionar modo de trabajo")
        self.selected_mode: AppMode = "real"
        self.setMinimumWidth(660)

        layout = QtWidgets.QVBoxLayout(self)

        title = QtWidgets.QLabel("Medicion PPG")
        title.setFont(QtGui.QFont("Arial", 15, QtGui.QFont.Weight.Bold))
        layout.addWidget(title)

        subtitle = QtWidgets.QLabel("Medicion de campo: toma rapida con la interfaz minima y los datos esenciales.")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.btn_real = QtWidgets.QPushButton("Medicion de campo")
        self.btn_real.setMinimumHeight(82)
        self.btn_real.setFont(QtGui.QFont("Arial", 12, QtGui.QFont.Weight.Bold))
        layout.addWidget(self.btn_real)

        modes_label = QtWidgets.QLabel("Otros modos")
        modes_label.setFont(QtGui.QFont("Arial", 10, QtGui.QFont.Weight.Bold))
        layout.addWidget(modes_label)

        grid = QtWidgets.QGridLayout()
        layout.addLayout(grid)

        self.btn_test = QtWidgets.QPushButton("Test de campo")
        self.btn_temp = QtWidgets.QPushButton("Solo temperatura")
        self.btn_reajustes = QtWidgets.QPushButton("Reajustes")
        self.btn_configurations = QtWidgets.QPushButton("Configuraciones")
        self.btn_3m = QtWidgets.QPushButton("Experimento 3M")
        self.btn_relations = QtWidgets.QPushButton("Estadisticas")
        self.btn_fourier = QtWidgets.QPushButton("Analisis experimental de Fourier")

        for button in [self.btn_test, self.btn_temp, self.btn_reajustes, self.btn_configurations, self.btn_3m, self.btn_relations, self.btn_fourier]:
            button.setMinimumHeight(38)

        grid.addWidget(self.btn_test, 0, 0)
        grid.addWidget(self.btn_temp, 0, 1)
        grid.addWidget(self.btn_reajustes, 1, 0)
        grid.addWidget(self.btn_configurations, 1, 1)
        grid.addWidget(self.btn_3m, 2, 0, 1, 2)
        grid.addWidget(self.btn_relations, 3, 0, 1, 2)
        grid.addWidget(self.btn_fourier, 4, 0, 1, 2)

        info = QtWidgets.QLabel(
            "Test de campo: toma con notas, parametros desplegables y graficas diagnosticas.\n"
            "Solo temperatura: registro NTC sin PPG.\n"
            "Reajustes: calibracion larga con controles completos.\n"
            "Configuraciones: tabla editable para crear, pegar y ejecutar pruebas de sensor.\n"
            "Experimento 3M: optimizacion adaptativa del sensor usando BPM manual, pulso PPG, SpO2, ruido, PI y saturacion.\n"
            "Estadisticas: sesiones, resultados, configuraciones y graficas comparativas.\n"
            "Fourier experimental: compara varios raw y razona que configuracion separa mejor el pulso."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.btn_updates = QtWidgets.QPushButton("Ultimas actualizaciones")
        self.btn_updates.setMinimumHeight(30)
        self.btn_updates.setStyleSheet("font-size: 8pt; color: #ddd;")
        layout.addWidget(self.btn_updates, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)

        layout.addStretch(1)
        dev = QtWidgets.QLabel("Desarrollado por Triple M")
        dev.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        dev.setStyleSheet("color: #666; font-size: 8pt;")
        layout.addWidget(dev)

        self.btn_real.clicked.connect(lambda: self.choose("real"))
        self.btn_test.clicked.connect(lambda: self.choose("test"))
        self.btn_temp.clicked.connect(lambda: self.choose("temp"))
        self.btn_reajustes.clicked.connect(lambda: self.choose("reajustes"))
        self.btn_configurations.clicked.connect(lambda: self.choose("configurations"))
        self.btn_3m.clicked.connect(lambda: self.choose("experimento_3m"))
        self.btn_relations.clicked.connect(lambda: self.choose("relations"))
        self.btn_fourier.clicked.connect(lambda: self.choose("fourier"))
        self.btn_updates.clicked.connect(self.show_latest_updates)

    def choose(self, mode: AppMode):
        self.selected_mode = mode
        self.accept()

    def show_latest_updates(self):
        update_dir = BASE_DIR / "actualizaciones"
        files = sorted(update_dir.glob("ACTUALIZACIONES_*.txt"), key=self.update_file_date)
        if not files:
            QtWidgets.QMessageBox.information(self, "Ultimas actualizaciones", "No hay archivo de actualizaciones.")
            return
        path = files[-1]
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Ultimas actualizaciones", f"No se pudo leer:\n{path}\n\n{exc}")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"Ultimas actualizaciones - {path.name}")
        dialog.resize(720, 520)
        layout = QtWidgets.QVBoxLayout(dialog)
        view = QtWidgets.QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        layout.addWidget(view)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def update_file_date(self, path):
        stem = path.stem.replace("ACTUALIZACIONES_", "")
        for fmt in ("%d%m%Y", "%Y%m%d"):
            try:
                return datetime.strptime(stem, fmt)
            except ValueError:
                pass
        return datetime.min
