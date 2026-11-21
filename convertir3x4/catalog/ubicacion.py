"""Dónde está cada archivo del catálogo y si se puede llegar a él. Sin Qt.

La regla de oro: un archivo que está en un disco o en el NAS DESCONECTADO no
"falta": va a volver cuando se conecte. Solo falta de verdad el que no está
aunque su disco o su NAS sí estén conectados (se movió o se borró).
"""

import ntpath
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor


def storage_root(path: str) -> str:
    """Raíz de la ubicación de un archivo: el recurso del NAS, el disco o el volumen.

    \\\\NAS\\fotos\\a\\b.jpg -> \\\\NAS\\fotos   ·   D:\\x\\y.jpg -> D:\\
    /Volumes/SSD/x/y.jpg -> /Volumes/SSD   ·   /home/u/x.jpg -> /
    """
    # Rutas de Windows (unidad o recurso de red), con \\ o con /: ntpath las
    # entiende en cualquier sistema.
    if re.match(r"^[A-Za-z]:[\\/]", path) or path.startswith(("\\\\", "//")):
        drive, _ = ntpath.splitdrive(path)
        return drive + ("\\" if len(drive) == 2 else "")
    parts = path.split("/")
    if len(parts) > 2 and parts[1] in ("Volumes", "media", "mnt"):
        return "/".join(parts[:3])
    return "/"


def friendly_root(root: str) -> str:
    """Cómo nombra el operador una ubicación: "el NAS", "el disco D:", "el disco «SSD»"."""
    if root.startswith(("\\\\", "//")):
        return "el NAS"
    if re.match(r"^[A-Za-z]:", root):
        return f"el disco {root[:2].upper()}"
    if root.startswith(("/Volumes/", "/media/", "/mnt/")):
        return f"el disco «{root.rstrip('/').rsplit('/', 1)[-1]}»"
    return "esta PC"


def availability(paths: list[str]) -> dict[str, bool]:
    """Qué archivos están, sin congelar la app si el NAS está desconectado ni
    tardar con el NAS lejos.

    En Windows, preguntar por un archivo de una carpeta de red caída puede
    tardar segundos POR ARCHIVO: se pregunta una vez por cada raíz. Y por
    Netbird cada pregunta al NAS tarda unos 80 ms: en vez de preguntar archivo
    por archivo, se lee una vez cada carpeta (varias a la vez) y se busca ahí.
    """
    roots = {r: os.path.exists(r) for r in {storage_root(p) for p in paths}}
    carpetas = {os.path.dirname(p) for p in paths if roots[storage_root(p)]}

    def leer(carpeta):
        try:
            # macOS devuelve los acentos descompuestos (NFD): se compara en NFC
            return carpeta, {_nfc(n) for n in os.listdir(carpeta)}
        except OSError:
            return carpeta, set()

    contenido: dict[str, set[str]] = {}
    if carpetas:
        with ThreadPoolExecutor(max_workers=min(16, len(carpetas))) as ex:
            contenido = dict(ex.map(leer, carpetas))
    return {p: roots[storage_root(p)]
            and _nfc(os.path.basename(p)) in contenido.get(os.path.dirname(p), ())
            for p in paths}


def _nfc(nombre: str) -> str:
    return unicodedata.normalize("NFC", nombre)
