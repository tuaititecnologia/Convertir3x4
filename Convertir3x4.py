"""Convertir a 3:4 — CDLM.

Punto de entrada y shim de compatibilidad. La lógica vive en el paquete
`convertir3x4/`; este archivo re-exporta la API pública (usada por tests y por
el entry point de PyInstaller) y arranca la app.
"""

from convertir3x4.config import (  # noqa: F401
    APP_VERSION, APP_TITLE, THUMB_SIZE, IMAGE_FILTER, JPEG_QUALITY,
    BLUR_RADIUS, VIDEO_FPS, VIDEO_DURATION, VIDEO_OVERSIZE, VIDEO_MAX_WIDTH,
    ASPECT_RATIOS, DEFAULT_ASPECT_W, DEFAULT_ASPECT_H,
)
from convertir3x4.convert.images import (  # noqa: F401
    convert_centered, convert_double, convert_on_background,
    _dominant_colors, _make_textured_bg, _make_zoned_bg, _darken, _vary_color,
)
from convertir3x4.convert.video import (  # noqa: F401
    convert_centered_video, convert_double_video, _write_video_frames,
)
from convertir3x4.convert.project import read_project_file  # noqa: F401
from convertir3x4.utils import open_folder  # noqa: F401
from convertir3x4.ui.style import DARK_STYLE  # noqa: F401
from convertir3x4.ui.widgets.media_grid import (  # noqa: F401
    ReorderListWidget, PATH_ROLE, MTIME_ROLE,
)
# `App` se mantiene como alias de la pestaña de conversión por compatibilidad
# (los tests usan App._read_project_file).
from convertir3x4.ui.tabs.tab_convertir import ConvertTab as App  # noqa: F401
from convertir3x4.__main__ import main


if __name__ == "__main__":
    main()
