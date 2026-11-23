"""Pestaña 'Catalogar'.

El catalogado y el análisis con IA corren en el NAS (convertir3x4/catalogador.py,
docs/catalogado-en-el-nas.md): todas las noches y cuando alguien lo pide desde
acá. Esta pestaña solo pide («Catalogar», o por partes con su flecha ▾) y
muestra lo que el NAS informa en la base. Mientras se ve, consulta cada 3 s.

También administra la lista de personas reconocibles: esta PC sube las fotos de
referencia (achicadas) y el NAS calcula sus firmas. La PC no usa modelos de IA.
"""

import datetime
import html
import os
import socket

from PyQt6.QtCore import QEvent, Qt, QSettings, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QFrame, QProgressBar, QListWidget,
    QListWidgetItem, QInputDialog, QScrollArea,
)

from ...appdata import abrir_repo, catalog_ref_dir, config_catalogo
from ...catalog import rutas
from ...catalog.db import CatalogoNoConfigurado
from ...catalog import referencias
from ...catalog.thumbs import foto_de_referencia
from ...workers import EngineWorker
from ..servicio_catalogo import servicio, texto_para
from ..widgets.adaptable import Adaptable, vaciar
from ..widgets.caja_conexion import CajaConexion
from ..widgets.dialogos import confirmar
from .. import icons
from ..style import T

_PERSON_ID_ROLE = Qt.ItemDataRole.UserRole
_PERSON_NAME_ROLE = Qt.ItemDataRole.UserRole + 1
_IMAGE_FILTER = "Imágenes (*.jpg *.jpeg *.png *.webp *.bmp)"
_CONTENT_MAX_W = 760
_PAGINA_MAX_W = 1600       # personas y su detalle usan más ancho que el resto
_LISTA_W = 340             # la lista de personas, lado a lado con el detalle
_DETALLE_MIN_W = 480       # lado a lado solo si al detalle le quedan estos px
# Tono de cada juicio -> color de la paleta activa (como en la Grabadora)
_TONOS = {"activo": "accent_text", "ok": "ok", "aviso": "warn", "error": "danger",
          "neutro": "text_3"}
_SECTION_GAP = 22
_POLL_MS = 3000
_MUCHAS_REFERENCIAS = 10   # más que esto, se pregunta antes de enviar
SIN_LATIDO = 60      # segundos: más que esto y el NAS no está catalogando
_RESPONSABLE = "Comuníquese con el responsable de la aplicación."


def _tono(tono: str) -> str:
    return getattr(T, _TONOS.get(tono, "text_3"))


def _n(k, uno, varios):
    return uno if k == 1 else f"{k} {varios}"


def texto_persona(p: dict) -> str:
    """Una persona de la lista, con lo que el NAS hizo con sus fotos."""
    n = p["listas"]
    texto = f"{p['name']} — " + ("1 foto" if n == 1 else f"{n} fotos")
    if p["pendientes"]:
        texto += " · " + ("1 en proceso en el NAS" if p["pendientes"] == 1 else
                          f"{p['pendientes']} en proceso en el NAS")
    k = len(p["sin_cara"])
    if k:
        texto += " · " + ("1 sin rostro visible" if k == 1 else f"{k} sin rostro visible")
    return texto


def resumen_pasada(resumen: dict | None) -> str:
    """Lo que hizo una pasada del NAS, en una o dos frases."""
    st = (resumen or {}).get("catalogado") or {}
    g = lambda k: st.get(k) or 0          # noqa: E731
    partes = []
    if g("added"):
        partes.append(_n(g("added"), "1 nuevo", "nuevos"))
    if g("updated"):
        partes.append(_n(g("updated"), "1 actualizado", "actualizados"))
    if g("moved"):
        partes.append(_n(g("moved"), "1 cambiado de carpeta", "cambiados de carpeta"))
    texto = (" y ".join(partes) if partes else "No había elementos nuevos") + "."
    texto = texto[0].upper() + texto[1:]
    if g("duplicates"):
        texto += f" {_n(g('duplicates'), '1 está repetido', 'están repetidos')}."
    if g("corrupt"):
        texto += (" 1 está dañado y no se pudo abrir." if g("corrupt") == 1 else
                  f" {g('corrupt')} están dañados y no se pudieron abrir.")
    if g("en_negro"):
        texto += (" 1 está en negro y no se etiqueta." if g("en_negro") == 1 else
                  f" {g('en_negro')} están en negro y no se etiquetan.")
    if g("missing"):
        texto += (" 1 ya no está en su carpeta." if g("missing") == 1 else
                  f" {g('missing')} ya no están en su carpeta.")
        texto += " Siguen en el catálogo, con sus etiquetas, por si aparecen en otra."
    if st.get("inexistentes"):
        texto += (f" La carpeta {_lista(st['inexistentes'])} no existe en el NAS."
                  if len(st["inexistentes"]) == 1 else
                  f" Las carpetas {_lista(st['inexistentes'])} no existen en el NAS.")
    if st.get("incompletas"):
        texto += (f" {_lista(st['incompletas'])} no se pudo leer completa; se revisa de "
                  "nuevo la próxima vez.")
    ia = (resumen or {}).get("ia") or {}
    sugeridas = (ia.get("scene_tags") or 0) + (ia.get("people_tags") or 0)
    if sugeridas:
        texto += (" La IA sugirió 1 etiqueta para revisar en Buscar." if sugeridas == 1 else
                  f" La IA sugirió {sugeridas} etiquetas para revisar en Buscar.")
    return texto


