from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from PyQt6 import QtCore, QtGui, QtWidgets

from .paths import BASE_DIR


AppMode = Literal["reajustes", "test", "real", "configurations", "experimento_3m", "experimento_vacio", "temp", "relations", "fourier", "animals"]
RUMIANDO_ASSET_DIR = Path(__file__).resolve().parent / "assets" / "rumiando"


class ModeSelectDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Seleccionar modo de trabajo")
        self.selected_mode: AppMode = "real"
        self.setMinimumSize(760, 680)
        self.apply_rumiando_style()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)

        hero = QtWidgets.QHBoxLayout()
        hero.setSpacing(14)
        self.hero_image = QtWidgets.QLabel()
        self.hero_image.setFixedSize(118, 108)
        self.hero_image.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        hero_pix = self.asset_pixmap("rumiando-sheep-tech-app-colors.png", QtCore.QSize(118, 108))
        if hero_pix.isNull():
            self.hero_image.hide()
        else:
            self.hero_image.setPixmap(hero_pix)
            hero.addWidget(self.hero_image)
        hero_text = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("Medicion PPG")
        title.setObjectName("title")
        title.setFont(QtGui.QFont("Arial", 17, QtGui.QFont.Weight.Bold))
        hero_text.addWidget(title)

        subtitle = QtWidgets.QLabel("Medicion de campo: toma rapida con la interfaz minima y los datos esenciales.")
        subtitle.setWordWrap(True)
        hero_text.addWidget(subtitle)
        hero_text.addStretch(1)
        hero.addLayout(hero_text, stretch=1)
        layout.addLayout(hero)

        self.btn_real = QtWidgets.QPushButton("Medicion de campo")
        self.btn_real.setObjectName("primaryMode")
        self.btn_real.setMinimumHeight(104)
        self.btn_real.setFont(QtGui.QFont("Arial", 15, QtGui.QFont.Weight.Bold))
        layout.addWidget(self.btn_real)

        self.btn_test = QtWidgets.QPushButton("Test de campo")
        self.btn_temp = QtWidgets.QPushButton("Solo temperatura")
        self.btn_reajustes = QtWidgets.QPushButton("Reajustes")
        self.btn_configurations = QtWidgets.QPushButton("Configuraciones")
        self.btn_3m = QtWidgets.QPushButton("Experimento 3M")
        self.btn_vacuum = QtWidgets.QPushButton("Experimento con vacio")
        self.btn_relations = QtWidgets.QPushButton("Estadisticas")
        self.btn_animals = QtWidgets.QPushButton("Animales")
        self.btn_fourier = QtWidgets.QPushButton("Analisis experimental de Fourier")

        secondary_buttons = [
            self.btn_relations, self.btn_animals, self.btn_fourier, self.btn_configurations,
            self.btn_test, self.btn_temp, self.btn_reajustes, self.btn_3m, self.btn_vacuum,
        ]
        for button in secondary_buttons:
            button.setMinimumHeight(58)
            button.setFont(QtGui.QFont("Arial", 11, QtGui.QFont.Weight.DemiBold))
            button.setIconSize(QtCore.QSize(36, 36))
        self.btn_real.setIconSize(QtCore.QSize(58, 58))
        self.apply_button_icons()

        main_buttons = QtWidgets.QGridLayout()
        main_buttons.setHorizontalSpacing(12)
        main_buttons.setVerticalSpacing(10)
        main_buttons.addWidget(self.btn_relations, 0, 0)
        main_buttons.addWidget(self.btn_animals, 0, 1)
        main_buttons.addWidget(self.btn_fourier, 1, 0)
        main_buttons.addWidget(self.btn_configurations, 1, 1)
        main_buttons.setColumnStretch(0, 1)
        main_buttons.setColumnStretch(1, 1)
        layout.addLayout(main_buttons)

        self.btn_other_toggle = QtWidgets.QPushButton("Otros")
        self.btn_other_toggle.setObjectName("otherToggle")
        self.btn_other_toggle.setCheckable(True)
        self.btn_other_toggle.setMinimumHeight(46)
        self.btn_other_toggle.setFont(QtGui.QFont("Arial", 10, QtGui.QFont.Weight.DemiBold))
        layout.addWidget(self.btn_other_toggle)

        self.other_modes_widget = QtWidgets.QWidget()
        other_layout = QtWidgets.QGridLayout(self.other_modes_widget)
        other_layout.setContentsMargins(0, 0, 0, 0)
        other_layout.setHorizontalSpacing(12)
        other_layout.setVerticalSpacing(10)
        other_layout.addWidget(self.btn_test, 0, 0)
        other_layout.addWidget(self.btn_temp, 0, 1)
        other_layout.addWidget(self.btn_reajustes, 1, 0)
        other_layout.addWidget(self.btn_3m, 1, 1)
        other_layout.addWidget(self.btn_vacuum, 2, 0, 1, 2)
        other_layout.setColumnStretch(0, 1)
        other_layout.setColumnStretch(1, 1)
        self.other_modes_widget.setVisible(False)
        layout.addWidget(self.other_modes_widget)

        info = QtWidgets.QLabel(
            "Test de campo: toma con notas, parametros desplegables y graficas diagnosticas.\n"
            "Solo temperatura: registro NTC sin PPG.\n"
            "Reajustes: calibracion larga con controles completos.\n"
            "Configuraciones: tabla editable para crear, pegar y ejecutar pruebas de sensor.\n"
            "Experimento 3M: optimizacion adaptativa del sensor usando BPM manual, pulso PPG, SpO2, ruido, PI y saturacion.\n"
            "Experimento con vacio: PPG y microfono sincronizados; el notch se aplica solo al informe final.\n"
            "Estadisticas: sesiones, resultados, configuraciones y graficas comparativas.\n"
            "Animales: fichas, notas, archivos, medias e historico por animal.\n"
            "Fourier experimental: compara varios raw y razona que configuracion separa mejor el pulso."
        )
        info.setObjectName("infoText")
        info.setWordWrap(True)
        layout.addWidget(info, stretch=1)

        self.btn_updates = QtWidgets.QPushButton("Ultimas actualizaciones")
        self.btn_updates.setObjectName("updatesButton")
        self.btn_updates.setMinimumHeight(30)
        self.btn_updates.setIcon(self.asset_icon("icon-listado-green.png"))
        self.btn_updates.setIconSize(QtCore.QSize(18, 18))
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
        self.btn_vacuum.clicked.connect(lambda: self.choose("experimento_vacio"))
        self.btn_relations.clicked.connect(lambda: self.choose("relations"))
        self.btn_animals.clicked.connect(lambda: self.choose("animals"))
        self.btn_fourier.clicked.connect(lambda: self.choose("fourier"))
        self.btn_other_toggle.toggled.connect(self.other_modes_widget.setVisible)
        self.btn_updates.clicked.connect(self.show_latest_updates)

    def apply_rumiando_style(self):
        self.setStyleSheet("""
            QDialog {
                background: #f4f8f1;
                color: #1f4f35;
            }
            QLabel {
                color: #1f4f35;
            }
            QLabel#title {
                color: #003f2a;
                letter-spacing: 0px;
            }
            QLabel#infoText {
                background: #ffffff;
                border: 1px solid #d7e7d8;
                border-radius: 6px;
                padding: 10px;
                color: #335f40;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #9fbea5;
                border-radius: 8px;
                color: #1f4f35;
                padding: 10px 14px;
                text-align: left;
            }
            QPushButton:hover {
                background: #eaf3eb;
                border-color: #3f6f4b;
            }
            QPushButton:pressed,
            QPushButton:checked {
                background: #dcebdd;
            }
            QPushButton#primaryMode {
                background: #e2efe3;
                border: 2px solid #3f6f4b;
                color: #1f4f35;
                font-weight: 700;
                padding: 14px 18px;
            }
            QPushButton#otherToggle {
                background: #f7fbf5;
                border-style: dashed;
            }
            QPushButton#updatesButton {
                background: transparent;
                border: none;
                color: #3f6f4b;
                font-size: 8pt;
                padding: 4px 8px;
            }
            QPushButton#updatesButton:hover {
                background: #eaf3eb;
                border: 1px solid #d7e7d8;
            }
        """)

    def asset_path(self, name: str) -> Path:
        return RUMIANDO_ASSET_DIR / name

    def asset_icon(self, name: str) -> QtGui.QIcon:
        path = self.asset_path(name)
        icon = QtGui.QIcon(str(path)) if path.exists() else QtGui.QIcon()
        if icon.isNull():
            return self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileIcon)
        return icon

    def asset_pixmap(self, name: str, size: QtCore.QSize) -> QtGui.QPixmap:
        path = self.asset_path(name)
        pix = QtGui.QPixmap(str(path)) if path.exists() else QtGui.QPixmap()
        if pix.isNull():
            return pix
        return pix.scaled(size, QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation)

    def apply_button_icons(self):
        icon_map = {
            self.btn_real: "rumiando-sheep-facing-left.png",
            self.btn_relations: "icon-estadisticas-green.png",
            self.btn_animals: "icon-ganado-outline-green.png",
            self.btn_fourier: "icon-ia-green.png",
            self.btn_configurations: "icon-settings-green.png",
            self.btn_reajustes: "icon-settings-green.png",
            self.btn_test: "icon-listado-green.png",
            self.btn_temp: "icon-listado-green.png",
            self.btn_3m: "icon-listado-green.png",
            self.btn_vacuum: "icon-listado-green.png",
        }
        for button, icon_name in icon_map.items():
            button.setIcon(self.asset_icon(icon_name))

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
