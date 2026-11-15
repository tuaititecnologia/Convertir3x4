"""Iconos de línea (Lucide, licencia ISC) en el color que se pida.

Los SVG de Lucide usan `stroke="currentColor"`, que Qt no resuelve: sin
reemplazarlo saldrían negros. Se reemplaza al cargar y se cachea.
"""

import os
from functools import lru_cache

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from .style import ASSETS_DIR, T

_ICONS_DIR = os.path.join(ASSETS_DIR, "icons")


@lru_cache(maxsize=None)
def _svg(name: str) -> str:
    with open(os.path.join(_ICONS_DIR, f"{name}.svg"), encoding="utf-8") as f:
        return f.read()


def pixmap(name: str, size: int = 18, color: str | None = None,
           stroke: float | None = None) -> QPixmap:
    color = color or T.text_2   # se resuelve al usarlo: el modo se elige al abrir
    svg = _svg(name).replace("currentColor", color)
    if stroke is not None:
        svg = svg.replace('stroke-width="2"', f'stroke-width="{stroke}"')
    ratio = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.setDevicePixelRatio(ratio)
    px.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    p = QPainter(px)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    return px


def icon(name: str, size: int = 18, color: str | None = None,
         active_color: str | None = None, stroke: float | None = None) -> QIcon:
    """QIcon con un color normal y, opcional, otro para el estado activo/seleccionado."""
    ic = QIcon()
    ic.addPixmap(pixmap(name, size, color, stroke), QIcon.Mode.Normal, QIcon.State.Off)
    if active_color:
        on = pixmap(name, size, active_color, stroke)
        ic.addPixmap(on, QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(on, QIcon.Mode.Active, QIcon.State.On)
    ic.addPixmap(pixmap(name, size, T.text_disabled, stroke), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic


def size(n: int) -> QSize:
    return QSize(n, n)
