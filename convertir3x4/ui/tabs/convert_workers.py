"""Trabajo en segundo plano de la pestaña Convertir (QThread + señales).

La lógica vive sin Qt en convert/batch.py, convert/clips.py y convert/download.py;
acá solo se corre fuera del hilo de la interfaz y se avisa el avance.
"""

import threading

from PyQt6.QtCore import QThread, pyqtSignal

from ...convert import batch, clips, download


class _Cancellable(QThread):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def cancelled(self) -> bool:
        return self._cancel.is_set()


class ThumbWorker(_Cancellable):
    """Miniatura, tira de cuadros y duración de cada video, de a uno."""
    ready = pyqtSignal(str, object)      # ruta, clips.ClipThumbs
    failed = pyqtSignal(str, str)        # ruta, motivo

    def __init__(self, paths: list[str], cache_dir: str, parent=None):
        super().__init__(parent)
        self._paths = list(paths)
        self._cache = cache_dir

    def run(self):
        for p in self._paths:
            if self.cancelled():
                return
            try:
                self.ready.emit(p, clips.clip_thumbs(p, self._cache))
            except FileNotFoundError:
                self.failed.emit(p, "No se encontró el archivo (¿se desconectó el disco o el NAS?).")
            except Exception as e:  # noqa: BLE001 — se muestra en la grilla
                self.failed.emit(p, f"No se pudo leer el video. {e}"[:300])


class ConvertWorker(_Cancellable):
    """Corre los trabajos de un lote, en orden. Un error no corta el lote."""
    job_progress = pyqtSignal(object, float)   # batch.Job, 0..1
    job_done = pyqtSignal(object)              # batch.Result
    finished_all = pyqtSignal(list, bool)      # [batch.Result], cancelado

    def __init__(self, jobs, out_dir: str, opts: batch.Options, parent=None):
        super().__init__(parent)
        self._jobs, self._out_dir, self._opts = jobs, out_dir, opts

    def run(self):
        results = []
        for job in self._jobs:
            if self.cancelled():
                break
            try:
                r = batch.run(job, self._out_dir, self._opts,
                              lambda f, j=job: self.job_progress.emit(j, f), self.cancelled)
            except clips.Cancelled:
                break
            results.append(r)
            self.job_done.emit(r)
        self.finished_all.emit(results, self.cancelled())


class DownloadWorker(_Cancellable):
    """Baja un link con yt-dlp."""
    progress = pyqtSignal(str, float, str)   # link, 0..1, texto
    done = pyqtSignal(str, str)              # link, ruta del MP4
    failed = pyqtSignal(str, str)            # link, motivo ("" si se canceló)

    def __init__(self, url: str, out_dir: str, tools_dir: str, parent=None):
        super().__init__(parent)
        self.url, self._out, self._tools = url, out_dir, tools_dir

    def run(self):
        try:
            path = download.download(self.url, self._out, self._tools,
                                     lambda f, m: self.progress.emit(self.url, f, m),
                                     self.cancelled)
        except clips.Cancelled:
            self.failed.emit(self.url, "")
        except download.DownloadError as e:
            self.failed.emit(self.url, str(e))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(self.url, f"No se pudo bajar el video. {e}"[:300])
        else:
            self.done.emit(self.url, path)
