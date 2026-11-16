"""Grillas de miniaturas (fotos y videos) con el mismo dibujo en toda la app.

- `ThumbDelegate`: celda uniforme, insignias (duración, estado) y foco.
- `DragOutListWidget` (Buscar): arrastra el archivo real a ProPresenter.
- `ReorderListWidget` (Convertir): reordenar arrastrando, a mano (el
  InternalMove de Qt no anda en IconMode).
- Las dos: vista previa de videos al pasar el mouse y mensaje si están vacías.
"""

import os

from PyQt6.QtCore import Qt, QPoint, QRect, QRectF, QSize, QTimer, QUrl, QMimeData, pyqtSignal
from PyQt6.QtGui import QPainter, QPainterPath, QPen, QColor, QDrag, QFontMetrics, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QListWidget, QListWidgetItem, QAbstractItemView,
    QStyledItemDelegate, QStyle,
)

from ..style import DARK, T

# Roles custom para guardar metadata en cada QListWidgetItem
PATH_ROLE = Qt.ItemDataRole.UserRole
MTIME_ROLE = Qt.ItemDataRole.UserRole + 1
MEDIA_ID_ROLE = Qt.ItemDataRole.UserRole + 2
KIND_ROLE = Qt.ItemDataRole.UserRole + 3        # "image" | "video"
DURATION_ROLE = Qt.ItemDataRole.UserRole + 4    # segundos (videos)
BADGE_ROLE = Qt.ItemDataRole.UserRole + 5       # texto corto arriba a la izquierda (ej. "IA")
STATE_ROLE = Qt.ItemDataRole.UserRole + 6       # ver STATES
PROGRESS_ROLE = Qt.ItemDataRole.UserRole + 7    # 0..1 mientras STATE_ROLE == "working"
STORYBOARD_ROLE = Qt.ItemDataRole.UserRole + 8  # ruta de la tira de cuadros (vista previa)
PREVIEW_ROLE = Qt.ItemDataRole.UserRole + 9     # QPixmap del cuadro de vista previa actual
SIZE_ROLE = Qt.ItemDataRole.UserRole + 10       # (ancho, alto) del original (vista previa de Convertir)

# Estados que se dibujan sobre la miniatura. Sin estado: normal.
#   loading  todavía no hay miniatura      working  convirtiendo/bajando (con %)
#   done     terminó bien                  warning  terminó, con un aviso (tooltip)
#   error    falló (tooltip con el motivo) missing  el archivo no está (se movió o borró)
#   offline  el disco/NAS entero no está: solo atenuada (el aviso lo explica una vez)
STATES = ("loading", "working", "done", "warning", "error", "missing", "offline")

STORYBOARD_FRAMES = 16   # = clips.STORYBOARD_FRAMES (sin importar clips: la grilla no usa ffmpeg)
STORYBOARD_COLS = 4
PREVIEW_INTERVAL_MS = 250


