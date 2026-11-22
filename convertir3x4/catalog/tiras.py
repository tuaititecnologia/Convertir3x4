"""Tira de cuadros de un video para el catálogo (vista previa en Buscar). Sin Qt.

La arma el NAS al catalogar y se guarda en la base (tabla `tira`), como la
miniatura: la PC no lee el video por la red. Mismo formato que la de Convertir
(`convert/clips.make_storyboard`): 16 cuadros de 240 px en una grilla de 4×4,
que la grilla recorre al pasar el puntero.

Una sola llamada a ffmpeg con un salto por cuadro y solo cuadros clave
(`-skip_frame nokey`): medido en el NAS el 22/11/2026, 3,5 s por video en lugar
de 10 s decodificando el cuadro exacto. Para una vista previa alcanza el cuadro
clave más cercano.
"""

import os
import subprocess
import tempfile

CUADROS = 16
COLUMNAS = 4
ANCHO = 240
TIRA_VERSION = 1


def hacer_tira(path: str, duracion: float | None) -> bytes | None:
    """JPEG de la tira, o None si el video no da cuadros (muy corto, dañado)."""
    import imageio_ffmpeg
    if not duracion or duracion <= 0:
        return None
    filas = -(-CUADROS // COLUMNAS)
    args = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y"]
    for k in range(1, CUADROS + 1):
        args += ["-skip_frame", "nokey", "-ss", f"{duracion * k / (CUADROS + 1):.3f}",
                 "-noaccurate_seek", "-i", path]
    filtro = "".join(f"[{i}:v]trim=end_frame=1,setpts=PTS-STARTPTS,"
                     f"scale={ANCHO}:-2:flags=fast_bilinear,setsar=1[v{i}];"
                     for i in range(CUADROS))
    filtro += "".join(f"[v{i}]" for i in range(CUADROS))
    filtro += f"concat=n={CUADROS}:v=1:a=0,tile={COLUMNAS}x{filas}[o]"
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "tira.jpg")
        try:
            subprocess.run(args + ["-filter_complex", filtro, "-map", "[o]",
                                   "-frames:v", "1", "-q:v", "4", out],
                           capture_output=True, timeout=180)
        except subprocess.TimeoutExpired:
            return None
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            return None
        with open(out, "rb") as f:
            return f.read()
