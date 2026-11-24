"""Pestaña 'Buscar': busca en el catálogo (palabras + fragmentos), muestra
miniaturas, permite taggear por lote y arrastrar a ProPresenter (como enlace).

Nunca toca la base desde el hilo de la interfaz: todo pasa por el servicio del
catálogo (`ui/servicio_catalogo.py`). Las miniaturas salen de la caché local de
esta PC y, si faltan, se bajan de la base en segundo plano.
"""

import ntpath
import os
import re
import socket
import threading

from PyQt6.QtCore import (
    QElapsedTimer, QPoint, QSettings, QSize, QStringListModel, Qt, QTimer, pyqtSignal,
)
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QCompleter, QProgressBar, QSizePolicy, QSplitter,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidgetItem, QFrame, QPushButton, QButtonGroup, QMessageBox,
)

from ...appdata import catalog_thumb_dir
from ...catalog.cache_miniaturas import CacheMiniaturas, CacheTiras
from ...catalog.ubicacion import availability, friendly_root, storage_root  # noqa: F401
from ..servicio_catalogo import servicio, texto_para
from ..widgets.caja_conexion import CajaConexion
from ..widgets.media_grid import (
    DragOutListWidget, PATH_ROLE, MEDIA_ID_ROLE, KIND_ROLE, DURATION_ROLE, STATE_ROLE,
    STORYBOARD_ROLE, TAGS_ROLE, LUGAR_ROLE, EVENTOS_ROLE, FECHA_ROLE,
)
from ...catalog import consulta
from ...catalog.consulta import analizar
from ..widgets.busqueda_avanzada import FormularioBusqueda
from ..widgets.detalle_medio import ANCHO as _ANCHO_DETALLES, PanelDetalles, estado_de_item
from ..widgets.dialogos import confirmar
from ..widgets.tag_bar import TagBar
from ...utils import open_file
from .. import icons
from ..style import T

_GRID_THUMB = 150
# Cambios que avisa el NAS: se juntan y se muestran a lo sumo cada 20 s. Durante
# una pasada llega un aviso por archivo; cada puesta al día rehace la búsqueda y
# revisa los archivos en el NAS, y las etiquetas que se pongan esperan detrás.
_VIVO_ESPERA_MS = 1500
_VIVO_MINIMO_MS = 20000
# Si una búsqueda tarda más que esto, se muestra que se están trayendo datos del NAS
# (antes no: una respuesta rápida no tiene que hacer parpadear nada).
_CARGANDO_MS = 300
# Cada cuánto se mira si se conectó o se desconectó el NAS (o un disco) mientras
# Buscar está a la vista: una pregunta por raíz, fuera del hilo de la interfaz.
_RAICES_MS = 5000
# Resultados por página. Al acercarse al final de la grilla se trae la siguiente.
# Desde afuera de la iglesia cada página es ~1,5 MB de miniaturas la primera vez.
_PAGINA = 120
_FALTA_PARA_EL_FINAL = 600     # px: a esta distancia del final se pide la siguiente


def _existe(raiz: str) -> bool:
    return os.path.exists(raiz)




class SearchLineEdit(QLineEdit):
    """Campo de búsqueda: botón X para limpiar + ESC vacía el campo. El botón
    de la derecha (o la flecha abajo) abre la búsqueda avanzada."""
    avanzada = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("search_field")
        self.setClearButtonEnabled(True)
        lupa = self.addAction(QIcon(), QLineEdit.ActionPosition.LeadingPosition)
        icons.set_icon(lupa, "search", 16, "text_3")
        self.accion_avanzada = self.addAction(QIcon(), QLineEdit.ActionPosition.TrailingPosition)
        icons.set_icon(self.accion_avanzada, "sliders-horizontal", 16, "text_2")
        self.accion_avanzada.setToolTip("Búsqueda avanzada: por etiqueta, tipo, carpeta, "
                                        "fecha… (flecha abajo)")
        self.accion_avanzada.triggered.connect(self.avanzada)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.text():
            self.clear()
            return
        if event.key() == Qt.Key.Key_Down and not event.modifiers():
            self.avanzada.emit()
            return
        super().keyPressEvent(event)