def format_duration(seconds: float | None) -> str:
    if not seconds:
        return ""
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class ThumbDelegate(QStyledItemDelegate):
    """Celda uniforme, como en Lightroom: cuadrado con la foto entera adentro.

    - Todas las celdas miden lo mismo (las filas no quedan serruchadas).
    - El nombre va en UNA línea, cortado con "…" si no entra.
    - Videos: insignia con la duración abajo a la derecha.
    - Seleccionada: borde de acento. Con el mouse encima: fondo más claro.
    """

    LABEL_H = 22
    PAD = 6

    def __init__(self, thumb: int, parent=None):
        super().__init__(parent)
        self.thumb = thumb

    def set_thumb(self, thumb: int):
        self.thumb = thumb

    def cell_size(self) -> QSize:
        return QSize(self.thumb + self.PAD * 2, self.thumb + self.PAD * 2 + self.LABEL_H)

    def sizeHint(self, option, index) -> QSize:
        # La celda toma el ancho de la columna (ver _fit_columns), sin el margen.
        view = self.parent()
        grid = view.gridSize() if view is not None else QSize()
        if grid.isValid() and grid.width() > 0:
            return QSize(grid.width() - 4, self.cell_size().height())
        return self.cell_size()

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)

        # Fondo de la celda
        bg = QColor(T.hover if hovered and not selected else T.panel)
        if selected:
            bg = QColor(T.accent_text)
            bg.setAlphaF(0.14)
        path = QPainterPath()
        path.addRoundedRect(r, T.radius, T.radius)
        painter.fillPath(path, bg)

        # Imagen: entera, centrada en el cuadrado, con esquinas redondeadas
        box = QRect(int(r.center().x() - self.thumb / 2), int(r.top()) + self.PAD - 2,
                    self.thumb, self.thumb)
        state = index.data(STATE_ROLE)
        icon: QIcon = index.data(Qt.ItemDataRole.DecorationRole)
        preview = index.data(PREVIEW_ROLE)
        dpr = painter.device().devicePixelRatioF()
        pm = None
        if isinstance(preview, QPixmap) and not preview.isNull():
            pm = preview
        elif isinstance(icon, QIcon) and not icon.isNull():
            pm = icon.pixmap(QSize(self.thumb, self.thumb) * dpr)
            pm.setDevicePixelRatio(dpr)
        if pm is not None:
            sz = pm.deviceIndependentSize().scaled(QSize(self.thumb, self.thumb).toSizeF(),
                                                   Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF(box.left() + (self.thumb - sz.width()) / 2,
                            box.top() + (self.thumb - sz.height()) / 2,
                            sz.width(), sz.height())
            clip = QPainterPath()
            clip.addRoundedRect(target, 4, 4)
            painter.setClipPath(clip)
            if state in ("error", "missing", "offline"):
                painter.setOpacity(0.35)
            painter.drawPixmap(target, pm, QRectF(pm.rect()))
            painter.setOpacity(1.0)
            painter.setClipping(False)
            img_rect = target
        else:
            ph = QPainterPath()
            ph.addRoundedRect(QRectF(box), 4, 4)
            painter.fillPath(ph, QColor(T.raised))
            img_rect = QRectF(box)

        self._paint_state(painter, state, index, img_rect)

        # Insignia de duración (videos)
        if index.data(KIND_ROLE) == "video" and state != "working":
            self._pill(painter, format_duration(index.data(DURATION_ROLE)) or "video",
                       img_rect, bottom_right=True)
        badge = index.data(BADGE_ROLE)
        if badge:
            # Sobre la miniatura el fondo es negro translúcido en los dos modos:
            # el violeta claro de la paleta oscura es el que se lee ahí.
            self._pill(painter, badge, img_rect, bottom_right=False, color=DARK["ia"])

        # Nombre en una línea
        fm = QFontMetrics(option.font)
        text_rect = QRect(int(r.left()) + self.PAD, box.bottom() + 4,
                          int(r.width()) - self.PAD * 2, self.LABEL_H - 4)
        name = fm.elidedText(index.data(Qt.ItemDataRole.DisplayRole) or "",
                             Qt.TextElideMode.ElideMiddle, text_rect.width())
        painter.setPen(QColor(T.text if selected else T.text_2))
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                         name)

        # Borde de selección por encima de todo; el foco de teclado, punteado
        if selected:
            painter.setPen(QPen(QColor(T.accent_text), 2))
            painter.drawPath(path)
        elif focused:
            painter.setPen(QPen(QColor(T.accent_text), 1.5, Qt.PenStyle.DashLine))
            painter.drawPath(path)
        painter.restore()

    def _paint_state(self, painter, state, index, img_rect: QRectF):
        """Lo que se dibuja encima de la miniatura según el estado del elemento."""
        has_image = index.data(Qt.ItemDataRole.DecorationRole) is not None and \
            not index.data(Qt.ItemDataRole.DecorationRole).isNull()
        if state == "missing" and has_image:
            # Sobre la miniatura atenuada, texto gris suelto no se lee: insignia.
            self._pill(painter, "No disponible", img_rect, bottom_right=False,
                       color=DARK["text"])
        elif state in ("loading", "missing"):
            painter.save()
            painter.setPen(QColor(T.text_3))
            painter.drawText(img_rect, int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                             "Cargando…" if state == "loading" else "No disponible")
            painter.restore()
        elif state == "working":
            frac = float(index.data(PROGRESS_ROLE) or 0.0)
            painter.save()
            scrim = QPainterPath()
            scrim.addRoundedRect(img_rect, 4, 4)
            painter.fillPath(scrim, QColor(0, 0, 0, 140))
            font = painter.font()
            font.setPixelSize(15)
            font.setWeight(600)
            painter.setFont(font)
            painter.setPen(QColor(T.on_image))
            painter.drawText(img_rect, int(Qt.AlignmentFlag.AlignCenter), f"{frac:.0%}")
            bar = QRectF(img_rect.left() + 8, img_rect.bottom() - 10, img_rect.width() - 16, 4)
            track = QPainterPath()
            track.addRoundedRect(bar, 2, 2)
            painter.fillPath(track, QColor(255, 255, 255, 60))
            fill = QPainterPath()
            fill.addRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * frac, bar.height()), 2, 2)
            painter.fillPath(fill, QColor(DARK["accent_text"]))
            painter.restore()
        elif state == "done":
            self._pill(painter, "✓ Listo", img_rect, bottom_right=False, color=T.on_image,
                       fill=QColor(T.accent))
        elif state == "warning":
            self._pill(painter, "! Revisar", img_rect, bottom_right=False, color=DARK["warn"])
        elif state == "error":
            self._pill(painter, "✕ Error", img_rect, bottom_right=False, color=DARK["danger"])

    def _pill(self, painter, text, img_rect: QRectF, bottom_right: bool, color: str | None = None,
              fill: QColor | None = None):
        color = color or T.on_image
        painter.save()   # la insignia cambia la letra; el nombre de abajo no
        font = painter.font()
        font.setPixelSize(11)
        font.setWeight(600)
        painter.setFont(font)
        fm = QFontMetrics(font)
        w, h = fm.horizontalAdvance(text) + 12, 18
        if bottom_right:
            x, y = img_rect.right() - w - 5, img_rect.bottom() - h - 5
        else:
            x, y = img_rect.left() + 5, img_rect.top() + 5
        pill = QPainterPath()
        pill.addRoundedRect(QRectF(x, y, w, h), 9, 9)
        painter.fillPath(pill, fill or QColor(0, 0, 0, 170))
        painter.setPen(QColor(color))
        painter.drawText(QRectF(x, y, w, h), int(Qt.AlignmentFlag.AlignCenter), text)
        painter.restore()


