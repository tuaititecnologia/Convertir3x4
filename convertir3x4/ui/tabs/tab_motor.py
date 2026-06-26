"""Pestaña 'Catalogar' (el motor): corre la Etapa 0 sobre una carpeta de medios.

Recorre la carpeta, extrae metadata técnica, hashes y miniaturas, y llena el
catálogo SQLite. Sin IA todavía (eso es la Fase 5). Emite `catalogChanged` al
terminar para que la pestaña Buscar se refresque.
"""

from PyQt6.QtCore import Qt, QSettings, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QFileDialog, QProgressBar, QFrame,
)

from ...appdata import db_path, thumb_dir
from ...catalog.db import connect
from ...catalog.repo import Repo
from ...engine.pipeline import run_stage0
from ...workers import EngineWorker


class MotorTab(QWidget):
    catalogChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = QSettings("CDLM", "Convertir3x4")
        self._worker: EngineWorker | None = None
        self._build_ui()
        self._refresh_count()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Catalogar medios")
        title.setObjectName("section_title")
        layout.addWidget(title)

        desc = QLabel(
            "Recorre una carpeta y registra cada imagen y video en el catálogo "
            "(dimensiones, duración, hashes, miniaturas y duplicados). Sin IA: "
            "es local, gratis y se puede repetir cuando quieras."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Selector de carpeta
        row = QHBoxLayout()
        self.txt_folder = QLineEdit()
        self.txt_folder.setPlaceholderText("Carpeta de medios a catalogar…")
        self.txt_folder.setText(self.settings.value("motor_last_dir", ""))
        row.addWidget(self.txt_folder, 1)
        self.btn_browse = QPushButton("Elegir…")
        self.btn_browse.clicked.connect(self._browse)
        row.addWidget(self.btn_browse)
        layout.addLayout(row)

        # Acción
        self.btn_run = QPushButton("Catalogar")
        self.btn_run.setObjectName("btn_action")
        self.btn_run.clicked.connect(self._run)
        layout.addWidget(self.btn_run)

        # Progreso
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        self.lbl_count = QLabel("")
        layout.addWidget(self.lbl_count)

        layout.addStretch()

    # ------------------------------------------------------------- acciones
    def _browse(self):
        start = self.txt_folder.text() or self.settings.value("motor_last_dir", "")
        path = QFileDialog.getExistingDirectory(self, "Carpeta de medios", start)
        if path:
            self.txt_folder.setText(path)

    def _refresh_count(self):
        try:
            conn = connect(db_path())
            n = Repo(conn).count_media()
            conn.close()
            self.lbl_count.setText(f"📚 {n} medios en el catálogo")
        except Exception as e:  # noqa: BLE001
            self.lbl_count.setText(f"No se pudo leer el catálogo: {e}")

    def _run(self):
        root = self.txt_folder.text().strip()
        if not root:
            self.lbl_status.setText("Elegí una carpeta primero.")
            return
        self.settings.setValue("motor_last_dir", root)
        self._set_running(True)
        self.progress.setValue(0)
        self.lbl_status.setText("Recorriendo carpeta…")

        tdir = thumb_dir()

        def job(progress_cb):
            # Conexión propia dentro del hilo worker (sqlite no cruza hilos).
            conn = connect(db_path())
            try:
                return run_stage0(Repo(conn), root, tdir, progress_cb=progress_cb)
            finally:
                conn.close()

        self._worker = EngineWorker(job)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, done, total, msg):
        self.progress.setMaximum(total or 1)
        self.progress.setValue(done)
        self.lbl_status.setText(f"Procesando {done}/{total}…")

    def _on_done(self, stats):
        self._set_running(False)
        self.lbl_status.setText(
            f"Listo: {stats.added} nuevos, {stats.updated} actualizados, "
            f"{stats.skipped} sin cambios, {stats.duplicates} duplicados, "
            f"{stats.corrupt} corruptos."
        )
        self._refresh_count()
        self.catalogChanged.emit()

    def _on_failed(self, err):
        self._set_running(False)
        self.lbl_status.setText(f"Error: {err}")

    def _set_running(self, running: bool):
        self.btn_run.setEnabled(not running)
        self.btn_browse.setEnabled(not running)
        self.txt_folder.setEnabled(not running)
        self.progress.setVisible(running)
