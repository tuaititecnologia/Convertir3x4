"""Avisos del catálogo: la PC se entera sola de lo que cambia en la base.

La app es cliente de un servidor (el NAS): lo nuevo que cataloga el NAS y las
etiquetas que pone la otra PC llegan como avisos de PostgreSQL (`LISTEN
catalogo`, ver la migración 3 en catalog/db.py). Este hilo los escucha con una
conexión propia (nunca la del servicio del catálogo) y emite `cambio`.

Si se corta la conexión, reintenta cada `REINTENTO` segundos. Al volver emite
`cambio` igual, porque mientras estuvo cortado pudo haberse perdido algún aviso.
"""

import threading

from PyQt6.QtCore import QThread, pyqtSignal

from ..catalog.db import CANAL_CAMBIOS

REINTENTO = 10.0


class AvisosCatalogo(QThread):
    cambio = pyqtSignal()

    def __init__(self, abrir=None, parent=None):
        super().__init__(parent)
        if abrir is None:
            from ..appdata import abrir_repo as abrir
        self._abrir = abrir
        self._parar = threading.Event()
        self.setObjectName("avisos-catalogo")

    def detener(self):
        self._parar.set()
        self.wait(5000)

    def run(self):
        conectado_antes = False
        while not self._parar.is_set():
            repo = None
            try:
                repo = self._abrir()
                repo.conn.execute(f"LISTEN {CANAL_CAMBIOS}")
                if conectado_antes:
                    self.cambio.emit()      # se reconectó: pudo perderse algo
                conectado_antes = True
                while not self._parar.is_set():
                    if any(True for _ in repo.conn.notifies(timeout=1.0)):
                        self.cambio.emit()
            except Exception:  # noqa: BLE001 — sin NAS o sin configurar: se reintenta
                pass
            finally:
                if repo is not None:
                    try:
                        repo.close()
                    except Exception:  # noqa: BLE001
                        pass
            self._parar.wait(REINTENTO)