def _fit_columns(view: QListWidget, delegate: "ThumbDelegate"):
    """Reparte el ancho sobrante entre las columnas, como Lightroom.

    Con celdas de ancho fijo, la columna que no entra deja un hueco a la derecha.
    Se calcula cuántas columnas entran y se ensancha cada celda para llenar la fila.
    """
    base = delegate.cell_size() + QSize(4, 4)
    # Qt pasa la última columna a la fila siguiente si la fila llena justo el
    # ancho, y además reserva el lugar de la barra de desplazamiento aunque no
    # se vea (medido: con 956 px entran 6 columnas de 155 pero no de 157).
    vbar = view.verticalScrollBar()
    reserve = 12 + (0 if vbar.isVisible() else vbar.sizeHint().width())
    avail = view.viewport().width() - reserve
    cols = max(1, avail // base.width())
    width = max(base.width(), avail // cols)
    if view.gridSize().width() != width or view.gridSize().height() != base.height():
        view.setGridSize(QSize(width, base.height()))


def _paint_empty(view: QListWidget, text: str):
    """Mensaje centrado en una grilla vacía (en vez de un hueco mudo)."""
    p = QPainter(view.viewport())
    p.setPen(QColor(T.text_3))
    p.drawText(view.viewport().rect().adjusted(24, 24, -24, -24),
               int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), text)
    p.end()


class _HoverPreview:
    """Vista previa de videos al pasar el mouse, como en YouTube.

    La tira de cuadros (STORYBOARD_ROLE) es un JPEG con 16 cuadros en 4x4 que
    arma clips.make_storyboard. Mientras el mouse está encima, la celda muestra
    un cuadro distinto cada PREVIEW_INTERVAL_MS. No reproduce el video.
    """

    def _init_hover(self):
        self._hover_row = -1
        self._hover_frame = 0
        self._sprites: dict[str, QPixmap] = {}
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(PREVIEW_INTERVAL_MS)
        self._hover_timer.timeout.connect(self._next_preview_frame)

    def _hover_at(self, pos):
        item = self.itemAt(pos)
        row = self.row(item) if item is not None else -1
        if row == self._hover_row:
            return
        self._stop_preview()
        if item is not None and item.data(STORYBOARD_ROLE) and item.data(STATE_ROLE) != "working":
            self._hover_row = row
            self._hover_frame = 0
            self._next_preview_frame()
            self._hover_timer.start()

    def _stop_preview(self):
        self._hover_timer.stop()
        if 0 <= self._hover_row < self.count():
            self.item(self._hover_row).setData(PREVIEW_ROLE, None)
        self._hover_row = -1

    def _next_preview_frame(self):
        if not (0 <= self._hover_row < self.count()):
            self._stop_preview()
            return
        item = self.item(self._hover_row)
        path = item.data(STORYBOARD_ROLE)
        sprite = self._sprites.get(path)
        if sprite is None:
            sprite = QPixmap(path)
            if sprite.isNull():
                self._stop_preview()
                return
            self._sprites[path] = sprite
        rows = -(-STORYBOARD_FRAMES // STORYBOARD_COLS)
        fw, fh = sprite.width() // STORYBOARD_COLS, sprite.height() // rows
        i = self._hover_frame % STORYBOARD_FRAMES
        frame = sprite.copy((i % STORYBOARD_COLS) * fw, (i // STORYBOARD_COLS) * fh, fw, fh)
        item.setData(PREVIEW_ROLE, frame)
        self._hover_frame += 1

    def leaveEvent(self, event):
        self._stop_preview()
        super().leaveEvent(event)


class DragOutListWidget(_HoverPreview, QListWidget):
    """Grilla de miniaturas que arrastra el ARCHIVO REAL hacia afuera.

    Mecánica validada en el spike de la Fase 0: ProPresenter (con "Manage Media
    Automatically" en OFF) recibe el drop como LINK a la ruta original, no como
    copia. Se ofrece Copy|Link y el destino decide; PP linkea.
    """

    def __init__(self, parent=None, thumb: int = 150):
        super().__init__(parent)
        self.setObjectName("media_grid")
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setMouseTracking(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._delegate = ThumbDelegate(thumb, self)
        self.setItemDelegate(self._delegate)
        self._empty_text = ""
        self._init_hover()
        self.set_thumb_size(thumb)

    def set_empty_text(self, text: str):
        """Mensaje centrado cuando la grilla está vacía (en vez de un hueco mudo)."""
        self._empty_text = text
        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.count() and self._empty_text:
            _paint_empty(self, self._empty_text)

    def set_thumb_size(self, thumb: int):
        self._delegate.set_thumb(thumb)
        self.setIconSize(QSize(thumb, thumb))
        _fit_columns(self, self._delegate)
        self.doItemsLayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        _fit_columns(self, self._delegate)

    def mouseMoveEvent(self, event):
        self._hover_at(event.pos())
        super().mouseMoveEvent(event)

    def startDrag(self, supported_actions):
        self._stop_preview()
        items = self.selectedItems()
        urls = []
        for it in items:
            path = it.data(PATH_ROLE)
            if path and os.path.exists(path):
                urls.append(QUrl.fromLocalFile(path))
        if not urls:
            return

        mime = QMimeData()
        mime.setUrls(urls)

        drag = QDrag(self)
        drag.setMimeData(mime)
        icon = items[0].icon()
        if not icon.isNull():
            drag.setPixmap(icon.pixmap(128, 128))
        drag.exec(
            Qt.DropAction.CopyAction | Qt.DropAction.LinkAction,
            Qt.DropAction.LinkAction,
        )


class ReorderListWidget(_HoverPreview, QListWidget):
    """QListWidget con drag & drop manual para reordenar."""
    orderChanged = pyqtSignal()

    def __init__(self, parent=None, thumb: int = 130):
        super().__init__(parent)
        self.setObjectName("media_grid")
        self.setMouseTracking(True)
        self._delegate = ThumbDelegate(thumb, self)
        self.setItemDelegate(self._delegate)
        self._empty_text = ""
        self._init_hover()
        # Estático: el reordenado es manual (abajo); el modo libre de Qt además
        # acomoda las filas con otra regla y no reparte bien el ancho.
        self.setMovement(QListWidget.Movement.Static)
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self._drag_start_pos = None
        self._drag_row: int | None = None
        self._dragging = False
        self._drop_indicator_row: int | None = None

    def set_thumb_size(self, thumb: int):
        self._delegate.set_thumb(thumb)
        self.setIconSize(QSize(thumb, thumb))
        _fit_columns(self, self._delegate)
        self.doItemsLayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        _fit_columns(self, self._delegate)

    def _drop_row_from_pos(self, pos) -> int | None:
        """Calcula en qué posición (entre items) caería el drop."""
        item = self.itemAt(pos)
        if not item:
            return None
        rect = self.visualItemRect(item)
        row = self.row(item)
        # Si el cursor está en la mitad derecha del item, insertar después
        if pos.x() > rect.center().x():
            return row + 1
        return row

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
            item = self.itemAt(event.pos())
            self._drag_row = self.row(item) if item else None
            self._dragging = False

    def mouseMoveEvent(self, event):
        if not self._dragging:
            self._hover_at(event.pos())
        if (self._drag_row is not None
                and self._drag_start_pos is not None
                and not self._dragging):
            dist = (event.pos() - self._drag_start_pos).manhattanLength()
            if dist > QApplication.startDragDistance():
                self._dragging = True
                self._stop_preview()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if self._dragging:
            self._drop_indicator_row = self._drop_row_from_pos(event.pos())
            self.viewport().update()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging and self._drag_row is not None:
            drop_row = self._drop_row_from_pos(event.pos())

            if drop_row is not None and drop_row != self._drag_row and drop_row != self._drag_row + 1:
                # takeItem devuelve el mismo item con todos sus datos (tipo,
                # duración, vista previa, estado): se reinserta tal cual.
                new_item = self.takeItem(self._drag_row)
                new_item.setData(PREVIEW_ROLE, None)
                insert_row = drop_row if drop_row < self._drag_row else drop_row - 1
                self.insertItem(insert_row, new_item)
                self.clearSelection()
                new_item.setSelected(True)
                self.orderChanged.emit()

            self.unsetCursor()

        self._drag_row = None
        self._drag_start_pos = None
        self._dragging = False
        self._drop_indicator_row = None
        self.viewport().update()
        super().mouseReleaseEvent(event)

    def set_empty_text(self, text: str):
        self._empty_text = text
        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.count() and self._empty_text:
            _paint_empty(self, self._empty_text)
            return
        if not self._dragging or self._drop_indicator_row is None:
            return

        row = self._drop_indicator_row
        # Calcular posición de la línea indicadora
        if row < self.count():
            rect = self.visualItemRect(self.item(row))
            x = rect.left() - self.spacing()
        else:
            # Después del último item
            rect = self.visualItemRect(self.item(self.count() - 1))
            x = rect.right() + self.spacing()

        painter = QPainter(self.viewport())
        pen = QPen(QColor(T.accent_text), 2)
        painter.setPen(pen)
        y_top = rect.top()
        y_bottom = rect.bottom()
        painter.drawLine(x, y_top, x, y_bottom)
        painter.setBrush(QColor(T.accent_text))
        painter.drawEllipse(QPoint(x, y_top), 3, 3)
        painter.drawEllipse(QPoint(x, y_bottom), 3, 3)
        painter.end()
