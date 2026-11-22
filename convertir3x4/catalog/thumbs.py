"""Miniaturas del catálogo (PIL, sin Qt → apto para hilos worker).

Se generan en memoria como JPEG y se guardan en la base (tabla `thumbnail`),
para que todas las PCs las vean aunque el archivo original falte o el NAS no
responda. Cada PC las guarda además en su caché local (`cache_miniaturas.py`).
"""

import io

from PIL import Image, ImageOps

from . import video_ffmpeg

THUMB_MAX = 320  # lado mayor de la miniatura, en px
# Sube cuando cambia cómo se hace la miniatura: el NAS rehace las anteriores
# (2: los videos ya no usan el primer cuadro, que suele ser negro).
MINIATURA_VERSION = 2


def make_thumb(src_path: str, kind: str, duracion: float | None = None) -> bytes | None:
    """JPEG de la miniatura, o None si el archivo no se puede leer. De un video,
    un cuadro que lo muestre (no el primero, que suele ser negro)."""
    try:
        if kind == "video":
            img = Image.fromarray(video_ffmpeg.cuadro_representativo(src_path, duracion)).convert("RGB")
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


def en_negro(jpeg: bytes | None) -> bool:
    """¿La miniatura es negra? (foto negra, o video en negro en todos los puntos)."""
    if not jpeg:
        return False
    import numpy as np
    with Image.open(io.BytesIO(jpeg)) as im:
        return video_ffmpeg.es_negro(np.asarray(im.convert("L")))


REFERENCIA_MAX = 1600  # lado mayor de una foto de referencia de una persona (lo que mira el NAS)


def foto_de_referencia(src_path: str) -> bytes:
    """La foto de una persona, achicada, para subirla a la base: el NAS calcula
    ahí su firma (faces.reference_feature trabaja sobre 1600 px). Lanza la
    excepción de PIL si el archivo no es una imagen."""
    with Image.open(src_path) as im:
        im.draft("RGB", (REFERENCIA_MAX, REFERENCIA_MAX))
        img = ImageOps.exif_transpose(im).convert("RGB")
    img.thumbnail((REFERENCIA_MAX, REFERENCIA_MAX), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()
