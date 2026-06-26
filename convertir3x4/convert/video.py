"""Exportación a video MP4 con fondo animado (pan sutil tipo Lissajous)."""

import math

import imageio
import numpy as np
from PIL import Image

from ..config import (
    VIDEO_FPS, VIDEO_DURATION, VIDEO_OVERSIZE, VIDEO_MAX_WIDTH,
    DEFAULT_ASPECT_W, DEFAULT_ASPECT_H,
)
from .images import _dominant_colors, _make_textured_bg, _make_zoned_bg


def _write_video_frames(oversized_bg, canvas_w, canvas_h, paste_fn, out_path,
                        fps=VIDEO_FPS, duration=VIDEO_DURATION, progress_cb=None):
    """Genera un MP4 con pan sutil sobre un fondo oversized."""
    margin_x = (oversized_bg.width - canvas_w) // 2
    margin_y = (oversized_bg.height - canvas_h) // 2
    total_frames = fps * duration

    writer = imageio.get_writer(out_path, fps=fps, codec="libx264",
                                macro_block_size=1,
                                output_params=["-crf", "23", "-pix_fmt", "yuv420p"])
    try:
        for i in range(total_frames):
            t = i / total_frames
            cx = margin_x + int(margin_x * (0.7 * math.sin(2 * math.pi * t)
                                            + 0.3 * math.sin(4 * math.pi * t)))
            cy = margin_y + int(margin_y * (0.7 * math.sin(2 * math.pi * t * 0.7)
                                            + 0.3 * math.cos(3 * math.pi * t)))
            frame = oversized_bg.crop((cx, cy, cx + canvas_w, cy + canvas_h))
            paste_fn(frame)
            writer.append_data(np.asarray(frame))
            if progress_cb:
                progress_cb(i + 1, total_frames)
    finally:
        writer.close()


def convert_centered_video(src_path: str, out_path: str, aspect: tuple[int, int] = None,
                           fps: int = VIDEO_FPS, duration: int = VIDEO_DURATION,
                           progress_cb=None):
    """Genera un MP4 con fondo animado y foto centrada estática."""
    aw, ah = aspect or (DEFAULT_ASPECT_W, DEFAULT_ASPECT_H)
    img = Image.open(src_path).convert("RGB")
    src_w, src_h = img.size

    if src_w / src_h > aw / ah:
        canvas_w = src_w
        canvas_h = int(src_w * ah / aw)
    else:
        canvas_h = src_h
        canvas_w = int(src_h * aw / ah)

    # Limitar resolución
    if canvas_w > VIDEO_MAX_WIDTH:
        scale = VIDEO_MAX_WIDTH / canvas_w
        canvas_w = VIDEO_MAX_WIDTH
        canvas_h = int(canvas_h * scale)
        img = img.resize((int(src_w * scale), int(src_h * scale)), Image.LANCZOS)
        src_w, src_h = img.size

    colors = _dominant_colors(img)
    os_w = int(canvas_w * VIDEO_OVERSIZE)
    os_h = int(canvas_h * VIDEO_OVERSIZE)
    oversized_bg = _make_textured_bg(os_w, os_h, colors)

    photo_x = (canvas_w - src_w) // 2
    photo_y = (canvas_h - src_h) // 2

    def paste_fn(frame):
        frame.paste(img, (photo_x, photo_y))

    _write_video_frames(oversized_bg, canvas_w, canvas_h, paste_fn, out_path,
                        fps, duration, progress_cb)


def convert_double_video(paths: list[str], out_path: str, aspect: tuple[int, int] = None,
                         fps: int = VIDEO_FPS, duration: int = VIDEO_DURATION,
                         progress_cb=None):
    """Genera un MP4 con fondo animado y dos fotos apiladas verticalmente."""
    aw, ah = aspect or (DEFAULT_ASPECT_W, DEFAULT_ASPECT_H)
    images = [Image.open(p).convert("RGB") for p in paths]

    if len(images) == 1:
        convert_centered_video(paths[0], out_path, aspect, fps, duration, progress_cb)
        return

    img1, img2 = images[0], images[1]
    ar1 = img1.size[0] / img1.size[1]
    ar2 = img2.size[0] / img2.size[1]

    if ar1 >= ar2:
        img_wide, img_tall = img1, img2
    else:
        img_wide, img_tall = img2, img1

    canvas_w = max(img_wide.size[0], img_tall.size[0])
    canvas_h = int(canvas_w * ah / aw)

    # Limitar resolución
    if canvas_w > VIDEO_MAX_WIDTH:
        scale_factor = VIDEO_MAX_WIDTH / canvas_w
        canvas_w = VIDEO_MAX_WIDTH
        canvas_h = int(canvas_h * scale_factor)
        img_wide = img_wide.resize(
            (int(img_wide.size[0] * scale_factor), int(img_wide.size[1] * scale_factor)),
            Image.LANCZOS)
        img_tall = img_tall.resize(
            (int(img_tall.size[0] * scale_factor), int(img_tall.size[1] * scale_factor)),
            Image.LANCZOS)

    margin = int(canvas_h * 0.02)

    def fit_image(img, max_w, max_h):
        w, h = img.size
        s = min(max_w / w, max_h / h)
        return img.resize((int(w * s), int(h * s)), Image.LANCZOS)

    wide_max_w = canvas_w - margin * 2
    wide_max_h = int(canvas_h * 0.45)
    img_wide_fit = fit_image(img_wide, wide_max_w, wide_max_h)

    tall_max_w = canvas_w - margin * 2
    tall_max_h = canvas_h - img_wide_fit.size[1] - margin * 3
    img_tall_fit = fit_image(img_tall, tall_max_w, tall_max_h)

    used_h = img_wide_fit.size[1] + img_tall_fit.size[1]
    remaining = canvas_h - used_h
    margin = remaining // 3

    colors_top = _dominant_colors(img_wide)
    colors_bottom = _dominant_colors(img_tall)

    os_w = int(canvas_w * VIDEO_OVERSIZE)
    os_h = int(canvas_h * VIDEO_OVERSIZE)
    oversized_bg = _make_zoned_bg(os_w, os_h, colors_top, colors_bottom)

    x1 = (canvas_w - img_wide_fit.size[0]) // 2
    y1 = margin
    x2 = (canvas_w - img_tall_fit.size[0]) // 2
    y2 = margin + img_wide_fit.size[1] + margin

    def paste_fn(frame):
        frame.paste(img_wide_fit, (x1, y1))
        frame.paste(img_tall_fit, (x2, y2))

    _write_video_frames(oversized_bg, canvas_w, canvas_h, paste_fn, out_path,
                        fps, duration, progress_cb)