class BuscarTab(QWidget):
    # Rutas elegidas en el catálogo para convertir (MainWindow las pasa a Convertir)
    enviarAConvertir = pyqtSignal(list)
    enviarAPersona = pyqtSignal(list, list)  # fotos de referencia (rutas, ids del catálogo)
    _raices_revisadas = pyqtSignal(dict)      # desde el hilo que pregunta por las raíces

    def __init__(self, parent=None, srv=None):
        super().__init__(parent)
        self._srv = srv or servicio()
        self._cache = CacheMiniaturas(catalog_thumb_dir())
        self._cache_tiras = CacheTiras(catalog_thumb_dir())
        self._tiras: dict[int, str | None] = {}     # id → tira local (None: no tiene)
        self._busqueda = 0          # solo se muestra la respuesta de la última búsqueda
        self._seleccion = 0         # idem para los tags de la selección
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(200)
        self._debounce.timeout.connect(self._do_search)
        self._build_ui()
        self._srv.estado_cambiado.connect(self._estado_catalogo)
        # Sin catálogo, se reintenta solo cada 20 s mientras la pestaña se ve.
        self._reintento = QTimer(self)
        self._reintento.setInterval(20000)
        self._reintento.timeout.connect(self._reintentar_si_hace_falta)
        self._reintento.start()
        # Lo que cambia en el catálogo aparece solo (ver cambio_en_el_catalogo)
        self._vivo = QTimer(self)
        self._vivo.setSingleShot(True)
        self._vivo.timeout.connect(self._actualizar_en_vivo)
        self._vivo_ultima = None            # QElapsedTimer de la última puesta al día
        self._desactualizada = False
        self._restaurando = False
        self._cargando = QTimer(self)
        self._cargando.setSingleShot(True)
        self._cargando.timeout.connect(self._mostrar_cargando)
        self._revisando_raices = False
        self._raices_timer = QTimer(self)
        self._raices_timer.setInterval(_RAICES_MS)
        self._raices_timer.timeout.connect(self._revisar_raices)
        self._raices_revisadas.connect(self._raices_cambiaron)
        self._total: int | None = None      # cuántos hay en total para esta búsqueda
        self._trayendo_mas = False
        self.grid.verticalScrollBar().valueChanged.connect(lambda _v: self._quizas_traer_mas())
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(14)

        # Barra superior: buscador · filtro por tipo · sugerencias IA · contador
        row = QHBoxLayout()
        row.setSpacing(12)
        self.txt_query = SearchLineEdit()
        self.txt_query.setPlaceholderText("Buscar por nombre, carpeta o etiqueta…  "
                                          "(también etiqueta:placa, tipo:video, desde:09/2025)")
        self.txt_query.setAccessibleName("Buscar en el catálogo")
        self.txt_query.textChanged.connect(lambda _: self._debounce.start())
        self.txt_query.textChanged.connect(self._revisar_consulta)
        self.txt_query.avanzada.connect(self._abrir_formulario)
        row.addWidget(self.txt_query, 1)
        self.formulario = FormularioBusqueda(self)
        self.formulario.buscar.connect(self.txt_query.setText)
        # Mientras se escribe se sugieren los filtros y las etiquetas (como Gmail)
        self._etiquetas_conocidas: list[str] = []
        self._lugares: list[str] = []
        self._eventos: list[str] = []
        self._sugerencias = QStringListModel(self)
        self.completar = QCompleter(self._sugerencias, self)
        self.completar.setWidget(self.txt_query)
        self.completar.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.completar.setMaxVisibleItems(12)
        self.completar.popup().setObjectName("sugerencias")
        self.completar.activated[str].connect(self._usar_sugerencia)
        self._desde_sugerencia = 0
        self.txt_query.textEdited.connect(self._sugerir)

        seg = QFrame()
        seg.setObjectName("segmented")
        seg_row = QHBoxLayout(seg)
        seg_row.setContentsMargins(3, 3, 3, 3)
        seg_row.setSpacing(2)
        self._kind_group = QButtonGroup(self)
        for kind, text in ((None, "Todo"), ("image", "Fotos"), ("video", "Videos")):
            b = QPushButton(text)
            b.setObjectName("segment")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAccessibleName(f"Mostrar: {text}")
            b.setProperty("kind", kind)
            # El filtro no se achica nunca (si no, los textos quedan cortados)
            b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self._kind_group.addButton(b)
            seg_row.addWidget(b)
            if kind is None:
                b.setChecked(True)
        # Filtra en la base: con páginas, filtrar lo ya traído dejaría casi vacío «Videos»
        self._kind_group.buttonClicked.connect(self._eligio_tipo)
        row.addWidget(seg)

        self.cmb_orden = QComboBox()
        self.cmb_orden.setAccessibleName("Orden de los resultados")
        self.cmb_orden.setToolTip("Orden de los resultados. La fecha es la de la toma, si "
                                  "la foto o el video la tiene.")
        for clave, texto in (("relevancia", "Más parecidos primero"),
                             ("nuevas", "Más recientes primero"),
                             ("viejas", "Más antiguos primero")):
            self.cmb_orden.addItem(texto, clave)
        self.cmb_orden.currentIndexChanged.connect(lambda _: self._do_search())
        self.txt_query.textChanged.connect(self._nombrar_orden)
        self._nombrar_orden()
        row.addWidget(self.cmb_orden)

        self.chk_pending = QPushButton("Sugerencias de la IA")
        self.chk_pending.setObjectName("toggle_chip")
        self.chk_pending.setCheckable(True)
        self.chk_pending.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_icon(self.chk_pending, "sparkles", 15, "text_2", active="ia")
        self.chk_pending.setToolTip("Mostrar solo los elementos con etiquetas sugeridas por la IA pendientes de revisión")
        self.chk_pending.toggled.connect(lambda _: self._do_search())
        row.addWidget(self.chk_pending)

        self.btn_detalles = QPushButton("Detalles")
        self.btn_detalles.setObjectName("toggle_panel")      # azul: el violeta es de la IA
        self.btn_detalles.setCheckable(True)
        self.btn_detalles.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_icon(self.btn_detalles, "panel-right", 15, "text_2", active="text")
        self.btn_detalles.setToolTip("Muestra u oculta, al costado, los detalles de lo "
                                     "seleccionado (tecla I)")
        self.btn_detalles.toggled.connect(self._ver_detalles)
        row.addWidget(self.btn_detalles)

        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("muted")
        self.lbl_count.setMinimumWidth(96)
        self.lbl_count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.lbl_count)
        layout.addLayout(row)

        # Lo escrito que no se entiende (una fecha mal escrita…): se dice por qué
        self.lbl_aviso_busqueda = QLabel("")
        self.lbl_aviso_busqueda.setObjectName("aviso_error")
        self.lbl_aviso_busqueda.setWordWrap(True)
        self.lbl_aviso_busqueda.setVisible(False)
        layout.addWidget(self.lbl_aviso_busqueda)

        # Se está trayendo la búsqueda del NAS. Su lugar queda reservado aunque
        # no se vea, para que la grilla no salte cada vez que aparece.
        self.barra_cargando = QProgressBar()
        self.barra_cargando.setRange(0, 0)              # sin porcentaje: solo "trabajando"
        self.barra_cargando.setTextVisible(False)
        self.barra_cargando.setAccessibleName("Buscando en el catálogo")
        pol = self.barra_cargando.sizePolicy()
        pol.setRetainSizeWhenHidden(True)
        self.barra_cargando.setSizePolicy(pol)
        self.barra_cargando.setVisible(False)
        layout.addWidget(self.barra_cargando)

        # Estado de la conexión con el catálogo (no se ve si está todo bien)
        self.conn_box = CajaConexion()
        self.conn_box.reintentar.connect(self.refresh)
        self.conn_box.set_estado(self._srv.estado if self._srv.estado != "ok" else "conectando",
                                 self._srv.texto)
        layout.addWidget(self.conn_box)

        # Un solo aviso con la causa, en vez de repetirla en cada miniatura
        self.offline_box = QFrame()
        self.offline_box.setObjectName("nonstandard")
        ob = QVBoxLayout(self.offline_box)
        ob.setContentsMargins(12, 8, 12, 8)
        ob.setSpacing(2)
        t = QLabel("No se encuentran los originales")
        t.setObjectName("nonstandard_title")
        ob.addWidget(t)
        self.lbl_offline = QLabel("")
        self.lbl_offline.setObjectName("nonstandard_text")
        self.lbl_offline.setWordWrap(True)
        ob.addWidget(self.lbl_offline)
        self.offline_box.setVisible(False)
        layout.addWidget(self.offline_box)

        self.grid = DragOutListWidget(thumb=_GRID_THUMB, etiquetas=True)
        self.grid.pedir_tira = self._pedir_tira
        self.grid.setAccessibleName("Resultados")
        self.grid.itemSelectionChanged.connect(self._on_selection_changed)
        self.grid.itemDoubleClicked.connect(self._open_item)
        self.grid.detalles.connect(self.btn_detalles.toggle)
        self.grid.currentItemChanged.connect(lambda *_: self._seguir_detalle())
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._menu_grilla)

        # Detalles de lo elegido, al costado de la grilla (tecla I). Queda
        # abierto o cerrado como lo deje el operador, con el ancho que le dio.
        self.detalles = PanelDetalles()
        self.detalles.cerrar.connect(lambda: self.btn_detalles.setChecked(False))
        self.detalles.ir_a.connect(self._ir_a_elemento)
        self.detalles.guardar.connect(self._guardar_detalle)
        self.detalles.set_editable(self._srv.estado == "ok")
        self.division = QSplitter(Qt.Orientation.Horizontal)
        self.division.setChildrenCollapsible(False)
        self.division.setHandleWidth(10)
        self.division.addWidget(self.grid)
        self.division.addWidget(self.detalles)
        self.division.setStretchFactor(0, 1)
        self.division.setStretchFactor(1, 0)
        self.division.splitterMoved.connect(lambda *_: self._guardar_ancho_detalles())
        self.detalles.setVisible(False)
        self._detalles_timer = QTimer(self)        # Ctrl+A en mil: se arma una vez
        self._detalles_timer.setSingleShot(True)
        self._detalles_timer.setInterval(40)
        self._detalles_timer.timeout.connect(self._actualizar_detalles)
        layout.addWidget(self.division, 1)
        self.btn_detalles.setChecked(_ajuste("buscar/detalles", False) in (True, "true"))

        # Tags de la selección, en un panel abajo
        panel = QFrame()
        panel.setObjectName("panel")
        p_lay = QVBoxLayout(panel)
        p_lay.setContentsMargins(14, 10, 14, 10)
        # Lo elegido en el catálogo se manda a Convertir sin buscarlo de nuevo
        acciones = QHBoxLayout()
        acciones.setSpacing(10)
        self.lbl_seleccion = QLabel("")
        self.lbl_seleccion.setObjectName("muted")
        acciones.addWidget(self.lbl_seleccion, 1)
        self.btn_persona = QPushButton("Agregar persona…")
        icons.set_icon(self.btn_persona, "user-plus", 15, "text_2")
        self.btn_persona.setToolTip("Usa las fotos seleccionadas para que el NAS reconozca a "
                                    "una persona (nueva o de la lista de Catalogar)")
        self.btn_persona.clicked.connect(self._mandar_a_persona)
        acciones.addWidget(self.btn_persona)
        self.btn_convertir = QPushButton("Enviar a Convertir")
        self.btn_convertir.setObjectName("btn_primary")
        icons.set_icon(self.btn_convertir, "wand-sparkles", 15, "on_image")
        self.btn_convertir.setToolTip("Agrega los elementos seleccionados a la grilla de Convertir (Ctrl+Enter)")
        self.btn_convertir.clicked.connect(self._mandar_a_convertir)
        acciones.addWidget(self.btn_convertir)
        p_lay.addLayout(acciones)
        self.tag_bar = TagBar(acciones_chip=[("Convertir en lugar", "lugar"),
                                             ("Convertir en evento", "evento")])
        self.tag_bar.tagApplied.connect(self._apply_tag_to_selection)
        self.tag_bar.tagRemoved.connect(self._remove_tag_from_selection)
        self.tag_bar.tagConfirmed.connect(self._confirm_tag_on_selection)
        self.tag_bar.accionChip.connect(self._convertir_etiqueta)
        p_lay.addWidget(self.tag_bar)
        # Lugar (uno por elemento) y eventos (varios): etiquetas de tipo especial
        especiales = QHBoxLayout()
        especiales.setSpacing(24)
        comun = [("Convertir en etiqueta común", "descriptivo")]
        self.lugar_bar = TagBar(
            titulo="Lugar", placeholder="Elegir el lugar…", vacio=None,
            ayuda="Dónde se tomó. Cada elemento tiene un solo lugar: elegir otro "
                  "reemplaza al anterior. Si el lugar no existe, se crea.",
            accesible="Poner el lugar de la selección", ancho_campo=(170, 240),
            acciones_chip=comun)
        self.lugar_bar.tagApplied.connect(lambda n: self._cambiar_especial("poner_lugar", n))
        self.lugar_bar.tagRemoved.connect(lambda n: self._cambiar_especial("quitar_de", n))
        self.lugar_bar.accionChip.connect(self._convertir_etiqueta)
        especiales.addWidget(self.lugar_bar, 1)
        self.evento_bar = TagBar(
            titulo="Eventos", placeholder="Agregar evento…", vacio=None,
            ayuda="De qué evento es (campamento, bautismo…). Puede tener varios. Si el "
                  "evento no existe, se crea.",
            accesible="Agregar un evento a la selección", ancho_campo=(170, 240),
            acciones_chip=comun)
        self.evento_bar.tagApplied.connect(lambda n: self._cambiar_especial("poner_evento", n))
        self.evento_bar.tagRemoved.connect(lambda n: self._cambiar_especial("quitar_de", n))
        self.evento_bar.accionChip.connect(self._convertir_etiqueta)
        especiales.addWidget(self.evento_bar, 1)
        p_lay.addLayout(especiales)
        layout.addWidget(panel)

    # ------------------------------------------------------------- datos
    def refresh(self):
        """Vuelve a buscar (p. ej. para ver lo recién catalogado)."""
        self._do_search()
        self._reload_known_tags()

    def _estado_catalogo(self, estado: str, texto: str):
        self.conn_box.set_estado(estado, texto)
        if estado == "error":
            self._fin_carga()
            self._results = []
            self.grid.clear()
            self.lbl_count.setText("")
            self.offline_box.setVisible(False)
            self.grid.set_empty_text("Mientras tanto, los archivos pueden abrirse desde la "
                                     "carpeta del NAS en el Explorador.")
            for barra in (self.tag_bar, self.lugar_bar, self.evento_bar):
                barra.set_enabled(False)
            self._actualizar_seleccion()
        self.detalles.set_editable(estado == "ok")

    def _reintentar_si_hace_falta(self):
        if self._srv.estado == "error" and self.isVisible():
            self.refresh()

    # ------------------------------------------------------------- en vivo
    def cambio_en_el_catalogo(self):
        """Llegó un aviso del NAS (algo se catalogó, otra PC etiquetó…). Se juntan
        los avisos seguidos y se pone al día sin que el operador haga nada."""
        # Un video sin tira puede tenerla ahora (el NAS la armó): se vuelve a preguntar
        self._tiras = {k: v for k, v in self._tiras.items() if v is not None}
        if not self.isVisible():
            self._desactualizada = True     # se pone al día cuando se vuelva a ver
            return
        if self._vivo.isActive():
            return
        espera = _VIVO_ESPERA_MS
        if self._vivo_ultima is not None:
            espera = max(espera, _VIVO_MINIMO_MS - self._vivo_ultima.elapsed())
        self._vivo.start(espera)

    def _ocupado(self) -> bool:
        """El operador está arrastrando, eligiendo con el mouse o con un menú
        abierto: rearmar la grilla en ese momento le movería todo."""
        return (QApplication.mouseButtons() != Qt.MouseButton.NoButton
                or QApplication.activePopupWidget() is not None
                or self.grid.state() == QAbstractItemView.State.DraggingState)

    def _actualizar_en_vivo(self):
        if not self.isVisible():
            self._desactualizada = True
            return
        if self._ocupado():
            self._vivo.start(1000)
            return
        self._vivo_ultima = QElapsedTimer()
        self._vivo_ultima.start()
        self._do_search(conservar=True)
        self._reload_known_tags()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._raices_timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self._raices_timer.start()
        if self._desactualizada:
            self._desactualizada = False
            self.cambio_en_el_catalogo()

    # ------------------------------------------------------------- NAS conectado
    def _revisar_raices(self):
        """¿Se conectó o se desconectó el NAS (o un disco) de lo que se ve?"""
        raices = list(getattr(self, "_raices", None) or {})
        if not raices or self._revisando_raices:
            return
        self._revisando_raices = True

        def preguntar():
            resultado = {r: _existe(r) for r in raices}
            try:
                self._raices_revisadas.emit(resultado)
            except RuntimeError:
                pass        # la pestaña se cerró mientras se preguntaba
        threading.Thread(target=preguntar, daemon=True, name="raices").start()

    def _raices_cambiaron(self, ahora: dict):
        self._revisando_raices = False
        antes = getattr(self, "_raices", None) or {}
        if all(antes.get(r) == ok for r, ok in ahora.items()):
            return
        self._raices = {**antes, **ahora}
        self._presentes = {m.path: self._raices.get(storage_root(m.path), False)
                           for m in getattr(self, "_results", [])}
        self._repintar_conservando()

    def _repintar_conservando(self):
        vista = self._vista()
        self.grid._stop_preview()
        self._restaurando = True
        try:
            self._apply_kind_filter()
        finally:
            self._restaurando = False
        self._volver_a(vista)
        self._on_selection_changed()

    def _vista(self) -> dict:
        """Lo que el operador tiene elegido y dónde está mirando."""
        sel = {it.data(MEDIA_ID_ROLE) for it in self.grid.selectedItems()}
        actual = self.grid.currentItem()
        arriba = self.grid.itemAt(QPoint(8, 8))
        return {"sel": sel,
                "actual": actual.data(MEDIA_ID_ROLE) if actual else None,
                "arriba": arriba.data(MEDIA_ID_ROLE) if arriba else None,
                "scroll": self.grid.verticalScrollBar().value()}

    def _volver_a(self, vista: dict):
        g = self.grid
        g.blockSignals(True)
        try:
            ancla = None
            for i in range(g.count()):
                it = g.item(i)
                mid = it.data(MEDIA_ID_ROLE)
                if mid == vista["actual"]:
                    g.setCurrentItem(it, g.selectionModel().SelectionFlag.NoUpdate)
                if mid in vista["sel"]:
                    it.setSelected(True)
                if mid == vista["arriba"]:
                    ancla = it
        finally:
            g.blockSignals(False)
        if ancla is not None:
            g.scrollToItem(ancla, QAbstractItemView.ScrollHint.PositionAtTop)
        else:
            g.verticalScrollBar().setValue(vista["scroll"])

    def _do_search(self, conservar: bool = False):
        if self._marcar_error(analizar(self.txt_query.text()).avisos):
            # Una fecha mal escrita no busca: la grilla queda como estaba
            self._fin_carga()
            return
        self._busqueda += 1
        n = self._busqueda
        query, pend, kind = self.txt_query.text(), self.chk_pending.isChecked(), self._kind()
        orden = self.cmb_orden.currentData()
        cache = self._cache
        # En vivo se vuelve a traer todo lo que ya se estaba viendo, no solo la 1.ª página
        cuantos = max(_PAGINA, len(getattr(self, "_results", []))) if conservar else _PAGINA
        self._trayendo_mas = False

        def buscar(repo):
            # Todo lo que puede tardar (base, NAS por SMB) pasa en el hilo del catálogo.
            results, total = repo.buscar(query, kind=kind, pending_only=pend, cuantos=cuantos,
                                         orden=orden)
            return _preparar(repo, cache, results) + (total,)

        if not conservar:
            self._cargando.start(_CARGANDO_MS)
        self._srv.pedir(buscar, ok=lambda r: self._mostrar_busqueda(n, *r, conservar=conservar),
                        error=lambda _e: self._fin_carga() if n == self._busqueda else None)

    def _nombrar_orden(self, *_):
        """Sin palabras sueltas, «parecidos» no dice nada: el orden es por nombre
        (los filtros como etiqueta:placa no ordenan)."""
        self.cmb_orden.setItemText(0, "Más parecidos primero"
                                   if analizar(self.txt_query.text()).palabras
                                   else "Por nombre")

    # ------------------------------------------------------------- búsqueda avanzada
    def _abrir_formulario(self):
        self.formulario.abrir_debajo_de(self.txt_query, self.txt_query.text())

    def _revisar_consulta(self, *_):
        """Con cada letra: los botones Todo / Fotos / Videos siguen al «tipo:»
        escrito, y si ya se corrigió lo que no se entendía, se saca el rojo. El
        rojo se pone recién al buscar (no mientras se escribe la fecha)."""
        c = analizar(self.txt_query.text())
        if not c.avisos:
            self._marcar_error([])
        tipo = c.tipo
        if tipo is not None and tipo != self._kind():
            for b in self._kind_group.buttons():
                if b.property("kind") == tipo:
                    b.setChecked(True)

    def _sugerir(self, *_):
        """Lo que se ofrece para la palabra que se está escribiendo."""
        cursor = self.txt_query.cursorPosition()
        desde, opciones = consulta.sugerencias(self.txt_query.text(), cursor,
                                               self._etiquetas_conocidas, self._lugares,
                                               self._eventos)
        if not opciones:
            self.completar.popup().hide()
            return
        self._desde_sugerencia = desde
        self._sugerencias.setStringList(opciones)
        caja = self.txt_query.cursorRect()
        ancho = self.txt_query.fontMetrics().horizontalAdvance(max(opciones, key=len))
        caja.setWidth(max(280, ancho + 48))
        self.completar.complete(caja)
        self.completar.popup().setCurrentIndex(self._sugerencias.index(0))

    def _usar_sugerencia(self, opcion: str):
        texto, cursor = self.txt_query.text(), self.txt_query.cursorPosition()
        resto = texto[cursor:]
        nuevo = texto[:self._desde_sugerencia] + opcion
        if not opcion.endswith(":") and not resto.startswith(" "):
            nuevo += " "
        self.txt_query.setText(nuevo + resto)
        self.txt_query.setCursorPosition(len(nuevo))
        if opcion.endswith(":"):                  # «etiqueta:» → ahora, las etiquetas
            QTimer.singleShot(0, self._sugerir)

    def _marcar_error(self, avisos: list[str]) -> bool:
        """Campo en rojo y la explicación debajo. True si hay algo mal."""
        self.lbl_aviso_busqueda.setText(" ".join(avisos))
        self.lbl_aviso_busqueda.setVisible(bool(avisos))
        if bool(self.txt_query.property("error")) != bool(avisos):
            self.txt_query.setProperty("error", bool(avisos))
            self.txt_query.style().unpolish(self.txt_query)
            self.txt_query.style().polish(self.txt_query)
        return bool(avisos)

    def _eligio_tipo(self, _boton):
        """Un botón Todo / Fotos / Videos manda: se saca el «tipo:» del texto."""
        texto = self.txt_query.text()
        sin = consulta.sin_tipo(texto)
        if sin != texto:
            self.txt_query.setText(sin)
        self._do_search()

    def _mostrar_cargando(self):
        self.barra_cargando.setVisible(True)
        self.lbl_count.setText("Buscando…")
        if self.grid.count() == 0:
            self.grid.set_empty_text("Buscando en el catálogo…")

    def _fin_carga(self):
        self._cargando.stop()
        self.barra_cargando.setVisible(False)

    def _kind(self) -> str | None:
        btn = self._kind_group.checkedButton()
        return btn.property("kind") if btn else None

    def _quizas_traer_mas(self):
        """Cerca del final de la grilla: la página siguiente, si hay."""
        sb = self.grid.verticalScrollBar()
        traidos = len(getattr(self, "_results", []))
        if (self._trayendo_mas or self._total is None or traidos >= self._total
                or sb.maximum() - sb.value() > _FALTA_PARA_EL_FINAL):
            return
        if self.txt_query.property("error"):
            return
        self._trayendo_mas = True
        n = self._busqueda
        query, pend, kind = self.txt_query.text(), self.chk_pending.isChecked(), self._kind()
        orden = self.cmb_orden.currentData()
        cache = self._cache

        def buscar(repo):
            results, total = repo.buscar(query, kind=kind, pending_only=pend,
                                         desde=traidos, cuantos=_PAGINA, orden=orden)
            return _preparar(repo, cache, results) + (total,)

        def fallo(_e):
            if n == self._busqueda:
                self._trayendo_mas = False
        self._srv.pedir(buscar, ok=lambda r: self._agregar_pagina(n, traidos, *r), error=fallo)

    def _agregar_pagina(self, n, desde, results, minis, faltan, presentes, raices, total):
        if n != self._busqueda or desde != len(self._results):
            return                      # cambió la búsqueda mientras tanto
        self._trayendo_mas = False
        if total is not None:
            self._total = total
        self._results += results
        self._minis.update(minis)
        self._bajando |= set(faltan)
        self._presentes.update(presentes)
        self._raices.update(raices)
        self._show_offline([m.path for m in self._results
                            if not self._presentes[m.path] or m.missing], self._raices)
        for m in results:
            self.grid.addItem(self._item(m, self._minis, self._presentes, self._raices,
                                         self._bajando))
        self._contar()
        if faltan:
            cache = self._cache
            self._srv.pedir(lambda repo: cache.bajar(repo, faltan),
                            ok=lambda nuevas: self._llegaron_miniaturas(n, nuevas))
        QTimer.singleShot(0, self._quizas_traer_mas)     # ventana alta: puede faltar más

    def _contar(self):
        n, total = self.grid.count(), self._total
        if total is not None and total > n:
            self.lbl_count.setText(f"{_miles(n)} de {_miles(total)}")
        else:
            self.lbl_count.setText("1 resultado" if n == 1 else f"{_miles(n)} resultados")

    def _mostrar_busqueda(self, n, results, minis, faltan, presentes, raices, total=None,
                          conservar: bool = False):
        if n != self._busqueda:
            return                      # llegó tarde: ya se escribió otra cosa
        self._fin_carga()
        self._total = len(results) if total is None else total
        if conservar and _iguales(getattr(self, "_results", None), results) \
                and presentes == getattr(self, "_presentes", None):
            # Cambió algo que no se ve en la grilla (p. ej. una etiqueta, el
            # texto de una foto o las notas): se pasa a lo ya cargado
            for antes, ahora in zip(self._results, results):
                antes.texto, antes.notas = ahora.texto, ahora.notas
                antes.lugar, antes.eventos = ahora.lugar, ahora.eventos
            self._contar()
            self._on_selection_changed()
            return
        vista = self._vista() if conservar else None
        self._results = results
        self._minis = minis
        self._bajando = set(faltan)
        self._presentes = presentes
        self._raices = raices
        if vista is not None:
            self._repintar_conservando()
        else:
            self._apply_kind_filter()
        if faltan:
            cache = self._cache
            self._srv.pedir(lambda repo: cache.bajar(repo, faltan),
                            ok=lambda nuevas: self._llegaron_miniaturas(n, nuevas))
        QTimer.singleShot(0, self._quizas_traer_mas)

    def _llegaron_miniaturas(self, n, nuevas: dict):
        if n != self._busqueda or not nuevas:
            return
        self._minis.update(nuevas)
        self._bajando -= nuevas.keys()
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            ruta = nuevas.get(it.data(MEDIA_ID_ROLE))
            if ruta:
                icon = _icono(ruta)
                if icon:
                    it.setIcon(icon)
                if it.data(STATE_ROLE) == "loading":
                    it.setData(STATE_ROLE, None)

    def _apply_kind_filter(self):
        btn = self._kind_group.checkedButton()
        kind = btn.property("kind") if btn else None
        results = [m for m in getattr(self, "_results", []) if kind is None or m.kind == kind]
        self._populate(results, getattr(self, "_minis", None),
                       getattr(self, "_presentes", None), getattr(self, "_raices", None),
                       getattr(self, "_bajando", None))

    def _item(self, m, minis: dict, present: dict, roots_ok: dict,
              bajando: set | None) -> QListWidgetItem:
        """La celda de un resultado: miniatura, nombre y si se puede usar."""
        item = QListWidgetItem(m.filename)
        item.setData(PATH_ROLE, m.path)
        item.setData(MEDIA_ID_ROLE, m.id)
        item.setData(KIND_ROLE, m.kind)
        item.setData(DURATION_ROLE, m.duration)
        _datos_de_ficha(item, m)
        if self._tiras.get(m.id):
            item.setData(STORYBOARD_ROLE, self._tiras[m.id])
        disponible = present[m.path] and not m.missing
        if not disponible:
            # No se puede arrastrar a ProPresenter: la miniatura va en gris.
            # Si falta el disco/NAS entero, el aviso de arriba lo explica una
            # vez. Si el disco está y el archivo no, la insignia va en la celda.
            item.setData(STATE_ROLE, "missing" if roots_ok[storage_root(m.path)]
                         else "offline")
        icon = _icono(minis.get(m.id))
        if icon:
            item.setIcon(icon)
        elif disponible and m.id in (bajando or ()):
            item.setData(STATE_ROLE, "loading")     # se está bajando de la base
        # Sin cartel al pasar el puntero: los detalles se ven con la tecla I.
        return item

    def _populate(self, results, minis: dict | None = None, present: dict | None = None,
                  raices: dict | None = None, bajando: set | None = None):
        """Llena la grilla. `minis` {id: miniatura local}; `present` {ruta: existe} y
        `raices` {raíz: conectada} vienen calculados del hilo del catálogo (si
        faltan, se calculan acá: solo en las pruebas)."""
        self.grid.clear()
        minis = minis or {}
        if present is None:
            present = availability([m.path for m in results])
        roots_ok: dict[str, bool] = dict(raices or {})
        for p in {storage_root(m.path) for m in results} - roots_ok.keys():
            roots_ok[p] = os.path.exists(p)
        self._show_offline([m.path for m in results
                            if not present[m.path] or m.missing], roots_ok)
        for m in results:
            self.grid.addItem(self._item(m, minis, present, roots_ok, bajando))
        self._contar()
        if self.txt_query.text().strip() or self.chk_pending.isChecked():
            filtro = self._kind_group.checkedButton()
            if filtro is not None and filtro.property("kind"):
                self.grid.set_empty_text("No hay nada que coincida con la búsqueda.\n"
                                         "Pruebe con otra palabra o elija «Todo» arriba.")
            else:
                self.grid.set_empty_text("No hay nada que coincida con la búsqueda.\n"
                                         "Pruebe con otra palabra o con parte del nombre.")
        elif self._kind_group.checkedButton() is not None and \
                self._kind_group.checkedButton().property("kind"):
            self.grid.set_empty_text("No hay nada de este tipo en el catálogo.\n"
                                     "Elija «Todo» arriba para ver el resto.")
        else:
            self.grid.set_empty_text("Todavía no hay nada en el catálogo.\n"
                                     "Vaya a Catalogar para agregar las carpetas del NAS.")
        self._on_selection_changed()

    def _open_item(self, item):
        """Doble clic: el video se ve en el reproductor de la PC (la foto, en su visor)."""
        path = item.data(PATH_ROLE)
        if path and not open_file(path):
            QMessageBox.information(
                self, "No se puede abrir",
                f"No se encuentra {os.path.basename(path)}.\n"
                "Verifique que el NAS o el disco donde está guardado estén conectados.")

    def _show_offline(self, missing: list[str], roots_ok: dict[str, bool]):
        if not missing:
            self.offline_box.setVisible(False)
            return
        roots = sorted({storage_root(p) for p in missing if not roots_ok[storage_root(p)]})
        n = len(missing)
        cuantos = "1 resultado" if n == 1 else f"{n} resultados"
        if roots:
            donde = " y ".join(friendly_root(r) for r in roots)
            verbo = "está" if n == 1 else "están"
            text = (f"{cuantos} {verbo} en {donde}, que no está conectado. Se muestran las "
                    "miniaturas, pero no pueden usarse en ProPresenter hasta que se conecte.")
        else:
            verbo = "ya no está" if n == 1 else "ya no están"
            text = (f"{cuantos} {verbo} en su carpeta: pueden haberse movido o borrado. "
                    "Elija uno y abra los detalles (tecla I) para ver su ubicación anterior.")
        self.lbl_offline.setText(text)
        self.offline_box.setVisible(True)

    # ------------------------------------------------------------- tags
    def _selected_ids(self) -> list[int]:
        ids = []
        for it in self.grid.selectedItems():
            mid = it.data(MEDIA_ID_ROLE)
            if mid is not None:
                ids.append(mid)
        return ids

    # ------------------------------------------------------------- a Convertir
    def _elegidos_disponibles(self) -> tuple[list[str], int]:
        """Rutas elegidas que se pueden convertir, y cuántas no están (disco o
        NAS desconectado, archivo movido)."""
        paths, faltan = [], 0
        for it in self.grid.selectedItems():
            p = it.data(PATH_ROLE)
            if not p:
                continue
            # Lo decidió la búsqueda (raíz conectada + lo que marcó el NAS): acá
            # no se pregunta al NAS, que desde el hilo de la interfaz la congela.
            if it.data(STATE_ROLE) in ("missing", "offline"):
                faltan += 1
            else:
                paths.append(p)
        return paths, faltan

    def _mandar_a_convertir(self):
        paths, faltan = self._elegidos_disponibles()
        if faltan:
            self.lbl_seleccion.setText(
                ("1 elemento no está disponible y no se envió" if faltan == 1 else
                 f"{faltan} elementos no están disponibles y no se enviaron")
                + ". Verifique que el disco o el NAS estén conectados.")
        if paths:
            self.enviarAConvertir.emit(paths)

    def _fotos_elegidas(self) -> tuple[list[str], int, int]:
        """Fotos elegidas que se pueden leer, cuántos videos hay (no sirven como
        referencia de un rostro) y cuántas fotos no están disponibles."""
        fotos, videos, faltan = [], 0, 0
        self._ids_elegidos = []
        for it in self.grid.selectedItems():
            p = it.data(PATH_ROLE)
            if not p:
                continue
            if it.data(KIND_ROLE) != "image":
                videos += 1
            elif it.data(STATE_ROLE) in ("missing", "offline"):
                faltan += 1
            else:
                fotos.append(p)
                self._ids_elegidos.append(it.data(MEDIA_ID_ROLE))
        return fotos, videos, faltan

    def _mandar_a_persona(self):
        fotos, videos, faltan = self._fotos_elegidas()
        avisos = []
        if videos:
            avisos.append("los videos no se usan para reconocer personas")
        if faltan:
            avisos.append("1 foto no está disponible" if faltan == 1 else
                          f"{faltan} fotos no están disponibles")
        if avisos:
            texto = "; ".join(avisos)
            self.lbl_seleccion.setText(texto[0].upper() + texto[1:] + " y no se enviaron.")
        if fotos:
            self.enviarAPersona.emit(fotos, list(self._ids_elegidos))

    def _menu_grilla(self, pos):
        from PyQt6.QtWidgets import QMenu
        if not self.grid.selectedItems():
            return
        menu = QMenu(self)
        accion = menu.addAction("Enviar a Convertir")
        accion.setEnabled(self.btn_convertir.isEnabled())
        persona = menu.addAction("Agregar persona…")
        persona.setEnabled(self.btn_persona.isEnabled())
        elegida = menu.exec(self.grid.viewport().mapToGlobal(pos))
        if elegida is accion:
            self._mandar_a_convertir()
        elif elegida is persona:
            self._mandar_a_persona()

    def _actualizar_seleccion(self):
        paths, faltan = self._elegidos_disponibles()
        n = len(self.grid.selectedItems())
        self.btn_convertir.setEnabled(bool(paths))
        self.btn_persona.setEnabled(bool(self._fotos_elegidas()[0]))
        if not n:
            self.lbl_seleccion.setText("Seleccione fotos o videos para enviarlos a Convertir, asignarles etiquetas "
                                       "o usarlos para reconocer a una persona.")
        else:
            txt = "1 seleccionado" if n == 1 else f"{n} seleccionados"
            if faltan:
                txt += " · 1 no está disponible" if faltan == 1 else f" · {faltan} no están disponibles"
            self.lbl_seleccion.setText(txt)

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._mandar_a_convertir()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------- detalles (I)
    def _ver_detalles(self, abierto: bool):
        self.detalles.setVisible(abierto)
        if abierto:
            ancho = int(_ajuste("buscar/detalles_ancho", _ANCHO_DETALLES) or _ANCHO_DETALLES)
            total = max(self.division.width(), ancho + 400)
            self.division.setSizes([total - ancho, ancho])
            self._actualizar_detalles()
        _guardar_ajuste("buscar/detalles", abierto)

    def _guardar_ancho_detalles(self):
        if self.btn_detalles.isChecked():
            _guardar_ajuste("buscar/detalles_ancho", self.division.sizes()[1])

    def _seguir_detalle(self):
        """Los detalles siguen a lo elegido (flechas, clic) y se ponen al día
        cuando cambia algo (una etiqueta, un aviso del NAS)."""
        if self.btn_detalles.isChecked() and not self._restaurando:
            self._detalles_timer.start()

    def _actualizar_detalles(self):
        if not self.btn_detalles.isChecked():
            return
        por_id = {m.id: m for m in getattr(self, "_results", [])}
        items = sorted(self.grid.selectedItems(), key=self.grid.row)   # orden de la grilla
        medios, estados = [], {}
        for it in items:
            m = por_id.get(it.data(MEDIA_ID_ROLE))
            if m is not None:
                medios.append(m)
                st = estado_de_item(it)
                if st:
                    estados[m.id] = st
        self.detalles.mostrar(medios, getattr(self, "_minis", None) or {}, estados)

    def _guardar_detalle(self, mid: int, campo: str, valor: str):
        """Texto de la foto o notas escritos en los detalles. Si no se puede
        guardar (NAS caído), se avisa en el panel y lo escrito queda ahí."""
        quien = socket.gethostname()
        if campo == "texto":
            hacer = lambda repo: repo.corregir_texto(mid, valor, quien)   # noqa: E731
        else:
            hacer = lambda repo: repo.guardar_notas(mid, valor, quien)    # noqa: E731

        def listo(_):
            for m in self._por_id([mid]):
                if campo == "texto":
                    m.texto = valor.strip() or None
                else:
                    m.notas = valor.strip()
            self.detalles.guardado(mid, campo, None)

        def fallo(e):
            self.detalles.guardado(mid, campo, texto_para(e))
            actual = self.detalles.media
            if actual is None or actual.id != mid:
                # Ya eligió otro: el aviso del panel no se vería y lo escrito se perdería
                nombre = next((m.filename for m in self._por_id([mid])), "un elemento")
                que = "el texto de la foto" if campo == "texto" else "las notas"
                self.lbl_seleccion.setText(
                    f"No se pudo guardar {que} de {nombre}. {texto_para(e)} "
                    "Vuelva a elegirlo y escríbalo de nuevo.")

        self._srv.pedir(hacer, ok=listo, error=fallo)

    def _ir_a_elemento(self, mid: int):
        """Clic en la lista de los detalles: se muestra en la grilla, sin cambiar lo elegido."""
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            if it.data(MEDIA_ID_ROLE) == mid:
                self.grid.setCurrentItem(it, self.grid.selectionModel().SelectionFlag.NoUpdate)
                self.grid.scrollToItem(it)
                self.grid.setFocus()
                return

    def _on_selection_changed(self):
        if self._restaurando:
            return                      # se está rearmando la grilla: se avisa al final
        self._seguir_detalle()
        self._actualizar_seleccion()
        ids = self._selected_ids()
        for barra in (self.tag_bar, self.lugar_bar, self.evento_bar):
            barra.set_enabled(bool(ids) and self._srv.estado == "ok")
        self._seleccion += 1
        n = self._seleccion
        if not ids:
            for barra in (self.tag_bar, self.lugar_bar, self.evento_bar):
                barra.set_chips([], 0)
            return
        # Buscar ya trajo las etiquetas de cada uno: se muestran al instante,
        # sin esperar al NAS (la consulta hacía fila detrás de las miniaturas).
        medios = self._por_id(ids)
        locales = _etiquetas_de(medios) if len(medios) == len(ids) else None
        if locales is not None:
            self.tag_bar.set_chips(locales[0], len(ids), locales[1])
            lugares, eventos = _especiales_de(medios)
            self.lugar_bar.set_chips(lugares, len(ids))
            self.evento_bar.set_chips(eventos, len(ids))
            return
        self._srv.pedir(lambda repo: (repo.tag_counts(ids), repo.pending_tag_counts(ids),
                                      repo.especiales_de(ids)),
                        ok=lambda r: self._mostrar_tags(n, len(ids), *r))

    def _por_id(self, ids: list[int]) -> list:
        """Los medios ya cargados en la grilla para estos ids (los que estén)."""
        cargados = {m.id: m for m in getattr(self, "_results", [])}
        return [cargados[i] for i in ids if i in cargados]

    def _mostrar_tags(self, n, cuantos, counts, pendientes, especiales=None):
        if n == self._seleccion:
            self.tag_bar.set_chips(counts, cuantos, pendientes)
            especiales = especiales or {}
            self.lugar_bar.set_chips(especiales.get("lugar", []), cuantos)
            self.evento_bar.set_chips(especiales.get("evento", []), cuantos)

    def _cambiar_tags(self, accion, name: str):
        """Pone, saca o confirma un tag en toda la selección. Si no se puede
        guardar (NAS caído), se avisa: no queda en cola para después."""
        ids = self._selected_ids()
        if not ids:
            return

        def hacer(repo):
            for mid in ids:
                getattr(repo, accion)(mid, name)

        def listo(_):
            _cambiar_locales(self._por_id(ids), accion, name)   # la barra, ya
            self._refrescar_celdas(ids)
            self._reload_known_tags()
            self._on_selection_changed()
            self._do_search(conservar=True)     # que la grilla muestre la etiqueta ya

        def fallo(e):
            self.lbl_seleccion.setText(_motivo(e, "No se pudo guardar la etiqueta. "))

        self._srv.pedir(hacer, ok=listo, error=fallo)

    def _refrescar_celdas(self, ids: list[int]):
        """Las celdas de esos elementos, con lo que ya se cambió en lo cargado (la
        nueva búsqueda puede volver igual y no rearmar la grilla)."""
        por_id = {m.id: m for m in self._por_id(ids)}
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            m = por_id.get(it.data(MEDIA_ID_ROLE))
            if m is not None:
                _datos_de_ficha(it, m)

    def _cambiar_especial(self, accion: str, name: str):
        """Pone el lugar, suma un evento o saca uno de los dos, en toda la selección."""
        ids = self._selected_ids()
        if not ids:
            return
        name = _ya_existe(name, self._lugares + self._eventos) or name   # con sus tildes

        def listo(_):
            _cambiar_especiales_locales(self._por_id(ids), accion, name)
            self._refrescar_celdas(ids)
            self._reload_known_tags()
            self._on_selection_changed()
            self._seguir_detalle()
            self._do_search(conservar=True)
            if accion != "quitar_de":
                self._ofrecer_a_hermanas(ids, name, accion)

        self._srv.pedir(lambda repo: getattr(repo, accion)(ids, name), ok=listo,
                        error=lambda e: self.lbl_seleccion.setText(
                            _motivo(e, "No se pudo guardar. ")))

    def _convertir_etiqueta(self, kind: str, name: str):
        """Clic derecho en un chip: la etiqueta pasa a lugar, a evento o a común,
        en todo el catálogo (no solo en lo elegido)."""
        que = {"lugar": "un lugar", "evento": "un evento",
               "descriptivo": "una etiqueta común"}[kind]
        if not confirmar(self, "Cambiar el tipo", f"¿Convertir «{name}» en {que}?",
                         "Se cambia en todos los elementos del catálogo que la tienen, "
                         "no solo en los seleccionados. Se puede volver atrás de la "
                         "misma forma.", "Convertir"):
            return

        def listo(_):
            self._reload_known_tags()
            self._do_search(conservar=True)

        self._srv.pedir(lambda repo: repo.cambiar_tipo(name, kind), ok=listo,
                        error=lambda e: self.lbl_seleccion.setText(
                            _motivo(e, "No se pudo convertir. ")))

    def _apply_tag_to_selection(self, name: str):
        ids = self._selected_ids()
        for lista, que, campo in ((self._lugares, "un lugar", "el campo Lugar"),
                                  (self._eventos, "un evento", "el campo Eventos")):
            ya = _ya_existe(name, lista)
            if ya:
                self.lbl_seleccion.setText(f"«{ya}» ya es {que}: póngalo desde {campo}.")
                return
        name = _ya_existe(name, self._etiquetas_conocidas) or name   # con sus tildes
        self._cambiar_tags("apply_tag", name)
        if ids:
            self._ofrecer_a_hermanas(ids, name)

    def _ofrecer_a_hermanas(self, ids: list[int], name: str, accion: str = "apply_tag"):
        """Después de etiquetar: si hay fotos y videos de la misma carpeta y del
        mismo día de toma sin esa etiqueta, se ofrece ponérsela (p. ej. el nombre
        de un evento). Se pregunta siempre: nunca se agrega sola."""
        def ok(hermanas):
            if not hermanas:
                return
            n = len(hermanas)
            cuales = "la otra foto o video" if n == 1 else f"las otras {n} fotos y videos"
            que = {"apply_tag": f"¿Agregar «{name}»",
                   "poner_lugar": f"¿Poner el lugar «{name}»",
                   "poner_evento": f"¿Agregar el evento «{name}»"}[accion]
            if not confirmar(self, "Lo mismo a los demás", f"{que} también a {cuales} "
                             "de esta carpeta?",
                             "Son de la misma carpeta y, si se sabe la fecha de la toma, del "
                             "mismo día. No se agrega a los que alguien se la quitó.",
                             "Agregar a 1 más" if n == 1 else f"Agregar a {n} más",
                             cancelar="Solo a lo elegido"):
                return

            def hacer(repo):
                if accion == "apply_tag":
                    for mid in hermanas:
                        repo.apply_tag(mid, name)
                else:
                    getattr(repo, accion)(hermanas, name)

            def listo(_):
                self.lbl_seleccion.setText(f"Se agregó «{name}» a {n} más." if n > 1 else
                                           f"Se agregó «{name}» a 1 más.")
                self._do_search(conservar=True)

            self._srv.pedir(hacer, ok=listo, error=lambda e: self.lbl_seleccion.setText(
                _motivo(e, "No se pudo guardar. ")))
        sin_lugar = accion == "poner_lugar"
        self._srv.pedir(lambda repo: repo.hermanas(ids, name, sin_lugar), ok=ok)

    def _remove_tag_from_selection(self, name: str):
        self._cambiar_tags("remove_tag", name)

    def _confirm_tag_on_selection(self, name: str):
        self._cambiar_tags("confirm_tag", name)

    # ------------------------------------------------------------- vista previa
    def _pedir_tira(self, item):
        """El puntero pasó por un video sin tira: se trae de la base (una vez) y
        queda en esta PC. Si el NAS todavía no la armó, no se muestra nada."""
        mid = item.data(MEDIA_ID_ROLE)
        if mid is None or mid in self._tiras:
            return
        self._tiras[mid] = None                  # pedida: no se vuelve a pedir
        cache = self._cache_tiras

        def ok(ruta):
            if not ruta:
                return
            self._tiras[mid] = ruta
            for i in range(self.grid.count()):
                it = self.grid.item(i)
                if it.data(MEDIA_ID_ROLE) == mid:
                    it.setData(STORYBOARD_ROLE, ruta)
                    self.grid.empezar_vista_previa(it)
                    break
        self._srv.pedir(lambda repo: cache.asegurar(repo, mid), ok=ok,
                        error=lambda _e: self._tiras.pop(mid, None))

    def _reload_known_tags(self):
        def ok(nombres):
            comunes = nombres["comunes"]
            self.tag_bar.set_known_tags(comunes)
            self.lugar_bar.set_known_tags(nombres["lugar"])
            self.evento_bar.set_known_tags(nombres["evento"])
            self.formulario.set_etiquetas(comunes, nombres["lugar"], nombres["evento"])
            self._etiquetas_conocidas = comunes
            self._lugares, self._eventos = nombres["lugar"], nombres["evento"]
        self._srv.pedir(lambda repo: repo.nombres_por_tipo(), ok=ok)


