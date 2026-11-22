"""Cuándo y dónde se tomó una foto o un video. Sin Qt.

Fotos: el EXIF (`DateTimeOriginal`, y si falta `DateTimeDigitized` o
`DateTime`; GPS si lo tiene). Videos: los metadatos que muestra ffmpeg
(`creation_time`, y `location` en los de teléfono). Medido en el archivo de la
iglesia el 22/11/2026: el 94 % de las fotos tiene fecha y el 4,7 % tiene GPS;
cerca de la mitad de los videos tiene fecha y los `.mts` no la tienen.

`METADATOS_VERSION`: subirla si cambia lo que se lee; el NAS completa una vez lo
ya catalogado (`media.metadatos_version`).
"""

import datetime
import re
import subprocess
from dataclasses import dataclass

from PIL import Image

METADATOS_VERSION = 1

# Sin zona en el EXIF: la hora es la de la cámara, en la iglesia.
ZONA_LOCAL = datetime.timezone(datetime.timedelta(hours=-3))
_MINIMO = datetime.datetime(1990, 1, 1, tzinfo=datetime.timezone.utc)

_EXIF_IFD, _GPS_IFD = 0x8769, 0x8825
_ORIGINAL, _DIGITALIZADA, _FECHA = 0x9003, 0x9004, 0x0132
_ZONA_ORIGINAL = 0x9011


@dataclass
class Metadatos:
    tomada: datetime.datetime | None = None
    lat: float | None = None
    lon: float | None = None


def leer(path: str, kind: str) -> Metadatos:
    """Nunca lanza: si no se puede leer, devuelve todo en None."""
    try:
        return _de_video(path) if kind == "video" else _de_foto(path)
    except Exception:  # noqa: BLE001 — archivo raro o ilegible: sin datos
        return Metadatos()


# ----------------------------------------------------------------- fotos
def _de_foto(path: str) -> Metadatos:
    with Image.open(path) as im:
        exif = im.getexif()
    if not exif:
        return Metadatos()
    sub = exif.get_ifd(_EXIF_IFD)
    zona = _zona(sub.get(_ZONA_ORIGINAL))
    tomada = None
    for texto in (sub.get(_ORIGINAL), sub.get(_DIGITALIZADA), exif.get(_FECHA)):
        tomada = fecha_exif(texto, zona)
        if tomada:
            break
    lat, lon = _gps(exif.get_ifd(_GPS_IFD))
    return Metadatos(tomada, lat, lon)


def fecha_exif(texto, zona: datetime.tzinfo | None = None) -> datetime.datetime | None:
    """'2019:07:14 10:31:02' → fecha con zona (la de la iglesia si no dice)."""
    if not isinstance(texto, str):
        return None
    texto = texto.strip().rstrip("\x00")
    try:
        f = datetime.datetime.strptime(texto[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    f = f.replace(tzinfo=zona or ZONA_LOCAL)
    return f if _razonable(f) else None


def _zona(texto) -> datetime.tzinfo | None:
    m = re.fullmatch(r"\s*([+-])(\d{2}):(\d{2})\s*", texto or "") if isinstance(texto, str) else None
    if not m:
        return None
    signo = 1 if m.group(1) == "+" else -1
    return datetime.timezone(signo * datetime.timedelta(hours=int(m.group(2)),
                                                        minutes=int(m.group(3))))


def _gps(gps) -> tuple[float | None, float | None]:
    if not gps:
        return None, None
    try:
        lat = _grados(gps.get(2), gps.get(1))
        lon = _grados(gps.get(4), gps.get(3))
    except (TypeError, ValueError, ZeroDivisionError):
        return None, None
    if lat is None or lon is None or (lat == 0 and lon == 0) \
            or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None
    return lat, lon


def _grados(dms, ref) -> float | None:
    if not dms or len(dms) != 3:
        return None
    g = float(dms[0]) + float(dms[1]) / 60 + float(dms[2]) / 3600
    if isinstance(ref, bytes):
        ref = ref.decode(errors="ignore")
    return -g if str(ref).strip().upper() in ("S", "W") else g


# ----------------------------------------------------------------- videos
def _de_video(path: str) -> Metadatos:
    import imageio_ffmpeg
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", path],
                       capture_output=True, text=True, errors="replace", timeout=60)
    return de_salida_ffmpeg(r.stderr)


def de_salida_ffmpeg(texto: str) -> Metadatos:
    """Lo que ffmpeg imprime con `-i`: el primer `creation_time` (UTC) y la
    ubicación ISO 6709 de los teléfonos (`location : -34.6037-058.3816/`)."""
    tomada = None
    m = re.search(r"creation_time\s*:\s*(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})", texto)
    if m:
        try:
            tomada = datetime.datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}").replace(
                tzinfo=datetime.timezone.utc)
        except ValueError:
            tomada = None
        if tomada and not _razonable(tomada):
            tomada = None
    lat = lon = None
    m = re.search(r"location(?:-eng)?\s*:\s*([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", texto)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
            lat = lon = None
    return Metadatos(tomada, lat, lon)


def _razonable(f: datetime.datetime) -> bool:
    """Descarta fechas de cámara sin configurar (1970, 2000-01-01 no se puede
    distinguir, pero sí lo anterior a 1990) y las del futuro."""
    ahora = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)
    return _MINIMO <= f <= ahora