def _cuando(t: datetime.datetime | None, ahora: datetime.datetime | None = None) -> str:
    """«hoy a las 02:10», «ayer a las 23:40», «el 19/11 a las 02:10»."""
    if t is None:
        return ""
    t = t.astimezone()
    ahora = (ahora or datetime.datetime.now().astimezone()).astimezone()
    hora = t.strftime("%H:%M")
    dias = (ahora.date() - t.date()).days
    if dias == 0:
        return f"hoy a las {hora}"
    if dias == 1:
        return f"ayer a las {hora}"
    return f"el {t.strftime('%d/%m')} a las {hora}"


def texto_estado(est: dict | None, pedidos: list, ahora=None) -> tuple[str, bool]:
    """(texto para el operador, ¿el NAS está trabajando ahora?)."""
    if est is None:
        return "", False
    sin_latido = est.get("segundos_sin_latido")
    fase = est.get("fase")
    if sin_latido is None:
        return ("El NAS todavía no está preparado para catalogar. " + _RESPONSABLE), False
    if fase == "detenido":
        return ("El NAS dejó de catalogar porque tiene una versión anterior de la "
                "aplicación. " + _RESPONSABLE), False
    if sin_latido > SIN_LATIDO:
        texto = ("El NAS no está catalogando: la última señal fue "
                 f"{_cuando(est.get('latido'), ahora)}. " + _RESPONSABLE)
        if pedidos:
            texto += " Su pedido queda en espera."
        return texto, False
    hechos, total = est.get("hechos"), est.get("total")
    avance = f": {hechos} de {total}" if total else ""
    if fase == "catalogando":
        return f"El NAS está revisando las carpetas{avance}.", True
    if fase == "preparando_ia":
        return "El NAS está preparando el análisis con IA.", True
    if fase == "analizando":
        return f"El NAS está analizando con IA{avance}.", True
    if fase == "leyendo_texto":
        return f"El NAS está leyendo el texto de las fotos{avance}.", True
    if fase == "esperando_copia":
        return ("El NAS está copiando un disco de la grabadora. Va a catalogar cuando "
                "termine la copia."), False
    if pedidos:
        return "El NAS va a empezar a catalogar en unos segundos.", True
    if est.get("ultima_fin") is None:
        return "El NAS todavía no catalogó las carpetas.", False
    cuando = _cuando(est.get("ultima_fin"), ahora)
    if est.get("ultima_error"):
        return (f"La última revisión, {cuando}, no pudo terminar. " + _RESPONSABLE), False
    return f"Última revisión: {cuando}. " + resumen_pasada(est.get("ultima_resumen")), False


def _lista(carpetas) -> str:
    nombres = [f"«{c}»" for c in carpetas]
    return nombres[0] if len(nombres) == 1 else ", ".join(nombres[:-1]) + " y " + nombres[-1]


