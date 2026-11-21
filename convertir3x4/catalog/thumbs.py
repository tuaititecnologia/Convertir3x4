"""Miniaturas del catálogo (PIL, sin Qt → apto para hilos worker).

Se generan en memoria como JPEG y se guardan en la base (tabla `thumbnail`),
para que todas las PCs las vean aunque el archivo original falte o el NAS no
responda. Cada PC las guarda además en su caché local (`cache_miniaturas.py`).
"""

import io

from PIL import Image, ImageOps

from . import video_ffmpeg

THUMB_MAX = 320  # lado mayor de la miniatura, en px


def make_thumb(src_path: str, kind: str) -> bytes | None:
    """JPEG de la miniatura, o None si el archivo no se puede leer."""
    try:
        if kind == "video":
            img = _first_frame(src_path)
        else:
            with Image.open(src_path) as im:
                im.draft("RGB", (THUMB_MAX * 2, THUMB_MAX * 2))  # JPEG: decodifica achicado
                img = ImageOps.exif_transpose(im).convert("RGB")
        if img is None:
            return None
        img.thumbnail((THUMB_MAX, THUMB_MAX), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=80)
        return out.getvalue()
    except Exception:
        return None


def _first_frame(path: str) -> Image.Image | None:
    _, frame = video_ffmpeg.leer(path, cuadro=True)
    return Image.fromarray(frame).convert("RGB")
