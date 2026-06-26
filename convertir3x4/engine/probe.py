"""Etapa 0 — probe: metadata técnica de cada archivo.

Imágenes vía Pillow; videos vía imageio (que usa el ffmpeg ya empaquetado, así
que NO se necesita ffprobe). Detecta corruptos y baja resolución de paso.
"""

import imageio
from PIL import Image

from ..catalog.models import ProbeResult

# Por debajo de esto (lado menor, en px) marcamos low_res.
LOW_RES_MIN_SIDE = 640


def probe_image(path: str) -> ProbeResult:
    try:
        with Image.open(path) as img:
            img.verify()  # detecta corrupción sin decodificar todo
        # verify() invalida el objeto; reabrir para leer tamaño.
        with Image.open(path) as img:
            w, h = img.size
    except Exception:
        return ProbeResult(kind="image", quality_flag="corrupt")

    aspect = (w / h) if h else None
    quality = "ok"
    if w and h and min(w, h) < LOW_RES_MIN_SIDE:
        quality = "low_res"
    return ProbeResult(kind="image", width=w, height=h, aspect=aspect,
                       quality_flag=quality)


def probe_video(path: str) -> ProbeResult:
    try:
        reader = imageio.get_reader(path)
        md = reader.get_meta_data()
        reader.close()
    except Exception:
        return ProbeResult(kind="video", quality_flag="corrupt")

    size = md.get("size") or (None, None)
    w, h = (size[0], size[1]) if size and len(size) == 2 else (None, None)
    aspect = (w / h) if (w and h) else None
    quality = "ok"
    if w and h and min(w, h) < LOW_RES_MIN_SIDE:
        quality = "low_res"
    return ProbeResult(
        kind="video", width=w, height=h, aspect=aspect,
        duration=md.get("duration"), codec=md.get("codec"),
        quality_flag=quality, extra={"fps": md.get("fps")},
    )


def probe(path: str, kind: str) -> ProbeResult:
    return probe_video(path) if kind == "video" else probe_image(path)
