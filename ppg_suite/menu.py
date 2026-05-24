from __future__ import annotations

from typing import Literal

from PyQt6 import QtCore, QtGui, QtWidgets


AppMode = Literal["reajustes", "test", "real"]

class ModeSelectDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Seleccionar modo de trabajo")
        self.selected_mode: AppMode = "real"
        self.setMinimumWidth(560)

        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("¿Qué tipo de medición quieres hacer?")
        title.setFont(QtGui.QFont("Arial", 13, QtGui.QFont.Weight.Bold))
        layout.addWidget(title)

        info = QtWidgets.QLabel(
            "Reajustes: máxima información, parámetros modificables y calibración larga.\n"
            "Test: medición normal con parámetros editables y gráficas diagnósticas.\n"
            "Real: interfaz ligera para ovejas, rápida y con solo lo importante."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        grid = QtWidgets.QGridLayout()
        layout.addLayout(grid)

        self.btn_reajustes = QtWidgets.QPushButton("1 - Medición de reajustes")
        self.btn_test = QtWidgets.QPushButton("2 - Medición test")
        self.btn_real = QtWidgets.QPushButton("3 - Medición real")

        self.btn_reajustes.setMinimumHeight(58)
        self.btn_test.setMinimumHeight(58)
        self.btn_real.setMinimumHeight(58)

        grid.addWidget(self.btn_reajustes, 0, 0)
        grid.addWidget(self.btn_test, 1, 0)
        grid.addWidget(self.btn_real, 2, 0)

        hint = QtWidgets.QLabel(
            "Para trabajo de campo con animales, usa Medición real. "
            "Para tocar LEDs, sample rate y revisar FFT como si fuera una sala de máquinas, usa Reajustes."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch(1)
        dev = QtWidgets.QLabel("Desarrollado por Triple M")
        dev.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        dev.setStyleSheet("color: #666; font-size: 8pt;")
        layout.addWidget(dev)

        self.btn_reajustes.clicked.connect(lambda: self.choose("reajustes"))
        self.btn_test.clicked.connect(lambda: self.choose("test"))
        self.btn_real.clicked.connect(lambda: self.choose("real"))

    def choose(self, mode: AppMode):
        self.selected_mode = mode
        self.accept()

