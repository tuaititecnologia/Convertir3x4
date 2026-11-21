"""Lectura de videos para el catálogo con imageio-ffmpeg directo. Sin Qt.

La capa de imageio elige el lector por la extensión y no abre `.mts`, `.m2t`
ni `.m2ts` (grabaciones de cámara del archivo histórico), aunque ffmpeg los lee
sin problema. Acá se le pasa el archivo directo a ffmpeg, que mira el contenido.
"""

import imageio_ffmpeg
import numpy as np


def leer(path: str, cuadro: bool = False):
    """(datos del video, primer cuadro RGB como array o None).

    Los datos son los de ffmpeg: `size` (ancho, alto), `fps`, `duration`,
    `codec`. Lanza una excepción si ffmpeg no puede abrirlo o no tiene cuadros.
    """
    gen = imageio_ffmpeg.read_frames(path)
    try:
        meta = next(gen)
        frame = None
        if cuadro:
            w, h = meta["size"]
            frame = np.frombuffer(next(gen), dtype=np.uint8).reshape(h, w, 3)
        return meta, frame
    finally:
        gen.close()
