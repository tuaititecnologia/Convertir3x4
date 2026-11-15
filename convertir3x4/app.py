"""Ventana principal: barra lateral de módulos (como el Lightroom nuevo) y, al
lado, el módulo activo (Buscar, Catalogar, Convertir).
"""

from PyQt6.QtCore import QSettings, QSize, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QStackedWidget,
    QToolButton, QVBoxLayout, QWidget,
)

from .config import APP_TITLE, APP_VERSION
from .ui import icons
from .ui.style import T
from .ui.widgets.help_panel import HelpPanel
from .ui.tabs.tab_convertir import ConvertTab
from .ui.tabs.tab_buscar import BuscarTab
from .ui.tabs.tab_motor import MotorTab
from .ui.tabs.tab_discos import DiscosTab

_RAIL_WIDTH = 76


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1200, 760)
        self.setMinimumSize(900, 520)

        # Sin argumentos: el nombre lo fija __main__ (CDLM/Convertir3x4); los tests usan otro.
        self.settings = QSettings()

        self.buscar_tab = BuscarTab()
        self.motor_tab = MotorTab()
        self.motor_tab.catalogChanged.connect(self.buscar_tab.refresh)
        self.discos_tab = DiscosTab()
        self.convert_tab = ConvertTab()
        self.convert_tab.titleChanged.connect(self.setWindowTitle)

        self.stack = QStackedWidget()
        self._nav = QButtonGroup(self)
        self._nav.setExclusive(True)
        rail = self._build_rail([
            (self.buscar_tab, "Buscar", "search"),
            (self.motor_tab, "Catalogar", "scan-search"),
            (self.convert_tab, "Convertir", "wand-sparkles"),
            (self.discos_tab, "Discos", "hard-drive"),
        ])

        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(rail)
        row.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        # La ayuda va POR ENCIMA del contenido, pegada a la derecha: no le quita
        # lugar a la pantalla de trabajo (en ventanas chicas la aplastaba).
        self.help_panel = HelpPanel(central)
        self.help_panel.closed.connect(lambda: self.set_help_visible(False))
        # La primera vez en esta PC la ayuda se abre sola; después, con el botón.
        self.set_help_visible(not self.settings.value("help_seen", False, type=bool))
        self.stack.currentChanged.connect(self.help_panel.set_module)

        # El título lo controla Convertir; al cambiar de módulo se restaura el
        # título base para no arrastrar el "● proyecto.c3x4".
        self.stack.currentChanged.connect(self._on_tab_changed)
        self._restore_geometry()

    def _build_rail(self, modules) -> QFrame:
        rail = QFrame()
        rail.setObjectName("nav_rail")
        rail.setFixedWidth(_RAIL_WIDTH)
        col = QVBoxLayout(rail)
        col.setContentsMargins(8, 14, 8, 12)
        col.setSpacing(6)
        for i, (widget, label, icon_name) in enumerate(modules):
            self.stack.addWidget(widget)
            b = QToolButton()
            b.setObjectName("nav_button")
            b.setText(label)
            b.setIcon(icons.icon(icon_name, 22, T.text_3, active_color=T.text))
            b.setIconSize(QSize(22, 22))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setCheckable(True)
            # Con Tab se llega; con el mouse no queda el borde de foco pegado
            b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            b.setFixedSize(_RAIL_WIDTH - 16, 58)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAccessibleName(f"Módulo {label}")
            # Atajo directo (no el "click animado" de setShortcut, que tarda 100 ms
            # y solo anda si el botón está visible). Ctrl en Windows, Cmd en Mac.
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self,
                      activated=lambda i=i: self.show_module(i))
            b.setToolTip(f"{label}  (Ctrl+{i + 1})")
            self._nav.addButton(b, i)
            col.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
        self._nav.idClicked.connect(self.stack.setCurrentIndex)
        self._nav.button(0).setChecked(True)
        col.addStretch(1)
        self.btn_help = QToolButton()
        self.btn_help.setObjectName("nav_button")
        self.btn_help.setText("Ayuda")
        self.btn_help.setIcon(icons.icon("circle-help", 22, T.text_3, active_color=T.text))
        self.btn_help.setIconSize(QSize(22, 22))
        self.btn_help.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.btn_help.setCheckable(True)
        self.btn_help.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_help.setFixedSize(_RAIL_WIDTH - 16, 58)
        self.btn_help.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_help.setToolTip("Cómo se usa este módulo (F1)")
        self.btn_help.setAccessibleName("Ayuda")
        self.btn_help.toggled.connect(self.set_help_visible)
        col.addWidget(self.btn_help, 0, Qt.AlignmentFlag.AlignHCenter)
        QShortcut(QKeySequence(Qt.Key.Key_F1), self,
                  activated=lambda: self.set_help_visible(not self.help_panel.isVisible()))
        col.addSpacing(6)
        ver = QLabel(APP_VERSION)
        ver.setObjectName("muted")
        ver.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        ver.setToolTip("Versión de la app")
        col.addWidget(ver)
        return rail

    def _place_help(self):
        c = self.centralWidget()
        w = self.help_panel.width()
        self.help_panel.setGeometry(c.width() - w, 0, w, c.height())
        self.help_panel.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_help()

    def set_help_visible(self, visible: bool):
        self._place_help()
        self.help_panel.setVisible(visible)
        if self.btn_help.isChecked() != visible:
            self.btn_help.blockSignals(True)
            self.btn_help.setChecked(visible)
            self.btn_help.blockSignals(False)
        if not visible:
            self.settings.setValue("help_seen", True)

    def show_module(self, index: int):
        self._nav.button(index).setChecked(True)
        self.stack.setCurrentIndex(index)

    def showEvent(self, event):
        super().showEvent(event)
        # Al abrir, el foco va al buscador (si no, cae en el primer botón de la
        # barra lateral y su borde de foco parece una selección).
        if not getattr(self, "_focused_once", False):
            self._focused_once = True
            self.buscar_tab.txt_query.setFocus()

    def _focus_module(self, index: int):
        """Al cambiar de módulo, el foco va a su lugar natural (o a ningún lado).

        Si no, Qt lo pasa al primer botón de la página y su borde de foco
        parece una selección (pasaba con "Conexión…" en Discos).
        """
        page = self.stack.widget(index)
        target = {self.buscar_tab: self.buscar_tab.txt_query,
                  self.convert_tab: self.convert_tab.list_widget}.get(page)
        if target is not None:
            target.setFocus()
        elif self.focusWidget() is not None:
            self.focusWidget().clearFocus()

    def _on_tab_changed(self, index: int):
        self._focus_module(index)
        if self.stack.widget(index) is not self.convert_tab:
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
        self.convert_tab.shutdown()
        self.convert_tab.save_state()
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)
