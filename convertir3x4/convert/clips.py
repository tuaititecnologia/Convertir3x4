"""Videos a la proporción de la pantalla, con el audio nivelado para la consola.

Es la receta del agente de ProPresenter (`tools/video_8x9.py`), llevada a la app.
Todo corre con el ffmpeg que trae `imageio-ffmpeg`, sin ffprobe ni otras
herramientas instaladas. Sin Qt: lo llama un worker de la pestaña Convertir.
Diseño y criterio en docs/convertir-fotos-y-videos.md.
"""

import json
import math
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field

import imageio_ffmpeg
from PIL import Image

from ..config import DEFAULT_ASPECT_W, DEFAULT_ASPECT_H, VIDEO_DURATION, VIDEO_OVERSIZE
from .images import _dominant_colors, _make_zoned_bg

# Lienzo de referencia: 8:9 al doble de la nativa del LED (448x504).
BASE_W, BASE_H = 896, 1008

# Audio: criterio del 30/10/2026. No es una opción de la app: la consola se calibra
# una sola vez contra este nivel.
LUFS = -16.0
TP = -1.0
TP_LIMIT = TP - 0.5      # el AAC sube los picos ~0,3 dB al codificar
LRA = 20.0               # alto para que loudnorm aplique ganancia fija, sin comprimir
LUFS_TOLERANCE = 1.0
FIX_LIMIT = 0.66         # -3,6 dBFS: margen que necesitó HISTORIA 37 para quedar bajo -1
FIX_EXTRA_GAIN = 0.35    # compensa lo que recorta el limitador

VIDEO_CODEC = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]
AUDIO_CODEC = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".heic"}


class ClipError(RuntimeError):
    """ffmpeg falló o el archivo no es un video legible."""


class Cancelled(Exception):
    """El usuario canceló la conversión."""


@dataclass
class ClipInfo:
    width: int            # tal como se ve (con la rotación del celular ya aplicada)
    height: int
    duration: float       # segundos
    fps: float
    has_audio: bool


@dataclass
class AudioReport:
    before: float | None = None     # LUFS del original (None: sin audio)
    after: float | None = None      # LUFS del resultado
    peak: float | None = None       # true peak del resultado, dBFS
    fixed: bool = False             # hubo que rehacer el audio con limitador
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- ffmpeg

def ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def _popen_kwargs() -> dict:
    kw = {"encoding": "utf-8", "errors": "replace"}
    if os.name == "nt":
        # En el .exe con ventana, sin esto se abre una consola por cada ffmpeg.
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([ffmpeg_exe(), "-hide_banner", "-nostdin", *args],
                          capture_output=True, **_popen_kwargs())


def run_ffmpeg(args: list[str], duration: float | None = None,
               progress_cb=None, cancel=None) -> str:
    """Corre ffmpeg informando el avance (0..1) y devuelve su stderr.

    `cancel()` se consulta con cada reporte de avance (unas dos veces por
    segundo); si devuelve True se mata el proceso y se levanta Cancelled.
    """
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-y",
           "-progress", "pipe:1", "-nostats", *args]
    # stderr a un archivo: si fuera a un pipe sin leer, ffmpeg se trabaría al llenarlo.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, **_popen_kwargs())
        try:
            for line in proc.stdout:
                if cancel and cancel():
                    proc.kill()
                    proc.wait()
                    raise Cancelled()
                if progress_cb and duration and line.startswith("out_time_us="):
                    try:
                        us = int(line.split("=", 1)[1])
                    except ValueError:
                        continue
                    progress_cb(max(0.0, min(1.0, us / 1e6 / duration)))
            proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        err.seek(0)
        stderr = err.read()
    if proc.returncode != 0:
        raise ClipError(stderr.strip()[-1500:] or f"ffmpeg terminó con código {proc.returncode}")
    return stderr


# ---------------------------------------------------------------- datos del video

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_SIZE_RE = re.compile(r"\b(\d{2,5})x(\d{2,5})\b")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_ROT_RE = re.compile(r"rotation of\s*(-?\d+(?:\.\d+)?)\s*degrees")


