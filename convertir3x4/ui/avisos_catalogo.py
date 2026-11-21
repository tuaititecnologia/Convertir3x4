"""Avisos del catálogo: la PC se entera sola de lo que cambia en la base.

La app es cliente de un servidor (el NAS): lo nuevo que cataloga el NAS y las
etiquetas que pone la otra PC llegan como avisos de PostgreSQL (`LISTEN
catalogo`, ver la migración 3 en catalog/db.py). Este hilo los escucha con una
conexión propia (nunca la del servicio del catálogo) y emite `cambio`.

Si se corta la conexión, reintenta cada `REINTENTO` segundos. Al volver emite
`cambio` igual, porque mientras estuvo cortado pudo haberse perdido algún aviso.

Cada `PING` segundos manda una consulta mínima. Sin eso, una conexión que quedó
medio cortada (se cayó Netbird, se reinició el NAS) parece viva y el hilo se
queda esperando avisos que nunca llegan. También evita que el servidor corte la
sesión por inactiva (`idle_session_timeout`).
"""

import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal

from ..catalog.db import CANAL_CAMBIOS

REINTENTO = 10.0
PING = 60.0


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
                ultimo_ping = time.monotonic()
                while not self._parar.is_set():
                    if any(True for _ in repo.conn.notifies(timeout=1.0)):
                        self.cambio.emit()
                    if time.monotonic() - ultimo_ping >= PING:
                        repo.conn.execute("SELECT 1")
                        ultimo_ping = time.monotonic()
            except Exception:  # noqa: BLE001 — sin NAS o sin configurar: se reintenta
                pass
            finally:
                if repo is not None:
                    try:
                        repo.close()
                    except Exception:  # noqa: BLE001
                        pass
            self._parar.wait(REINTENTO)
