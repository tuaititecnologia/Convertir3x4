"""¿Hay una versión nueva de la aplicación? (pedido del 23/11/2026).

Al abrir (y cada unas horas) se consulta la última versión publicada en GitHub
(el repositorio es público: no hace falta clave). Si es más nueva, la interfaz
ofrece «Actualizar»:

- En Windows (el .exe de las PCs de la iglesia) se baja el .exe nuevo al lado
  del actual (`<nombre>.exe.nuevo`), se verifica su tamaño y su sha256 (el que
  publica GitHub) y, al cerrar la aplicación, un .cmd chico espera a que el .exe
  quede libre, lo reemplaza y vuelve a abrirlo. Un .exe abierto no se puede
  sobrescribir: por eso el reemplazo lo hace otro proceso, después de cerrar.
- En la Mac o en Linux solo se abre la página de la descarga.

Sin Qt: la interfaz está en `ui/widgets/aviso_actualizacion.py`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass

REPO = "tuaititecnologia/Convertir3x4"
API_ULTIMA = f"https://api.github.com/repos/{REPO}/releases/latest"
_UA = {"User-Agent": "CDLM-Convertir", "Accept": "application/vnd.github+json"}

# Cómo termina el nombre del archivo publicado para cada sistema (build.yml).
_SUFIJO = {"windows": "-windows.exe", "macos": "-macos.dmg", "linux": "-linux"}


class ErrorDeActualizacion(Exception):
    """Algo falló al bajar la versión nueva; el texto es para el operador."""


@dataclass(frozen=True)
class Publicada:
    version: str            # "v0.8.1"
    pagina: str             # página de la versión en GitHub
    url: str | None         # el archivo para este sistema (None si no hay)
    tamano: int = 0
    sha256: str | None = None


def numeros(version: str) -> tuple[int, int, int] | None:
    """"v0.8.1" → (0, 8, 1). None para "dev", "dev-1a2b3c4" o algo raro."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (version or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def hay_nueva(actual: str, publicada: str) -> bool:
    """Solo entre versiones numeradas: en desarrollo nunca se ofrece."""
    a, p = numeros(actual), numeros(publicada)
    return a is not None and p is not None and p > a


def plataforma() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def leer_publicada(datos: dict, sistema: str | None = None) -> Publicada | None:
    """Arma `Publicada` con la respuesta de la API de GitHub."""
    version = datos.get("tag_name")
    if not version or datos.get("draft") or datos.get("prerelease"):
        return None
    sufijo = _SUFIJO[sistema or plataforma()]
    url, tamano, sha = None, 0, None
    for a in datos.get("assets") or []:
        if str(a.get("name", "")).endswith(sufijo):
            url = a.get("browser_download_url")
            tamano = int(a.get("size") or 0)
            digest = a.get("digest") or ""
            sha = digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else None
            break
    return Publicada(version, datos.get("html_url") or f"https://github.com/{REPO}/releases",
                     url, tamano, sha)


def ultima(timeout: float = 8) -> Publicada | None:
    """La última versión publicada, o None si no se pudo saber (sin internet,
    GitHub no responde…): nunca molesta al operador por eso."""
    try:
        req = urllib.request.Request(API_ULTIMA, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return leer_publicada(json.load(r))
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ Windows

def exe_actual() -> str | None:
    """El .exe que está corriendo, si es el empaquetado (no en desarrollo)."""
    return sys.executable if getattr(sys, "frozen", False) else None


def se_instala_sola(p: Publicada, sistema: str | None = None, exe: str | None = None) -> bool:
    """En Windows, con el .exe empaquetado y su carpeta con permiso de escritura."""
    exe = exe if exe is not None else exe_actual()
    return ((sistema or plataforma()) == "windows" and bool(p.url) and bool(exe)
            and os.access(os.path.dirname(os.path.abspath(exe)), os.W_OK))


def ruta_nueva(exe: str) -> str:
    return exe + ".nuevo"


def limpiar_restos(exe: str | None = None) -> None:
    """Borra un .nuevo que quedó de una actualización que no se terminó."""
    exe = exe if exe is not None else exe_actual()
    if exe:
        try:
            os.remove(ruta_nueva(exe))
        except OSError:
            pass


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(1 << 20):
            h.update(b)
    return h.hexdigest()


def descargar(p: Publicada, destino: str, progreso=None, cancelar=None) -> str:
    """Baja el archivo de `p` a `destino` y lo verifica. Si algo falla no queda
    nada a medias."""
    if not p.url:
        raise ErrorDeActualizacion("Esta versión no tiene un archivo para esta PC.")
    parcial = destino + ".parcial"
    try:
        req = urllib.request.Request(p.url, headers={"User-Agent": _UA["User-Agent"]})
        with urllib.request.urlopen(req, timeout=60) as r, open(parcial, "wb") as f:
            total = int(r.headers.get("Content-Length") or p.tamano or 0)
            hecho = 0
            while bloque := r.read(1 << 16):
                if cancelar and cancelar():
                    raise ErrorDeActualizacion("Se canceló la descarga.")
                f.write(bloque)
                hecho += len(bloque)
                if progreso and total:
                    progreso(hecho, total)
        if p.tamano and os.path.getsize(parcial) != p.tamano:
            raise ErrorDeActualizacion("La descarga quedó incompleta. Intente nuevamente.")
        if p.sha256 and _sha256(parcial) != p.sha256:
            raise ErrorDeActualizacion("El archivo descargado no coincide con el publicado. "
                                       "Intente nuevamente.")
        os.replace(parcial, destino)
        return destino
    except OSError as e:
        raise ErrorDeActualizacion("No se pudo descargar la versión nueva. Verifique la "
                                   "conexión a internet.") from e
    finally:
        try:
            os.remove(parcial)
        except OSError:
            pass


# El .cmd recibe las rutas como argumentos (%1 el nuevo, %2 el actual): así no
# importa la codificación de las rutas con tilde. Reintenta durante un minuto
# hasta que el .exe actual quede libre; `ping` hace de pausa porque `timeout`
# no anda sin consola.
SCRIPT_REEMPLAZO = "\r\n".join([
    "@echo off",
    "set N=0",
    ":otra",
    'move /y "%~1" "%~2" >nul 2>&1 && goto listo',
    "set /a N+=1",
    "if %N% geq 60 goto fin",
    "ping -n 2 127.0.0.1 >nul",
    "goto otra",
    ":listo",
    'if not "%~3"=="sin-abrir" start "" "%~2"',
    ":fin",
    '(goto) 2>nul & del "%~f0"',
    "",
])


def preparar_reemplazo(nuevo: str, exe: str, abrir: bool = True) -> list[str]:
    """Escribe el .cmd y devuelve la orden para correrlo (`lanzar_reemplazo`)."""
    fd, script = tempfile.mkstemp(prefix="cdlm-actualizar-", suffix=".cmd")
    with os.fdopen(fd, "w", encoding="ascii", newline="") as f:
        f.write(SCRIPT_REEMPLAZO)
    orden = ["cmd.exe", "/c", script, nuevo, exe]
    return orden if abrir else orden + ["sin-abrir"]


def lanzar_reemplazo(orden: list[str], carpeta: str) -> subprocess.Popen:
    """Lo corre aparte y sin ventana: sigue vivo cuando la aplicación se cierra."""
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(orden, cwd=carpeta, creationflags=flags, close_fds=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
