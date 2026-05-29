from __future__ import annotations

from typing import Literal

from PyQt6 import QtCore, QtGui, QtWidgets


AppMode = Literal["reajustes", "test", "real", "scheduled64", "scheduled12", "temp"]


class ModeSelectDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Seleccionar modo de trabajo | menú bloques v2")
        self.selected_mode: AppMode = "real"
        self.setMinimumWidth(680)

        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("¿Qué tipo de medición quieres hacer? | bloques v2")
        title.setFont(QtGui.QFont("Arial", 13, QtGui.QFont.Weight.Bold))
        layout.addWidget(title)

        info = QtWidgets.QLabel(
            "Bloques temporales: tomas largas o programadas para comparar configuraciones.\n"
            "Test de campo: medición normal con notas, parámetros editables y gráficas diagnósticas.\n"
            "Real: interfaz ligera para trabajo rápido con animales."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        grid = QtWidgets.QGridLayout()
        layout.addLayout(grid)

        temporal_label = QtWidgets.QLabel("Bloques temporales")
        temporal_label.setFont(QtGui.QFont("Arial", 10, QtGui.QFont.Weight.Bold))
        grid.addWidget(temporal_label, 0, 0)

        field_label = QtWidgets.QLabel("Mediciones de campo")
        field_label.setFont(QtGui.QFont("Arial", 10, QtGui.QFont.Weight.Bold))
        grid.addWidget(field_label, 0, 1)

        self.btn_reajustes = QtWidgets.QPushButton("1 - Reajustes / larga duración")
        self.btn_scheduled64 = QtWidgets.QPushButton("2 - 64 configuraciones | 20 minutos")
        self.btn_scheduled12 = QtWidgets.QPushButton("3 - 12 configuraciones recomendadas")
        self.btn_temp = QtWidgets.QPushButton("4 - Solo temperatura / diagnóstico")
        self.btn_test = QtWidgets.QPushButton("5 - Medición test de campo")
        self.btn_real = QtWidgets.QPushButton("6 - Medición real")

        for button in [
            self.btn_reajustes,
            self.btn_scheduled64,
            self.btn_scheduled12,
            self.btn_temp,
            self.btn_test,
            self.btn_real,
        ]:
            button.setMinimumHeight(52)

        grid.addWidget(self.btn_reajustes, 1, 0)
        grid.addWidget(self.btn_scheduled64, 2, 0)
        grid.addWidget(self.btn_scheduled12, 3, 0)
        grid.addWidget(self.btn_temp, 4, 0)
        grid.addWidget(self.btn_test, 1, 1)
        grid.addWidget(self.btn_real, 2, 1)

        hint = QtWidgets.QLabel(
            "Los bloques programados cambian la configuración del Arduino automáticamente "
            "y guardan la etiqueta de cada configuración en el raw."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch(1)
        dev = QtWidgets.QLabel("Desarrollado por Triple M")
        dev.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        dev.setStyleSheet("color: #666; font-size: 8pt;")
        layout.addWidget(dev)

        self.btn_reajustes.clicked.connect(lambda: self.choose("reajustes"))
        self.btn_scheduled64.clicked.connect(lambda: self.choose("scheduled64"))
        self.btn_scheduled12.clicked.connect(lambda: self.choose("scheduled12"))
        self.btn_temp.clicked.connect(lambda: self.choose("temp"))
        self.btn_test.clicked.connect(lambda: self.choose("test"))
        self.btn_real.clicked.connect(lambda: self.choose("real"))

    def choose(self, mode: AppMode):
        self.selected_mode = mode
        self.accept()
