"""Etapa 0 — inventario: recorre carpetas y lista archivos de medios."""

import os

from ..catalog.alcance import es_de_sistema
from ..catalog.db import IMAGE_EXTS, VIDEO_EXTS


def kind_for(path: str) -> str | None:
    """Devuelve 'image' | 'video' | None según la extensión."""
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def scan(root: str, errores: list | None = None, entrar=None) -> list[str]:
    """Recorre `root` recursivamente y devuelve rutas absolutas de medios.

    Ignora archivos ocultos y carpetas ocultas o del sistema (.zfs, @eaDir,
    papeleras: ver `alcance.es_de_sistema`). `entrar(ruta_de_carpeta)` decide
    si se entra en cada subcarpeta (las excluidas en el árbol de carpetas).
    Si se pasa `errores`, anota ahí las carpetas que no se pudieron leer (p.ej.
    el NAS se cayó a mitad de camino): con eso la lista está incompleta.
    """
    found = []
    onerror = errores.append if errores is not None else None
    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
        dirnames[:] = [d for d in dirnames if not es_de_sistema(d)
                       and (entrar is None or entrar(os.path.join(dirpath, d)))]
        for name in filenames:
            if name.startswith("."):
                continue
            if kind_for(name) is not None:
                found.append(os.path.join(dirpath, name))
    found.sort()
    return found


def leer_carpetas(root: str, errores: list | None = None) -> list[str]:
    """Todas las carpetas debajo de `root`, relativas y con "/", sin abrir
    archivos ni seguir enlaces. Omite las del sistema (y lo de adentro). Lo que
    no se puede leer va a `errores` y el recorrido sigue."""
    out: list[str] = []
    pila = [("", root)]
    while pila:
        rel, ruta = pila.pop()
        try:
            with os.scandir(ruta) as it:
                hijas = [(e.name, e.path) for e in it
                         if e.is_dir(follow_symlinks=False) and not es_de_sistema(e.name)]
        except OSError as e:
            if errores is not None:
                errores.append(e)
            continue
        for nombre, camino in hijas:
            r = f"{rel}/{nombre}" if rel else nombre
            out.append(r)
            pila.append((r, camino))
    out.sort()
    return out


def folder_origin(path: str, root: str) -> str:
    """Carpeta de origen relativa a `root` (pista de ministerio/contexto)."""
    rel = os.path.relpath(os.path.dirname(path), root)
    return "" if rel == "." else rel
