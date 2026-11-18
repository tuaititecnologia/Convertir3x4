"""El catálogo visto desde la interfaz: un hilo con su propia conexión.

La interfaz nunca toca la base directamente: si el NAS no responde, conectar o
consultar puede tardar segundos y congelaría la ventana. Buscar y Catalogar le
piden cosas a este servicio con `pedir(fn, ok, error)`: `fn(repo)` corre en el
hilo del catálogo, de a una por vez y en orden, y `ok(resultado)` o
`error(excepción)` vuelven al hilo de la interfaz.

Si se corta la conexión, la próxima consulta reconecta sola. El estado de la
conexión ("conectando", "ok" o "error", con un texto para el operador) se
anuncia con `estado_cambiado`, para mostrarlo como en la Grabadora.

Hay un solo servicio en toda la app (`servicio()`); su hilo se cierra al salir.
Las pasadas largas (catalogar, analizar) no van acá: usan `EngineWorker` con
una conexión propia, para no frenar la búsqueda mientras tanto.
"""

import atexit
import itertools

import psycopg
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QObject, QThread, pyqtSignal, pyqtSlot

from ..catalog.db import CatalogoIncompatible, CatalogoNoConfigurado, CatalogoNoDisponible

_ERRORES_DE_CONEXION = (CatalogoNoDisponible, CatalogoIncompatible, CatalogoNoConfigurado)


def texto_para(e: BaseException) -> str:
    """Lo que ve el operador cuando algo del catálogo falla."""
    if isinstance(e, _ERRORES_DE_CONEXION):
        return e.MENSAJE
    return ("No se pudo completar la operación. Intente nuevamente; si el problema "
            "persiste, comuníquese con el responsable de la aplicación.")


class _Trabajador(QObject):
    hecho = pyqtSignal(int, object)
    fallo = pyqtSignal(int, object)

    def __init__(self, abrir):
        super().__init__()
        self._abrir = abrir
        self._repo = None

    @pyqtSlot(int, object)
    def ejecutar(self, pid: int, fn):
        try:
            if self._repo is None:
                self._repo = self._abrir()
            self.hecho.emit(pid, fn(self._repo))
        except (psycopg.OperationalError, psycopg.InterfaceError) as e:
            self._soltar()
            self.fallo.emit(pid, CatalogoNoDisponible(str(e).strip()))
        except _ERRORES_DE_CONEXION as e:
            self._soltar()
            self.fallo.emit(pid, e)
        except Exception as e:  # noqa: BLE001 — cualquier otro error vuelve a quien pidió
            self.fallo.emit(pid, e)

    @pyqtSlot()
    def cerrar(self):
        self._soltar()

    def _soltar(self):
        if self._repo is not None:
            try:
                self._repo.close()
            except Exception:  # noqa: BLE001
                pass
            self._repo = None


class ServicioCatalogo(QObject):
    estado_cambiado = pyqtSignal(str, str)     # "conectando" | "ok" | "error", texto
    _pedido = pyqtSignal(int, object)
    _cierre = pyqtSignal()

    def __init__(self, abrir=None, parent=None):
        super().__init__(parent)
        if abrir is None:
            from ..appdata import abrir_repo as abrir
        self._ids = itertools.count(1)
        self._esperando: dict[int, tuple] = {}
        self.estado = "conectando"
        self.texto = ""
        self._hilo = QThread()
        self._hilo.setObjectName("catalogo")
        self._trabajador = _Trabajador(abrir)
        self._trabajador.moveToThread(self._hilo)
        self._pedido.connect(self._trabajador.ejecutar)
        self._cierre.connect(self._trabajador.cerrar)
        self._trabajador.hecho.connect(self._al_terminar)
        self._trabajador.fallo.connect(self._al_fallar)
        self._hilo.start()

    def pedir(self, fn, ok=None, error=None) -> int:
        """Corre `fn(repo)` en el hilo del catálogo. Devuelve el número de pedido."""
        pid = next(self._ids)
        self._esperando[pid] = (ok, error)
        if self.estado == "error":
            self._set_estado("conectando", "")
        self._pedido.emit(pid, fn)
        return pid

    def cerrar(self):
        try:
            if self._hilo.isRunning():
                self._cierre.emit()
                self._hilo.quit()
                self._hilo.wait(5000)
        except RuntimeError:
            pass    # al salir, Qt ya lo borró: el hilo terminó con la app

    # ------------------------------------------------------------- interno
    def _al_terminar(self, pid: int, valor):
        ok, _ = self._esperando.pop(pid, (None, None))
        self._set_estado("ok", "")
        _llamar(ok, valor)

    def _al_fallar(self, pid: int, e):
        _, error = self._esperando.pop(pid, (None, None))
        if isinstance(e, _ERRORES_DE_CONEXION):
            self._set_estado("error", texto_para(e))
        _llamar(error, e)

    def _set_estado(self, estado: str, texto: str):
        if (estado, texto) != (self.estado, self.texto):
            self.estado, self.texto = estado, texto
            self.estado_cambiado.emit(estado, texto)


def _llamar(cb, valor):
    if cb is None:
        return
    try:
        cb(valor)
    except RuntimeError as e:
        # La pestaña que pidió ya se cerró (objeto de Qt borrado): no hay a quién avisar.
        if "deleted" not in str(e):
            raise


_servicio: ServicioCatalogo | None = None


def servicio() -> ServicioCatalogo:
    """El servicio del catálogo de la app (se crea la primera vez)."""
    global _servicio
    if _servicio is None or sip.isdeleted(_servicio):
        _servicio = ServicioCatalogo()
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(_servicio.cerrar)
        atexit.register(_servicio.cerrar)
    return _servicio
