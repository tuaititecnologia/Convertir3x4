"""Worker genérico para correr trabajo pesado del motor fuera del hilo de UI.

`EngineWorker` ejecuta una función `fn(progress_cb)` en un QThread y comunica
progreso/resultado/error por señales. La función recibe un callback
`progress_cb(done, total, msg)` que reemite la señal `progress`.

Importante: cada worker abre su PROPIA conexión al catálogo dentro de `fn`
(`with abrir_repo() as repo:`) y la cierra al terminar: una conexión de
PostgreSQL no se comparte entre hilos. Las consultas cortas de la interfaz van
por `ui/servicio_catalogo.py`.
"""

from PyQt6.QtCore import QThread, pyqtSignal


class EngineWorker(QThread):
    progress = pyqtSignal(int, int, str)   # done, total, mensaje/ruta
    finished_ok = pyqtSignal(object)       # resultado de fn
    failed = pyqtSignal(str)
    fallo = pyqtSignal(object)             # la excepción, para elegir el mensaje

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            result = self._fn(lambda d, t, m: self.progress.emit(d, t, m))
            self.finished_ok.emit(result)
        except Exception as e:  # noqa: BLE001 — reportamos cualquier fallo a la UI
            self.fallo.emit(e)
            self.failed.emit(f"{type(e).__name__}: {e}")
