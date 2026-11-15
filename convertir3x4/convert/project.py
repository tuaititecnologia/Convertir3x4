"""Lectura/escritura de archivos de proyecto .c3x4 (lista de imágenes + fondo)."""

import json


def read_project_file(path: str) -> dict | str:
    """Lee y valida un archivo de proyecto. Retorna dict o mensaje de error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            project = json.load(f)
    except (json.JSONDecodeError, OSError):
        return "No se pudo abrir el proyecto: el archivo está dañado o no es un proyecto de esta app."
    if not isinstance(project, dict):
        return "Ese archivo no es un proyecto de esta app."
    images = project.get("images", [])
    if not isinstance(images, list):
        return "Ese archivo no es un proyecto de esta app."
    # Filtrar entradas que no sean strings
    project["images"] = [p for p in images if isinstance(p, str)]
    return project
