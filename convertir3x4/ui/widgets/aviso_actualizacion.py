"""Aviso de versión nueva, arriba de los módulos (pedido del 23/11/2026).

«Hay una versión nueva de la aplicación (v0.8.1). [Actualizar] [Más tarde]».
En Windows, «Actualizar» la descarga y la instala al cerrar (la aplicación se
vuelve a abrir sola); en la Mac, «Descargar» abre la página de la descarga.
La consulta y la descarga van fuera del hilo de la interfaz (`EngineWorker`);
lo que no es interfaz está en `actualizacion.py`.
"""

import os

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from ... import actualizacion as act
from ...config import APP_VERSION
from ...workers import EngineWorker

_CADA = 6 * 60 * 60 * 1000          # la PC de ProPresenter queda abierta días


class AvisoActualizacion(QFrame):
    """Oculto hasta que haya algo que ofrecer. `lista` se emite cuando la
    versión nueva quedó descargada y hay que cerrar para instalarla."""
    lista = pyqtSignal()

    def __init__(self, parent=None, version_actual: str = APP_VERSION):
        super().__init__(parent)
        self.setObjectName("conn_conectando")
        self._actual = version_actual
        self._publicada: act.Publicada | None = None
        self._descartada: str | None = None
        self._worker = None
        self.orden_de_reemplazo: list[str] | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)
        self.lbl = QLabel()
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl, 1)
        self.btn_actualizar = QPushButton("Actualizar")
        self.btn_actualizar.setObjectName("btn_primary")
        self.btn_actualizar.clicked.connect(self._actualizar)
        lay.addWidget(self.btn_actualizar, 0, Qt.AlignmentFlag.AlignVCenter)
        self.btn_luego = QPushButton("Más tarde")
        self.btn_luego.clicked.connect(self._mas_tarde)
        lay.addWidget(self.btn_luego, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setVisible(False)
        self._timer = QTimer(self)
        self._timer.setInterval(_CADA)
        self._timer.timeout.connect(self.revisar)

    # ------------------------------------------------------------- consulta
    def empezar(self):
        """Lo llama __main__ (no las pruebas): revisa ahora y cada unas horas."""
        if act.numeros(self._actual) is None:
            return                              # en desarrollo no se ofrece nada
        act.limpiar_restos()
        self.revisar()
        self._timer.start()

    def revisar(self):
        if (self._worker is not None or self.orden_de_reemplazo
                or act.numeros(self._actual) is None):
            return
        self._worker = EngineWorker(lambda _p: act.ultima(), self)
        self._worker.finished_ok.connect(self.recibir)
        self._worker.finished.connect(self._soltar)
        self._worker.start()

    def _soltar(self):
        self._worker = None

    def recibir(self, p: act.Publicada | None):
        """Resultado de la consulta (público para las pruebas)."""
        if p is None or not act.hay_nueva(self._actual, p.version):
            return
        if self.isVisible() and self._publicada == p:
            return
        if p.version == self._descartada:
            return
        self._publicada = p
        self._ofrecer()

    def _ofrecer(self):
        p = self._publicada
        sola = act.se_instala_sola(p)
        self.lbl.setText(f"Hay una versión nueva de la aplicación ({p.version}). "
                         + ("Se descarga y se instala al cerrar la aplicación."
                            if sola else "Descárguela e instálela en lugar de esta."))
        self.btn_actualizar.setText("Actualizar" if sola else "Descargar")
        self.btn_actualizar.setEnabled(True)
        self.btn_luego.setVisible(True)
        self.setVisible(True)

    def _mas_tarde(self):
        """Se vuelve a ofrecer la próxima vez que se abra la aplicación."""
        self._descartada = self._publicada.version if self._publicada else None
        self.setVisible(False)

    # ------------------------------------------------------------- descarga
    def _actualizar(self):
        p = self._publicada
        if p is None:
            return
        if not act.se_instala_sola(p):
            QDesktopServices.openUrl(QUrl(p.pagina))
            return
        exe = act.exe_actual()
        destino = act.ruta_nueva(exe)
        self.btn_actualizar.setEnabled(False)
        self.btn_luego.setVisible(False)
        self.lbl.setText(f"Descargando la versión {p.version}…")
        self._worker = EngineWorker(lambda prog: act.descargar(
            p, destino, progreso=lambda h, t: prog(h, t, "")), self)
        self._worker.progress.connect(self._progreso)
        self._worker.finished_ok.connect(lambda nuevo: self.descargada(nuevo, exe))
        self._worker.fallo.connect(self._fallo)
        self._worker.finished.connect(self._soltar)
        self._worker.start()

    def _progreso(self, hecho: int, total: int, _msg: str):
        p = self._publicada
        self.lbl.setText(f"Descargando la versión {p.version}… {hecho * 100 // max(total, 1)} %")

    def _fallo(self, e: BaseException):
        texto = str(e) if isinstance(e, act.ErrorDeActualizacion) else (
            "No se pudo descargar la versión nueva. Intente nuevamente.")
        self.lbl.setText(texto)
        self.btn_actualizar.setText("Reintentar")
        self.btn_actualizar.setEnabled(True)
        self.btn_luego.setVisible(True)

    def descargada(self, nuevo: str, exe: str):
        """La versión nueva está al lado de la actual: se instala al cerrar."""
        self.orden_de_reemplazo = act.preparar_reemplazo(nuevo, exe)
        self._carpeta = os.path.dirname(os.path.abspath(exe))
        self.lbl.setText(f"La versión {self._publicada.version} está lista. La aplicación se "
                         "cierra y se vuelve a abrir ya actualizada.")
        self.btn_actualizar.setVisible(False)
        self.btn_luego.setVisible(False)
        self.lista.emit()

    def instalar_al_cerrar(self):
        """Lo llama la ventana cuando efectivamente se cierra."""
        if self.orden_de_reemplazo:
            act.lanzar_reemplazo(self.orden_de_reemplazo, self._carpeta)
            self.orden_de_reemplazo = None

    def cierre_cancelado(self):
        """El operador no quiso cerrar todavía (p. ej. Convertir estaba trabajando)."""
        self.lbl.setText(f"La versión {self._publicada.version} está lista. Se instala cuando "
                         "cierre la aplicación.")
