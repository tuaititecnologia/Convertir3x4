"""Dónde está cada archivo del catálogo y si se puede llegar a él. Sin Qt.

La regla de oro: un archivo que está en un disco o en el NAS DESCONECTADO no
"falta": va a volver cuando se conecte. Solo falta de verdad el que no está
aunque su disco o su NAS sí estén conectados (se movió o se borró).
"""

import ntpath
import os
import re


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
    """Qué archivos están, sin congelar la app si el NAS está desconectado.

    En Windows, preguntar por un archivo de una carpeta de red caída puede
    tardar segundos POR ARCHIVO. Se pregunta una vez por cada raíz; solo si la
    raíz responde se pregunta archivo por archivo.
    """
    roots: dict[str, bool] = {}
    out = {}
    for p in paths:
        r = storage_root(p)
        if r not in roots:
            roots[r] = os.path.exists(r)
        out[p] = roots[r] and os.path.exists(p)
    return out
