from __future__ import annotations

from typing import Literal

from PyQt6 import QtCore, QtGui, QtWidgets


AppMode = Literal["reajustes", "test", "real", "scheduled64", "scheduled12", "temp"]


class ModeSelectDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Seleccionar modo de trabajo")
        self.selected_mode: AppMode = "real"
        self.setMinimumWidth(660)

        layout = QtWidgets.QVBoxLayout(self)

        title = QtWidgets.QLabel("Medición PPG")
        title.setFont(QtGui.QFont("Arial", 15, QtGui.QFont.Weight.Bold))
        layout.addWidget(title)

        subtitle = QtWidgets.QLabel("Medición de campo: toma rápida con la interfaz mínima y los datos esenciales.")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.btn_real = QtWidgets.QPushButton("Medición de campo")
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
        self.btn_scheduled64 = QtWidgets.QPushButton("64 configuraciones")
        self.btn_scheduled12 = QtWidgets.QPushButton("12 configuraciones")

        for button in [
            self.btn_test,
            self.btn_temp,
            self.btn_reajustes,
            self.btn_scheduled64,
            self.btn_scheduled12,
        ]:
            button.setMinimumHeight(38)

        grid.addWidget(self.btn_test, 0, 0)
        grid.addWidget(self.btn_temp, 0, 1)
        grid.addWidget(self.btn_reajustes, 1, 0)
        grid.addWidget(self.btn_scheduled64, 1, 1)
        grid.addWidget(self.btn_scheduled12, 2, 0)

        info = QtWidgets.QLabel(
            "Test de campo: toma con notas, parámetros desplegables y gráficas diagnósticas.\n"
            "Solo temperatura: registro NTC sin PPG.\n"
            "Reajustes: calibración larga con controles completos.\n"
            "64 configuraciones: barrido automático de 20 minutos.\n"
            "12 configuraciones: selector manual de configuraciones recomendadas."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        layout.addStretch(1)
        dev = QtWidgets.QLabel("Desarrollado por Triple M")
        dev.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        dev.setStyleSheet("color: #666; font-size: 8pt;")
        layout.addWidget(dev)

        self.btn_real.clicked.connect(lambda: self.choose("real"))
        self.btn_test.clicked.connect(lambda: self.choose("test"))
        self.btn_temp.clicked.connect(lambda: self.choose("temp"))
        self.btn_reajustes.clicked.connect(lambda: self.choose("reajustes"))
        self.btn_scheduled64.clicked.connect(lambda: self.choose("scheduled64"))
        self.btn_scheduled12.clicked.connect(lambda: self.choose("scheduled12"))

    def choose(self, mode: AppMode):
        self.selected_mode = mode
        self.accept()
