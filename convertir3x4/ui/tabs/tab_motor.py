"""Pestaña 'Catalogar' (el motor).

1. Catalogar: recorre las carpetas del NAS de la configuración (`catalogo.ini`)
   y llena el catálogo (metadata, hashes, miniaturas).
2. Análisis con IA: propone tags de escena y reconoce a las personas de la lista.
3. Personas reconocibles: la lista de quiénes se reconocen (y nadie más).

Todo el trabajo pesado corre en un EngineWorker con su propia conexión; las
consultas cortas (cantidades, personas) van por el servicio del catálogo.
Emite `catalogChanged` al terminar para que la pestaña Buscar se refresque.
"""

import os

from PyQt6.QtCore import Qt, QSettings, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QProgressBar, QListWidget,
    QListWidgetItem, QInputDialog, QMessageBox,
)

from ...appdata import abrir_repo, config_catalogo, models_dir
from ...catalog import rutas
from ...catalog.db import CatalogoNoConfigurado
from ...engine import ai_models
from ...engine.pipeline import CatalogoOcupado, catalogar
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


def _resumen_catalogo(stats) -> str:
    """El resultado de catalogar en una frase, solo con lo que importa."""
    def n(k, uno, varios):
        return uno if k == 1 else f"{k} {varios}"
    partes = []
    if stats.added:
        partes.append(n(stats.added, "1 nuevo", "nuevos"))
    if stats.updated:
        partes.append(n(stats.updated, "1 actualizado", "actualizados"))
    if stats.moved:
        partes.append(n(stats.moved, "1 cambiado de carpeta", "cambiados de carpeta"))
    texto = "Catalogación terminada: " + (" y ".join(partes) if partes else "no había elementos nuevos") + "."
    if stats.skipped and partes:
        texto += f" {n(stats.skipped, '1 ya estaba', 'ya estaban')}."
    if stats.duplicates:
        texto += f" {n(stats.duplicates, '1 está repetido', 'están repetidos')}."
    if stats.corrupt:
        texto += (" 1 está dañado y no se pudo abrir." if stats.corrupt == 1 else
                  f" {stats.corrupt} están dañados y no se pudieron abrir.")
    if stats.missing:
        texto += (" 1 ya no está en su carpeta." if stats.missing == 1 else
                  f" {stats.missing} ya no están en su carpeta.")
        texto += " Siguen en el catálogo, con sus etiquetas, por si aparecen en otra."
    if stats.ocupadas:
        texto += (f" No se revisó {_lista(stats.ocupadas)} porque otra PC la estaba "
                  "catalogando." if len(stats.ocupadas) == 1 else
                  f" No se revisaron {_lista(stats.ocupadas)} porque otra PC las estaba "
                  "catalogando.")
    if stats.inexistentes:
        texto += (f" La carpeta {_lista(stats.inexistentes)} no existe en el NAS."
                  if len(stats.inexistentes) == 1 else
                  f" Las carpetas {_lista(stats.inexistentes)} no existen en el NAS.")
    if stats.incompletas:
        texto += (f" {_lista(stats.incompletas)} no se pudo leer completa; se revisa de "
                  "nuevo la próxima vez.")
    return texto


def _lista(carpetas) -> str:
    nombres = [f"«{c}»" for c in carpetas]
    return nombres[0] if len(nombres) == 1 else ", ".join(nombres[:-1]) + " y " + nombres[-1]


