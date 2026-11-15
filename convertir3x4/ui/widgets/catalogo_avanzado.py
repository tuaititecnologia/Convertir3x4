"""Opciones avanzadas del catálogo: escondidas detrás del ⚙ de Catalogar.

Por ahora, limpiar el catálogo: quitar las fotos y videos cuyo archivo ya no
existe (se movió o se borró). En dos pasos, buscar y después quitar con
confirmación, porque se pierden sus tags. Lo que está en un disco o en el NAS
desconectado NUNCA se toca: con el NAS apagado se vaciaría todo el catálogo.
"""

import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
    QPushButton, QVBoxLayout,
)

from ...appdata import db_path
from ...catalog.db import connect
from ...catalog.repo import Repo
from ...catalog.ubicacion import friendly_root, revisar_faltantes
from ...workers import EngineWorker


class CatalogoAvanzado(QDialog):
    catalogChanged = pyqtSignal()

    def __init__(self, parent=None, ruta_db: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Opciones avanzadas del catálogo")
        self.setMinimumWidth(500)
        self._db = ruta_db or db_path()
        self._faltan = []
        self._worker: EngineWorker | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(8)

        t = QLabel("Limpiar el catálogo")
        t.setObjectName("section_title")
        lay.addWidget(t)
        ayuda = QLabel(
            "Busca fotos y videos que siguen en el catálogo pero cuyo archivo ya no existe "
            "(se movió o se borró) y los quita. Lo que está en un disco o en el NAS "
            "desconectado no se toca: vuelve solo cuando se conecta.")
        ayuda.setObjectName("muted")
        ayuda.setWordWrap(True)
        lay.addWidget(ayuda)
        lay.addSpacing(6)

        fila = QHBoxLayout()
        self.btn_buscar = QPushButton("Buscar lo que ya no existe")
        self.btn_buscar.clicked.connect(self.buscar)
        fila.addWidget(self.btn_buscar)
        fila.addStretch()
        lay.addLayout(fila)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        self.lbl_resultado = QLabel("")
        self.lbl_resultado.setWordWrap(True)
        self.lbl_resultado.setVisible(False)
        lay.addWidget(self.lbl_resultado)

        fila2 = QHBoxLayout()
        self.btn_quitar = QPushButton("Quitar del catálogo…")
        self.btn_quitar.setObjectName("btn_primary")
        self.btn_quitar.setVisible(False)
        self.btn_quitar.clicked.connect(self._confirmar_quitar)
        fila2.addWidget(self.btn_quitar)
        fila2.addStretch()
        lay.addLayout(fila2)

        lay.addSpacing(10)
        botones = QDialogButtonBox()
        cerrar = botones.addButton("Cerrar", QDialogButtonBox.ButtonRole.RejectRole)
        cerrar.clicked.connect(self.reject)
        lay.addWidget(botones)

    # ------------------------------------------------------------- buscar
    def buscar(self):
        ruta = self._db

        def fn(progress_cb):
            conn = connect(ruta)
            try:
                return revisar_faltantes(Repo(conn).all_media(), progress_cb)
            finally:
                conn.close()
        self._correr(fn, self._mostrar_revision)

    def _mostrar_revision(self, rev):
        self._faltan = rev.faltan
        n = len(rev.faltan)
        partes = ["No hay nada para limpiar: todos los archivos están en su lugar." if not n else
                  "Hay 1 foto o video cuyo archivo ya no existe." if n == 1 else
                  f"Hay {n} fotos y videos cuyo archivo ya no existe."]
        if rev.sin_revisar:
            donde = " y ".join(friendly_root(r) for r in rev.raices_desconectadas)
            partes.append(f"No se revisaron {rev.sin_revisar} porque {donde} no está conectado; "
                          "esos no se tocan.")
        self._resultado(" ".join(partes))
        self.btn_quitar.setText("Quitar 1 del catálogo…" if n == 1 else f"Quitar {n} del catálogo…")
        self.btn_quitar.setVisible(n > 0)

    # ------------------------------------------------------------- quitar
    def _confirmar_quitar(self):
        n = len(self._faltan)
        if not n:
            return
        ejemplos = "\n".join(f"  • {m.filename}" for m in self._faltan[:5])
        if n > 5:
            ejemplos += f"\n  … y {n - 5} más"
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Quitar del catálogo")
        msg.setText(f"¿Quitar del catálogo {'1 foto o video' if n == 1 else f'{n} fotos y videos'} "
                    "cuyo archivo ya no existe?")
        msg.setInformativeText(f"{ejemplos}\n\nSe pierden sus tags. No se puede deshacer.")
        quitar = msg.addButton("Quitar del catálogo", QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(msg.buttons()[-1])
        msg.exec()
        if msg.clickedButton() is quitar:
            self.quitar()

    def quitar(self):
        ruta, ids = self._db, [m.id for m in self._faltan]

        def fn(_cb):
            conn = connect(ruta)
            try:
                huerfanas = Repo(conn).delete_media(ids)
            finally:
                conn.close()
            for t in huerfanas:            # miniaturas que nadie más usa
                try:
                    os.remove(t)
                except OSError:
                    pass
            return len(ids)
        self._correr(fn, self._quitado)

    def _quitado(self, n):
        self._faltan = []
        self.btn_quitar.setVisible(False)
        self._resultado("Listo: se quitó 1 del catálogo." if n == 1 else
                        f"Listo: se quitaron {n} del catálogo.")
        self.catalogChanged.emit()

    # ------------------------------------------------------------- comunes
    def _correr(self, fn, on_ok):
        self.btn_buscar.setEnabled(False)
        self.btn_quitar.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        w = EngineWorker(fn)
        w.progress.connect(lambda d, t, _m: (self.progress.setRange(0, t or 1),
                                             self.progress.setValue(d)))

        def ok(res):
            self._fin()
            on_ok(res)

        def fallo(_msg):
            self._fin()
            self._resultado("No se pudo terminar. Probá de nuevo.")
        w.finished_ok.connect(ok)
        w.failed.connect(fallo)
        self._worker = w
        w.start()

    def _fin(self):
        if self._worker is not None:
            self._worker.wait()
        self._worker = None
        self.progress.setVisible(False)
        self.btn_buscar.setEnabled(True)
        self.btn_quitar.setEnabled(True)

    def _resultado(self, texto):
        self.lbl_resultado.setText(texto)
        self.lbl_resultado.setVisible(bool(texto))
