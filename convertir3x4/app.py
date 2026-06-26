"""Ventana principal del administrador: QTabWidget que aloja las herramientas.

Por ahora una sola pestaña ("Convertir"). Las pestañas Buscar, Organizar,
Motor y Cuarentena se agregan en las fases siguientes del plan.
"""

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QMainWindow, QTabWidget

from .config import APP_TITLE
from .ui.tabs.tab_convertir import ConvertTab
from .ui.tabs.tab_buscar import BuscarTab
from .ui.tabs.tab_motor import MotorTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1100, 660)
        self.setMinimumSize(850, 450)

        self.settings = QSettings("CDLM", "Convertir3x4")

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.buscar_tab = BuscarTab()
        self.tabs.addTab(self.buscar_tab, "Buscar")

        self.motor_tab = MotorTab()
        self.motor_tab.catalogChanged.connect(self.buscar_tab.refresh)
        self.tabs.addTab(self.motor_tab, "Catalogar")

        self.convert_tab = ConvertTab()
        self.convert_tab.titleChanged.connect(self.setWindowTitle)
        self.tabs.addTab(self.convert_tab, "Convertir")

        # El título lo controla la pestaña Convertir; al cambiar de pestaña,
        # restaurar el título base para no arrastrar el "● proyecto.c3x4".
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self._restore_geometry()

    def _on_tab_changed(self, index: int):
        if self.tabs.widget(index) is not self.convert_tab:
            self.setWindowTitle(APP_TITLE)
        else:
            self.convert_tab._update_title()

    def _restore_geometry(self):
        geom = self.settings.value("geometry")
        if geom:
            self.restoreGeometry(geom)

    def closeEvent(self, event):
        if not self.convert_tab.confirm_close():
            event.ignore()
            return
        self.convert_tab.save_state()
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)