def _preparar(repo, cache, results) -> tuple:
    """Lo que necesita la grilla además de los resultados, en el hilo del catálogo:
    miniaturas locales, cuáles bajar, y qué raíces (NAS, discos) están conectadas."""
    minis, faltan = cache.locales(repo, [m.id for m in results])
    paths = [m.path for m in results]
    presentes = availability(paths)
    raices = {r: os.path.exists(r) for r in {storage_root(p) for p in paths}}
    return results, minis, faltan, presentes, raices


def _miles(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _etiquetas_de(medios: list):
    """Los chips de la barra con las etiquetas que trajo Buscar: [(nombre, en
    cuántos)] como `Repo.tag_counts` y {nombre: sin confirmar} como
    `Repo.pending_tag_counts`. None si falta alguno (se le pregunta al NAS)."""
    if not medios or any(m.etiquetas is None for m in medios):
        return None
    cuantos, pendientes = {}, {}
    for m in medios:
        for nombre, confirmada in m.etiquetas:
            cuantos[nombre] = cuantos.get(nombre, 0) + 1
            if not confirmada:
                pendientes[nombre] = pendientes.get(nombre, 0) + 1
    return sorted(cuantos.items(), key=lambda t: (-t[1], t[0])), pendientes


def _ya_existe(nombre: str, conocidos: list[str]) -> str | None:
    """El nombre ya existente que es el mismo con o sin tildes y mayúsculas
    («predicacion» → «predicación»), o None."""
    clave = consulta._simple(" ".join(nombre.split()))
    return next((c for c in conocidos if consulta._simple(c) == clave), None)


def _datos_de_ficha(item, m):
    """Lo que la celda muestra debajo de la miniatura (ver ThumbDelegate)."""
    item.setData(TAGS_ROLE, m.etiquetas or [])
    item.setData(LUGAR_ROLE, m.lugar)
    item.setData(EVENTOS_ROLE, list(m.eventos or []))
    f = m.tomada
    item.setData(FECHA_ROLE, f"{(f.astimezone() if f.tzinfo else f):%d/%m/%Y}" if f else None)


def _especiales_de(medios: list) -> tuple[list, list]:
    """([(lugar, en cuántos)], [(evento, en cuántos)]) de lo que trajo Buscar."""
    lugares, eventos = {}, {}
    for m in medios:
        if m.lugar:
            lugares[m.lugar] = lugares.get(m.lugar, 0) + 1
        for e in m.eventos or []:
            eventos[e] = eventos.get(e, 0) + 1
    orden = lambda d: sorted(d.items(), key=lambda t: (-t[1], t[0]))  # noqa: E731
    return orden(lugares), orden(eventos)


def _cambiar_especiales_locales(medios: list, accion: str, name: str):
    """Lo mismo que acaba de guardar el NAS, en lo ya cargado (ver _cambiar_locales)."""
    name = " ".join(name.strip().lower().split())
    for m in medios:
        if accion == "poner_lugar":
            m.lugar = name
        elif accion == "poner_evento":
            m.eventos = sorted(set(m.eventos or []) | {name})
        else:
            if m.lugar == name:
                m.lugar = None
            m.eventos = [e for e in m.eventos or [] if e != name]


def _motivo(e: BaseException, prefijo: str) -> str:
    """El mensaje para el operador: el de la base si es un error de uso (un nombre
    que ya es de otro tipo), o el prefijo y el de conexión."""
    from ...catalog.repo import NombreEnUso, NoSePuedeConvertir
    if isinstance(e, (NombreEnUso, NoSePuedeConvertir)):
        return str(e)
    return prefijo + texto_para(e)


def _cambiar_locales(medios: list, accion: str, name: str):
    """Lo mismo que acaba de guardar el NAS, en las etiquetas ya cargadas, para
    que la barra cambie sin esperar a la nueva búsqueda (que después lo confirma)."""
    name = " ".join(name.strip().lower().split())          # Repo.normalize_tag
    for m in medios:
        if m.etiquetas is None:
            continue
        otras = [e for e in m.etiquetas if e[0] != name]
        if accion == "apply_tag" or (accion == "confirm_tag" and len(otras) < len(m.etiquetas)):
            otras.append((name, True))
        m.etiquetas = sorted(otras, key=lambda e: (not e[1], e[0]))


def _iguales(antes, ahora) -> bool:
    """Misma grilla: mismos elementos, en el mismo orden y en el mismo estado."""
    if antes is None:
        return False
    clave = lambda m: (m.id, m.path, m.missing_since, m.quality_flag, m.width, m.height,  # noqa: E731
                       tuple(m.etiquetas or ()), m.lugar, tuple(m.eventos or ()), m.tomada)
    return [clave(m) for m in antes] == [clave(m) for m in ahora]


def _icono(ruta: str | None) -> QIcon | None:
    if not ruta:
        return None
    px = QPixmap(ruta)
    return None if px.isNull() else QIcon(px)


def _ajuste(clave: str, defecto=None):
    return QSettings().value(clave, defecto)


def _guardar_ajuste(clave: str, valor):
    QSettings().setValue(clave, valor)
