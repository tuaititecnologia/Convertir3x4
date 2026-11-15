"""Pestaña 'Catalogar' (el motor).

1. Etapa 0: recorre una carpeta y llena el catálogo (metadata, hashes, miniaturas).
2. Análisis con IA: propone tags de escena y reconoce a las personas de la lista.
3. Personas reconocibles: la lista de quiénes se reconocen (y nadie más).

Todo el trabajo pesado corre en un EngineWorker. Emite `catalogChanged` al
terminar para que la pestaña Buscar se refresque.
"""

import os

from PyQt6.QtCore import Qt, QSettings, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QFileDialog, QProgressBar, QListWidget,
    QListWidgetItem, QInputDialog, QMessageBox,
)

from ...appdata import db_path, thumb_dir, models_dir
from ...catalog.db import connect
from ...catalog.repo import Repo
from ...engine import ai_models
from ...engine.pipeline import run_stage0
from ...workers import EngineWorker
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
    texto = "Listo: " + (" y ".join(partes) if partes else "no había nada nuevo") + "."
    if stats.skipped and partes:
        texto += f" {n(stats.skipped, '1 ya estaba', 'ya estaban')}."
    if stats.duplicates:
        texto += f" {n(stats.duplicates, '1 está repetido', 'están repetidos')}."
    if stats.corrupt:
        texto += (" 1 está dañado y no se pudo abrir." if stats.corrupt == 1 else
                  f" {stats.corrupt} están dañados y no se pudieron abrir.")
    return texto


