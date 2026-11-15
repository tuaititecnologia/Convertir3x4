"""Utilidades del sistema independientes de la UI."""

import os
import platform
import subprocess


def open_folder(path: str):
    """Abre una carpeta en Finder/Explorer."""
    if platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    elif platform.system() == "Windows":
        subprocess.Popen(["explorer", path])
    else:
        subprocess.Popen(["xdg-open", path])


def open_file(path: str) -> bool:
    """Abre un archivo con el programa de la PC (reproductor de video, visor de
    fotos). Devuelve False si el archivo no está (NAS o disco desconectado)."""
    if not path or not os.path.exists(path):
        return False
    if platform.system() == "Windows":
        os.startfile(path)  # noqa: S606 — es la forma de Windows de "abrir con"
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
    return True
