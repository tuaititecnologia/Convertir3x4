"""Constantes y configuración global de la app."""

# _version.py lo genera el CI en la raíz del repo; en dev no existe.
try:
    from _version import APP_VERSION
except ImportError:
    APP_VERSION = "dev"

APP_TITLE = f"Convertir a 3:4 — CDLM ({APP_VERSION})"
THUMB_SIZE = 130
IMAGE_FILTER = "Imágenes (*.jpg *.jpeg *.png *.bmp *.webp);;Todos (*.*)"
JPEG_QUALITY = 92
BLUR_RADIUS = 40
VIDEO_FPS = 24
VIDEO_DURATION = 30
VIDEO_OVERSIZE = 1.6
VIDEO_MAX_WIDTH = 1920
ASPECT_RATIOS = [
    ("8:9 — LED CDLM", 8, 9),
    ("3:4", 3, 4),
    ("2:3", 2, 3),
    ("4:5", 4, 5),
    ("9:16", 9, 16),
    ("1:1", 1, 1),
]
DEFAULT_ASPECT_W, DEFAULT_ASPECT_H = 8, 9