class MotorTab(QWidget):
    catalogChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = QSettings("CDLM", "Convertir3x4")
        self._worker: EngineWorker | None = None
        self._on_done_cb = None
        self._build_ui()
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
        cab.addWidget(self._title("Sumar fotos y videos al catálogo"))
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
        layout.addWidget(self._text(
            "Elegí una carpeta y la app suma al catálogo todas sus fotos y videos, para "
            "encontrarlos después en Buscar. Se puede repetir cuando quieras: solo agrega "
            "lo que todavía no estaba."))

        row = QHBoxLayout()
        row.setSpacing(8)
        self.txt_folder = QLineEdit()
        self.txt_folder.setPlaceholderText("Carpeta con fotos y videos…")
        self.txt_folder.setAccessibleName("Carpeta a catalogar")
        self.txt_folder.setText(self.settings.value("motor_last_dir", ""))
        row.addWidget(self.txt_folder, 1)
        self.btn_browse = QPushButton("Elegir carpeta…")
        self.btn_browse.clicked.connect(self._browse)
        row.addWidget(self.btn_browse)
        layout.addLayout(row)

        self.btn_run = QPushButton("Catalogar")
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
            "Propone tags (bautismo, alabanza, placa, flyer…) y reconoce a las personas "
            "de la lista de abajo. Todo queda como sugerencia hasta que alguien lo "
            "confirma en Buscar. Funciona sin internet; solo la primera vez necesita "
            "bajar unos 650 MB."))
        self.btn_ai = QPushButton("Analizar con IA")
        icons.set_icon(self.btn_ai, "sparkles", 15, "text")
        self.btn_ai.clicked.connect(self._run_ai)
        layout.addLayout(self._left(self.btn_ai))

        # --- Personas
        layout.addSpacing(_SECTION_GAP)
        layout.addWidget(self._title("Personas reconocibles"))
        layout.addWidget(self._text(
            "Solo se reconoce a quien esté en esta lista (pastores, invitados, "
            "gente importante). Las demás caras se ignoran y no se guardan. "
            "Cargá de 3 a 5 fotos por persona donde se le vea bien la cara."))
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
        dlg = CatalogoAvanzado(self)
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
    def _refresh_count(self):
        try:
            conn = connect(db_path())
            repo = Repo(conn)
            n, pend = repo.count_media(), repo.count_pending_tags()
            conn.close()
            txt = "1 foto o video en el catálogo" if n == 1 else f"{n} fotos y videos en el catálogo"
            if pend:
                txt += f" · {pend} con sugerencias de la IA sin revisar"
            self.lbl_count.setText(txt)
        except Exception as e:  # noqa: BLE001
            self.lbl_count.setText("No se pudo abrir el catálogo.")

    def _refresh_people(self):
        self.lst_people.clear()
        try:
            conn = connect(db_path())
            people = Repo(conn).list_persons()
            conn.close()
        except Exception:  # noqa: BLE001
            people = []
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
        for w in (self.btn_run, self.btn_browse, self.txt_folder, self.btn_ai,
                  self.btn_add_person):
            w.setEnabled(not busy)
        has_sel = self._selected_person() is not None
        self.btn_add_photos.setEnabled(not busy and has_sel)
        self.btn_del_person.setEnabled(not busy and has_sel)
        self.progress.setVisible(busy)

    # ------------------------------------------------------------- jobs
    def _start(self, fn, on_done, status: str):
        self._set_status(status)
        self.progress.setValue(0)
        self._worker = EngineWorker(fn)
        self._worker.progress.connect(self._on_progress)
        self._on_done_cb = on_done
        self._worker.finished_ok.connect(self._finish)
        self._worker.failed.connect(self._on_failed)
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
        # El detalle técnico (err) no le sirve al operador.
        self._set_status("No se pudo terminar. Probá de nuevo; si vuelve a pasar, "
                         "avisale a quien mantiene la app.")

    @staticmethod
    def _ensure_models(mdir, progress_cb):
        def cb(done, total, name):
            mb = 1024 * 1024
            progress_cb(done // mb, total // mb,
                        f"⬇ Preparando el análisis (solo la primera vez): "
                        f"{done // mb} de {total // mb} MB…")
        ai_models.ensure(mdir, cb)

    # ------------------------------------------------------------- Etapa 0
    def _browse(self):
        start = self.txt_folder.text() or self.settings.value("motor_last_dir", "")
        path = QFileDialog.getExistingDirectory(self, "Elegí la carpeta con fotos y videos", start)
        if path:
            self.txt_folder.setText(path)

    def _run_stage0(self):
        root = self.txt_folder.text().strip()
        if not root:
            self._set_status("Elegí una carpeta primero.")
            return
        self.settings.setValue("motor_last_dir", root)
        tdir = thumb_dir()

        def job(progress_cb):
            # Conexión propia dentro del hilo worker (sqlite no cruza hilos).
            conn = connect(db_path())
            try:
                return run_stage0(Repo(conn), root, tdir, progress_cb=progress_cb)
            finally:
                conn.close()

        def done(stats):
            self._set_status(_resumen_catalogo(stats))
            self._refresh_count()
            self.catalogChanged.emit()

        self._start(job, done, "Revisando la carpeta…")

    # ------------------------------------------------------------- IA
    def _run_ai(self):
        mdir = models_dir()

        def job(progress_cb):
            self._ensure_models(mdir, progress_cb)
            from ...engine.ai_pipeline import run_ai
            threads = max(1, min(6, (os.cpu_count() or 2) - 1))
            conn = connect(db_path())
            try:
                return run_ai(Repo(conn), mdir, progress_cb=progress_cb, threads=threads)
            finally:
                conn.close()

        def done(st):
            sugeridos = st.scene_tags + st.people_tags
            msg = (f"Listo: se analizaron {st.analyzed}. "
                   + ("No hay sugerencias nuevas." if not sugeridos else
                      "Hay 1 tag sugerido para revisar en Buscar." if sugeridos == 1 else
                      f"Hay {sugeridos} tags sugeridos para revisar en Buscar."))
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
                                        "Nombre (así aparece como tag):")
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
            conn = connect(db_path())
            try:
                repo = Repo(conn)
                pid = person_id or repo.add_person(name)
                for p, e in embs:
                    repo.add_person_reference(pid, p, e)
            finally:
                conn.close()
            return len(embs), no_face

        def done(result):
            added, no_face = result
            msg = f"{name}: {added} foto{'s' if added != 1 else ''} de referencia cargada{'s' if added != 1 else ''}."
            if no_face:
                msg += (f" En {len(no_face)} no se encontró una cara: "
                        + ", ".join(os.path.basename(p) for p in no_face[:5]))
            if added:
                msg += " Corré «Analizar con IA» para buscarla en el catálogo."
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
                         "referencia. Los tags que ya tiene en fotos se mantienen.",
                         f"Quitar a {name}", peligro=True):
            return
        conn = connect(db_path())
        try:
            Repo(conn).delete_person(pid)
        finally:
            conn.close()
        self._refresh_people()
