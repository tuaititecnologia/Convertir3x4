"""Avisos del catálogo: la PC se entera sola de lo que cambia en la base.

La app es cliente de un servidor (el NAS): lo nuevo que cataloga el NAS y las
etiquetas que pone la otra PC llegan como avisos de PostgreSQL (`LISTEN
catalogo`, ver la migración 3 en catalog/db.py). Este hilo los escucha con una
conexión propia (nunca la del servicio del catálogo) y emite `cambio`.

Si se corta la conexión, reintenta cada `REINTENTO` segundos. Al volver emite
`cambio` igual, porque mientras estuvo cortado pudo haberse perdido algún aviso.

Aparte escucha `carpetas_nas_leidas`: el catalogador terminó de releer las
carpetas del NAS (lo pidió la ventana de carpetas de alguna PC). Sale por
`carpetas` y por `hub().carpetas`, y no por `cambio`: Buscar no tiene que volver
a consultar cada vez que alguien abre el árbol.

Cada `PING` segundos manda una consulta mínima. Sin eso, una conexión que quedó
medio cortada (se cayó Netbird, se reinició el NAS) parece viva y el hilo se
queda esperando avisos que nunca llegan. También evita que el servidor corte la
sesión por inactiva (`idle_session_timeout`).
"""

import threading
import time

from PyQt6 import sip
from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..catalog.db import CANAL_CAMBIOS, CANAL_CARPETAS_LEIDAS

REINTENTO = 10.0
PING = 60.0


class _Hub(QObject):
    """Para las ventanas que no conocen al hilo de avisos (la de carpetas)."""
    carpetas = pyqtSignal()


_hub: _Hub | None = None


def hub() -> _Hub:
    global _hub
    if _hub is None or sip.isdeleted(_hub):
        _hub = _Hub()
    return _hub


class AvisosCatalogo(QThread):
    cambio = pyqtSignal()
    carpetas = pyqtSignal()

    def __init__(self, abrir=None, parent=None):
        super().__init__(parent)
        if abrir is None:
            from ..appdata import abrir_repo as abrir
        self._abrir = abrir
        self._parar = threading.Event()
        self._reconectar = threading.Event()
        self.carpetas.connect(hub().carpetas)
        self.setObjectName("avisos-catalogo")

    def detener(self):
        self._parar.set()
        self.wait(5000)

    def reconectar(self):
        """Cambió la conexión al catálogo (otro servidor o clave): se vuelve a
        conectar ya, con los datos nuevos."""
        self._reconectar.set()

    def run(self):
        conectado_antes = False
        while not self._parar.is_set():
            repo = None
            try:
                repo = self._abrir()
                repo.conn.execute(f"LISTEN {CANAL_CARPETAS_LEIDAS}")
                repo.conn.execute(f"LISTEN {CANAL_CAMBIOS}")
                if conectado_antes:
                    self.cambio.emit()      # se reconectó: pudo perderse algo
                conectado_antes = True
                ultimo_ping = time.monotonic()
                while not self._parar.is_set() and not self._reconectar.is_set():
                    canales = {n.channel for n in repo.conn.notifies(timeout=1.0)}
                    if CANAL_CARPETAS_LEIDAS in canales:
                        self.carpetas.emit()
                    if canales - {CANAL_CARPETAS_LEIDAS}:
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
            if self._reconectar.is_set():
                self._reconectar.clear()
                conectado_antes = True      # al volver, `cambio`: todo se pone al día
                continue
            self._parar.wait(REINTENTO)
