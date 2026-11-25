"""Opciones avanzadas del catálogo: escondidas detrás del ⚙ de Catalogar.

- Carpetas que se catalogan: «Elegir carpetas…», que abre el árbol de
  carpetas del NAS (widgets/arbol_carpetas.py). Sin lista acá (decisión del
  25/11/2026): el árbol ya muestra qué se cataloga.

- Conexión al catálogo (widgets/conexion_catalogo.py).

- Limpiar el catálogo: quitar las fotos y videos que la catalogación marcó
  como faltantes (su archivo ya no está en su carpeta del NAS). En dos pasos,
  ver y después quitar con confirmación, porque se pierden sus etiquetas.
  Un medio solo se marca como faltante si su carpeta se pudo leer entera, así
  que un NAS caído nunca vacía el catálogo.
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox,
    QProgressBar, QPushButton, QVBoxLayout,
)

from ...appdata import abrir_repo, catalog_thumb_dir
from ...catalog.cache_miniaturas import CacheMiniaturas
from ...workers import EngineWorker
from ..servicio_catalogo import servicio, texto_para
from .dialogos import crecer


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
            "Se eligen sobre las carpetas del NAS, en un árbol.")
        ayuda.setObjectName("muted")
        ayuda.setWordWrap(True)
        lay.addWidget(ayuda)
        self.lbl_carpetas = QLabel("")
        self.lbl_carpetas.setWordWrap(True)
        self.lbl_carpetas.setVisible(False)
        lay.addWidget(self.lbl_carpetas)
        fila = QHBoxLayout()
        self.btn_elegir_carpetas = QPushButton("Elegir carpetas…")
        self.btn_elegir_carpetas.clicked.connect(self.elegir_carpetas)
        fila.addWidget(self.btn_elegir_carpetas)
        fila.addStretch()
        lay.addLayout(fila)
        self._releer_carpetas()
        lay.addSpacing(14)

        # --- conexión
        t = QLabel("Conexión al catálogo")
        t.setObjectName("section_title")
        lay.addWidget(t)
        ayuda = QLabel("El servidor del catálogo, su clave y dónde está la carpeta multimedia "
                       "del NAS en esta PC.")
        ayuda.setObjectName("muted")
        ayuda.setWordWrap(True)
        lay.addWidget(ayuda)
        fila = QHBoxLayout()
        self.btn_conexion = QPushButton("Conexión al catálogo…")
        self.btn_conexion.clicked.connect(self._abrir_conexion)
        fila.addWidget(self.btn_conexion)
        fila.addStretch()
        lay.addLayout(fila)
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
    def _releer_carpetas(self):
        def ok(alcance):
            # Qué se cataloga lo muestra el árbol; acá solo se avisa si no hay nada
            texto = "" if alcance else ("Todavía no se cataloga ninguna carpeta. Elija las "
                                        "carpetas del NAS que quiere encontrar en Buscar.")
            self.lbl_carpetas.setText(texto)
            self.lbl_carpetas.setVisible(bool(texto))
            crecer(self)
        def error(e):
            self.lbl_carpetas.setText(texto_para(e))
            self.lbl_carpetas.setVisible(True)
            crecer(self)
        self._srv.pedir(lambda repo: repo.alcance(), ok=ok, error=error)

    def elegir_carpetas(self):
        from .arbol_carpetas import ElegirCarpetas
        dlg = ElegirCarpetas(self, srv=self._srv)
        dlg.guardado.connect(self._carpetas_guardadas)
        dlg.exec()

    def _carpetas_guardadas(self):
        self._releer_carpetas()
        self.catalogChanged.emit()

    def _abrir_conexion(self):
        from .conexion_catalogo import abrir_conexion
        abrir_conexion(self)

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
        crecer(self)

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
        crecer(self)
