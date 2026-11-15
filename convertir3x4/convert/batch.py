"""Arma y corre los trabajos de la pestaña Convertir, sin Qt.

La pestaña solo dice qué está seleccionado (en orden), qué acción se eligió y
las opciones; acá se decide qué función corresponde, cómo se llama la salida y
cómo se informa el avance. Reglas (docs/convertir-fotos-y-videos.md):

- Centrada: una salida por elemento. Foto -> JPG (o MP4 con fondo animado si
  está activado "Exportar fotos como video"); video -> MP4 con su audio nivelado.
- Doble: de a pares, en el orden de la grilla. Si en el par hay un video, sale
  video. Si queda uno suelto, sale solo.
- Sobre fondo: una salida por elemento sobre el fondo elegido. Video -> MP4.

Los archivos salen numerados (`0001_…`) para conservar el orden de la grilla,
que es el orden en que se arman las presentaciones.
"""

import os
from dataclasses import dataclass, field
from typing import Callable

from ..catalog.db import IMAGE_EXTS, VIDEO_EXTS
from . import clips
from .images import convert_centered, convert_double, convert_on_background
from .video import convert_centered_video, convert_double_video

CENTRADA, DOBLE, FONDO = "centrada", "doble", "fondo"
MODES = (CENTRADA, DOBLE, FONDO)


def kind_of(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return None


@dataclass
class Options:
    aspect: tuple[int, int] = (8, 9)
    photos_as_video: bool = False
    background: str | None = None


@dataclass
class Job:
    inputs: list[str]
    out_name: str
    mode: str
    is_video: bool                  # la salida es MP4
    has_clip: bool                  # alguna entrada es un video (usa clips.py)


@dataclass
class Result:
    job: Job
    out_path: str
    audio: clips.AudioReport | None = None
    error: str | None = None


def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def plan(paths: list[str], mode: str, opts: Options) -> list[Job]:
    """Lista de trabajos para la selección (en el orden de la grilla)."""
    if mode not in MODES:
        raise ValueError(f"Acción desconocida: {mode}")
    if mode == FONDO and not opts.background:
        raise ValueError("Falta elegir el fondo.")
    w, h = opts.aspect
    sfx = f"_{w}x{h}"
    groups = ([paths[i:i + 2] for i in range(0, len(paths), 2)] if mode == DOBLE
              else [[p] for p in paths])
    jobs = []
    for i, group in enumerate(groups, start=1):
        has_clip = any(kind_of(p) == "video" for p in group)
        is_video = has_clip or (opts.photos_as_video and mode != FONDO)
        names = "+".join(_stem(p) for p in group)
        extra = "_fondo" if mode == FONDO else ""
        ext = ".mp4" if is_video else ".jpg"
        jobs.append(Job(group, f"{i:04d}_{names}{extra}{sfx}{ext}", mode, is_video, has_clip))
    return jobs


def describe(paths: list[str], mode: str, opts: Options) -> str:
    """Qué va a salir, en palabras del operador (para el panel de acciones)."""
    if not paths:
        return ""
    try:
        jobs = plan(paths, mode, opts)
    except ValueError as e:
        return str(e)
    n_video = sum(j.is_video for j in jobs)
    n_photo = len(jobs) - n_video
    parts = []
    if n_photo:
        parts.append("1 foto" if n_photo == 1 else f"{n_photo} fotos")
    if n_video:
        parts.append("1 video" if n_video == 1 else f"{n_video} videos")
    text = "Sale " if len(jobs) == 1 else "Salen "
    text += " y ".join(parts)
    if mode == DOBLE and len(paths) % 2:
        text += " (la última queda sola)"
    return text + "."


def run(job: Job, out_dir: str, opts: Options,
        progress_cb: Callable[[float], None] | None = None,
        cancel: Callable[[], bool] | None = None) -> Result:
    """Corre un trabajo. Avance 0..1. Si se cancela, no deja el archivo a medias."""
    out_path = os.path.join(out_dir, job.out_name)
    aspect = opts.aspect
    cb = progress_cb or (lambda f: None)

    def frames_cb(done, total):
        # Las funciones de foto-a-video avisan por cuadro; acá se cancela entre cuadros.
        if cancel and cancel():
            raise clips.Cancelled()
        cb(done / total if total else 0.0)

    audio = None
    try:
        if job.has_clip:
            if job.mode == CENTRADA or (job.mode == DOBLE and len(job.inputs) == 1):
                audio = clips.convert_centered_clip(job.inputs[0], out_path, aspect, cb, cancel)
            elif job.mode == DOBLE:
                audio = clips.convert_double_clip(job.inputs, out_path, aspect, cb, cancel)
            else:
                audio = clips.convert_clip_on_background(job.inputs[0], opts.background,
                                                         out_path, aspect, cb, cancel)
        elif job.mode == CENTRADA:
            if job.is_video:
                convert_centered_video(job.inputs[0], out_path, aspect=aspect, progress_cb=frames_cb)
            else:
                convert_centered(job.inputs[0], out_path, aspect=aspect)
        elif job.mode == DOBLE:
            if job.is_video:
                convert_double_video(job.inputs, out_path, aspect=aspect, progress_cb=frames_cb)
            else:
                convert_double(job.inputs, out_path, aspect=aspect)
        else:
            convert_on_background(job.inputs[0], opts.background, out_path, aspect=aspect)
    except clips.Cancelled:
        _remove(out_path)
        raise
    except Exception as e:  # noqa: BLE001 — se informa en la grilla, no corta el lote
        _remove(out_path)
        return Result(job, out_path, error=_friendly(e))
    cb(1.0)
    return Result(job, out_path, audio=audio)


def _remove(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _friendly(e: Exception) -> str:
    """Mensaje para el operador: qué pasó, sin el detalle técnico de ffmpeg."""
    if isinstance(e, clips.ClipError):
        return ("No se pudo convertir el video: puede estar dañado o en un formato "
                "que no se puede leer.")
    if isinstance(e, FileNotFoundError):
        return "No se encontró el archivo (¿se desconectó el disco o el NAS?)."
    if isinstance(e, PermissionError):
        return "No se puede guardar en esa carpeta: no hay permiso. Elegí otra."
    return "Algo falló al convertir este archivo."