class MotorTab(QWidget):
    catalogChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = QSettings("CDLM", "Convertir3x4")
        self._worker: EngineWorker | None = None
        self._on_done_cb = None
        self._srv = servicio()
        self._build_ui()
        self._srv.estado_cambiado.connect(self._estado_catalogo)
        self._refresh_count()
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
        cab.addWidget(self._title("Agregar fotos y videos al catálogo"))
        cab.addStretch()
        # Lo que casi nunca se usa (limpiar el catálogo) va escondido en el ⚙
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
            "La aplicación agrega al catálogo todas las fotos y videos de las carpetas del "
            "NAS que figuran abajo, para encontrarlos luego en Buscar. Puede repetirse en "
            "cualquier momento: solo se agrega lo nuevo y se actualiza lo que cambió."))
        self.lbl_carpetas = QLabel()
        self.lbl_carpetas.setWordWrap(True)
        self.lbl_carpetas.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.lbl_carpetas)
        self._carpetas: tuple[str, ...] = ()
        self._cargar_carpetas()

        self.btn_run = QPushButton("Catalogar ahora")
        self.btn_run.setObjectName("btn_primary")
        self.btn_run.clicked.connect(self._run_stage0)
        layout.addLayout(self._left(self.btn_run))

        # Estado del trabajo en curso, pegado a las acciones que lo disparan
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

        # --- IA
        layout.addSpacing(_SECTION_GAP)
        layout.addWidget(self._title("Análisis con IA"))
        layout.addWidget(self._text(
            "Sugiere etiquetas (bautismo, alabanza, placa, flyer…) y reconoce a las "
            "personas de la lista de abajo. Todo queda como sugerencia hasta que se "
            "confirma en Buscar. Funciona sin internet; solo la primera vez necesita "
            "descargar aprox. 650 MB."))
        self.btn_ai = QPushButton("Analizar con IA")
        icons.set_icon(self.btn_ai, "sparkles", 15, "text")
        self.btn_ai.clicked.connect(self._run_ai)
        layout.addLayout(self._left(self.btn_ai))

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
        layout.addStretch()
        self._update_buttons()

    def _abrir_avanzado(self):
        from ..widgets.catalogo_avanzado import CatalogoAvanzado
        dlg = CatalogoAvanzado(self, carpetas=self._carpetas)
        dlg.catalogarCarpeta.connect(self.catalogar_una_carpeta)
        dlg.catalogChanged.connect(self._refresh_count)
        dlg.catalogChanged.connect(self.catalogChanged.emit)
        dlg.exec()

    def _set_status(self, text: str):
        self.lbl_status.setText(text)
        self.lbl_status.setVisible(bool(text))

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

    # ------------------------------------------------------------- estado
    def _cargar_carpetas(self):
        """Lee de la configuración qué carpetas del NAS se catalogan."""
        try:
            cfg = config_catalogo()
        except CatalogoNoConfigurado as e:
            self._carpetas, self._raiz = (), ""
            self.lbl_carpetas.setText(e.MENSAJE)
            return
        self._carpetas, self._raiz = cfg.carpetas, cfg.raiz
        if not cfg.carpetas:
            self.lbl_carpetas.setText(
                "Todavía no hay carpetas del NAS para catalogar en esta PC. Comuníquese "
                "con el responsable de la aplicación.")
            return
        sep = "\\" if cfg.raiz.startswith("\\") or cfg.raiz[1:2] == ":" else "/"
        nombres = [c.replace("/", sep) for c in cfg.carpetas]
        self.lbl_carpetas.setText("Carpetas del NAS que se catalogan: " + ", ".join(nombres) + ".")

    def _estado_catalogo(self, estado: str, texto: str):
        self.conn_box.set_estado(estado, texto)
        if estado == "error":
            self.lbl_count.setText("")
        self._update_buttons()

    def _reintentar(self):
        self._cargar_carpetas()
        self._refresh_count()
        self._refresh_people()

    def _refresh_count(self):
        def contar(repo):
            return repo.count_media(), repo.count_pending_tags(), len(repo.faltantes())

        def mostrar(r):
            n, pend, faltan = r
            txt = ("1 foto o video en el catálogo" if n == 1 else
                   f"{n} fotos y videos en el catálogo")
            if faltan:
                txt += " · 1 ya no está en su carpeta" if faltan == 1 else \
                    f" · {faltan} ya no están en su carpeta"
            if pend:
                txt += f" · {pend} con sugerencias de la IA sin revisar"
            self.lbl_count.setText(txt)

        self._srv.pedir(contar, ok=mostrar)

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
        self.btn_run.setEnabled(not busy and not sin_catalogo and bool(self._carpetas))
        for w in (self.btn_ai, self.btn_add_person):
            w.setEnabled(not busy and not sin_catalogo)
        has_sel = self._selected_person() is not None
        self.btn_add_photos.setEnabled(not busy and not sin_catalogo and has_sel)
        self.btn_del_person.setEnabled(not busy and not sin_catalogo and has_sel)
        self.progress.setVisible(busy)

    # ------------------------------------------------------------- jobs
    def _start(self, fn, on_done, status: str):
        self._set_status(status)
        self.progress.setValue(0)
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
        self.progress.setMaximum(total or 1)
        self.progress.setValue(done)
        if msg.startswith("⬇"):
            self._set_status(msg)
        else:
            self._set_status(f"Procesando {done} de {total}…")

    def _on_failed(self, err):
        self._worker.wait()
        self._worker = None
        self._update_buttons()
        # El detalle técnico no le sirve al operador.
        if isinstance(err, CatalogoOcupado):
            self._set_status(err.MENSAJE)
        else:
            self._set_status(texto_para(err))

    @staticmethod
    def _ensure_models(mdir, progress_cb):
        def cb(done, total, name):
            mb = 1024 * 1024
            progress_cb(done // mb, total // mb,
                        f"⬇ Preparando el análisis (solo la primera vez): "
                        f"{done // mb} de {total // mb} MB…")
        ai_models.ensure(mdir, cb)

    # ------------------------------------------------------------- catalogar
    def _run_stage0(self):
        self._catalogar(list(self._carpetas))

    def catalogar_una_carpeta(self, ruta: str):
        """A mano, una carpeta (p. ej. un evento recién subido). Tiene que estar
        dentro de alguna de la lista; toma el bloqueo de esa carpeta."""
        rel = rutas.a_relativa(ruta, self._raiz) if self._raiz else None
        madre = next((c for c in self._carpetas if rel is not None and rutas.dentro_de(rel, c)),
                     None)
        if madre is None:
            self._set_status("Esa carpeta no está entre las que se catalogan. Elija una "
                             "carpeta dentro de: " + _lista(self._carpetas) + ".")
            return
        self._catalogar([rel], bloqueo=madre)

    def _catalogar(self, carpetas: list[str], bloqueo: str | None = None):
        if not carpetas:
            return

        def job(progress_cb):
            with abrir_repo() as repo:
                return catalogar(repo, carpetas, progress_cb=progress_cb, bloqueo=bloqueo)

        def done(stats):
            self._set_status(_resumen_catalogo(stats))
            self._refresh_count()
            self.catalogChanged.emit()

        self._start(job, done, "Revisando las carpetas del NAS…")

    # ------------------------------------------------------------- IA
    def _run_ai(self):
        mdir = models_dir()

        def job(progress_cb):
            self._ensure_models(mdir, progress_cb)
            from ...engine.ai_pipeline import run_ai
            threads = max(1, min(6, (os.cpu_count() or 2) - 1))
            with abrir_repo() as repo:
                return run_ai(repo, mdir, progress_cb=progress_cb, threads=threads)

        def done(st):
            sugeridos = st.scene_tags + st.people_tags
            msg = (f"Análisis terminado: {st.analyzed} elementos analizados. "
                   + ("No hay sugerencias nuevas." if not sugeridos else
                      "Hay 1 etiqueta sugerida para revisar en Buscar." if sugeridos == 1 else
                      f"Hay {sugeridos} etiquetas sugeridas para revisar en Buscar."))
            if st.missing_files:
                msg += (f" {st.missing_files} no se encontraron; se vuelven a intentar la "
                        "próxima vez.")
            if st.errors:
                msg += f" {st.errors} no se pudieron abrir."
            if not st.analyzed and not st.missing_files and not st.errors:
                msg = "No había nada nuevo para analizar."
            self._set_status(msg)
            self._refresh_count()
            self.catalogChanged.emit()

        self._start(job, done, "Preparando el análisis…")

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
                msg += " Ejecute «Analizar con IA» para buscarla en el catálogo."
            self._set_status(msg)
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
                        error=lambda e: self._set_status(texto_para(e)))
