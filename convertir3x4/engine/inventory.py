"""Etapa 0 — inventario: recorre carpetas y lista archivos de medios."""

import os

from ..catalog.db import IMAGE_EXTS, VIDEO_EXTS


def kind_for(path: str) -> str | None:
    """Devuelve 'image' | 'video' | None según la extensión."""
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def scan(root: str, errores: list | None = None) -> list[str]:
    """Recorre `root` recursivamente y devuelve rutas absolutas de medios.

    Ignora archivos ocultos y carpetas ocultas (p.ej. .git, .DS_Store).
    Si se pasa `errores`, anota ahí las carpetas que no se pudieron leer (p.ej.
    el NAS se cayó a mitad de camino): con eso la lista está incompleta.
    """
    found = []
    onerror = errores.append if errores is not None else None
    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            if kind_for(name) is not None:
                found.append(os.path.join(dirpath, name))
    found.sort()
    return found


def folder_origin(path: str, root: str) -> str:
    """Carpeta de origen relativa a `root` (pista de ministerio/contexto)."""
    rel = os.path.relpath(os.path.dirname(path), root)
    return "" if rel == "." else rel