class MotorTab(QWidget):
    catalogChanged = pyqtSignal()

    def __init__(self, parent=None, srv=None):
        super().__init__(parent)
        self.settings = QSettings("CDLM", "Convertir3x4")
        self._worker: EngineWorker | None = None
        self._on_done_cb = None
        self._srv = srv or servicio()
        self._carpetas: tuple[str, ...] = ()
        self._raiz = ""
        self._ultima_fin = None
        self._trabajando = False
        self._pedidos: list = []
        self._consultando = False
        self._personas_en_proceso = False   # hay fotos que el NAS todavía no procesó
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self.refresh)
        self._srv.estado_cambiado.connect(self._estado_catalogo)
        self.refresh()
        self._refresh_people()

    # ------------------------------------------------------------- UI
    def _build_ui(self):
        # La pestaña entera se desplaza (como la Grabadora). Lo del catálogo va en
        # una columna de ancho legible, alineada a la izquierda (como una página de
        # ajustes); las personas y su detalle usan el ancho de la página.
        afuera = QVBoxLayout(self)
        afuera.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        afuera.addWidget(self.scroll)
        lienzo = QWidget()
        self.scroll.setWidget(lienzo)
        fila_lienzo = QHBoxLayout(lienzo)
        fila_lienzo.setContentsMargins(32, 28, 32, 24)
        pagina = QWidget()
        pagina.setMaximumWidth(_PAGINA_MAX_W)
        fila_lienzo.addWidget(pagina, 1)
        fila_lienzo.addStretch(0)
        pag = QVBoxLayout(pagina)
        pag.setContentsMargins(0, 0, 0, 0)
        pag.setSpacing(10)
        fila_col = QHBoxLayout()
        content = QWidget()
        content.setMaximumWidth(_CONTENT_MAX_W)
        fila_col.addWidget(content, 1)
        fila_col.addStretch(0)
        pag.addLayout(fila_col)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        cab = QHBoxLayout()
        cab.addWidget(self._title("Catálogo de fotos y videos"))
        cab.addStretch()
        # Lo que casi nunca se usa (limpiar, sacar una carpeta) va escondido en el ⚙
        self.btn_avanzado = QPushButton()
        self.btn_avanzado.setObjectName("btn_icon")
        icons.set_icon(self.btn_avanzado, "settings", 16, "text_2")
        self.btn_avanzado.setFixedSize(30, 30)
        self.btn_avanzado.setToolTip("Opciones avanzadas del catálogo")
        self.btn_avanzado.setAccessibleName("Opciones avanzadas del catálogo")
        self.btn_avanzado.clicked.connect(self._abrir_avanzado)
        cab.addWidget(self.btn_avanzado)
        layout.addLayout(cab)
        self.conn_box = CajaConexion()
        self.conn_box.reintentar.connect(self._reintentar)
        self.conn_box.set_estado(self._srv.estado if self._srv.estado != "ok" else "conectando",
                                 self._srv.texto)
        layout.addWidget(self.conn_box)
        layout.addWidget(self._text(
            "El NAS revisa todas las noches las carpetas que figuran abajo y agrega al "
            "catálogo las fotos y videos nuevos, para encontrarlos luego en Buscar. También "
            "sugiere etiquetas (bautismo, alabanza, placa, flyer…) y reconoce a las personas "
            "de la lista; las sugerencias se confirman en Buscar. Si necesita antes algo "
            "recién copiado, use «Catalogar»; con su flecha puede elegir una sola carpeta "
            "o qué hacer."))
        self.lbl_carpetas = QLabel()
        self.lbl_carpetas.setWordWrap(True)
        self.lbl_carpetas.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.lbl_carpetas)

        from ..widgets.catalogar_por_partes import BotonCatalogar
        self.boton_catalogar = BotonCatalogar()
        self.boton_catalogar.todo.connect(self._pedir_todo)
        self.boton_catalogar.partes.connect(self._abrir_partes)
        self.btn_run = self.boton_catalogar.principal
        layout.addLayout(self._left(self.boton_catalogar))

        # Lo que informa el NAS, pegado a la acción que lo dispara
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setVisible(False)   # sin mensaje no ocupa lugar
        layout.addWidget(self.lbl_status)
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("muted")
        layout.addWidget(self.lbl_count)

        # --- Personas
        layout.addSpacing(_SECTION_GAP)
        layout.addWidget(self._title("Personas reconocibles"))
        layout.addWidget(self._text(
            "Solo se reconoce a las personas de esta lista (pastores, invitados y otras "
            "personas relevantes). Los demás rostros se ignoran y no se guardan. "
            "Cargue de 3 a 5 fotos por persona en las que el rostro se vea con claridad. "
            "También puede elegirlas en Buscar, con «Agregar persona»."))
        layout.addStretch()

        # Lista y detalle de la persona elegida: lado a lado si hay ancho; si no,
        # el detalle debajo. Lo decide el ancho de la ventana (ver _decidir_personas).
        self.personas = Adaptable(_LISTA_W + 16 + _DETALLE_MIN_W, espacio=16,
                                  por_su_ancho=False)
        self.personas.cambio.connect(self._acomodar_personas)
        lado_lista = QWidget()
        self.lado_lista = lado_lista
        ll = QVBoxLayout(lado_lista)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(10)
        self.lst_people = QListWidget()
        self.lst_people.setObjectName("people_list")
        self.lst_people.setAccessibleName("Personas reconocibles")
        self.lst_people.setMinimumHeight(150)
        self.lst_people.itemSelectionChanged.connect(self._on_person_sel)
        ll.addWidget(self.lst_people, 1)
        prow = QHBoxLayout()
        prow.setSpacing(8)
        self.btn_add_person = QPushButton("Agregar persona…")
        self.btn_add_person.clicked.connect(self._add_person)
        self.btn_add_photos = QPushButton("Agregar fotos de la persona…")
        self.btn_add_photos.clicked.connect(self._add_photos)
        self.btn_del_person = QPushButton("Quitar de la lista")
        self.btn_del_person.clicked.connect(self._delete_person)
        # Los botones, uno debajo del otro: la lista es angosta al lado del detalle
        botones = QVBoxLayout()
        botones.setSpacing(6)
        for b in (self.btn_add_person, self.btn_add_photos, self.btn_del_person):
            botones.addLayout(self._left(b))
        ll.addLayout(botones)
        self.lbl_people_status = QLabel("")
        self.lbl_people_status.setWordWrap(True)
        self.lbl_people_status.setVisible(False)
        ll.addWidget(self.lbl_people_status)
        self.personas.agregar(lado_lista)

        self.lado_detalle = QWidget()
        ld = QVBoxLayout(self.lado_detalle)
        ld.setContentsMargins(0, 0, 0, 0)
        self.lbl_sin_persona = QLabel("Elija una persona de la lista para ver si sus fotos "
                                      "de referencia sirven para reconocerla.")
        self.lbl_sin_persona.setObjectName("muted")
        self.lbl_sin_persona.setWordWrap(True)
        self.lbl_sin_persona.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ld.addWidget(self.lbl_sin_persona, 1)
        self.panel_persona = self._armar_panel_persona()
        ld.addWidget(self.panel_persona)
        ld.addStretch(1)
        self.personas.agregar(self.lado_detalle, 1)
        pag.addWidget(self.personas)
        pag.addStretch(1)
        self.scroll.viewport().installEventFilter(self)
        self._acomodar_personas(self.personas.lado_a_lado)
        self._update_buttons()

    # ------------------------------------------------------------- detalle de la persona
    def _armar_panel_persona(self) -> QFrame:
        """La persona elegida: si sus fotos sirven (y qué hacer si no), cuántas
        fotos del catálogo tienen su nombre, y cada foto de referencia con el
        rostro que se tomó y lo que se vio en ella."""
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setAccessibleName("Detalle de la persona elegida")
        pc = QVBoxLayout(panel)
        pc.setContentsMargins(18, 14, 18, 16)
        pc.setSpacing(12)
        cabeza = QVBoxLayout()
        cabeza.setSpacing(2)
        self.lbl_persona_nombre = QLabel("")
        self.lbl_persona_nombre.setObjectName("section_title")
        cabeza.addWidget(self.lbl_persona_nombre)
        self.lbl_persona_estado = QLabel("")
        self.lbl_persona_estado.setWordWrap(True)
        cabeza.addWidget(self.lbl_persona_estado)
        pc.addLayout(cabeza)

        # Mientras se trae la persona del NAS: arriba, donde se mira al hacer clic
        self.barra_persona = QProgressBar()
        self.barra_persona.setRange(0, 0)              # sin porcentaje: solo "trabajando"
        self.barra_persona.setTextVisible(False)
        self.barra_persona.setAccessibleName("Trayendo los datos de la persona")
        self.barra_persona.setVisible(False)
        pc.addWidget(self.barra_persona)
        self.lbl_persona_cargando = QLabel("Trayendo los datos de la persona…")
        self.lbl_persona_cargando.setWordWrap(True)
        self.lbl_persona_cargando.setVisible(False)
        pc.addWidget(self.lbl_persona_cargando)

        # Cómo está y qué hacer. Si hay un problema, en un recuadro que se note.
        self.caja_persona = QFrame()
        cj = QVBoxLayout(self.caja_persona)
        cj.setContentsMargins(0, 0, 0, 0)
        cj.setSpacing(4)
        self.lbl_persona_que = QLabel("")
        self.lbl_persona_que.setWordWrap(True)
        cj.addWidget(self.lbl_persona_que)
        self.lbl_persona_hacer = QLabel("")
        self.lbl_persona_hacer.setWordWrap(True)
        cj.addWidget(self.lbl_persona_hacer)
        pc.addWidget(self.caja_persona)
        self.lbl_persona_catalogo = QLabel("")
        self.lbl_persona_catalogo.setObjectName("muted")
        self.lbl_persona_catalogo.setWordWrap(True)
        pc.addWidget(self.lbl_persona_catalogo)

        t = QLabel("Fotos de referencia")
        t.setObjectName("subtitulo")
        pc.addWidget(t)
        self.lbl_fotos_ayuda = QLabel("El recuadro amarillo marca el rostro que se usa para "
                                      "reconocer a la persona.")
        self.lbl_fotos_ayuda.setObjectName("muted")
        self.lbl_fotos_ayuda.setWordWrap(True)
        pc.addWidget(self.lbl_fotos_ayuda)
        self.lista_fotos = QVBoxLayout()
        self.lista_fotos.setSpacing(10)
        pc.addLayout(self.lista_fotos)
        panel.setVisible(False)
        self._celdas_foto: dict[int, tuple[QLabel, QLabel]] = {}
        self._cache_refs = None
        self._revision_clave = None
        self._persona_detalle: int | None = None
        self._fila_elegida: dict | None = None
        return panel

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Type.Resize and obj is self.scroll.viewport()
                and event.size().width() != event.oldSize().width()):
            self._decidir_personas()
        return super().eventFilter(obj, event)

    def _decidir_personas(self):
        margenes = self.scroll.widget().layout().contentsMargins()
        disponible = min(self.scroll.viewport().width() - margenes.left() - margenes.right(),
                         _PAGINA_MAX_W)
        self.personas.ajustar(disponible)

    def _acomodar_personas(self, lado_a_lado: bool):
        """Lado a lado: la lista con su ancho fijo y el detalle el resto. Uno
        abajo del otro: la lista con el ancho de la columna y alto para unas 6."""
        if lado_a_lado:
            self.lado_lista.setFixedWidth(_LISTA_W)
        else:
            self.lado_lista.setMinimumWidth(0)
            self.lado_lista.setMaximumWidth(_CONTENT_MAX_W)
        self.lst_people.setMaximumHeight(16777215 if lado_a_lado else 180)
        self._acomodar_lado_detalle()

    def _acomodar_lado_detalle(self):
        hay = not self.panel_persona.isHidden()
        self.lbl_sin_persona.setVisible(not hay and self.personas.lado_a_lado)
        self.lado_detalle.setVisible(hay or self.personas.lado_a_lado)

    def _on_person_sel(self):
        self._update_buttons()
        sel = self._selected_person()
        if sel is None:
            self._persona_detalle = None
            self.panel_persona.setVisible(False)
            self._acomodar_lado_detalle()
            return
        if sel[0] != self._persona_detalle:
            self._persona_detalle = sel[0]
            self._revision_clave = None
            self.lbl_persona_nombre.setText(sel[1])
            self.lbl_persona_estado.setText("")
            self.caja_persona.setVisible(False)
            self.lbl_persona_catalogo.setText("")
            vaciar(self.lista_fotos)
            self._celdas_foto = {}
            self.lbl_fotos_ayuda.setVisible(False)
            self.lbl_persona_cargando.setText("Trayendo los datos de la persona…")
            self.lbl_persona_cargando.setVisible(True)
            self.barra_persona.setVisible(True)
            self.panel_persona.setVisible(True)
            self._acomodar_lado_detalle()
        self._pedir_detalle()

    def _cache(self):
        if self._cache_refs is None:
            from ...catalog.cache_referencias import CacheReferencias
            self._cache_refs = CacheReferencias(catalog_ref_dir())
        return self._cache_refs

    def _pedir_detalle(self):
        """Lee la persona y juzga sus fotos fuera del hilo de la interfaz. En dos
        pasos: primero los datos sin las fotos (llegan enseguida) con las
        imágenes que ya están en la caché de esta PC; después, las que faltan."""
        pid = self._persona_detalle
        if pid is None:
            return
        cache = self._cache()

        def leer(repo):
            d = repo.detalle_persona(pid, fotos=False)
            if d is None:
                return None, []
            rev = referencias.revisar(d, imagenes=False)
            refs = {r["id"]: r for r in d["referencias"]}
            faltan = []
            for f in rev.fotos:
                ref = refs[f.id]
                if not ref["tiene_foto"]:
                    continue
                guardada = cache.leer(ref)
                if guardada:
                    f.miniatura, f.cara = guardada
                else:
                    faltan.append(ref)
            return rev, faltan

        def ok(res):
            rev, faltan = res
            if pid != self._persona_detalle:
                return
            self._mostrar_revision(rev, pendientes={r["id"] for r in faltan})
            if faltan:
                self._pedir_imagenes(pid, faltan)

        def error(e):
            if pid == self._persona_detalle:
                self.barra_persona.setVisible(False)
                self.lbl_persona_cargando.setText(texto_para(e))
                self.lbl_persona_cargando.setVisible(True)
        self._srv.pedir(leer, ok=ok, error=error)

    def _pedir_imagenes(self, pid: int, refs: list[dict]):
        """Trae del NAS las fotos que esta PC todavía no tiene y arma sus imágenes."""
        cache = self._cache()

        def leer(repo):
            fotos = repo.fotos_de_referencia([r["id"] for r in refs])
            out = {}
            for r in refs:
                if r["id"] in fotos:
                    mini, cara = referencias.imagenes_de({**r, "foto": fotos[r["id"]]})
                    cache.guardar(r, mini, cara)
                    out[r["id"]] = (mini, cara)
            return out

        def ok(imagenes):
            if pid != self._persona_detalle:
                return
            for ref_id, celdas in self._celdas_foto.items():
                if ref_id in imagenes:
                    for lbl, datos in zip(celdas, imagenes[ref_id]):
                        _poner_imagen(lbl, datos)
                elif any(lbl.pixmap().isNull() and lbl.text() for lbl in celdas):
                    for lbl in celdas:
                        lbl.setText("")            # ya no viene: sin imagen
            self.lbl_fotos_ayuda.setVisible(
                any(c[1].pixmap() is not None and not c[1].pixmap().isNull()
                    for c in self._celdas_foto.values()))

        def error(_e):
            for celdas in self._celdas_foto.values():
                for lbl in celdas:
                    if lbl.text():
                        lbl.setText("")
        self._srv.pedir(leer, ok=ok, error=error)

    def _mostrar_revision(self, rev, pendientes: set[int] = frozenset()):
        self.barra_persona.setVisible(False)
        if rev is None:                     # la quitaron desde la otra PC
            self.panel_persona.setVisible(False)
            self._acomodar_lado_detalle()
            return
        clave = (rev.juicio.tono, rev.juicio.titulo, tuple(rev.juicio.detalle), rev.hacer,
                 rev.confirmadas, rev.sugeridas,
                 tuple((f.id, f.juicio.tono, tuple(f.juicio.detalle)) for f in rev.fotos))
        if clave == self._revision_clave:   # nada cambió: no se redibuja (no parpadea)
            return
        self._revision_clave = clave
        self.lbl_persona_cargando.setVisible(False)
        self.lbl_persona_nombre.setText(rev.nombre)
        self.lbl_persona_estado.setText(rev.juicio.titulo)
        self.lbl_persona_estado.setStyleSheet(
            "" if rev.juicio.tono == "ok" else f"color: {_tono(rev.juicio.tono)}; font-weight: 600;")
        que = " ".join(rev.juicio.detalle)
        self.lbl_persona_que.setText(que)
        self.lbl_persona_que.setVisible(bool(que))
        self.lbl_persona_hacer.setText(f"<b>Qué hacer:</b> {html.escape(rev.hacer)}"
                                       if rev.hacer else "")
        self.lbl_persona_hacer.setVisible(bool(rev.hacer))
        problema = rev.juicio.tono in ("aviso", "error")
        self.caja_persona.setObjectName("nonstandard" if problema else "")
        self.caja_persona.layout().setContentsMargins(
            *((12, 10, 12, 10) if problema else (0, 0, 0, 0)))
        self.caja_persona.style().unpolish(self.caja_persona)
        self.caja_persona.style().polish(self.caja_persona)
        self.caja_persona.setVisible(bool(que or rev.hacer))
        self.lbl_persona_catalogo.setText(referencias.texto_catalogo(rev))
        vaciar(self.lista_fotos)
        self._celdas_foto = {}
        for f in rev.fotos:
            self.lista_fotos.addWidget(self._fila_foto(f, f.id in pendientes))
        self.lbl_fotos_ayuda.setVisible(any(f.cara for f in rev.fotos))

    def _fila_foto(self, f, pendiente: bool = False) -> QFrame:
        """Una foto de referencia: la foto con el rostro marcado, el rostro, qué
        se vio y el botón para quitarla."""
        fila = QFrame()
        fila.setAccessibleName(f"Foto de referencia {f.archivo}")
        h = QHBoxLayout(fila)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)
        celdas = []
        for datos, lado in ((f.miniatura, referencias.MINI_LADO),
                            (f.cara, referencias.CARA_LADO)):
            img = QLabel()
            img.setFixedSize(lado, lado)
            img.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            if pendiente:
                img.setObjectName("muted")
                img.setAlignment(Qt.AlignmentFlag.AlignCenter)
                img.setText("Cargando…")
            else:
                _poner_imagen(img, datos)
            celdas.append(img)
            h.addWidget(img, 0, Qt.AlignmentFlag.AlignTop)
        self._celdas_foto[f.id] = tuple(celdas)
        texto = QVBoxLayout()
        texto.setSpacing(3)
        nombre = QLabel(f.archivo or "Foto sin nombre")
        nombre.setToolTip(f.ruta)
        nombre.setWordWrap(True)
        texto.addWidget(nombre)
        juicio = QLabel(f.juicio.titulo)
        juicio.setStyleSheet(f"color: {_tono(f.juicio.tono)}; font-weight: 600;")
        texto.addWidget(juicio)
        for linea in f.juicio.detalle:
            d = QLabel(linea)
            d.setWordWrap(True)
            d.setObjectName("muted")
            texto.addWidget(d)
        quitar = QPushButton("Quitar esta foto")
        quitar.setObjectName("btn_link")
        quitar.setEnabled(f.juicio.tono != "activo")
        quitar.clicked.connect(lambda _=False, ref=f.id, nom=f.archivo: self._quitar_foto(ref, nom))
        texto.addWidget(quitar, 0, Qt.AlignmentFlag.AlignLeft)
        texto.addStretch(1)
        h.addLayout(texto, 1)
        return fila

    def _quitar_foto(self, ref_id: int, archivo: str):
        sel = self._selected_person()
        nombre = sel[1] if sel else "la persona"
        if not confirmar(self, "Quitar foto de referencia",
                         f"¿Quitar la foto {archivo} de {nombre}?",
                         "Deja de usarse para reconocer a la persona. En la próxima pasada el "
                         "NAS vuelve a revisar los rostros de todo el catálogo. Las etiquetas "
                         "que ya se sugirieron con esta foto se mantienen: revíselas en Buscar.",
                         "Quitar la foto", peligro=True):
            return
        self._srv.pedir(lambda repo: repo.quitar_referencia(ref_id),
                        ok=lambda _: self._refresh_people(),
                        error=lambda e: self._set_people_status(texto_para(e)))

    def _abrir_avanzado(self):
        from ..widgets.catalogo_avanzado import CatalogoAvanzado
        dlg = CatalogoAvanzado(self, carpetas=self._carpetas, raiz=self._raiz or "",
                               srv=self._srv)
        dlg.catalogChanged.connect(self.refresh)
        dlg.catalogChanged.connect(self.catalogChanged.emit)
        dlg.exec()

    def _set_status(self, text: str):
        self.lbl_status.setText(text)
        self.lbl_status.setVisible(bool(text))

    def _set_people_status(self, text: str):
        self.lbl_people_status.setText(text)
        self.lbl_people_status.setVisible(bool(text))

    @staticmethod
    def _left(widget) -> QHBoxLayout:
        """Un botón con su ancho natural, alineado a la izquierda."""
        row = QHBoxLayout()
        row.addWidget(widget)
        row.addStretch()
        return row

    @staticmethod
    def _title(text):
        lbl = QLabel(text)
        lbl.setObjectName("section_title")
        return lbl

    @staticmethod
    def _text(text):
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        return lbl

    # ------------------------------------------------------------- ciclo
    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._refresh_people()
        self._pedir_detalle()               # p. ej. cuántas fotos tienen ya su nombre
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    # ------------------------------------------------------------- estado
    def _estado_catalogo(self, estado: str, texto: str):
        self.conn_box.set_estado(estado, texto)
        if estado == "error":
            self.lbl_count.setText("")
            self._set_status("")
            self.progress.setVisible(False)
        self._update_buttons()

    def _reintentar(self):
        self.refresh()
        self._refresh_people()

    def cambio_en_el_catalogo(self):
        """Aviso del NAS (ui/avisos_catalogo.py): p. ej. ya procesó las fotos de
        una persona, o la otra PC agregó a alguien."""
        self._refresh_people()

    def refresh(self):
        """Estado del NAS, pedidos en espera y cantidades (una sola consulta)."""
        if self._personas_en_proceso:
            self._refresh_people()          # por si no llega el aviso
        if self._consultando:
            return
        self._consultando = True

        def leer(repo):
            return (repo.estado_catalogado(), repo.pedidos_abiertos(), repo.count_media(),
                    repo.count_pending_tags(), len(repo.faltantes()),
                    repo.carpetas_catalogadas())

        def listo(_r=None):
            self._consultando = False

        def mostrar(r):
            listo()
            self._mostrar(*r)

        self._srv.pedir(leer, ok=mostrar, error=listo)

    def _mostrar(self, est, pedidos, n, pend, faltan, carpetas=()):
        self._pedidos = pedidos
        self._carpetas = tuple(carpetas)
        try:
            self._raiz = config_catalogo().raiz
        except CatalogoNoConfigurado:
            self._raiz = ""
        if self._carpetas:
            sep = "\\" if self._raiz.startswith("\\") or self._raiz[1:2] == ":" else "/"
            nombres = [c.replace("/", sep) for c in self._carpetas]
            self.lbl_carpetas.setText("Carpetas del NAS que se catalogan: " + ", ".join(nombres)
                                      + ". Se agregan o quitan en las opciones avanzadas (⚙).")
        else:
            self.lbl_carpetas.setText("Todavía no hay carpetas para catalogar. Agréguelas en las "
                                      "opciones avanzadas (⚙, arriba a la derecha).")
        texto, self._trabajando = texto_estado(est, pedidos)
        self._set_status(texto)
        total = est.get("total")
        en_curso = est.get("fase") in ("catalogando", "analizando", "preparando_ia",
                                       "leyendo_texto") and self._trabajando
        self.progress.setVisible(en_curso)
        if en_curso:
            self.progress.setMaximum(total or 0)     # sin total: barra en movimiento
            self.progress.setValue(est.get("hechos") or 0)
        txt = ("1 foto o video en el catálogo" if n == 1 else
               f"{n} fotos y videos en el catálogo")
        if faltan:
            txt += " · 1 ya no está en su carpeta" if faltan == 1 else \
                f" · {faltan} ya no están en su carpeta"
        if pend:
            txt += f" · {pend} con sugerencias de la IA sin revisar"
        self.lbl_count.setText(txt)
        # Terminó una pasada (de esta PC o de la noche): que Buscar se entere.
        fin = est.get("ultima_fin")
        if fin != self._ultima_fin:
            if self._ultima_fin is not None:
                self.catalogChanged.emit()
                self._pedir_detalle()       # la pasada pudo sugerir su nombre en fotos
            self._ultima_fin = fin
        self._update_buttons()

    def _refresh_people(self):
        self._srv.pedir(lambda repo: repo.list_persons(), ok=self._mostrar_personas,
                        error=lambda _e: self._mostrar_personas([]))

    def _mostrar_personas(self, people):
        sel = self._selected_person()
        self._personas_en_proceso = any(p["pendientes"] for p in people)
        # Rehacer la lista no es «elegir otra persona»: el detalle no se vacía
        self.lst_people.blockSignals(True)
        self.lst_people.clear()
        for p in people:
            it = QListWidgetItem(texto_persona(p))
            it.setData(_PERSON_ID_ROLE, p["id"])
            it.setData(_PERSON_NAME_ROLE, p["name"])
            if p["sin_cara"]:
                it.setToolTip("Sin un rostro visible (no se usan): " + ", ".join(p["sin_cara"]))
            self.lst_people.addItem(it)
            if sel and sel[0] == p["id"]:
                it.setSelected(True)
        if not people:
            it = QListWidgetItem("Todavía no hay nadie en la lista.")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.lst_people.addItem(it)
        self.lst_people.blockSignals(False)
        actual = self._selected_person()
        if actual is None:
            self._on_person_sel()           # la quitaron: se cierra el detalle
        else:
            # Las fotos se vuelven a traer solo si algo de la persona cambió (la
            # lista se relee cada 3 s mientras el NAS procesa fotos)
            fila = next((p for p in people if p["id"] == actual[0]), None)
            if fila != self._fila_elegida:
                self._pedir_detalle()
        self._fila_elegida = next((p for p in people if actual and p["id"] == actual[0]), None)
        self._update_buttons()

    def _selected_person(self) -> tuple[int, str] | None:
        items = self.lst_people.selectedItems()
        if not items:
            return None
        return items[0].data(_PERSON_ID_ROLE), items[0].data(_PERSON_NAME_ROLE)

    def _update_buttons(self):
        busy = self._worker is not None and self._worker.isRunning()
        sin_catalogo = self._srv.estado == "error"
        self.boton_catalogar.setEnabled(not sin_catalogo and bool(self._carpetas)
                                and not self._trabajando and not self._pedidos)
        self.btn_add_person.setEnabled(not busy and not sin_catalogo)
        has_sel = self._selected_person() is not None
        self.btn_add_photos.setEnabled(not busy and not sin_catalogo and has_sel)
        self.btn_del_person.setEnabled(not busy and not sin_catalogo and has_sel)

    # ------------------------------------------------------------- pedidos al NAS
    def _pedir_todo(self):
        self._pedir(None)

    def _abrir_partes(self):
        """La flecha de «Catalogar»: qué hacer, dónde y si se rehace."""
        from ..widgets.catalogar_por_partes import CatalogarPorPartes
        dlg = CatalogarPorPartes(self, carpetas=self._carpetas, raiz=self._raiz or "")
        if dlg.exec():
            self._pedir(dlg.carpeta_elegida(), dlg.pasos, dlg.rehacer)

    def catalogar_una_carpeta(self, ruta: str, pasos=None, rehacer: bool = False):
        """Una subcarpeta (p. ej. un evento recién copiado). Tiene que estar dentro
        de alguna de la lista; el NAS toma el bloqueo de esa carpeta."""
        rel = rutas.a_relativa(ruta, self._raiz) if self._raiz else None
        madre = next((c for c in self._carpetas if rel is not None and rutas.dentro_de(rel, c)),
                     None)
        if madre is None:
            self._set_status("Esa carpeta no está entre las que se catalogan. Elija una "
                             "carpeta dentro de: " + _lista(self._carpetas) + ".")
            return
        self._pedir(rel, pasos, rehacer)

    def _pedir(self, carpeta: str | None, pasos=None, rehacer: bool = False):
        quien = socket.gethostname()
        self._pedidos = [{"carpeta": carpeta}]          # el botón se apaga ya
        self._update_buttons()
        self._srv.pedir(lambda repo: repo.pedir_catalogado(carpeta, quien, pasos, rehacer),
                        ok=lambda _id: self.refresh(),
                        error=lambda e: (self._set_status(texto_para(e)), self.refresh()))

    # ------------------------------------------------------------- trabajos de la PC
    def _start(self, fn, on_done, status: str):
        self._set_people_status(status)
        self._worker = EngineWorker(fn)
        self._worker.progress.connect(self._on_progress)
        self._on_done_cb = on_done
        self._worker.finished_ok.connect(self._finish)
        self._worker.fallo.connect(self._on_failed)
        self._worker.start()
        self._update_buttons()

    def _finish(self, result):
        self._worker.wait()
        self._worker = None
        self._update_buttons()
        self._on_done_cb(result)

    def _on_progress(self, done, total, msg):
        pass

    def _on_failed(self, err):
        self._worker.wait()
        self._worker = None
        self._update_buttons()
        self._set_people_status(texto_para(err))

    # ------------------------------------------------------------- personas
    def _add_person(self):
        name, ok = QInputDialog.getText(self, "Agregar persona",
                                        "Nombre (así aparece como etiqueta):")
        name = " ".join(name.split())
        if not ok or not name:
            return
        self._pick_and_add_refs(None, name)

    def _add_photos(self):
        sel = self._selected_person()
        if sel:
            self._pick_and_add_refs(sel[0], sel[1])

    def _pick_and_add_refs(self, person_id: int | None, name: str):
        start = self.settings.value("people_last_dir", "")
        paths, _ = QFileDialog.getOpenFileNames(
            self, f"Fotos de referencia de {name}", start, _IMAGE_FILTER)
        if not paths:
            return
        self.settings.setValue("people_last_dir", os.path.dirname(paths[0]))
        self.enviar_referencias(person_id, name, paths)

    # ------------------------------------------------------------- desde Buscar
    def recibir_fotos_de_persona(self, paths: list[str], media_ids: list[int] | None = None):
        """Fotos elegidas en Buscar: se pregunta de quién son (alguien de la lista
        o una persona nueva) y se envían al NAS como fotos de referencia. Si vienen
        sus `media_ids` (fotos del catálogo), se manda solo cuáles son: el NAS lee
        los archivos y no viajan por la red."""
        if not paths:
            return
        if self._worker is not None and self._worker.isRunning():
            self._set_people_status("Espere a que terminen de enviarse las fotos anteriores "
                                    "y vuelva a intentarlo.")
            return
        if self._srv.estado == "error":
            self._set_people_status("No hay conexión con el catálogo del NAS. Las fotos no "
                                    "se enviaron.")
            return
        # La lista se lee de la base (fuera del hilo de la interfaz): puede que
        # esta pestaña todavía no se haya mostrado nunca.
        self._srv.pedir(lambda repo: repo.list_persons(),
                        ok=lambda people: self._asignar_fotos(paths, people, media_ids),
                        error=lambda e: self._set_people_status(texto_para(e)))

    def _asignar_fotos(self, paths: list[str], people: list[dict],
                       media_ids: list[int] | None = None):
        nombre = self._elegir_persona([p["name"] for p in people], len(paths))
        nombre = " ".join((nombre or "").split())
        if not nombre:
            return
        if len(paths) > _MUCHAS_REFERENCIAS and not confirmar(
                self, "Fotos de referencia", f"¿Enviar {len(paths)} fotos de {nombre}?",
                "Con 3 a 5 fotos en las que el rostro se vea con claridad alcanza. Muchas "
                "fotos no mejoran el reconocimiento.", "Enviar las fotos"):
            return
        # El mismo nombre escrito con otras mayúsculas es la misma persona.
        existente = next((p for p in people if p["name"].casefold() == nombre.casefold()), None)
        pid, nombre = (existente["id"], existente["name"]) if existente else (None, nombre)
        if media_ids:
            self.referencias_del_catalogo(pid, nombre, media_ids)
        else:
            self.enviar_referencias(pid, nombre, paths)

    def referencias_del_catalogo(self, person_id: int | None, name: str, media_ids: list[int]):
        """Fotos del catálogo: se anotan cuáles son y el NAS lee los archivos."""
        n = len(media_ids)

        def listo(_pid):
            self._set_people_status(
                f"{name}: " + ("se envió 1 foto" if n == 1 else f"se enviaron {n} fotos")
                + " al NAS, que busca el rostro en unos segundos. La persona se reconoce en "
                  "el catálogo desde la próxima pasada (esta noche, o antes con «Catalogar»).")
            self._refresh_people()

        self._srv.pedir(lambda repo: repo.referencias_desde_el_catalogo(name, media_ids, person_id),
                        ok=listo, error=lambda e: self._set_people_status(texto_para(e)))

    def _elegir_persona(self, nombres: list[str], n: int) -> str | None:
        """Nombre elegido o escrito, o None si se canceló."""
        dlg = QInputDialog(self)
        dlg.setWindowTitle("Agregar persona")
        dlg.setLabelText(
            ("¿De quién es esta foto?" if n == 1 else f"¿De quién son estas {n} fotos?")
            + "\nElija una persona de la lista o escriba el nombre de una persona nueva "
              "(así aparece como etiqueta).\n\nSe usa el rostro más grande de cada foto: "
              "elija fotos en las que la persona aparezca sola o en primer plano.")
        dlg.setComboBoxItems(nombres)
        dlg.setComboBoxEditable(True)
        dlg.setTextValue("")
        dlg.setOkButtonText("Enviar fotos")
        dlg.setCancelButtonText("Cancelar")
        if not dlg.exec():
            return None
        return dlg.textValue()

    def enviar_referencias(self, person_id: int | None, name: str, paths: list[str]):
        """Achica las fotos y las sube al NAS, que calcula las firmas."""
        def job(progress_cb):
            # Leer las fotos (pueden estar en el NAS) va fuera del hilo de la interfaz.
            fotos, ilegibles = [], []
            for i, p in enumerate(paths, 1):
                try:
                    fotos.append((p, foto_de_referencia(p)))
                except Exception:  # noqa: BLE001 — no es una imagen o no se puede leer
                    ilegibles.append(p)
                progress_cb(i, len(paths), p)
            if fotos:
                with abrir_repo() as repo:
                    repo.subir_fotos_de_referencia(name, fotos, person_id)
            return len(fotos), ilegibles

        def done(result):
            subidas, ilegibles = result
            msg = ""
            if subidas:
                msg = (f"{name}: " + ("se envió 1 foto" if subidas == 1 else
                                      f"se enviaron {subidas} fotos")
                       + " al NAS, que busca el rostro en unos segundos. La persona se "
                         "reconoce en el catálogo desde la próxima pasada (esta noche, o "
                         "antes con «Catalogar»).")
            if ilegibles:
                msg += ((" " if msg else "") + "No se pudieron abrir: "
                        + ", ".join(os.path.basename(p) for p in ilegibles[:5]) + ".")
            self._set_people_status(msg)
            self._refresh_people()

        self._start(job, done, f"Enviando fotos de {name} al NAS…")

    def _delete_person(self):
        sel = self._selected_person()
        if not sel:
            return
        pid, name = sel
        if not confirmar(self, "Quitar persona", f"¿Quitar a {name} de la lista?",
                         "Deja de reconocerse en fotos nuevas y se borran sus fotos de "
                         "referencia. Las etiquetas que ya tiene en fotos se mantienen.",
                         f"Quitar a {name}", peligro=True):
            return
        self._srv.pedir(lambda repo: repo.delete_person(pid),
                        ok=lambda _: self._refresh_people(),
                        error=lambda e: self._set_people_status(texto_para(e)))


def _poner_imagen(lbl: QLabel, datos: bytes | None):
    """Muestra un JPEG del detalle de la persona (vienen al doble: pantallas 2x)."""
    lbl.setText("")
    lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
    pm = QPixmap()
    if datos and pm.loadFromData(datos):
        pm.setDevicePixelRatio(2.0)
        lbl.setPixmap(pm)
