"""Bajar videos de YouTube, Facebook y otros sitios con yt-dlp. Sin Qt.

yt-dlp y deno NO vienen dentro de la app: se bajan la primera vez que se usan a
`tools_dir` (la carpeta de datos de la app). Motivo (docs/convertir-fotos-y-videos.md):
YouTube rompe yt-dlp seguido; si viniera en el .exe, cada arreglo obligaría a
sacar una versión nueva. Así se actualiza solo (`yt-dlp -U`, una vez por día).

- yt-dlp: ejecutable oficial de GitHub, verificado contra el SHA2-256SUMS de la
  misma versión. Trae yt-dlp-ejs adentro.
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
    """No se pudo bajar el video o las herramientas. El mensaje es para el operador."""


def is_link(text: str) -> bool:
    return bool(re.match(r"https?://\S+$", text.strip()))


# ---------------------------------------------------------------- plataforma

def _ytdlp_asset() -> str:
    if sys.platform == "win32":
        return "yt-dlp.exe"
    if sys.platform == "darwin":
        return "yt-dlp_macos"
    return "yt-dlp_linux"


def _deno_target() -> str:
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "x86_64-pc-windows-msvc"
    if sys.platform == "darwin":
        return "aarch64-apple-darwin" if machine in ("arm64", "aarch64") else "x86_64-apple-darwin"
    return "x86_64-unknown-linux-gnu"


def ytdlp_path(tools_dir: str) -> str:
    return os.path.join(tools_dir, "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp")


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
        raise DownloadError("No se pudo conectar a internet. Revisá la conexión.") from e


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_executable(path: str) -> None:
    if os.name != "nt":
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def ensure_ytdlp(tools_dir: str, progress_cb=None, cancel=None) -> str:
    """Baja yt-dlp si falta; si ya está, lo actualiza como mucho una vez por día."""
    os.makedirs(tools_dir, exist_ok=True)
    exe = ytdlp_path(tools_dir)
    if os.path.exists(exe):
        _maybe_update(exe, tools_dir)
        return exe
    asset = _ytdlp_asset()
    sums_path = os.path.join(tools_dir, "SHA2-256SUMS")
    _fetch(f"{YTDLP_BASE}/SHA2-256SUMS", sums_path, cancel=cancel)
    expected = None
    with open(sums_path, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) == 2 and parts[1] == asset:
                expected = parts[0].lower()
    os.remove(sums_path)
    if not expected:
        raise DownloadError("No se pudo preparar la descarga. Probá de nuevo más tarde.")
    tmp = exe + ".bajando"
    _fetch(f"{YTDLP_BASE}/{asset}", tmp, progress_cb, cancel)
    if _sha256(tmp) != expected:
        os.remove(tmp)
        raise DownloadError("No se pudo preparar la descarga. Probá de nuevo más tarde.")
    os.replace(tmp, exe)
    _make_executable(exe)
    _touch_update_stamp(tools_dir)
    return exe


def _stamp(tools_dir: str) -> str:
    return os.path.join(tools_dir, "yt-dlp.actualizado")


def _touch_update_stamp(tools_dir: str) -> None:
    with open(_stamp(tools_dir), "w") as f:
        f.write(str(time.time()))


def _maybe_update(exe: str, tools_dir: str) -> None:
    try:
        if time.time() - os.path.getmtime(_stamp(tools_dir)) < UPDATE_EVERY:
            return
    except OSError:
        pass
    # Si falla (sin internet), se sigue con la versión que hay.
    try:
        subprocess.run([exe, "-U"], capture_output=True, timeout=120, **_popen_kwargs())
    except (OSError, subprocess.SubprocessError):
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
        raise DownloadError("No se pudo preparar la descarga. Probá de nuevo más tarde.")
    with zipfile.ZipFile(zpath) as z:
        z.extract(os.path.basename(exe), tools_dir)
    os.remove(zpath)
    _make_executable(exe)
    return exe


def tools_ready(tools_dir: str) -> bool:
    return os.path.exists(ytdlp_path(tools_dir)) and os.path.exists(deno_path(tools_dir))


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
        cb(0.0, "Preparando la descarga (solo la primera vez)…")
    prep = "Preparando la descarga (solo la primera vez)…"
    ytdlp = ensure_ytdlp(tools_dir, lambda f: cb(0.05 * f, prep), cancel)
    deno = ensure_deno(tools_dir, lambda f: cb(0.05 + 0.05 * f, prep), cancel)

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
            phase, last, final = 0, 0.0, None
            for line in proc.stdout:
                if cancel and cancel():
                    proc.kill()
                    proc.wait()
                    raise clips.Cancelled()
                line = line.strip()
                m = _PERCENT.fullmatch(line) or (_PERCENT.search(line) if line.endswith("%") else None)
                if m:
                    pct = float(m.group(1)) / 100
                    if pct + 0.2 < last:      # video y audio se bajan por separado
                        phase = min(phase + 1, 1)
                    last = pct
                    cb(0.1 + 0.9 * min(1.0, (phase + pct) / 2), "Bajando el video…")
                elif line and os.path.isabs(line):
                    final = line
            proc.wait()
            err.seek(0)
            stderr = err.read()
        if proc.returncode != 0 or not final or not os.path.exists(final):
            raise DownloadError(_explain(stderr))
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


def _explain(stderr: str) -> str:
    """Error de yt-dlp en palabras del operador."""
    low = stderr.lower()
    if "private" in low or "login" in low or "sign in" in low:
        return "El video es privado o pide iniciar sesión: no se puede bajar."
    if "unsupported url" in low:
        return "No se pueden bajar videos de ese sitio."
    if "unable to download" in low or "network" in low or "timed out" in low:
        return "No se pudo conectar. Revisá la conexión a internet."
    return "No se pudo bajar el video. Probá abrir el link en el navegador para ver si anda."
