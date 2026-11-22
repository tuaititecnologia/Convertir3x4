"""Opciones avanzadas del catálogo: escondidas detrás del ⚙ de Catalogar.

- Carpetas que se catalogan (desde la v0.8.0 se manejan solo desde acá; antes
  estaban en el compose del NAS): agregar una carpeta de la raíz y quitarla.
  Al quitarla se ofrece sacar también del catálogo sus fotos y videos (con sus
  etiquetas), con confirmación; si no, quedan pero el NAS no las revisa más.

- Limpiar el catálogo: quitar las fotos y videos que la catalogación marcó
  como faltantes (su archivo ya no está en su carpeta del NAS). En dos pasos,
  ver y después quitar con confirmación, porque se pierden sus etiquetas.
  Un medio solo se marca como faltante si su carpeta se pudo leer entera, así
  que un NAS caído nunca vacía el catálogo.
"""

import socket

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QMessageBox,
    QProgressBar, QPushButton, QVBoxLayout,
)

from ...appdata import abrir_repo, catalog_thumb_dir
from ...catalog.cache_miniaturas import CacheMiniaturas
from ...workers import EngineWorker
from ...catalog import rutas
from ...catalog.db import ConfiguracionInvalida
from ..servicio_catalogo import servicio, texto_para


class CatalogoAvanzado(QDialog):
    catalogChanged = pyqtSignal()

    def __init__(self, parent=None, carpetas=(), abrir=None, raiz: str = "", srv=None):
        super().__init__(parent)
        self.setWindowTitle("Opciones avanzadas del catálogo")
        self.setMinimumWidth(500)
        self._abrir = abrir or abrir_repo
        self._srv = srv or servicio()
        self._raiz = raiz
        self._carpetas = tuple(carpetas)
        self._faltan = []
        self._worker: EngineWorker | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(8)

        # --- carpetas
        t = QLabel("Carpetas que se catalogan")
        t.setObjectName("section_title")
        lay.addWidget(t)
        ayuda = QLabel(
            "El NAS revisa estas carpetas todas las noches y cuando se pide «Catalogar». "
            "Deben estar dentro de la carpeta compartida del NAS.")
        ayuda.setObjectName("muted")
        ayuda.setWordWrap(True)
        lay.addWidget(ayuda)
        self.lst_carpetas = QListWidget()
        self.lst_carpetas.setAccessibleName("Carpetas que se catalogan")
        self.lst_carpetas.setMaximumHeight(130)
        self.lst_carpetas.itemSelectionChanged.connect(self._botones_carpetas)
        lay.addWidget(self.lst_carpetas)
        fila = QHBoxLayout()
        self.btn_agregar_carpeta = QPushButton("Agregar carpeta…")
        self.btn_agregar_carpeta.clicked.connect(self._agregar_carpeta)
        fila.addWidget(self.btn_agregar_carpeta)
        self.btn_quitar_carpeta = QPushButton("Quitar de la lista…")
        self.btn_quitar_carpeta.clicked.connect(self._quitar_carpeta)
        fila.addWidget(self.btn_quitar_carpeta)
        fila.addStretch()
        lay.addLayout(fila)
        self.lbl_carpetas = QLabel("")
        self.lbl_carpetas.setWordWrap(True)
        self.lbl_carpetas.setVisible(False)
        lay.addWidget(self.lbl_carpetas)
        self._mostrar_carpetas(self._carpetas)
        lay.addSpacing(14)

        # --- limpiar
        t = QLabel("Limpiar el catálogo")
        t.setObjectName("section_title")
        lay.addWidget(t)
        ayuda = QLabel(
            "Muestra las fotos y videos que siguen en el catálogo pero cuyo archivo ya no "
            "está en su carpeta del NAS (porque se movió o se borró), y permite quitarlos. "
            "Si un archivo solo cambió de carpeta, al catalogar se reconoce solo y conserva "
            "sus etiquetas: conviene catalogar antes de limpiar.")
        ayuda.setObjectName("muted")
        ayuda.setWordWrap(True)
        lay.addWidget(ayuda)
        lay.addSpacing(6)

        fila = QHBoxLayout()
        self.btn_buscar = QPushButton("Ver lo que ya no está")
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

    # ------------------------------------------------------------- carpetas
    def _mostrar_carpetas(self, carpetas):
        self._carpetas = tuple(carpetas)
        self.lst_carpetas.clear()
        self.lst_carpetas.addItems(self._carpetas)
        if not self._carpetas:
            self._aviso_carpetas("Todavía no hay ninguna. Agregue las carpetas del NAS que "
                                 "quiere encontrar en Buscar.")
        self._botones_carpetas()

    def _botones_carpetas(self):
        self.btn_quitar_carpeta.setEnabled(bool(self.lst_carpetas.selectedItems()))

    def _aviso_carpetas(self, texto: str):
        self.lbl_carpetas.setText(texto)
        self.lbl_carpetas.setVisible(bool(texto))

    def _releer_carpetas(self, aviso: str = ""):
        def ok(carpetas):
            self._mostrar_carpetas(carpetas)
            if aviso:
                self._aviso_carpetas(aviso)
            self.catalogChanged.emit()
        self._srv.pedir(lambda repo: repo.carpetas_catalogadas(), ok=ok,
                        error=lambda e: self._aviso_carpetas(texto_para(e)))

    def _agregar_carpeta(self):
        ruta = QFileDialog.getExistingDirectory(self, "Elija la carpeta del NAS", self._raiz)
        if ruta:
            self.agregar_carpeta(ruta)

    def agregar_carpeta(self, ruta: str):
        rel = rutas.a_relativa(ruta, self._raiz) if self._raiz else None
        if not rel:
            self._aviso_carpetas("Esa carpeta no está dentro de la carpeta compartida del NAS "
                                 f"({self._raiz}). Elija una carpeta dentro de ella.")
            return
        quien = socket.gethostname()

        def fallo(e):
            if isinstance(e, ConfiguracionInvalida):
                texto = str(e)
                if "ya está" in texto:
                    self._aviso_carpetas(f"«{rel}» ya está en la lista.")
                elif "dentro de" in texto:
                    self._aviso_carpetas(f"«{rel}» se superpone con otra carpeta de la lista "
                                         "(una está dentro de la otra). Deje solo una.")
                else:
                    self._aviso_carpetas("Elija una carpeta dentro de la carpeta compartida "
                                         "del NAS.")
            else:
                self._aviso_carpetas(texto_para(e))
        self._srv.pedir(lambda repo: repo.agregar_carpeta(rel, quien),
                        ok=lambda c: self._releer_carpetas(
                            f"Se agregó «{c}». El NAS la revisa esta noche, o antes con "
                            "«Catalogar»."),
                        error=fallo)

    def _quitar_carpeta(self):
        items = self.lst_carpetas.selectedItems()
        if not items:
            return
        ruta = items[0].text()
        self._srv.pedir(lambda repo: len(repo.medios_en_carpeta(ruta)),
                        ok=lambda n: self._confirmar_quitar_carpeta(ruta, n),
                        error=lambda e: self._aviso_carpetas(texto_para(e)))

    def _confirmar_quitar_carpeta(self, ruta: str, n: int):
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Quitar carpeta")
        msg.setText(f"¿Quitar «{ruta}» de las carpetas que se catalogan?")
        cuantos = "1 foto o video" if n == 1 else f"{n} fotos y videos"
        if n:
            msg.setInformativeText(
                f"Tiene {cuantos} en el catálogo. Puede quitarlos también del catálogo (se "
                "pierden sus etiquetas, también en la otra PC; los archivos no se tocan) o "
                "dejarlos: siguen en Buscar, pero el NAS no los revisa más.")
            con = msg.addButton(f"Quitar también {'la foto o video' if n == 1 else f'los {n}'}",
                                QMessageBox.ButtonRole.DestructiveRole)
            sin = msg.addButton("Solo de la lista", QMessageBox.ButtonRole.AcceptRole)
        else:
            msg.setInformativeText("No tiene fotos ni videos en el catálogo.")
            con = None
            sin = msg.addButton("Quitar de la lista", QMessageBox.ButtonRole.AcceptRole)
        cancelar = msg.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(cancelar)
        msg.setEscapeButton(cancelar)
        msg.exec()
        elegido = msg.clickedButton()
        if elegido is con:
            self.quitar_carpeta(ruta, con_sus_medios=True)
        elif elegido is sin:
            self.quitar_carpeta(ruta, con_sus_medios=False)

    def quitar_carpeta(self, ruta: str, con_sus_medios: bool):
        def hacer(repo):
            ids = repo.quitar_carpeta(ruta, con_sus_medios)
            if ids:
                CacheMiniaturas(catalog_thumb_dir()).olvidar(ids)
            return len(ids)

        def ok(n):
            self._releer_carpetas(
                f"Se quitó «{ruta}» de la lista" + (
                    "." if not con_sus_medios else
                    " y 1 foto o video del catálogo." if n == 1 else
                    f" y {n} fotos y videos del catálogo."))
        self._srv.pedir(hacer, ok=ok, error=lambda e: self._aviso_carpetas(texto_para(e)))

    # ------------------------------------------------------------- buscar
    def buscar(self):
        abrir = self._abrir

        def fn(_cb):
            with abrir() as repo:
                return repo.faltantes()
        self._correr(fn, self._mostrar_faltantes)

    def _mostrar_faltantes(self, faltan):
        self._faltan = faltan
        n = len(faltan)
        self._resultado(
            "No hay nada para limpiar: todos los archivos están en su lugar." if not n else
            "Hay 1 foto o video cuyo archivo ya no está en su carpeta." if n == 1 else
            f"Hay {n} fotos y videos cuyo archivo ya no está en su carpeta.")
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
                    "cuyo archivo ya no está en su carpeta?")
        msg.setInformativeText(f"{ejemplos}\n\nSe perderán sus etiquetas, también en la otra PC. "
                               "Esta acción no se puede deshacer.")
        quitar = msg.addButton("Quitar del catálogo", QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(msg.buttons()[-1])
        msg.exec()
        if msg.clickedButton() is quitar:
            self.quitar()

    def quitar(self):
        abrir, ids = self._abrir, [m.id for m in self._faltan]

        def fn(_cb):
            with abrir() as repo:
                repo.delete_media(ids)
            CacheMiniaturas(catalog_thumb_dir()).olvidar(ids)
            return len(ids)
        self._correr(fn, self._quitado)

    def _quitado(self, n):
        self._faltan = []
        self.btn_quitar.setVisible(False)
        self._resultado("Se quitó 1 elemento del catálogo." if n == 1 else
                        f"Se quitaron {n} elementos del catálogo.")
        self.catalogChanged.emit()

    # ------------------------------------------------------------- comunes
    def _correr(self, fn, on_ok):
        self.btn_buscar.setEnabled(False)
        self.btn_quitar.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        w = EngineWorker(fn)
        self._al_terminar = on_ok
        # Métodos (no lambdas): si la ventana se cierra antes, Qt corta la conexión.
        w.progress.connect(self._avance)
        w.finished_ok.connect(self._ok)
        w.fallo.connect(self._fallo)
        self._worker = w
        w.start()

    def _avance(self, hecho, total, _msg):
        self.progress.setRange(0, total or 1)
        self.progress.setValue(hecho)

    def _ok(self, resultado):
        self._fin()
        self._al_terminar(resultado)

    def _fallo(self, e):
        self._fin()
        self._resultado(texto_para(e))

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