def parse_info(stderr: str) -> ClipInfo:
    """Interpreta la salida de `ffmpeg -i` (no hay ffprobe en la app)."""
    dur = _DURATION_RE.search(stderr)
    duration = (int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3))
                if dur else 0.0)

    width = height = 0
    fps = 30.0
    rotation = 0.0
    lines = stderr.splitlines()
    for i, line in enumerate(lines):
        if ": Video:" not in line or "attached pic" in line:
            continue
        size = _SIZE_RE.search(line.split(": Video:", 1)[1])
        if not size:
            continue
        width, height = int(size.group(1)), int(size.group(2))
        f = _FPS_RE.search(line)
        if f:
            fps = float(f.group(1))
        # La rotación aparece en las líneas de "Side data" de este mismo stream.
        for nxt in lines[i + 1:]:
            if "Stream #" in nxt:
                break
            r = _ROT_RE.search(nxt)
            if r:
                rotation = float(r.group(1))
        break
    if not width:
        raise ClipError("No se encontró una pista de video.")
    if round(abs(rotation)) % 180 == 90:
        width, height = height, width

    has_audio = any(": Audio:" in ln for ln in lines if "Stream #" in ln)
    return ClipInfo(width, height, duration, fps, has_audio)


def clip_info(path: str) -> ClipInfo:
    r = _run(["-i", path])          # sin salida, ffmpeg sale con error: es lo esperado
    if "Invalid data" in r.stderr or "No such file" in r.stderr:
        raise ClipError(f"No se pudo leer {os.path.basename(path)}.")
    return parse_info(r.stderr)


