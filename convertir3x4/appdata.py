"""Rutas de datos de la app (base del catálogo, caché de miniaturas).

Glue de runtime: usa QStandardPaths para ubicar un directorio de datos por
usuario. Las capas `engine`/`catalog` quedan libres de Qt y reciben rutas
explícitas desde acá.
"""

import os

from PyQt6.QtCore import QStandardPaths


def app_data_dir() -> str:
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation)
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".convertir3x4")
    os.makedirs(base, exist_ok=True)
    return base


def db_path() -> str:
    return os.path.join(app_data_dir(), "catalog.db")


def thumb_dir() -> str:
    d = os.path.join(app_data_dir(), "thumbs")
    os.makedirs(d, exist_ok=True)
    return d
