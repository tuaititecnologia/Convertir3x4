"""Utilidades del sistema independientes de la UI."""

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