def is_image(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS


def canvas_size(aspect: tuple[int, int] | None = None) -> tuple[int, int]:
    """Lienzo de salida: 896x1008 en 8:9; otras proporciones, con los mismos píxeles."""
    aw, ah = aspect or (DEFAULT_ASPECT_W, DEFAULT_ASPECT_H)
    w = math.sqrt(BASE_W * BASE_H * aw / ah)
    h = w * ah / aw
    return int(round(w / 2)) * 2, int(round(h / 2)) * 2


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


# ---------------------------------------------------------------- audio

def measure_loudness(path: str, stream: int = 0, duration=None,
                     progress_cb=None, cancel=None) -> dict | None:
    """Primera pasada de loudnorm. None si la pista está en silencio."""
    stderr = run_ffmpeg(["-i", path, "-map", f"0:a:{stream}", "-af",
                         f"loudnorm=I={LUFS}:TP={TP_LIMIT}:LRA={LRA}:print_format=json",
                         "-f", "null", "-"], duration, progress_cb, cancel)
    try:
        m = json.loads(stderr[stderr.rindex("{"):stderr.rindex("}") + 1])
    except ValueError as e:
        raise ClipError("No se pudo medir el volumen del audio.") from e
    if m.get("input_i") in (None, "-inf") or not math.isfinite(float(m["input_i"])):
        return None
    return m


def _loudnorm_filter(m: dict) -> str:
    return (f"loudnorm=I={LUFS}:TP={TP_LIMIT}:LRA={LRA}:linear=true"
            f":measured_I={m['input_i']}:measured_TP={m['input_tp']}"
            f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
            f":offset={m['target_offset']},aresample=48000")


def measure_final(path: str) -> tuple[float | None, float | None]:
    """LUFS integrado y true peak del resultado, con ebur128."""
    r = _run(["-nostats", "-i", path, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"])
    summary = r.stderr[r.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
    p = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
    integrated = float(i.group(1)) if i else None
    peak = float(p.group(1)) if p and p.group(1) != "-inf" else None
    return integrated, peak


def _fix_audio(out_path: str, audio_src: str, audio_stream: int, before: float,
               cancel=None) -> None:
    """Rehace solo el audio: ganancia fija y limitador de pico real (192 kHz).

    Es el arreglo manual de HISTORIA 37: cuando el original es muy bajo y tiene
    picos, loudnorm pasa a modo dinámico y el pico queda arriba de -1.
    """
    gain = LUFS - before + FIX_EXTRA_GAIN
    tmp = _partial_path(out_path, "audio")
    try:
        run_ffmpeg(["-i", out_path, "-i", audio_src, "-map", "0:v", "-map",
                    f"1:a:{audio_stream}", "-c:v", "copy", "-af",
                    f"aresample=192000,volume={gain:.2f}dB,"
                    f"alimiter=limit={FIX_LIMIT}:level=false:attack=1:release=50,"
                    f"aresample=48000", *AUDIO_CODEC, "-shortest",
                    "-movflags", "+faststart", tmp], cancel=cancel)
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _check_audio(out_path: str, report: AudioReport, audio_src: str | None,
                 audio_stream: int, cancel=None) -> AudioReport:
    if report.before is None:
        return report
    report.after, report.peak = measure_final(out_path)
    if report.peak is not None and report.peak > TP:
        _fix_audio(out_path, audio_src, audio_stream, report.before, cancel)
        report.fixed = True
        report.after, report.peak = measure_final(out_path)
    if report.peak is not None and report.peak > TP:
        report.warnings.append(f"El pico quedó en {report.peak:.1f} dBFS, arriba de {TP:.0f}.")
    if report.after is not None and abs(report.after - LUFS) > LUFS_TOLERANCE:
        report.warnings.append(
            f"El volumen quedó en {report.after:.1f} LUFS, a más de "
            f"{LUFS_TOLERANCE:.0f} dB de {LUFS:.0f} (el audio es muy corto o muy desparejo).")
    return report


# ---------------------------------------------------------------- armado

def _partial_path(out_path: str, tag: str = "parcial") -> str:
    base, ext = os.path.splitext(out_path)
    return f"{base}.{tag}{ext or '.mp4'}"


class _Stages:
    """Reparte el avance de varias pasadas de ffmpeg en una sola barra 0..1."""

    def __init__(self, progress_cb, weights: list[float]):
        self.cb = progress_cb
        total = sum(weights) or 1.0
        self.spans, acc = [], 0.0
        for w in weights:
            self.spans.append((acc / total, w / total))
            acc += w

    def stage(self, i: int):
        if not self.cb:
            return None
        start, width = self.spans[i]
        return lambda f: self.cb(start + width * f)

    def done(self):
        if self.cb:
            self.cb(1.0)


def _encode(inputs: list[str], filter_complex: str, out_path: str, duration: float,
            audio: tuple[int, int] | None, audio_measure: dict | None,
            progress_cb, cancel, extra_out: list[str] | None = None) -> None:
    """Pasada principal: arma el video y aplica la ganancia de loudnorm.

    `audio` es (índice de input, índice de pista) o None si el resultado va mudo.
    Escribe en un archivo parcial y lo renombra al final: si se cancela o falla,
    no queda un MP4 a medio escribir con el nombre final.
    """
    tmp = _partial_path(out_path)
    args = [*inputs, "-filter_complex", filter_complex, "-map", "[v]", *VIDEO_CODEC]
    if audio is not None:
        args += ["-map", f"{audio[0]}:a:{audio[1]}"]
        if audio_measure:
            args += ["-af", _loudnorm_filter(audio_measure)]
        args += AUDIO_CODEC
    else:
        args += ["-an"]
    args += [*(extra_out or []), "-movflags", "+faststart", tmp]
    try:
        run_ffmpeg(args, duration, progress_cb, cancel)
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def convert_centered_clip(src: str, out_path: str, aspect=None,
                          progress_cb=None, cancel=None) -> AudioReport:
    """Video entero y centrado, con el mismo video ampliado y desenfocado de fondo."""
    info = clip_info(src)
    w, h = canvas_size(aspect)
    graph = (
        f"[0:v]split[a][b];"
        f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"gblur=sigma=40,eq=brightness=-0.08[bg];"
        f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v]"
    )
    return _convert(["-i", src], graph, out_path, info.duration,
                    audio_src=src if info.has_audio else None, audio_input=0,
                    progress_cb=progress_cb, cancel=cancel)


def convert_clip_on_background(src: str, bg_path: str, out_path: str, aspect=None,
                               progress_cb=None, cancel=None) -> AudioReport:
    """Video en la mitad de arriba sobre un fondo elegido (como convert_on_background)."""
    info = clip_info(src)
    w, h = canvas_size(aspect)
    margin = int(w * 0.05)
    max_w, max_h = _even(w - margin * 2), _even(h * 0.50 - margin)
    with tempfile.TemporaryDirectory() as tmpdir:
        bg_png = os.path.join(tmpdir, "fondo.png")
        _cover(Image.open(bg_path).convert("RGB"), w, h).save(bg_png)
        graph = (
            f"[1:v]scale={max_w}:{max_h}:force_original_aspect_ratio=decrease"
            f":force_divisible_by=2,setsar=1[fg];"
            f"[0:v][fg]overlay=(W-w)/2:{margin}:shortest=1,setsar=1,format=yuv420p[v]"
        )
        inputs = ["-loop", "1", "-framerate", f"{info.fps:g}", "-i", bg_png, "-i", src]
        return _convert(inputs, graph, out_path, info.duration,
                        audio_src=src if info.has_audio else None, audio_input=1,
                        progress_cb=progress_cb, cancel=cancel,
                        extra_out=["-t", f"{info.duration:.3f}"])


def double_layout(size_a: tuple[int, int], size_b: tuple[int, int],
                  w: int, h: int) -> list[tuple[int, int, int, int, int]]:
    """Lugar de cada elemento en Doble: [(índice, x, y, ancho, alto)] de arriba a abajo.

    Misma regla que convert_double: el más ancho arriba, con hasta 45 % del alto,
    y el más alto ocupa el resto.
    """
    ar_a, ar_b = size_a[0] / size_a[1], size_b[0] / size_b[1]
    order = [(0, size_a), (1, size_b)] if ar_a >= ar_b else [(1, size_b), (0, size_a)]
    margin = int(h * 0.02)

    def fit(size, max_w, max_h):
        s = min(max_w / size[0], max_h / size[1])
        return _even(size[0] * s), _even(size[1] * s)

    top_w, top_h = fit(order[0][1], w - margin * 2, int(h * 0.45))
    bot_w, bot_h = fit(order[1][1], w - margin * 2, h - top_h - margin * 3)
    margin = (h - top_h - bot_h) // 3
    return [
        (order[0][0], (w - top_w) // 2, margin, top_w, top_h),
        (order[1][0], (w - bot_w) // 2, margin * 2 + top_h, bot_w, bot_h),
    ]


def convert_double_clip(paths: list[str], out_path: str, aspect=None,
                        progress_cb=None, cancel=None) -> AudioReport:
    """Dos elementos apilados, al menos uno video. La salida es siempre video.

    Dura lo que el video más largo; el más corto queda en su último cuadro.
    El audio es el del primero (en el orden recibido) que tenga audio.
    El fondo es el de colores de convert_double, con el mismo recorrido animado
    que "Exportar como video".
    """
    if len(paths) != 2:
        raise ValueError("Doble necesita exactamente dos elementos.")
    w, h = canvas_size(aspect)
    infos = [None if is_image(p) else clip_info(p) for p in paths]
    videos = [i for i in infos if i is not None]
    if not videos:
        raise ValueError("Con dos fotos se usa convert_double.")
    duration = max(v.duration for v in videos)
    fps = next(v.fps for v in infos if v is not None)

    with tempfile.TemporaryDirectory() as tmpdir:
        frames = []  # imagen representativa de cada elemento (para tamaño y colores)
        for k, (p, info) in enumerate(zip(paths, infos)):
            frames.append(Image.open(p).convert("RGB") if info is None
                          else _grab_frame(p, info, os.path.join(tmpdir, f"cuadro{k}.png")))
        sizes = [(f.width, f.height) for f in frames]
        layout = double_layout(sizes[0], sizes[1], w, h)

        # Fondo animado: lienzo más grande y un recorte que se mueve (Lissajous,
        # mismo camino que _write_video_frames, ciclo de VIDEO_DURATION segundos).
        top_idx, bot_idx = layout[0][0], layout[1][0]
        os_w, os_h = _even(w * VIDEO_OVERSIZE), _even(h * VIDEO_OVERSIZE)
        bg = _make_zoned_bg(os_w, os_h, _dominant_colors(frames[top_idx]),
                            _dominant_colors(frames[bot_idx]))
        bg_png = os.path.join(tmpdir, "fondo.png")
        bg.save(bg_png)
        mx, my = (os_w - w) // 2, (os_h - h) // 2
        p = f"(2*PI*t/{VIDEO_DURATION})"
        cx = f"{mx}+{mx}*(0.7*sin({p})+0.3*sin(2*{p}))"
        cy = f"{my}+{my}*(0.7*sin(0.7*{p})+0.3*cos(1.5*{p}))"

        inputs = ["-loop", "1", "-framerate", f"{fps:g}", "-i", bg_png]
        parts = [f"[0:v]crop={w}:{h}:x='{cx}':y='{cy}'[bg]"]
        for n, (idx, x, y, fw, fh) in enumerate(layout, start=1):
            if infos[idx] is None:
                img_png = os.path.join(tmpdir, f"foto{idx}.png")
                frames[idx].resize((fw, fh), Image.LANCZOS).save(img_png)
                inputs += ["-loop", "1", "-framerate", f"{fps:g}", "-i", img_png]
                parts.append(f"[{n}:v]setsar=1[e{n}]")
            else:
                inputs += ["-i", paths[idx]]
                parts.append(f"[{n}:v]scale={fw}:{fh},setsar=1[e{n}]")
        # overlay repite el último cuadro del video más corto (eof_action=repeat).
        (_, x1, y1, _, _), (_, x2, y2, _, _) = layout
        parts.append(f"[bg][e1]overlay={x1}:{y1}[m]")
        parts.append(f"[m][e2]overlay={x2}:{y2},setsar=1,format=yuv420p[v]")

        audio_src, audio_input = None, 0
        input_of = {idx: n for n, (idx, *_r) in enumerate(layout, start=1)}
        for idx, info in enumerate(infos):
            if info is not None and info.has_audio:
                audio_src, audio_input = paths[idx], input_of[idx]
                break
        return _convert(inputs, ";".join(parts), out_path, duration,
                        audio_src=audio_src, audio_input=audio_input,
                        progress_cb=progress_cb, cancel=cancel,
                        extra_out=["-t", f"{duration:.3f}"])


def _convert(inputs, graph, out_path, duration, audio_src, audio_input,
             progress_cb, cancel, extra_out=None) -> AudioReport:
    """Mide el audio, arma el video y verifica. Común a las tres composiciones."""
    report = AudioReport()
    stages = _Stages(progress_cb, [0.15, 0.80, 0.05])
    measure = None
    if audio_src is not None:
        measure = measure_loudness(audio_src, 0, duration, stages.stage(0), cancel)
        if measure is not None:
            report.before = float(measure["input_i"])
    audio = (audio_input, 0) if audio_src is not None else None
    _encode(inputs, graph, out_path, duration, audio, measure,
            stages.stage(1), cancel, extra_out)
    _check_audio(out_path, report, audio_src, 0, cancel)
    stages.done()
    return report


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Escala para cubrir w x h y recorta al centro (como el fondo de convert_on_background)."""
    s = max(w / img.width, h / img.height)
    img = img.resize((max(w, round(img.width * s)), max(h, round(img.height * s))),
                     Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


def _grab_frame(path: str, info: ClipInfo, out_png: str) -> Image.Image:
    """Un cuadro del video (a 1 s, o a la mitad si es más corto), ya rotado."""
    t = min(1.0, info.duration / 2) if info.duration else 0
    r = _run(["-y", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", out_png])
    if r.returncode != 0 or not os.path.exists(out_png):
        raise ClipError(f"No se pudo leer un cuadro de {os.path.basename(path)}.")
    with Image.open(out_png) as im:
        return im.convert("RGB")


# ---------------------------------------------------------------- vista previa

STORYBOARD_FRAMES = 16
STORYBOARD_COLS = 4
STORYBOARD_WIDTH = 240


def make_storyboard(path: str, out_jpg: str, info: ClipInfo | None = None) -> None:
    """Tira de cuadros para la vista previa al pasar el mouse (como YouTube).

    16 cuadros de 240 px repartidos en todo el video, en una grilla de 4x4 en un
    solo JPEG. En videos largos solo decodifica cuadros clave, así que tarda
    décimas de segundo.
    """
    info = info or clip_info(path)
    n = STORYBOARD_FRAMES
    rate = (n + 1) / info.duration if info.duration else 1
    skip = ["-skip_frame", "nokey"] if info.duration >= 60 else []
    rows = math.ceil(n / STORYBOARD_COLS)
    r = _run(["-y", *skip, "-i", path, "-an", "-vf",
              f"fps={rate:.6f},scale={STORYBOARD_WIDTH}:-2,tile={STORYBOARD_COLS}x{rows}",
              "-frames:v", "1", "-q:v", "5", out_jpg])
    if r.returncode != 0 or not os.path.exists(out_jpg):
        raise ClipError(f"No se pudo armar la vista previa de {os.path.basename(path)}.")


# ---------------------------------------------------------------- miniaturas de la grilla

@dataclass
class ClipThumbs:
    info: ClipInfo
    poster: str        # JPG: un cuadro del video (la miniatura de la grilla)
    storyboard: str    # JPG: tira de 16 cuadros (vista previa al pasar el mouse)


def clip_thumbs(path: str, cache_dir: str) -> ClipThumbs:
    """Miniatura y tira de cuadros de un video, en caché por ruta + fecha + tamaño.

    La segunda vez que se carga el mismo video no se vuelve a llamar a ffmpeg
    para los cuadros (sí para los datos, que son instantáneos).
    """
    import hashlib
    st = os.stat(path)
    key = hashlib.sha1(f"{path}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8")).hexdigest()[:20]
    os.makedirs(cache_dir, exist_ok=True)
    poster = os.path.join(cache_dir, f"{key}.jpg")
    board = os.path.join(cache_dir, f"{key}_cuadros.jpg")
    info = clip_info(path)
    if not os.path.exists(poster):
        tmp_png = os.path.join(cache_dir, f"{key}.png")
        try:
            frame = _grab_frame(path, info, tmp_png)
            frame.thumbnail((480, 480))
            frame.save(poster, "JPEG", quality=85)
        finally:
            if os.path.exists(tmp_png):
                os.remove(tmp_png)
    if not os.path.exists(board):
        make_storyboard(path, board, info)
    return ClipThumbs(info, poster, board)
