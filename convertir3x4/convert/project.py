"""Lectura/escritura de archivos de proyecto .c3x4 (lista de imágenes + fondo)."""

import json


def read_project_file(path: str) -> dict | str:
    """Lee y valida un archivo de proyecto. Retorna dict o mensaje de error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            project = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return f"No se pudo leer el proyecto:\n{e}"
    if not isinstance(project, dict):
        return "El archivo no es un proyecto válido."
    images = project.get("images", [])
    if not isinstance(images, list):
        return "El archivo no es un proyecto válido."
    # Filtrar entradas que no sean strings
    project["images"] = [p for p in images if isinstance(p, str)]
    return project
