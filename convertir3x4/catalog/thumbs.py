"""Generación de miniaturas pre-cacheadas (PIL, sin Qt → apto para hilos worker).

Las miniaturas se guardan como JPEG en un directorio de caché, nombradas por el
sha256 del medio para deduplicar también las miniaturas. La UI solo carga el
archivo resultante.
"""

import os

import imageio
from PIL import Image

THUMB_MAX = 320  # lado mayor de la miniatura, en px


def thumb_path_for(cache_dir: str, sha: str) -> str:
    return os.path.join(cache_dir, f"{sha}.jpg")


def make_thumb(src_path: str, kind: str, sha: str, cache_dir: str) -> str | None:
    """Genera (si no existe) la miniatura y devuelve su ruta, o None si falla."""
    os.makedirs(cache_dir, exist_ok=True)
    out = thumb_path_for(cache_dir, sha)
    if os.path.exists(out):
        return out
    try:
        if kind == "video":
            img = _first_frame(src_path)
        else:
            img = Image.open(src_path).convert("RGB")
        if img is None:
            return None
        img.thumbnail((THUMB_MAX, THUMB_MAX), Image.LANCZOS)
        img.save(out, "JPEG", quality=80)
        return out
    except Exception:
        return None


def _first_frame(path: str) -> Image.Image | None:
    reader = imageio.get_reader(path)
    try:
        frame = reader.get_data(0)
    finally:
        reader.close()
    return Image.fromarray(frame).convert("RGB")
