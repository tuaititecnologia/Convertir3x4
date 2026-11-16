"""Bajar videos de YouTube, Facebook y otros sitios con yt-dlp. Sin Qt.

yt-dlp y deno NO vienen dentro de la app: se bajan la primera vez que se usan a
`tools_dir` (la carpeta de datos de la app). Motivo (docs/convertir-fotos-y-videos.md):
YouTube rompe yt-dlp seguido; si viniera en el .exe, cada arreglo obligaría a
sacar una versión nueva. Así se actualiza solo (`yt-dlp -U`, una vez por día).

- yt-dlp: la versión oficial "ya descomprimida" de GitHub (yt-dlp_macos.zip,
  yt-dlp_win.zip, yt-dlp_linux.zip), verificada contra el SHA2-256SUMS de la
  misma versión. Trae yt-dlp-ejs adentro. NO la de un solo archivo: esa se
  descomprime en cada uso y en la Mac tardaba ~16 s en arrancar cada vez (el
  sistema revisa lo descomprimido); la descomprimida arranca en ~0,2 s (15/11/2026).
  Como esa versión no se actualiza sola (-U), la app compara una vez por día el
  SHA2-256SUMS de la última versión y la reemplaza si cambió.
- deno: versión fija y sha256 fijo por plataforma. Solo YouTube lo necesita.
- ffmpeg: el de la app (imageio-ffmpeg), para unir video y audio.
"""

import hashlib
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
import zipfile
from typing import Callable

from . import clips

YTDLP_BASE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download"
DENO_VERSION = "2.9.8"
DENO_ZIPS = {  # sha256 de los .zip oficiales (deno-<target>.zip.sha256sum, v2.9.8)
    "x86_64-pc-windows-msvc": "cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf",
    "aarch64-apple-darwin": "9f0bf660393da2eaff044cdc7444cb08c73f235849b61d224d825a194ce7c98e",
    "x86_64-apple-darwin": "4b2f5edd2639b7470f034238b321d7002d8dfb4726d8558f52c17afac370eb2c",
    "x86_64-unknown-linux-gnu": "e890975bc2549807bbcc0b584ffafda72cc7c93349dd99c80483dd784f6c4609",
}
UPDATE_EVERY = 24 * 3600
_UA = {"User-Agent": "CDLM-Convertir"}

# Los reels de Facebook traen "4.7K views · 138 reactions ｜ " al principio del título
_FB_PREFIX = re.compile(r"^[\d.,KM]+ views · [\d.,KM]+ reactions ｜ ")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)%")


class DownloadError(RuntimeError):
    """No se pudo bajar el video o las herramientas. El mensaje es para el operador.

    `por_el_programa`: el error no es del video (privado, sitio no soportado) ni
    de la conexión, así que puede arreglarse con una versión nueva de yt-dlp
    (YouTube cambia seguido y rompe las versiones viejas)."""

    def __init__(self, msg: str, por_el_programa: bool = False):
        super().__init__(msg)
        self.por_el_programa = por_el_programa


def is_link(text: str) -> bool:
    return bool(re.match(r"https?://\S+$", text.strip()))


# ---------------------------------------------------------------- plataforma

def _ytdlp_asset() -> str:
    if sys.platform == "win32":
        return "yt-dlp_win.zip"
    if sys.platform == "darwin":
        return "yt-dlp_macos.zip"
    return "yt-dlp_linux.zip"


def _deno_target() -> str:
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "x86_64-pc-windows-msvc"
    if sys.platform == "darwin":
        return "aarch64-apple-darwin" if machine in ("arm64", "aarch64") else "x86_64-apple-darwin"
    return "x86_64-unknown-linux-gnu"


def _ytdlp_dir(tools_dir: str) -> str:
    return os.path.join(tools_dir, "yt-dlp-app")


def ytdlp_path(tools_dir: str) -> str | None:
    """El ejecutable dentro de la carpeta descomprimida (None si no está)."""
    d = _ytdlp_dir(tools_dir)
    try:
        nombres = sorted(os.listdir(d))
    except OSError:
        return None
    for n in nombres:
        if n.startswith("yt-dlp") and os.path.isfile(os.path.join(d, n)):
            return os.path.join(d, n)
    return None


