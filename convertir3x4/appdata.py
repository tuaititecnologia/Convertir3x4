"""Datos de la app en esta PC: carpeta de datos, cachés y la configuración
del catálogo (`catalogo.ini`).

Glue de runtime: usa QStandardPaths para ubicar un directorio de datos por
usuario. Las capas `engine`/`catalog` quedan libres de Qt y reciben rutas y
configuración explícitas desde acá.
"""

import os
import sys

from PyQt6.QtCore import QStandardPaths


def app_data_dir() -> str:
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation)
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".convertir3x4")
    os.makedirs(base, exist_ok=True)
    return base


CONFIG_CATALOGO = "catalogo.ini"


def rutas_config_catalogo() -> list[str]:
    """Dónde se busca `catalogo.ini`, en orden: junto al .exe (o, sin empaquetar,
    en la carpeta del proyecto) y en la carpeta de datos del usuario."""
    if getattr(sys, "frozen", False):
        junto = os.path.dirname(sys.executable)
    else:
        junto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [os.path.join(junto, CONFIG_CATALOGO),
            os.path.join(app_data_dir(), CONFIG_CATALOGO)]


def config_catalogo():
    """Configuración del catálogo de esta PC.

    Primero las variables CDLM_CATALOGO_* (desarrollo), después `catalogo.ini`.
    Lanza `CatalogoNoConfigurado` si no hay ninguna o si el archivo tiene un
    dato que no sirve (el detalle queda en la excepción, no para el operador).
    """
    from .catalog.db import CatalogoNoConfigurado, ConfigCatalogo, ConfiguracionInvalida
    try:
        cfg = ConfigCatalogo.desde_entorno()
        if cfg is not None:
            return cfg
        for ruta in rutas_config_catalogo():
            cfg = ConfigCatalogo.desde_archivo(ruta)
            if cfg is not None:
                return cfg
    except (ConfiguracionInvalida, ValueError) as e:
        raise CatalogoNoConfigurado(str(e)) from e
    raise CatalogoNoConfigurado("no hay catalogo.ini")


def abrir_repo():
    """Abre el catálogo (conexión nueva). Quien lo abre lo cierra: `repo.close()`
    o `with abrir_repo() as repo:`. Nunca desde el hilo de la interfaz: puede
    tardar unos segundos si el NAS no responde."""
    from .catalog.db import conectar
    from .catalog.repo import Repo
    cfg = config_catalogo()
    return Repo(conectar(cfg), cfg.raiz)


def thumb_dir() -> str:
    d = os.path.join(app_data_dir(), "thumbs")
    os.makedirs(d, exist_ok=True)
    return d


def catalog_thumb_dir() -> str:
    """Caché local de las miniaturas del catálogo (la original está en la base)."""
    d = os.path.join(thumb_dir(), "catalogo")
    os.makedirs(d, exist_ok=True)
    return d


def models_dir() -> str:
    """Modelos de IA (se bajan una vez; ver engine/ai_models.py)."""
    d = os.path.join(app_data_dir(), "models")
    os.makedirs(d, exist_ok=True)
    return d


def tools_dir() -> str:
    """yt-dlp y deno (se bajan la primera vez; ver convert/download.py)."""
    d = os.path.join(app_data_dir(), "tools")
    os.makedirs(d, exist_ok=True)
    return d


def clip_cache_dir() -> str:
    """Miniaturas y tiras de cuadros de los videos de Convertir."""
    d = os.path.join(thumb_dir(), "videos")
    os.makedirs(d, exist_ok=True)
    return d
