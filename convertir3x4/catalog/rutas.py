"""Rutas del catálogo: relativas a la raíz del recurso compartido. Sin Qt.

En la base cada medio se guarda con su ruta relativa a la raíz
(`\\\\truenas\\multimedia`), siempre con "/" (p. ej. `Publicar/2026/culto/a.jpg`).
Cada PC arma la ruta completa con su raíz. Así las dos PCs comparten el
catálogo aunque una use otra letra o, en desarrollo, una Mac con
`/Volumes/multimedia`.
"""

import ntpath
import posixpath
import re


def _es_windows(raiz: str) -> bool:
    return bool(re.match(r"^([A-Za-z]:|\\\\|//)", raiz))


def _mod(raiz: str):
    return ntpath if _es_windows(raiz) else posixpath


def a_relativa(path: str, raiz: str) -> str | None:
    """Ruta relativa a `raiz` con "/", "" para la raíz misma, o None si `path`
    está fuera de la raíz. En Windows no distingue mayúsculas."""
    m = _mod(raiz)
    r, p = m.normpath(raiz), m.normpath(path)
    igual = (lambda x: x.lower()) if m is ntpath else (lambda x: x)
    if igual(p) == igual(r):
        return ""
    prefijo = r if r.endswith(m.sep) else r + m.sep
    if not igual(p).startswith(igual(prefijo)):
        return None
    return p[len(prefijo):].replace(m.sep, "/")


def a_completa(rel: str, raiz: str) -> str:
    """Ruta completa en esta PC a partir de la relativa guardada en la base."""
    if not rel:
        return raiz
    return _mod(raiz).join(raiz, *rel.split("/"))


def carpeta_de(rel: str) -> str:
    """Carpeta (relativa) de un archivo relativo: `a/b/c.jpg` -> `a/b`."""
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def dentro_de(rel: str, carpeta: str) -> bool:
    """¿`rel` está dentro de `carpeta` (relativa)? La raíz ("") contiene todo."""
    return not carpeta or rel == carpeta or rel.startswith(carpeta + "/")
