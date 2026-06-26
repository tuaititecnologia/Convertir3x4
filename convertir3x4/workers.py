"""Worker genérico para correr trabajo pesado del motor fuera del hilo de UI.

`EngineWorker` ejecuta una función `fn(progress_cb)` en un QThread y comunica
progreso/resultado/error por señales. La función recibe un callback
`progress_cb(done, total, msg)` que reemite la señal `progress`.

Importante: cada worker debe abrir su PROPIA conexión SQLite dentro de `fn`
(sqlite3 no comparte conexiones entre hilos). Con WAL, un escritor en el worker
y lectores en el hilo de UI conviven sin problema.
"""

from PyQt6.QtCore import QThread, pyqtSignal


class EngineWorker(QThread):
    progress = pyqtSignal(int, int, str)   # done, total, mensaje/ruta
    finished_ok = pyqtSignal(object)       # resultado de fn
    failed = pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            result = self._fn(lambda d, t, m: self.progress.emit(d, t, m))
            self.finished_ok.emit(result)
        except Exception as e:  # noqa: BLE001 — reportamos cualquier fallo a la UI
            self.failed.emit(f"{type(e).__name__}: {e}")
