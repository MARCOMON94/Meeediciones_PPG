from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from PyQt6 import QtCore, QtGui, QtWidgets

from .data_import import DataImportResult, import_resultados_folder
from .firmware_update import (
    NANO_33_IOT_FQBN,
    arduino_cli_path,
    available_firmware_ports,
    compile_firmware,
    upload_firmware,
)
from .paths import ARDUINO_FIRMWARE_SKETCH, RESULTS_DIR, RUMIANDO_ASSET_DIR, UPDATES_DIR


AppMode = Literal["reajustes", "test", "real", "configurations", "experimento_3m", "experimento_vacio", "temp", "relations", "fourier", "animals"]
def icon_text(text: str) -> str:
    return f"  {text}"


class ModeSelectDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Seleccionar modo de trabajo")
        self.selected_mode: AppMode = "real"
        self.setMinimumSize(760, 590)
        self.apply_rumiando_style()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)

        hero = QtWidgets.QHBoxLayout()
        hero.setSpacing(18)
        hero.setContentsMargins(4, 0, 4, 4)
        self.hero_image = QtWidgets.QLabel()
        self.hero_image.setFixedSize(130, 116)
        self.hero_image.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        hero_pix = self.asset_pixmap("rumiando-sheep-tech-app-colors.png", QtCore.QSize(130, 116))
        if hero_pix.isNull():
            self.hero_image.hide()
        else:
            self.hero_image.setPixmap(hero_pix)
            hero.addWidget(self.hero_image)
        hero_text = QtWidgets.QVBoxLayout()
        hero_text.setContentsMargins(0, 8, 0, 0)
        hero_text.setSpacing(8)
        title = QtWidgets.QLabel("MEEEDICIONES")
        title.setObjectName("title")
        title.setFont(QtGui.QFont("Arial", 22, QtGui.QFont.Weight.Bold))
        hero_text.addWidget(title)

        subtitle = QtWidgets.QLabel("Medición de campo: toma rápida con la interfaz mínima y los datos esenciales.")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        hero_text.addWidget(subtitle)
        hero_text.addStretch(1)
        hero.addLayout(hero_text, stretch=1)
        layout.addLayout(hero)

        self.btn_real = QtWidgets.QPushButton(icon_text("Medición de campo"))
        self.btn_real.setObjectName("primaryMode")
        self.btn_real.setMinimumHeight(104)
        self.btn_real.setFont(QtGui.QFont("Arial", 16, QtGui.QFont.Weight.Bold))
        layout.addWidget(self.btn_real)

        self.btn_test = QtWidgets.QPushButton(icon_text("Test de campo"))
        self.btn_temp = QtWidgets.QPushButton(icon_text("Solo temperatura"))
        self.btn_reajustes = QtWidgets.QPushButton(icon_text("Reajustes"))
        self.btn_configurations = QtWidgets.QPushButton(icon_text("Configuraciones"))
        self.btn_3m = QtWidgets.QPushButton(icon_text("Experimento 3M"))
        self.btn_vacuum = QtWidgets.QPushButton(icon_text("Experimento con vacío"))
        self.btn_relations = QtWidgets.QPushButton(icon_text("Estadísticas"))
        self.btn_animals = QtWidgets.QPushButton(icon_text("Animales"))
        self.btn_fourier = QtWidgets.QPushButton(icon_text("Análisis experimental de Fourier"))

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
            "Test de campo: toma con notas, parámetros desplegables y gráficas diagnósticas.\n"
            "Solo temperatura: registro NTC sin PPG.\n"
            "Reajustes: calibración larga con controles completos.\n"
            "Configuraciones: tabla editable para crear, pegar y ejecutar pruebas de sensor.\n"
            "Experimento 3M: optimización adaptativa del sensor usando BPM manual, pulso PPG, SpO2, ruido, PI y saturación.\n"
            "Experimento con vacío: PPG y micrófono sincronizados; el notch se aplica solo al informe final.\n"
            "Estadísticas: sesiones, resultados, configuraciones y gráficas comparativas.\n"
            "Animales: fichas, notas, archivos, medias e histórico por animal.\n"
            "Fourier experimental: compara varios raw y razona qué configuración separa mejor el pulso."
        )
        info.setObjectName("infoText")
        info.setWordWrap(True)
        layout.addWidget(info, stretch=1)

        utility_buttons = QtWidgets.QHBoxLayout()
        utility_buttons.setSpacing(12)
        self.btn_import_data = QtWidgets.QPushButton("IMPORTAR DATOS")
        self.btn_import_data.setObjectName("bottomToolButton")
        self.btn_import_data.setToolTip("Importar una carpeta llamada resultados a la carpeta de datos actual.")
        self.btn_update_firmware = QtWidgets.QPushButton("ACTUALIZAR FIRMWARE")
        self.btn_update_firmware.setObjectName("bottomToolButton")
        self.btn_update_firmware.setToolTip("Compilar y subir el firmware Arduino incluido, o abrirlo con Arduino IDE.")
        for button in (self.btn_import_data, self.btn_update_firmware):
            button.setMinimumHeight(42)
            button.setFont(QtGui.QFont("Arial", 10, QtGui.QFont.Weight.Bold))
            utility_buttons.addWidget(button)
        layout.addLayout(utility_buttons)

        self.btn_updates = QtWidgets.QPushButton(icon_text("Últimas actualizaciones"))
        self.btn_updates.setObjectName("updatesButton")
        self.btn_updates.setMinimumHeight(30)
        self.btn_updates.setIcon(self.asset_icon("icon-listado-green.png"))
        self.btn_updates.setIconSize(QtCore.QSize(18, 18))
        layout.addWidget(self.btn_updates, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)

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
        self.btn_other_toggle.toggled.connect(self.toggle_other_modes)
        self.btn_updates.clicked.connect(self.show_latest_updates)
        self.btn_import_data.clicked.connect(self.import_data)
        self.btn_update_firmware.clicked.connect(self.update_firmware)

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
            QLabel#subtitle {
                color: #214d37;
                font-size: 10pt;
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
                padding: 14px 10px;
                text-align: center;
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
            QPushButton#bottomToolButton {
                background: #eef6ef;
                border: 1px solid #6f9778;
                color: #0e4b32;
                padding: 9px 12px;
                text-align: center;
            }
            QPushButton#bottomToolButton:hover {
                background: #dfeee2;
                border-color: #2f6643;
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

    def toggle_other_modes(self, checked: bool):
        self.other_modes_widget.setVisible(checked)
        QtCore.QTimer.singleShot(0, self.adjust_other_modes_size)

    def adjust_other_modes_size(self):
        layout = self.layout()
        if layout is not None:
            layout.activate()
        target = self.sizeHint()
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        max_height = 1200
        if screen is not None:
            max_height = max(520, screen.availableGeometry().height() - 40)
        self.resize(
            max(self.width(), self.minimumWidth(), target.width()),
            min(max_height, max(self.height(), self.minimumHeight(), target.height())),
        )

    def show_latest_updates(self):
        update_dir = UPDATES_DIR
        files = sorted(update_dir.glob("ACTUALIZACIONES_*.txt"), key=self.update_file_date)
        if not files:
            QtWidgets.QMessageBox.information(self, "Últimas actualizaciones", "No hay archivo de actualizaciones.")
            return
        path = files[-1]
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Últimas actualizaciones", f"No se pudo leer:\n{path}\n\n{exc}")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"Últimas actualizaciones - {path.name}")
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

    def import_data(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Seleccionar carpeta resultados",
            str(Path.home()),
            QtWidgets.QFileDialog.Option.ShowDirsOnly,
        )
        if not folder:
            return
        try:
            result = import_resultados_folder(Path(folder), RESULTS_DIR)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Importar datos", str(exc))
            return

        summary = self.data_import_summary(result)
        if result.errors:
            self.show_text_dialog("Importar datos", summary)
        else:
            QtWidgets.QMessageBox.information(self, "Importar datos", summary)

    def data_import_summary(self, result: DataImportResult) -> str:
        lines = [
            "ImportaciÃ³n completada.",
            "",
            f"Origen: {result.source}",
            f"Destino: {result.destination}",
            "",
            f"Archivos copiados: {result.copied_files}",
            f"Archivos ya existentes omitidos: {result.skipped_existing}",
            f"Carpetas creadas: {result.created_dirs}",
            f"Datos copiados: {self.format_bytes(result.bytes_copied)}",
        ]
        if result.errors:
            lines.extend(["", "Errores:", *result.errors])
        return "\n".join(lines)

    def update_firmware(self):
        sketch = ARDUINO_FIRMWARE_SKETCH
        if not sketch.exists():
            QtWidgets.QMessageBox.warning(
                self,
                "Actualizar firmware",
                f"No se encontrÃ³ el firmware Arduino incluido:\n{sketch}",
            )
            return

        if not arduino_cli_path():
            msg = QtWidgets.QMessageBox(self)
            msg.setIcon(QtWidgets.QMessageBox.Icon.Information)
            msg.setWindowTitle("Actualizar firmware")
            msg.setText("No se encontrÃ³ arduino-cli en este ordenador.")
            msg.setInformativeText(
                "Voy a abrir el archivo del firmware. Desde Arduino IDE puedes seleccionar la placa "
                "Arduino Nano 33 IoT, el puerto USB y pulsar Subir."
            )
            open_btn = msg.addButton("Abrir firmware", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
            msg.addButton("Cancelar", QtWidgets.QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            if msg.clickedButton() == open_btn:
                self.open_local_file(sketch)
            return

        ports = available_firmware_ports()
        if not ports:
            msg = QtWidgets.QMessageBox(self)
            msg.setIcon(QtWidgets.QMessageBox.Icon.Warning)
            msg.setWindowTitle("Actualizar firmware")
            msg.setText("No se ha detectado ningÃºn puerto USB para Arduino.")
            msg.setInformativeText("Conecta el Arduino por USB. TambiÃ©n puedes abrir el firmware manualmente.")
            open_btn = msg.addButton("Abrir firmware", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
            msg.addButton("Cancelar", QtWidgets.QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            if msg.clickedButton() == open_btn:
                self.open_local_file(sketch)
            return

        port = ports[0].device
        if len(ports) > 1:
            labels = [port_info.label for port_info in ports]
            selected, ok = QtWidgets.QInputDialog.getItem(
                self,
                "Actualizar firmware",
                "Selecciona el Arduino conectado:",
                labels,
                0,
                False,
            )
            if not ok:
                return
            port = ports[labels.index(selected)].device

        confirm = QtWidgets.QMessageBox.question(
            self,
            "Actualizar firmware",
            "Se compilarÃ¡ y subirÃ¡ el firmware incluido al Arduino conectado.\n\n"
            f"Placa: {NANO_33_IOT_FQBN}\n"
            f"Puerto: {port}\n\n"
            "No desconectes el cable durante la subida.",
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            compile_result = compile_firmware(sketch.parent)
            if not compile_result.ok:
                self.show_text_dialog("Actualizar firmware", "No se pudo compilar el firmware.\n\n" + compile_result.detail)
                return
            upload_result = upload_firmware(sketch.parent, port)
            if not upload_result.ok:
                self.show_text_dialog("Actualizar firmware", "No se pudo subir el firmware.\n\n" + upload_result.detail)
                return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        QtWidgets.QMessageBox.information(self, "Actualizar firmware", "Firmware actualizado correctamente.")

    def open_local_file(self, path: Path):
        ok = QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))
        if not ok:
            QtWidgets.QMessageBox.warning(self, "Abrir archivo", f"No se pudo abrir:\n{path}")

    def show_text_dialog(self, title: str, text: str):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(820, 560)
        layout = QtWidgets.QVBoxLayout(dialog)
        view = QtWidgets.QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        layout.addWidget(view)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def format_bytes(self, value: int) -> str:
        amount = float(value)
        for unit in ("B", "KB", "MB", "GB"):
            if amount < 1024 or unit == "GB":
                return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
            amount /= 1024
        return f"{value} B"

    def update_file_date(self, path):
        stem = path.stem.replace("ACTUALIZACIONES_", "")
        for fmt in ("%d%m%Y", "%Y%m%d"):
            try:
                return datetime.strptime(stem, fmt)
            except ValueError:
                pass
        return datetime.min