def deno_path(tools_dir: str) -> str:
    return os.path.join(tools_dir, "deno.exe" if sys.platform == "win32" else "deno")


def _popen_kwargs() -> dict:
    kw = {"encoding": "utf-8", "errors": "replace"}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


# ---------------------------------------------------------------- herramientas

def _fetch(url: str, dest: str, progress_cb=None, cancel=None) -> None:
    req = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while chunk := r.read(1 << 16):
                if cancel and cancel():
                    raise clips.Cancelled()
                f.write(chunk)
                done += len(chunk)
                if progress_cb and total:
                    progress_cb(done / total)
    except OSError as e:
        raise DownloadError("No se pudo conectar a internet. Verifique la conexión.") from e


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_executable(path: str) -> None:
    if os.name != "nt":
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _hash_instalado(tools_dir: str) -> str:
    try:
        with open(os.path.join(tools_dir, "yt-dlp-app.sha256"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _hash_publicado(tools_dir: str, cancel=None) -> str:
    """sha256 del zip de la última versión, según su SHA2-256SUMS."""
    sums_path = os.path.join(tools_dir, "SHA2-256SUMS")
    _fetch(f"{YTDLP_BASE}/SHA2-256SUMS", sums_path, cancel=cancel)
    asset, expected = _ytdlp_asset(), ""
    try:
        with open(sums_path, encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2 and parts[1] == asset:
                    expected = parts[0].lower()
    finally:
        os.remove(sums_path)
    if not expected:
        raise DownloadError("No se pudo instalar el componente de descarga. Intente nuevamente más tarde.")
    return expected


def _instalar_ytdlp(tools_dir: str, expected: str, progress_cb=None, cancel=None) -> None:
    """Baja el zip, lo verifica, lo descomprime aparte y recién ahí reemplaza."""
    zpath = os.path.join(tools_dir, "yt-dlp-app.zip.parcial")
    nuevo = _ytdlp_dir(tools_dir) + ".nuevo"
    try:
        _fetch(f"{YTDLP_BASE}/{_ytdlp_asset()}", zpath, progress_cb, cancel)
        if _sha256(zpath) != expected:
            raise DownloadError("No se pudo instalar el componente de descarga. Intente nuevamente más tarde.")
        shutil.rmtree(nuevo, ignore_errors=True)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(nuevo)
    finally:
        if os.path.exists(zpath):
            os.remove(zpath)
    for n in os.listdir(nuevo):
        if n.startswith("yt-dlp") and os.path.isfile(os.path.join(nuevo, n)):
            _make_executable(os.path.join(nuevo, n))
    actual = _ytdlp_dir(tools_dir)
    shutil.rmtree(actual, ignore_errors=True)
    os.replace(nuevo, actual)
    with open(os.path.join(tools_dir, "yt-dlp-app.sha256"), "w", encoding="utf-8") as f:
        f.write(expected)
    # La versión vieja, de un solo archivo (hasta el 15/11/2026)
    for viejo in ("yt-dlp", "yt-dlp.exe"):
        if os.path.isfile(os.path.join(tools_dir, viejo)):
            os.remove(os.path.join(tools_dir, viejo))
    _calentar(ytdlp_path(tools_dir), cancel)


def _calentar(exe: str | None, cancel=None) -> None:
    """Arranca el programa recién instalado una vez. La primera vez el sistema lo
    revisa (en la Mac ~15 s; en Windows, el antivirus): mejor que pase ahora y no
    en medio de la primera descarga."""
    if not exe:
        return
    try:
        proc = subprocess.Popen([exe, "--version"], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, **_popen_kwargs())
    except OSError:
        return
    _cortar_si_cancelan(proc, cancel or (lambda: False), limite=120)
    proc.wait()
    if cancel and cancel():
        raise clips.Cancelled()


def ensure_ytdlp(tools_dir: str, progress_cb=None, cancel=None, actualizar: bool = True) -> str:
    """Baja yt-dlp si falta; si ya está y `actualizar`, lo actualiza como mucho
    una vez por día (la app lo hace al abrirse, no en medio de una descarga)."""
    os.makedirs(tools_dir, exist_ok=True)
    exe = ytdlp_path(tools_dir)
    if exe:
        if actualizar:
            _maybe_update(tools_dir, cancel)
        return ytdlp_path(tools_dir) or exe
    _instalar_ytdlp(tools_dir, _hash_publicado(tools_dir, cancel), progress_cb, cancel)
    _touch_update_stamp(tools_dir)
    exe = ytdlp_path(tools_dir)
    if not exe:
        raise DownloadError("No se pudo instalar el componente de descarga. Intente nuevamente más tarde.")
    return exe


def _stamp(tools_dir: str) -> str:
    return os.path.join(tools_dir, "yt-dlp.actualizado")


def _touch_update_stamp(tools_dir: str) -> None:
    with open(_stamp(tools_dir), "w") as f:
        f.write(str(time.time()))


def _maybe_update(tools_dir: str, cancel=None) -> None:
    """Una vez por día: si hay una versión nueva, la instala. Sin internet, sigue
    con la que hay."""
    try:
        if time.time() - os.path.getmtime(_stamp(tools_dir)) < UPDATE_EVERY:
            return
    except OSError:
        pass
    try:
        expected = _hash_publicado(tools_dir, cancel)
        if expected != _hash_instalado(tools_dir):
            _instalar_ytdlp(tools_dir, expected, cancel=cancel)
    except clips.Cancelled:
        raise
    except (DownloadError, OSError, zipfile.BadZipFile):
        return
    _touch_update_stamp(tools_dir)


def ensure_deno(tools_dir: str, progress_cb=None, cancel=None) -> str:
    os.makedirs(tools_dir, exist_ok=True)
    exe = deno_path(tools_dir)
    if os.path.exists(exe):
        return exe
    target = _deno_target()
    zpath = os.path.join(tools_dir, f"deno-{target}.zip")
    _fetch(f"https://github.com/denoland/deno/releases/download/v{DENO_VERSION}/deno-{target}.zip",
           zpath, progress_cb, cancel)
    if _sha256(zpath) != DENO_ZIPS[target]:
        os.remove(zpath)
        raise DownloadError("No se pudo instalar el componente de descarga. Intente nuevamente más tarde.")
    with zipfile.ZipFile(zpath) as z:
        z.extract(os.path.basename(exe), tools_dir)
    os.remove(zpath)
    _make_executable(exe)
    _calentar(exe, cancel)
    return exe


def hay_version_nueva(tools_dir: str, cancel=None) -> bool:
    """¿La última versión publicada de yt-dlp es distinta de la instalada?
    Sin internet, responde que no."""
    try:
        return _hash_publicado(tools_dir, cancel) != _hash_instalado(tools_dir)
    except (DownloadError, OSError):
        return False


def actualizar(tools_dir: str, progress_cb=None, cancel=None) -> None:
    """Instala ya la última versión de yt-dlp (sin esperar al chequeo diario)."""
    _instalar_ytdlp(tools_dir, _hash_publicado(tools_dir, cancel), progress_cb, cancel)
    _touch_update_stamp(tools_dir)


def preparar(tools_dir: str, progress_cb=None, cancel=None) -> None:
    """Lo que hace la app al abrirse: instala lo que falte (solo si se lo pidieron)
    o actualiza yt-dlp una vez por día, en segundo plano."""
    cb = progress_cb or (lambda f: None)
    ensure_ytdlp(tools_dir, lambda f: cb(0.4 * f), cancel)
    ensure_deno(tools_dir, lambda f: cb(0.4 + 0.6 * f), cancel)
    cb(1.0)


def tools_ready(tools_dir: str) -> bool:
    return bool(ytdlp_path(tools_dir)) and os.path.exists(deno_path(tools_dir))


# ---------------------------------------------------------------- descarga

def clean_name(filename: str) -> str:
    return _FB_PREFIX.sub("", filename)


def download(url: str, out_dir: str, tools_dir: str,
             progress_cb: Callable[[float, str], None] | None = None,
             cancel: Callable[[], bool] | None = None) -> str:
    """Baja `url` a `out_dir` y devuelve la ruta del MP4.

    Avance: `progress_cb(0..1, texto)`. Primero las herramientas (solo la primera
    vez), después el video. Baja a una carpeta temporal propia: si se cancela o
    falla, no quedan archivos a medias en `out_dir`.
    """
    cb = progress_cb or (lambda f, m: None)
    if not tools_ready(tools_dir):
        cb(0.0, "Instalando el componente de descarga (solo la primera vez)…")
    prep = "Instalando el componente de descarga (solo la primera vez)…"
    ytdlp = ensure_ytdlp(tools_dir, lambda f: cb(0.05 * f, prep), cancel, actualizar=False)
    deno = ensure_deno(tools_dir, lambda f: cb(0.05 + 0.05 * f, prep), cancel)

    if cancel and cancel():
        raise clips.Cancelled()
    cb(0.1, "Buscando el video…")
    os.makedirs(out_dir, exist_ok=True)
    work = os.path.join(out_dir, f".descargando-{uuid.uuid4().hex[:8]}")
    os.makedirs(work)
    cmd = [ytdlp, "--no-playlist", "--newline", "--no-simulate",
           "-f", "bv*+ba/b", "--merge-output-format", "mp4",
           "--ffmpeg-location", clips.ffmpeg_exe(),
           "--js-runtimes", f"deno:{deno}",
           "--progress-template", "download:%(progress._percent_str)s",
           "-o", os.path.join(work, "%(title).80s [%(id)s].%(ext)s"),
           "--print", "after_move:filepath", url]
    try:
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as err:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, **_popen_kwargs())
            # Cancelar corta al instante, aunque yt-dlp no esté escribiendo nada
            # (antes se revisaba solo al llegar una línea: al cerrar la app con
            # una descarga arrancando, la app se cerraba con error).
            vigia = threading.Thread(target=_cortar_si_cancelan, args=(proc, cancel), daemon=True)
            vigia.start()
            phase, last, final = 0, 0.0, None
            for line in proc.stdout:
                line = line.strip()
                m = _PERCENT.fullmatch(line) or (_PERCENT.search(line) if line.endswith("%") else None)
                if m:
                    pct = float(m.group(1)) / 100
                    if pct + 0.2 < last:      # video y audio se bajan por separado
                        phase = min(phase + 1, 1)
                    last = pct
                    cb(0.1 + 0.9 * min(1.0, (phase + pct) / 2), "Descargando el video…")
                elif line and os.path.isabs(line):
                    final = line
            proc.wait()
            if cancel and cancel():
                raise clips.Cancelled()
            err.seek(0)
            stderr = err.read()
        if proc.returncode != 0 or not final or not os.path.exists(final):
            msg = _explain(stderr)
            raise DownloadError(msg, por_el_programa=msg == _GENERICO)
        dest = os.path.join(out_dir, clean_name(os.path.basename(final)))
        base, ext = os.path.splitext(dest)
        n = 2
        while os.path.exists(dest):
            dest = f"{base} ({n}){ext}"
            n += 1
        shutil.move(final, dest)
        cb(1.0, "Listo")
        return dest
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _cortar_si_cancelan(proc: subprocess.Popen, cancel, limite: float | None = None) -> None:
    """Espera a que termine `proc`; lo corta si cancelan o si pasa `limite` segundos."""
    if not cancel:
        return
    inicio = time.time()
    while proc.poll() is None:
        if cancel() or (limite and time.time() - inicio > limite):
            proc.kill()
            return
        time.sleep(0.15)


def _explain(stderr: str) -> str:
    """Error de yt-dlp en palabras del operador."""
    low = stderr.lower()
    if "private" in low or "login" in low or "sign in" in low:
        return "El video es privado o requiere iniciar sesión: no se puede descargar."
    if "unsupported url" in low:
        return "No se pueden descargar videos de ese sitio."
    if "unable to download" in low or "network" in low or "timed out" in low:
        return "No se pudo conectar. Verifique la conexión a internet."
    return _GENERICO


_GENERICO = "No se pudo descargar el video. Verifique que el enlace se abra en el navegador."
