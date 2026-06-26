"""Ventana principal del administrador: QTabWidget que aloja las herramientas.

Por ahora una sola pestaña ("Convertir"). Las pestañas Buscar, Organizar,
Motor y Cuarentena se agregan en las fases siguientes del plan.
"""

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QMainWindow, QTabWidget

from .config import APP_TITLE
from .ui.tabs.tab_convertir import ConvertTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1100, 660)
        self.setMinimumSize(850, 450)

        self.settings = QSettings("CDLM", "Convertir3x4")

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.convert_tab = ConvertTab()
        self.convert_tab.titleChanged.connect(self.setWindowTitle)
        self.tabs.addTab(self.convert_tab, "Convertir")

        self._restore_geometry()

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
