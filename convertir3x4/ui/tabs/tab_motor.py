"""Pestaña 'Catalogar'.

El catalogado y el análisis con IA corren en el NAS (convertir3x4/catalogador.py,
docs/catalogado-en-el-nas.md): todas las noches y cuando alguien lo pide desde
acá. Esta pestaña solo pide («Catalogar ahora», o una subcarpeta desde el ⚙) y
muestra lo que el NAS informa en la base. Mientras se ve, consulta cada 3 s.

También administra la lista de personas reconocibles: la firma de cada foto de
referencia se calcula en esta PC y se guarda en la base.
"""

import datetime
import os
import socket

from PyQt6.QtCore import Qt, QSettings, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QProgressBar, QListWidget,
    QListWidgetItem, QInputDialog,
)

from ...appdata import abrir_repo, config_catalogo, models_dir
from ...catalog import rutas
from ...catalog.db import CatalogoNoConfigurado
from ...engine import ai_models
from ...workers import EngineWorker
from ..servicio_catalogo import servicio, texto_para
from ..widgets.caja_conexion import CajaConexion
from ..widgets.dialogos import confirmar
from .. import icons
from ..style import T

_PERSON_ID_ROLE = Qt.ItemDataRole.UserRole
_IMAGE_FILTER = "Imágenes (*.jpg *.jpeg *.png *.webp *.bmp)"
_CONTENT_MAX_W = 760
_SECTION_GAP = 22
_POLL_MS = 3000
SIN_LATIDO = 60      # segundos: más que esto y el NAS no está catalogando
_RESPONSABLE = "Comuníquese con el responsable de la aplicación."


