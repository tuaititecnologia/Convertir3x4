"""Lectura de videos para el catálogo con imageio-ffmpeg directo. Sin Qt.

La capa de imageio elige el lector por la extensión y no abre `.mts`, `.m2t`
ni `.m2ts` (grabaciones de cámara del archivo histórico), aunque ffmpeg los lee
sin problema. Acá se le pasa el archivo directo a ffmpeg, que mira el contenido.
"""

import imageio_ffmpeg
import numpy as np

# Un cuadro es «negro» si es oscuro y parejo (brillo medio y variación bajos, 0-255).
# Medido en el catálogo: 140 miniaturas de video así, casi todas tomadas por «logo».
NEGRO_BRILLO = 16
NEGRO_VARIACION = 8
# Dónde se busca un cuadro que muestre el video (fracción de la duración).
PUNTOS = (0.1, 0.3, 0.5)


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


def es_negro(cuadro: np.ndarray) -> bool:
    """Oscuro y parejo: el arranque de una grabación, un fundido, una foto tapada."""
    luz = cuadro.mean(axis=2) if cuadro.ndim == 3 else cuadro
    return float(luz.mean()) < NEGRO_BRILLO and float(luz.std()) < NEGRO_VARIACION


def cuadro_representativo(path: str, duracion: float | None = None) -> np.ndarray:
    """Un cuadro que muestre el video: el primero que no sea negro entre el 10 %,
    el 30 % y el 50 % de la duración. Las grabaciones suelen empezar en negro,
    así que el primer cuadro no sirve de miniatura. Si todos son negros, devuelve
    el último que leyó (el video está en negro). Lanza si ffmpeg no lo abre."""
    tiempos = [duracion * f for f in PUNTOS] if duracion and duracion > 0 else [2.0, 10.0]
    ultimo = None
    for t in tiempos:
        try:
            cuadro = _cuadro_en(path, t)
        except Exception:  # noqa: BLE001 — no se pudo saltar ahí: se prueba otro punto
            continue
        if cuadro is None:
            continue
        if not es_negro(cuadro):
            return cuadro
        ultimo = cuadro
    if ultimo is None:
        _, ultimo = leer(path, cuadro=True)     # sin duración útil: el primero
    return ultimo


def _cuadro_en(path: str, segundos: float) -> np.ndarray | None:
    # -ss antes de -i: ffmpeg salta directo (por cuadros clave), sin decodificar lo anterior
    gen = imageio_ffmpeg.read_frames(path, input_params=["-ss", f"{segundos:.3f}"])
    try:
        meta = next(gen)
        w, h = meta["size"]
        datos = next(gen, None)
        return None if datos is None else np.frombuffer(datos, dtype=np.uint8).reshape(h, w, 3)
    finally:
        gen.close()
