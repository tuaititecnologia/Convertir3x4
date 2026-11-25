"""«Probar» de la pantalla «Conexión al catálogo». Sin Qt.

No modifica nada: se conecta sin preparar el esquema (no toma el bloqueo ni
migra una base más vieja), lee la versión y las carpetas que se catalogan, y
mira si la carpeta multimedia de esta PC existe y las contiene. Puede tardar
unos segundos (servidor que no responde, share por la red): nunca desde el hilo
de la interfaz.
"""

import os

import psycopg

from . import rutas
from .alcance import Alcance
from .db import CatalogoNoDisponible, ConfigCatalogo, VERSION_ESQUEMA, conectar, version_esquema


def _carpetas(conn, version: int) -> tuple[str, ...]:
    """Las raíces que se catalogan, según lo que tenga esa base."""
    if version >= 10:
        filas = conn.execute("SELECT ruta, incluir FROM carpeta_catalogada").fetchall()
        return Alcance({r["ruta"]: r["incluir"] for r in filas}).raices()
    if version >= 7:
        filas = conn.execute("SELECT ruta FROM carpeta_catalogada").fetchall()
        return tuple(sorted(r["ruta"] for r in filas))
    return ()


def probar(cfg: ConfigCatalogo, existe=os.path.isdir) -> tuple[bool, str]:
    """(todo bien, texto para el operador)."""
    try:
        conn = conectar(cfg, preparar=False)
    except CatalogoNoDisponible as e:
        detalle = (e.detalle or "").lower()
        if "password" in detalle or "authentication" in detalle:
            return False, ("El servidor respondió, pero no aceptó el usuario o la clave. "
                           "Revise la clave (y el usuario, en «Otros datos»).")
        if "does not exist" in detalle and "database" in detalle:
            return False, (f"El servidor respondió, pero no tiene la base «{cfg.base}». "
                           "Revise el nombre de la base en «Otros datos».")
        return False, (f"No se pudo llegar al servidor {cfg.servidor}. Revise que la PC esté "
                       "en la red de la iglesia o conectada a Netbird, y que el servidor y "
                       "el puerto estén bien escritos.")
    try:
        version = version_esquema(conn)
        if version > VERSION_ESQUEMA:
            return False, ("Se conectó, pero el catálogo es de una versión más nueva de la "
                           "aplicación. Instale la versión nueva para usarlo.")
        carpetas = _carpetas(conn, version)
    except psycopg.Error:
        return False, ("Se conectó con el servidor, pero no se pudo leer el catálogo. "
                       "Intente nuevamente.")
    finally:
        conn.close()

    if not cfg.raiz or not existe(cfg.raiz):
        return False, (f"Se conectó con el catálogo, pero esta PC no encuentra la carpeta "
                       f"{cfg.raiz or '(vacía)'}. Revise que el NAS esté conectado en esta PC "
                       "y que la ruta esté bien escrita.")
    if carpetas and not any(existe(rutas.a_completa(c, cfg.raiz)) for c in carpetas):
        nombres = ", ".join(carpetas[:4]) + (" …" if len(carpetas) > 4 else "")
        return False, (f"La carpeta {cfg.raiz} existe, pero no tiene ninguna de las carpetas "
                       f"que se catalogan ({nombres}). Revise que sea la carpeta multimedia "
                       "del NAS.")
    texto = "Todo está bien: se conectó con el catálogo y encontró la carpeta multimedia."
    if 0 < version < VERSION_ESQUEMA:
        texto += (" Al guardar, el catálogo se actualiza a esta versión de la aplicación, y "
                  "las PCs con la versión anterior dejan de poder usarlo hasta actualizarse.")
    return True, texto