def _n(k, uno, varios):
    return uno if k == 1 else f"{k} {varios}"


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
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self.refresh)
        self._srv.estado_cambiado.connect(self._estado_catalogo)
        self.refresh()
        self._refresh_people()

    # ------------------------------------------------------------- UI
    def _build_ui(self):
        # Columna de ancho legible, alineada a la izquierda (como una página de
        # ajustes): a todo el ancho, los textos y botones quedaban sueltos.
        outer = QHBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 24)
        content = QWidget()
        content.setMaximumWidth(_CONTENT_MAX_W)
        outer.addWidget(content, 1)
        outer.addStretch(0)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        cab = QHBoxLayout()
        cab.addWidget(self._title("Catálogo de fotos y videos"))
        cab.addStretch()
        # Lo que casi nunca se usa (una subcarpeta, limpiar) va escondido en el ⚙
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
            "recién copiado, use «Catalogar ahora»."))
        self.lbl_carpetas = QLabel()
        self.lbl_carpetas.setWordWrap(True)
        self.lbl_carpetas.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.lbl_carpetas)

        self.btn_run = QPushButton("Catalogar ahora")
        self.btn_run.setObjectName("btn_primary")
        self.btn_run.clicked.connect(self._pedir_todo)
        layout.addLayout(self._left(self.btn_run))

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
            "Cargue de 3 a 5 fotos por persona en las que el rostro se vea con claridad."))
        self.lst_people = QListWidget()
        self.lst_people.setObjectName("people_list")
        self.lst_people.setAccessibleName("Personas reconocibles")
        self.lst_people.setMaximumHeight(150)
        self.lst_people.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.lst_people)
        prow = QHBoxLayout()
        prow.setSpacing(8)
        self.btn_add_person = QPushButton("Agregar persona…")
        self.btn_add_person.clicked.connect(self._add_person)
        self.btn_add_photos = QPushButton("Agregar fotos de la persona…")
        self.btn_add_photos.clicked.connect(self._add_photos)
        self.btn_del_person = QPushButton("Quitar de la lista")
        self.btn_del_person.clicked.connect(self._delete_person)
        for b in (self.btn_add_person, self.btn_add_photos, self.btn_del_person):
            prow.addWidget(b)
        prow.addStretch()
        layout.addLayout(prow)
        self.lbl_people_status = QLabel("")
        self.lbl_people_status.setWordWrap(True)
        self.lbl_people_status.setVisible(False)
        layout.addWidget(self.lbl_people_status)
        layout.addStretch()
        self._update_buttons()

    def _abrir_avanzado(self):
        from ..widgets.catalogo_avanzado import CatalogoAvanzado
        dlg = CatalogoAvanzado(self, carpetas=self._carpetas)
        dlg.catalogarCarpeta.connect(self.catalogar_una_carpeta)
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

    def refresh(self):
        """Estado del NAS, pedidos en espera y cantidades (una sola consulta)."""
        if self._consultando:
            return
        self._consultando = True

        def leer(repo):
            return (repo.estado_catalogado(), repo.pedidos_abiertos(), repo.count_media(),
                    repo.count_pending_tags(), len(repo.faltantes()))

        def listo(_r=None):
            self._consultando = False

        def mostrar(r):
            listo()
            self._mostrar(*r)

        self._srv.pedir(leer, ok=mostrar, error=listo)

    def _mostrar(self, est, pedidos, n, pend, faltan):
        self._pedidos = pedidos
        self._carpetas = tuple(est.get("carpetas") or ())
        try:
            self._raiz = config_catalogo().raiz
        except CatalogoNoConfigurado:
            self._raiz = ""
        if self._carpetas:
            sep = "\\" if self._raiz.startswith("\\") or self._raiz[1:2] == ":" else "/"
            nombres = [c.replace("/", sep) for c in self._carpetas]
            self.lbl_carpetas.setText("Carpetas del NAS que se catalogan: " + ", ".join(nombres) + ".")
        else:
            self.lbl_carpetas.setText("")
        texto, self._trabajando = texto_estado(est, pedidos)
        self._set_status(texto)
        total = est.get("total")
        en_curso = est.get("fase") in ("catalogando", "analizando", "preparando_ia") and self._trabajando
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
            self._ultima_fin = fin
        self._update_buttons()

    def _refresh_people(self):
        self._srv.pedir(lambda repo: repo.list_persons(), ok=self._mostrar_personas,
                        error=lambda _e: self._mostrar_personas([]))

    def _mostrar_personas(self, people):
        self.lst_people.clear()
        for pid, name, n in people:
            it = QListWidgetItem(f"{name} — {n} foto{'s' if n != 1 else ''}")
            it.setData(_PERSON_ID_ROLE, pid)
            self.lst_people.addItem(it)
        if not people:
            it = QListWidgetItem("Todavía no hay nadie en la lista.")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.lst_people.addItem(it)
        self._update_buttons()

    def _selected_person(self) -> tuple[int, str] | None:
        items = self.lst_people.selectedItems()
        if not items:
            return None
        return items[0].data(_PERSON_ID_ROLE), items[0].text().split(" — ")[0]

    def _update_buttons(self):
        busy = self._worker is not None and self._worker.isRunning()
        sin_catalogo = self._srv.estado == "error"
        self.btn_run.setEnabled(not sin_catalogo and bool(self._carpetas)
                                and not self._trabajando and not self._pedidos)
        self.btn_add_person.setEnabled(not busy and not sin_catalogo)
        has_sel = self._selected_person() is not None
        self.btn_add_photos.setEnabled(not busy and not sin_catalogo and has_sel)
        self.btn_del_person.setEnabled(not busy and not sin_catalogo and has_sel)

    # ------------------------------------------------------------- pedidos al NAS
    def _pedir_todo(self):
        self._pedir(None)

    def catalogar_una_carpeta(self, ruta: str):
        """Una subcarpeta (p. ej. un evento recién copiado). Tiene que estar dentro
        de alguna de la lista; el NAS toma el bloqueo de esa carpeta."""
        rel = rutas.a_relativa(ruta, self._raiz) if self._raiz else None
        madre = next((c for c in self._carpetas if rel is not None and rutas.dentro_de(rel, c)),
                     None)
        if madre is None:
            self._set_status("Esa carpeta no está entre las que se catalogan. Elija una "
                             "carpeta dentro de: " + _lista(self._carpetas) + ".")
            return
        self._pedir(rel)

    def _pedir(self, carpeta: str | None):
        quien = socket.gethostname()
        self._pedidos = [{"carpeta": carpeta}]          # el botón se apaga ya
        self._update_buttons()
        self._srv.pedir(lambda repo: repo.pedir_catalogado(carpeta, quien),
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
        if msg.startswith("⬇"):
            self._set_people_status(msg)

    def _on_failed(self, err):
        self._worker.wait()
        self._worker = None
        self._update_buttons()
        self._set_people_status(texto_para(err))

    @staticmethod
    def _ensure_models(mdir, progress_cb):
        def cb(done, total, name):
            mb = 1024 * 1024
            progress_cb(done // mb, total // mb,
                        f"⬇ Preparando el reconocimiento (solo la primera vez): "
                        f"{done // mb} de {total // mb} MB…")
        ai_models.ensure(mdir, cb)

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
        mdir = models_dir()

        def job(progress_cb):
            self._ensure_models(mdir, progress_cb)
            from ...engine.ai_pipeline import reference_embedding
            from ...engine.faces import FaceEngine
            engine = FaceEngine(mdir)
            embs, no_face = [], []
            for i, p in enumerate(paths, 1):
                try:
                    e = reference_embedding(engine, p)
                except Exception:  # noqa: BLE001 — foto ilegible
                    e = None
                (embs.append((p, e)) if e is not None else no_face.append(p))
                progress_cb(i, len(paths), p)
            if not embs:
                return 0, no_face
            with abrir_repo() as repo:
                pid = person_id or repo.add_person(name)
                for p, e in embs:
                    repo.add_person_reference(pid, p, e)
            return len(embs), no_face

        def done(result):
            added, no_face = result
            msg = f"{name}: {added} foto{'s' if added != 1 else ''} de referencia cargada{'s' if added != 1 else ''}."
            if no_face:
                msg += (f" En {len(no_face)} no se detectó un rostro: "
                        + ", ".join(os.path.basename(p) for p in no_face[:5]))
            if added:
                msg += (" El NAS la busca en el catálogo esta noche; para hacerlo antes, "
                        "use «Catalogar ahora».")
            self._set_people_status(msg)
            self._refresh_people()

        self._start(job, done, f"Leyendo fotos de {name}…")

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
