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


def origen_config_catalogo() -> tuple[str, str | None]:
    """De dónde sale la configuración del catálogo de esta PC: ("entorno",
    None) con las variables de desarrollo, ("archivo", ruta) o ("ninguno", None)."""
    if os.environ.get("CDLM_CATALOGO_SERVIDOR"):
        return "entorno", None
    for ruta in rutas_config_catalogo():
        if os.path.isfile(ruta):
            return "archivo", ruta
    return "ninguno", None


def ruta_para_guardar_config() -> str | None:
    """Dónde escribe la pantalla «Conexión al catálogo»: el mismo archivo de
    donde se leyó; si no había, junto al .exe (la app queda portable) o, si ahí
    no se puede escribir (Program Files), en la carpeta de datos del usuario.
    None con las variables de desarrollo: ahí no se guarda nada."""
    origen, ruta = origen_config_catalogo()
    if origen == "entorno":
        return None
    if ruta:
        return ruta
    junto, datos = rutas_config_catalogo()
    carpeta = os.path.dirname(junto)
    return junto if os.access(carpeta, os.W_OK) else datos


def guardar_config_catalogo(cfg) -> str:
    """Escribe `catalogo.ini` (completo: primero a un temporal y después lo
    reemplaza, así un corte no lo deja a medias). Devuelve dónde quedó."""
    ruta = ruta_para_guardar_config()
    if ruta is None:
        raise PermissionError("con las variables CDLM_CATALOGO_* no se guarda la configuración")
    try:
        _escribir(ruta, cfg.a_ini())
    except PermissionError:
        # En Windows, os.access no sabe si Program Files deja escribir: se prueba
        junto, datos = rutas_config_catalogo()
        if ruta != junto or origen_config_catalogo()[0] == "archivo":
            raise
        ruta = datos
        _escribir(ruta, cfg.a_ini())
    return ruta


def _escribir(ruta: str, texto: str):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".nuevo"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(texto)
        os.replace(tmp, ruta)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


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


def catalog_ref_dir() -> str:
    """Caché local de las imágenes del detalle de las personas (Catalogar)."""
    d = os.path.join(thumb_dir(), "referencias")
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
