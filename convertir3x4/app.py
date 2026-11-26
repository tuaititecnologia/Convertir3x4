"""Ventana principal: barra lateral de módulos (como el Lightroom nuevo) y, al
lado, el módulo activo (Buscar, Catalogar, Convertir).
"""

from PyQt6.QtCore import QSettings, QSize, Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QStackedWidget,
    QToolButton, QVBoxLayout, QWidget,
)

from .config import APP_TITLE, APP_VERSION
from .ui import icons
from .ui.style import T
from .ui.widgets.aviso_actualizacion import AvisoActualizacion
from .ui.widgets.help_panel import HelpPanel
from .ui.tabs.tab_convertir import ConvertTab
from .ui.tabs.tab_buscar import BuscarTab
from .ui.tabs.tab_motor import MotorTab
from .ui.tabs.tab_discos import DiscosTab

_RAIL_WIDTH = 80      # «Grabadora» (56 px a 11 px) tiene que entrar sin cortarse


class NavButton(QToolButton):
    """Botón de módulo de la barra lateral, con un aviso numérico opcional
    (p. ej. discos de la grabadora enchufados que todavía no se miraron)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._badge = 0

    def set_badge(self, n: int):
        if n != self._badge:
            self._badge = n
            self.update()

    def badge(self) -> int:
        return self._badge

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._badge:
            return
        from PyQt6.QtGui import QColor, QFont, QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = 18
        x, y = self.width() - d - 8, 4
        p.setPen(QColor(T.panel))
        p.setBrush(QColor(T.accent))
        p.drawEllipse(x, y, d, d)
        f = QFont(self.font())
        f.setPixelSize(11)
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(QColor(T.on_image))
        p.drawText(x, y, d, d, int(Qt.AlignmentFlag.AlignCenter), str(min(self._badge, 9)))
        p.end()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._avisos = None
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

        self.stack = QStackedWidget()
        self._nav = QButtonGroup(self)
        self._nav.setExclusive(True)
        rail = self._build_rail([
            (self.buscar_tab, "Buscar", "search"),
            (self.motor_tab, "Catalogar", "scan-search"),
            (self.convert_tab, "Convertir", "wand-sparkles"),
            (self.discos_tab, "Grabadora", "hard-drive"),
        ])

        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(rail)
        # Arriba de los módulos, el aviso de versión nueva (oculto si no hay)
        derecha = QVBoxLayout()
        derecha.setContentsMargins(0, 0, 0, 0)
        derecha.setSpacing(0)
        self.aviso_version = AvisoActualizacion()
        self.aviso_version.lista.connect(self._cerrar_para_actualizar)
        # Si el catálogo es de una versión más nueva, se busca la actualización
        # enseguida (la PC de ProPresenter queda abierta días: no esperar 6 horas)
        from .catalog.db import CatalogoIncompatible
        from .ui.servicio_catalogo import servicio
        servicio().estado_cambiado.connect(
            lambda estado, texto: estado == "error" and texto == CatalogoIncompatible.MENSAJE
            and self.aviso_version.revisar())
        # Se guardó otra conexión (pantalla «Conexión al catálogo»): todo se pone al día
        servicio().reconectado.connect(self._conexion_cambiada)
        derecha.addWidget(self.aviso_version)
        derecha.addWidget(self.stack, 1)
        row.addLayout(derecha, 1)
        self.setCentralWidget(central)
        # La ayuda va POR ENCIMA del contenido, pegada a la derecha: no le quita
        # lugar a la pantalla de trabajo (en ventanas chicas la aplastaba).
        self.help_panel = HelpPanel(central)
        self.help_panel.closed.connect(lambda: self.set_help_visible(False))
        # La primera vez en esta PC la ayuda se abre sola; después, con el botón.
        self.set_help_visible(not self.settings.value("help_seen", False, type=bool))
        self.stack.currentChanged.connect(self.help_panel.set_module)

        self.stack.currentChanged.connect(self._on_tab_changed)
        self.discos_tab.pendientes.connect(self._aviso_grabadora)
        self.buscar_tab.enviarAConvertir.connect(self._a_convertir)
        self.buscar_tab.enviarAPersona.connect(self._a_persona)
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
            b = NavButton()
            b.setObjectName("nav_button")
            b.setText(label)
            icons.set_icon(b, icon_name, 22, "text_3", active="text")
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
        icons.set_icon(self.btn_help, "circle-help", 22, "text_3", active="text")
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
        ver.setToolTip("Versión de la aplicación")
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

    def _a_convertir(self, paths: list[str]):
        """Desde Buscar: lo elegido pasa a Convertir y se muestra Convertir."""
        self.convert_tab.recibir(paths)
        self.show_module(self.stack.indexOf(self.convert_tab))

    def _a_persona(self, paths: list[str], media_ids: list | None = None):
        """Desde Buscar: fotos de una persona para el reconocimiento facial. Se
        muestra Catalogar, donde está la lista de personas y el resultado. Con
        los ids del catálogo, el NAS lee los archivos (no viajan por la red)."""
        self.show_module(self.stack.indexOf(self.motor_tab))
        self.motor_tab.recibir_fotos_de_persona(paths, media_ids or None)

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

    def _aviso_grabadora(self, n: int):
        b = self._nav.button(self.stack.indexOf(self.discos_tab))
        b.set_badge(n)
        b.setToolTip("Grabadora" + ("" if not n else
                     "  ·  hay un disco nuevo conectado al NAS" if n == 1 else
                     f"  ·  hay {n} discos nuevos conectados al NAS"))

    def _focus_module(self, index: int):
        """Al cambiar de módulo, el foco va a su lugar natural (o a ningún lado).

        Si no, Qt lo pasa al primer botón de la página y su borde de foco
        parece una selección (pasaba con "Conexión…" en Discos).
        """
        page = self.stack.widget(index)
        target = {self.buscar_tab: self.buscar_tab.txt_query,
                  self.convert_tab: self.convert_tab.list_widget}.get(page)
        if target is None:
            # La página misma recibe el foco: no dibuja borde y no lo deja caer
            # en un botón (ni de la página ni de la barra lateral).
            page.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            target = page
        target.setFocus()

    def _on_tab_changed(self, index: int):
        self._focus_module(index)

    def _restore_geometry(self):
        geom = self.settings.value("geometry")
        if geom:
            self.restoreGeometry(geom)

    def iniciar_avisos(self):
        """Escucha los avisos del NAS para que Buscar muestre sola lo que cambia.
        Lo llama __main__ (no las pruebas, que no tienen NAS)."""
        from .ui.avisos_catalogo import AvisosCatalogo
        self._avisos = AvisosCatalogo(parent=self)
        self._avisos.cambio.connect(self.buscar_tab.cambio_en_el_catalogo)
        self._avisos.cambio.connect(self.motor_tab.cambio_en_el_catalogo)
        self._avisos.start()

    def _conexion_cambiada(self):
        if self._avisos is not None:
            self._avisos.reconectar()
        self.buscar_tab.refresh()
        self.motor_tab.refresh()

    def revisar_configuracion(self):
        """Al arrancar: si esta PC no tiene configurado el catálogo (o el archivo
        tiene un dato que no sirve), se abre la pantalla de conexión una vez.
        Lo llama __main__, no las pruebas."""
        from .appdata import config_catalogo
        from .catalog.db import CatalogoNoConfigurado
        try:
            config_catalogo()
        except CatalogoNoConfigurado:
            from .ui.widgets.conexion_catalogo import abrir_conexion
            QTimer.singleShot(0, lambda: abrir_conexion(self))

    def _cerrar_para_actualizar(self):
        """La versión nueva ya está descargada: al cerrar se instala y la
        aplicación se vuelve a abrir. Si Convertir está trabajando y el operador
        prefiere esperar, se instala cuando cierre."""
        if not self.close():
            self.aviso_version.cierre_cancelado()

    def closeEvent(self, event):
        if not self.convert_tab.confirm_close():
            event.ignore()
            return
        self.convert_tab.shutdown()
        if self._avisos is not None:
            self._avisos.detener()
        self.convert_tab.save_state()
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)
        self.aviso_version.instalar_al_cerrar()
