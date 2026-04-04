"""Convertir a 3:4 — CDLM: Convierte fotos a formato 3:4 portrait."""

import json
import os
import platform
import random
import subprocess
import sys
import threading

from PyQt6.QtCore import Qt, QSize, QRect, QPoint, QSettings, QStandardPaths, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor
from PIL import Image, ImageDraw, ImageFilter
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QListWidget, QListWidgetItem, QFileDialog,
    QProgressBar, QAbstractItemView, QMessageBox, QSlider, QFrame,
    QSizePolicy,
)

# --- Constantes ---
APP_TITLE = "Convertir a 3:4 — CDLM"
THUMB_SIZE = 130
IMAGE_FILTER = "Imágenes (*.jpg *.jpeg *.png *.bmp *.webp);;Todos (*.*)"
JPEG_QUALITY = 92
BLUR_RADIUS = 40
ASPECT_W, ASPECT_H = 3, 4


def _dominant_colors(img: Image.Image, n: int = 5) -> list[tuple[int, int, int]]:
    """Extrae los N colores más frecuentes de una imagen (reducida)."""
    small = img.copy()
    small.thumbnail((80, 80), Image.LANCZOS)
    # Cuantizar a pocos colores
    quantized = small.quantize(colors=n, method=Image.Quantize.FASTOCTREE)
    palette = quantized.getpalette()
    # Contar frecuencia de cada índice
    counts = sorted(quantized.getcolors(), reverse=True)
    colors = []
    for _, idx in counts[:n]:
        r, g, b = palette[idx * 3], palette[idx * 3 + 1], palette[idx * 3 + 2]
        colors.append((r, g, b))
    return colors


def _vary_color(color: tuple[int, int, int], amount: int = 25) -> tuple[int, int, int]:
    """Varía ligeramente un color RGB."""
    return tuple(max(0, min(255, c + random.randint(-amount, amount))) for c in color)


def _make_textured_bg(width: int, height: int, colors: list[tuple[int, int, int]]) -> Image.Image:
    """Genera un fondo con textura suave usando los colores dominantes de la foto."""
    base_color = colors[0]
    bg = Image.new("RGB", (width, height), base_color)
    draw = ImageDraw.Draw(bg)

    # Dibujar manchas suaves con los colores de la paleta
    n_blobs = random.randint(8, 15)
    for _ in range(n_blobs):
        color = _vary_color(random.choice(colors), 30)
        # Elipses grandes en posiciones aleatorias
        cx = random.randint(-width // 4, width + width // 4)
        cy = random.randint(-height // 4, height + height // 4)
        rx = random.randint(width // 4, width)
        ry = random.randint(height // 4, height)
        draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=color)

    # Blur fuerte para suavizar las manchas
    bg = bg.filter(ImageFilter.GaussianBlur(radius=min(width, height) // 4))
    return bg


def _make_zoned_bg(
    width: int, height: int,
    colors_top: list[tuple[int, int, int]],
    colors_bottom: list[tuple[int, int, int]],
) -> Image.Image:
    """Genera un fondo donde los colores de arriba dominan arriba y los de abajo dominan abajo."""
    bg = Image.new("RGB", (width, height), colors_top[0])
    draw = ImageDraw.Draw(bg)

    # Manchas con colores de arriba en la mitad superior
    for _ in range(random.randint(5, 8)):
        color = _vary_color(random.choice(colors_top), 30)
        cx = random.randint(-width // 4, width + width // 4)
        cy = random.randint(-height // 4, height // 2)
        rx = random.randint(width // 4, width)
        ry = random.randint(height // 6, height // 3)
        draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=color)

    # Manchas con colores de abajo en la mitad inferior
    for _ in range(random.randint(5, 8)):
        color = _vary_color(random.choice(colors_bottom), 30)
        cx = random.randint(-width // 4, width + width // 4)
        cy = random.randint(height // 2, height + height // 4)
        rx = random.randint(width // 4, width)
        ry = random.randint(height // 6, height // 3)
        draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=color)

    # Blur fuerte para fusionar todo suavemente
    bg = bg.filter(ImageFilter.GaussianBlur(radius=min(width, height) // 4))
    return bg


def convert_centered(src_path: str, out_path: str):
    """Convierte una imagen a 3:4 con fondo de color dominante texturizado."""
    img = Image.open(src_path).convert("RGB")
    src_w, src_h = img.size

    # Calcular tamaño del canvas 3:4 basado en la imagen
    if src_w / src_h > ASPECT_W / ASPECT_H:
        canvas_w = src_w
        canvas_h = int(src_w * ASPECT_H / ASPECT_W)
    else:
        canvas_h = src_h
        canvas_w = int(src_h * ASPECT_W / ASPECT_H)

    # Fondo: textura con colores dominantes de la foto
    colors = _dominant_colors(img)
    bg = _make_textured_bg(canvas_w, canvas_h, colors)

    # Centrar la imagen original
    x = (canvas_w - src_w) // 2
    y = (canvas_h - src_h) // 2
    bg.paste(img, (x, y))

    bg.save(out_path, "JPEG", quality=JPEG_QUALITY)


def convert_double(paths: list[str], out_path: str):
    """Apila 2 fotos (o 1) verticalmente en un canvas 3:4 con fondo texturizado."""
    images = [Image.open(p).convert("RGB") for p in paths]

    if len(images) == 1:
        # Foto sola: va en la mitad superior
        img = images[0]
        src_w, src_h = img.size
        if src_w / src_h > ASPECT_W / ASPECT_H:
            canvas_w = src_w
            canvas_h = int(src_w * ASPECT_H / ASPECT_W)
        else:
            canvas_h = src_h
            canvas_w = int(src_h * ASPECT_W / ASPECT_H)

        colors = _dominant_colors(img)
        bg = _make_textured_bg(canvas_w, canvas_h, colors)
        x = (canvas_w - src_w) // 2
        bg.paste(img, (x, 0))
        bg.save(out_path, "JPEG", quality=JPEG_QUALITY)
        return

    # 2 fotos: apilar verticalmente
    img1, img2 = images[0], images[1]

    # Determinar cuál es más ancha (por aspect ratio)
    ar1 = img1.size[0] / img1.size[1]
    ar2 = img2.size[0] / img2.size[1]

    # La más ancha va primero (arriba), la más alta abajo
    if ar1 >= ar2:
        img_wide, img_tall = img1, img2
    else:
        img_wide, img_tall = img2, img1

    # Canvas basado en la imagen más grande
    canvas_w = max(img_wide.size[0], img_tall.size[0])
    canvas_h = int(canvas_w * ASPECT_H / ASPECT_W)
    margin = int(canvas_h * 0.02)  # 2% de margen mínimo

    def fit_image(img, max_w, max_h):
        w, h = img.size
        scale = min(max_w / w, max_h / h)
        return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    # La más ancha ocupa el ancho disponible, limitada al 45% de alto
    wide_max_w = canvas_w - margin * 2
    wide_max_h = int(canvas_h * 0.45)
    img_wide_fit = fit_image(img_wide, wide_max_w, wide_max_h)

    # La más alta usa el espacio restante
    tall_max_w = canvas_w - margin * 2
    tall_max_h = canvas_h - img_wide_fit.size[1] - margin * 3
    img_tall_fit = fit_image(img_tall, tall_max_w, tall_max_h)

    # Márgenes dinámicos con el espacio sobrante
    used_h = img_wide_fit.size[1] + img_tall_fit.size[1]
    remaining = canvas_h - used_h
    margin = remaining // 3

    # Fondo zonificado: colores de arriba para arriba, colores de abajo para abajo
    colors_top = _dominant_colors(img_wide)
    colors_bottom = _dominant_colors(img_tall)
    bg = _make_zoned_bg(canvas_w, canvas_h, colors_top, colors_bottom)

    # Foto ancha centrada arriba
    x1 = (canvas_w - img_wide_fit.size[0]) // 2
    y1 = margin
    bg.paste(img_wide_fit, (x1, y1))

    # Foto alta centrada abajo
    x2 = (canvas_w - img_tall_fit.size[0]) // 2
    y2 = margin + img_wide_fit.size[1] + margin
    bg.paste(img_tall_fit, (x2, y2))

    bg.save(out_path, "JPEG", quality=JPEG_QUALITY)


def convert_on_background(src_path: str, bg_path: str, out_path: str):
    """Monta una foto en la mitad superior de un canvas 3:4 con fondo personalizado."""
    img = Image.open(src_path).convert("RGB")
    bg_img = Image.open(bg_path).convert("RGB")

    # Canvas 3:4 basado en la imagen más grande
    canvas_w = max(img.size[0], bg_img.size[0])
    canvas_h = int(canvas_w * ASPECT_H / ASPECT_W)

    # Escalar fondo para cubrir el canvas
    bg_scale = max(canvas_w / bg_img.size[0], canvas_h / bg_img.size[1])
    bg_resized = bg_img.resize(
        (int(bg_img.size[0] * bg_scale), int(bg_img.size[1] * bg_scale)), Image.LANCZOS)
    # Crop centrado al canvas
    bx = (bg_resized.size[0] - canvas_w) // 2
    by = (bg_resized.size[1] - canvas_h) // 2
    bg = bg_resized.crop((bx, by, bx + canvas_w, by + canvas_h))

    # Foto en la mitad superior con márgenes ~5%
    margin = int(canvas_w * 0.05)
    max_w = canvas_w - margin * 2
    max_h = int(canvas_h * 0.50) - margin

    w, h = img.size
    scale = min(max_w / w, max_h / h)
    img_fit = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    x = (canvas_w - img_fit.size[0]) // 2
    y = margin
    bg.paste(img_fit, (x, y))

    bg.save(out_path, "JPEG", quality=JPEG_QUALITY)


def open_folder(path: str):
    """Abre una carpeta en Finder/Explorer."""
    if platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    elif platform.system() == "Windows":
        subprocess.Popen(["explorer", path])
    else:
        subprocess.Popen(["xdg-open", path])

DARK_STYLE = """
QMainWindow, QWidget { background-color: #1a1d23; color: #e0e0e0; }
QPushButton {
    background-color: #2a3040; color: #e0e0e0; border: 1px solid #3a4558;
    border-radius: 6px; padding: 6px 14px; font-size: 13px;
}
QPushButton:hover { background-color: #354060; border-color: #4a5a78; }
QPushButton:pressed { background-color: #0078d4; }
QPushButton:disabled { color: #555555; background-color: #1e2230; border-color: #252a35; }
QPushButton[active="true"] { border-color: #0078d4; color: #5aafff; }
QPushButton#btn_icon {
    background-color: transparent; border: 1px solid #2a3040; border-radius: 4px;
    font-size: 16px; padding: 0px;
}
QPushButton#btn_icon:hover { background-color: #354060; border-color: #4a5a78; }
QPushButton#btn_icon:disabled { opacity: 0.3; background-color: transparent; border-color: #252a35; }
QPushButton#btn_load { background-color: #1a3550; border-color: #2a5070; color: #70b8ff; }
QPushButton#btn_load:hover { background-color: #204060; border-color: #3070a0; }
QPushButton#btn_action { background-color: #1a3a2a; border-color: #2a5a3a; color: #70d090; }
QPushButton#btn_action:hover { background-color: #204a34; border-color: #30704a; }
QPushButton#btn_action:disabled { color: #445544; background-color: #1e2520; border-color: #252a35; }
QPushButton#btn_load_bg {
    background-color: transparent; border: none; color: #6680a0;
    font-size: 12px; padding: 4px 8px;
}
QPushButton#btn_load_bg:hover { color: #88aacc; }
QFrame#sidebar {
    background-color: #1e2230; border: 1px solid #2a3040; border-radius: 6px;
}
QFrame#separator { background-color: #2a3040; }
QLabel#section_title { color: #7090b0; font-size: 12px; font-weight: bold; }
QLabel#bg_preview {
    background-color: #252a38; border: 1px solid #3a4558; border-radius: 4px;
}
QListWidget {
    background-color: #1e2230; border: 1px solid #2a3040; border-radius: 6px;
    outline: none;
}
QListWidget::item {
    background-color: #252a38; border: 2px solid transparent;
    border-radius: 4px; padding: 4px; margin: 4px;
}
QListWidget::item:selected { border-color: #0078d4; background-color: #1a3050; }
QListWidget::item:hover:!selected { background-color: #2a3248; }
QProgressBar {
    background-color: #252a38; border: none; border-radius: 3px; height: 6px;
}
QProgressBar::chunk { background-color: #0078d4; border-radius: 3px; }
QLabel { color: #8899aa; font-size: 13px; }
QSlider::groove:horizontal {
    background: #2a3040; height: 4px; border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #0078d4; width: 14px; height: 14px; margin: -5px 0;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover { background: #3098e8; }
QPushButton#btn_sort {
    background-color: transparent; border: none; color: #6680a0;
    font-size: 12px; padding: 4px 8px;
}
QPushButton#btn_sort:hover { color: #88aacc; }
QPushButton#btn_sort[active="true"] { color: #5aafff; }
"""

# Rol custom para guardar el path completo y la fecha de modificación
PATH_ROLE = Qt.ItemDataRole.UserRole
MTIME_ROLE = Qt.ItemDataRole.UserRole + 1


class ReorderListWidget(QListWidget):
    """QListWidget con drag & drop manual para reordenar."""
    orderChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self._drag_start_pos = None
        self._drag_row: int | None = None
        self._dragging = False
        self._drop_indicator_row: int | None = None

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
        if (self._drag_row is not None
                and self._drag_start_pos is not None
                and not self._dragging):
            dist = (event.pos() - self._drag_start_pos).manhattanLength()
            if dist > QApplication.startDragDistance():
                self._dragging = True
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
                src_item = self.item(self._drag_row)
                path = src_item.data(PATH_ROLE)
                mtime = src_item.data(MTIME_ROLE)
                icon = src_item.icon()
                text = src_item.text()

                self.takeItem(self._drag_row)
                # Ajustar índice después del takeItem
                insert_row = drop_row if drop_row < self._drag_row else drop_row - 1
                new_item = QListWidgetItem()
                new_item.setIcon(icon)
                new_item.setText(text)
                new_item.setData(PATH_ROLE, path)
                new_item.setData(MTIME_ROLE, mtime)
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

    def paintEvent(self, event):
        super().paintEvent(event)
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
        pen = QPen(QColor("#0078d4"), 2)
        painter.setPen(pen)
        y_top = rect.top()
        y_bottom = rect.bottom()
        painter.drawLine(x, y_top, x, y_bottom)
        painter.setBrush(QColor("#0078d4"))
        painter.drawEllipse(QPoint(x, y_top), 3, 3)
        painter.drawEllipse(QPoint(x, y_bottom), 3, 3)
        painter.end()


class App(QMainWindow):
    _progress_signal = pyqtSignal(int)
    _done_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1100, 660)
        self.setMinimumSize(850, 450)

        self.sort_field: str | None = None
        self.sort_asc: bool = True
        self._pixmap_cache: dict[str, QPixmap] = {}  # path -> pixmap original
        self._bg_path: str | None = None
        self._project_path: str | None = None
        self._project_dirty: bool = False
        self._clean_snapshot: tuple = ((), None)

        self.settings = QSettings("CDLM", "Convertir3x4")

        self._progress_signal.connect(self._on_progress)
        self._done_signal.connect(self._on_processing_done)

        self._build_ui()
        self._connect_signals()
        self._restore_state()
        self._clean_snapshot = self._current_snapshot()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # ==================== ZONA IZQUIERDA (grilla) ====================
        left_layout = QVBoxLayout()
        left_layout.setSpacing(6)

        # --- Toolbar superior ---
        toolbar_layout = QHBoxLayout()

        self.btn_new_project = QPushButton("\U0001F4C4")
        self.btn_new_project.setObjectName("btn_icon")
        self.btn_new_project.setToolTip("Nuevo proyecto")
        self.btn_new_project.setFixedSize(32, 32)
        toolbar_layout.addWidget(self.btn_new_project)

        self.btn_load_project = QPushButton("\U0001F4C2")
        self.btn_load_project.setObjectName("btn_icon")
        self.btn_load_project.setToolTip("Abrir proyecto")
        self.btn_load_project.setFixedSize(32, 32)
        toolbar_layout.addWidget(self.btn_load_project)

        self.btn_save_project = QPushButton("\U0001F4BE")
        self.btn_save_project.setObjectName("btn_icon")
        self.btn_save_project.setToolTip("Guardar proyecto")
        self.btn_save_project.setFixedSize(32, 32)
        self.btn_save_project.setEnabled(False)
        toolbar_layout.addWidget(self.btn_save_project)

        self.btn_close_project = QPushButton("\u2716")
        self.btn_close_project.setObjectName("btn_icon")
        self.btn_close_project.setToolTip("Cerrar proyecto")
        self.btn_close_project.setFixedSize(32, 32)
        self.btn_close_project.setEnabled(False)
        toolbar_layout.addWidget(self.btn_close_project)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        toolbar_layout.addWidget(separator)

        self.btn_load = QPushButton("Cargar imágenes")
        self.btn_load.setObjectName("btn_load")
        toolbar_layout.addWidget(self.btn_load)

        self.lbl_count = QLabel("0 fotos cargadas")
        toolbar_layout.addWidget(self.lbl_count)

        toolbar_layout.addStretch()
        left_layout.addLayout(toolbar_layout)

        # --- Grilla de imágenes ---
        self.list_widget = ReorderListWidget()
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.setSpacing(6)
        self.list_widget.setWordWrap(True)
        left_layout.addWidget(self.list_widget)

        # --- Barra inferior: ordenamiento + slider ---
        bottom_layout = QHBoxLayout()

        sort_label = QLabel("Ordenar por:")
        bottom_layout.addWidget(sort_label)

        self.btn_sort_nombre = QPushButton("Nombre")
        self.btn_sort_nombre.setObjectName("btn_sort")
        self.btn_sort_nombre.setFixedWidth(90)
        bottom_layout.addWidget(self.btn_sort_nombre)

        self.btn_sort_fecha = QPushButton("Fecha")
        self.btn_sort_fecha.setObjectName("btn_sort")
        self.btn_sort_fecha.setFixedWidth(90)
        bottom_layout.addWidget(self.btn_sort_fecha)

        bottom_layout.addStretch()

        zoom_label = QLabel("🔍")
        zoom_label.setFixedWidth(20)
        bottom_layout.addWidget(zoom_label)
        self.thumb_slider = QSlider(Qt.Orientation.Horizontal)
        self.thumb_slider.setRange(60, 300)
        self.thumb_slider.setValue(THUMB_SIZE)
        self.thumb_slider.setFixedWidth(150)
        bottom_layout.addWidget(self.thumb_slider)
        left_layout.addLayout(bottom_layout)

        # --- Barra de progreso (oculta) ---
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        left_layout.addWidget(self.progress)

        main_layout.addLayout(left_layout, stretch=1)

        # ==================== SIDEBAR DERECHA (acciones) ====================
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(200)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(14, 16, 14, 16)
        sidebar_layout.setSpacing(0)

        # --- Sección: Foto centrada ---
        lbl1 = QLabel("FOTO CENTRADA")
        lbl1.setObjectName("section_title")
        sidebar_layout.addWidget(lbl1)
        sidebar_layout.addSpacing(8)

        self.btn_centrada = QPushButton("Convertir")
        self.btn_centrada.setObjectName("btn_action")
        self.btn_centrada.setEnabled(False)
        sidebar_layout.addWidget(self.btn_centrada)

        # Separador
        sidebar_layout.addSpacing(14)
        sep1 = QFrame()
        sep1.setObjectName("separator")
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setFixedHeight(1)
        sidebar_layout.addWidget(sep1)
        sidebar_layout.addSpacing(14)

        # --- Sección: Doble foto ---
        lbl2 = QLabel("DOBLE FOTO")
        lbl2.setObjectName("section_title")
        sidebar_layout.addWidget(lbl2)
        sidebar_layout.addSpacing(8)

        self.btn_doble = QPushButton("Convertir")
        self.btn_doble.setObjectName("btn_action")
        self.btn_doble.setEnabled(False)
        sidebar_layout.addWidget(self.btn_doble)

        # Separador
        sidebar_layout.addSpacing(14)
        sep2 = QFrame()
        sep2.setObjectName("separator")
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFixedHeight(1)
        sidebar_layout.addWidget(sep2)
        sidebar_layout.addSpacing(14)

        # --- Sección: Foto y fondo ---
        lbl3 = QLabel("FOTO Y FONDO")
        lbl3.setObjectName("section_title")
        sidebar_layout.addWidget(lbl3)
        sidebar_layout.addSpacing(8)

        self.lbl_bg_preview = QLabel("Sin fondo")
        self.lbl_bg_preview.setObjectName("bg_preview")
        self.lbl_bg_preview.setFixedHeight(110)
        self.lbl_bg_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_bg_preview.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_bg_preview.mousePressEvent = lambda e: self._load_background()
        sidebar_layout.addWidget(self.lbl_bg_preview)
        sidebar_layout.addSpacing(8)

        self.btn_load_bg = QPushButton("Cargar fondo")
        self.btn_load_bg.setObjectName("btn_load_bg")
        sidebar_layout.addWidget(self.btn_load_bg)
        sidebar_layout.addSpacing(12)

        self.btn_foto_fondo = QPushButton("Convertir")
        self.btn_foto_fondo.setObjectName("btn_action")
        self.btn_foto_fondo.setEnabled(False)
        sidebar_layout.addWidget(self.btn_foto_fondo)

        sidebar_layout.addStretch()
        main_layout.addWidget(sidebar)

    def _connect_signals(self):
        self.btn_load.clicked.connect(self._load_images)
        self.btn_new_project.clicked.connect(self._new_project)
        self.btn_load_project.clicked.connect(self._load_project)
        self.btn_save_project.clicked.connect(self._save_project)
        self.btn_close_project.clicked.connect(self._close_project)
        self.btn_centrada.clicked.connect(self._action_foto_centrada)
        self.btn_doble.clicked.connect(self._action_doble_foto)
        self.btn_load_bg.clicked.connect(self._load_background)
        self.btn_foto_fondo.clicked.connect(self._action_foto_fondo)
        self.btn_sort_nombre.clicked.connect(lambda: self._toggle_sort("nombre"))
        self.btn_sort_fecha.clicked.connect(lambda: self._toggle_sort("fecha"))
        self.list_widget.itemSelectionChanged.connect(self._update_buttons)
        self.list_widget.orderChanged.connect(self._mark_dirty)
        self.thumb_slider.valueChanged.connect(self._on_thumb_resize)

    # -------------------------------------------------------- Pixmap cache
    def _cached_pixmap(self, path: str) -> QPixmap | None:
        if path not in self._pixmap_cache:
            px = QPixmap(path)
            if px.isNull():
                return None
            # Guardar a un tamaño razonable para no usar demasiada RAM
            if px.width() > 600 or px.height() > 600:
                px = px.scaled(600, 600,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
            self._pixmap_cache[path] = px
        return self._pixmap_cache[path]

    def _make_icon(self, path: str, size: int) -> QIcon | None:
        px = self._cached_pixmap(path)
        if px is None:
            return None
        return QIcon(px.scaled(size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    # -------------------------------------------------------- Proyecto: nuevo / cerrar
    def _reset_project(self):
        """Limpia todo el estado del proyecto."""
        self.list_widget.clear()
        self._pixmap_cache.clear()
        self._bg_path = None
        self.lbl_bg_preview.clear()
        self.lbl_bg_preview.setText("Sin fondo")
        self._project_path = None
        self._mark_clean()
        self._update_count()
        self._update_buttons()

    def _new_project(self):
        if self._project_dirty:
            resp = self._ask_save_before_action()
            if resp == QMessageBox.StandardButton.Cancel:
                return
            if resp == QMessageBox.StandardButton.Yes:
                self._save_project()
        self._reset_project()

    def _close_project(self):
        if self._project_dirty:
            resp = self._ask_save_before_action()
            if resp == QMessageBox.StandardButton.Cancel:
                return
            if resp == QMessageBox.StandardButton.Yes:
                self._save_project()
        self._reset_project()

    # -------------------------------------------------------- Carga
    def _load_images(self):
        last_dir = self.settings.value("last_dir", "")
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Seleccionar imágenes", last_dir, IMAGE_FILTER,
        )
        if not paths:
            return
        self.settings.setValue("last_dir", os.path.dirname(paths[0]))

        existing = set()
        for i in range(self.list_widget.count()):
            existing.add(self.list_widget.item(i).data(PATH_ROLE))

        size = self.thumb_slider.value()
        for path in paths:
            if path in existing:
                continue
            icon = self._make_icon(path, size)
            if icon is None:
                continue
            item = QListWidgetItem()
            item.setIcon(icon)
            item.setText(os.path.basename(path))
            item.setData(PATH_ROLE, path)
            item.setData(MTIME_ROLE, os.path.getmtime(path))
            self.list_widget.addItem(item)

        self._update_count()
        self._mark_dirty()

    # -------------------------------------------------------- Ordenamiento
    def _toggle_sort(self, field: str):
        if self.list_widget.count() == 0:
            return

        if self.sort_field == field:
            self.sort_asc = not self.sort_asc
        else:
            self.sort_field = field
            self.sort_asc = True

        # Extraer items, ordenar, recargar
        items_data = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            items_data.append({
                "path": item.data(PATH_ROLE),
                "mtime": item.data(MTIME_ROLE),
                "icon": item.icon(),
                "text": item.text(),
            })

        if field == "nombre":
            items_data.sort(key=lambda d: d["text"].lower(), reverse=not self.sort_asc)
        elif field == "fecha":
            items_data.sort(key=lambda d: d["mtime"], reverse=not self.sort_asc)

        self.list_widget.clear()
        for d in items_data:
            item = QListWidgetItem()
            item.setIcon(d["icon"])
            item.setText(d["text"])
            item.setData(PATH_ROLE, d["path"])
            item.setData(MTIME_ROLE, d["mtime"])
            self.list_widget.addItem(item)

        # Actualizar texto de botones
        arrow = " ↑" if self.sort_asc else " ↓"
        self.btn_sort_nombre.setText("Nombre" + (arrow if field == "nombre" else ""))
        self.btn_sort_nombre.setProperty("active", field == "nombre")
        self.btn_sort_nombre.style().unpolish(self.btn_sort_nombre)
        self.btn_sort_nombre.style().polish(self.btn_sort_nombre)

        self.btn_sort_fecha.setText("Fecha" + (arrow if field == "fecha" else ""))
        self.btn_sort_fecha.setProperty("active", field == "fecha")
        self.btn_sort_fecha.style().unpolish(self.btn_sort_fecha)
        self.btn_sort_fecha.style().polish(self.btn_sort_fecha)

        self._update_count()
        self._mark_dirty()

    # -------------------------------------------------------- Estado
    def _update_count(self):
        n = self.list_widget.count()
        self.lbl_count.setText(f"{n} fotos cargadas")
        has_content = n > 0 or self._bg_path is not None
        self.btn_save_project.setEnabled(has_content or self._project_path is not None)
        self.btn_close_project.setEnabled(has_content or self._project_path is not None)

    def _update_buttons(self):
        n = len(self.list_widget.selectedItems())
        self.btn_centrada.setEnabled(n >= 1)
        self.btn_doble.setEnabled(n >= 2 and n % 2 == 0)
        self.btn_foto_fondo.setEnabled(n >= 1 and self._bg_path is not None)

    # -------------------------------------------------------- Proyectos
    @staticmethod
    def _read_project_file(path: str) -> dict | str:
        """Lee y valida un archivo de proyecto. Retorna dict o mensaje de error."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                project = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            return f"No se pudo leer el proyecto:\n{e}"
        if not isinstance(project, dict):
            return "El archivo no es un proyecto válido."
        images = project.get("images", [])
        if not isinstance(images, list):
            return "El archivo no es un proyecto válido."
        # Filtrar entradas que no sean strings
        project["images"] = [p for p in images if isinstance(p, str)]
        return project

    def _current_snapshot(self) -> tuple:
        """Retorna una representación del estado actual para comparar cambios."""
        paths = tuple(
            self.list_widget.item(i).data(PATH_ROLE)
            for i in range(self.list_widget.count())
        )
        return (paths, self._bg_path)

    def _mark_clean(self):
        self._clean_snapshot = self._current_snapshot()
        self._project_dirty = False
        self._update_title()

    def _mark_dirty(self):
        dirty = self._current_snapshot() != self._clean_snapshot
        if dirty != self._project_dirty:
            self._project_dirty = dirty
            self._update_title()

    def _update_title(self):
        title = APP_TITLE
        if self._project_path:
            name = os.path.splitext(os.path.basename(self._project_path))[0]
            title = f"{name} — {APP_TITLE}"
        if self._project_dirty:
            title = f"● {title}"
        self.setWindowTitle(title)

    def _write_project(self, path: str):
        images = []
        for i in range(self.list_widget.count()):
            images.append(self.list_widget.item(i).data(PATH_ROLE))
        project = {
            "version": 1,
            "images": images,
            "background": self._bg_path,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(project, f, ensure_ascii=False, indent=2)
        self._project_path = path
        self._mark_clean()

    def _save_project(self):
        if self._project_path:
            self._write_project(self._project_path)
        else:
            self._save_project_as()

    def _save_project_as(self):
        last_dir = self.settings.value("last_project_dir", "")
        path, _ = QFileDialog.getSaveFileName(
            self, "Guardar proyecto", last_dir,
            "Proyecto Convertir 3×4 (*.c3x4)",
        )
        if not path:
            return
        self.settings.setValue("last_project_dir", os.path.dirname(path))
        self._write_project(path)

    def _load_project(self):
        if self._project_dirty:
            resp = self._ask_save_before_action()
            if resp == QMessageBox.StandardButton.Cancel:
                return
            if resp == QMessageBox.StandardButton.Yes:
                self._save_project()

        last_dir = self.settings.value("last_project_dir", "")
        path, _ = QFileDialog.getOpenFileName(
            self, "Abrir proyecto", last_dir,
            "Proyecto Convertir 3×4 (*.c3x4)",
        )
        if not path:
            return
        self.settings.setValue("last_project_dir", os.path.dirname(path))

        result = self._read_project_file(path)
        if isinstance(result, str):
            QMessageBox.critical(self, "Error", result)
            return
        project = result

        image_paths = project["images"]
        bg_path = project.get("background")

        # Verificar archivos faltantes
        missing = [p for p in image_paths if not os.path.exists(p)]
        if missing:
            n = len(missing)
            detail = "\n".join(f"  • {os.path.basename(p)}" for p in missing[:10])
            if n > 10:
                detail += f"\n  … y {n - 10} más"
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("Archivos no encontrados")
            msg.setText(f"{n} imagen(es) del proyecto no se encontraron:")
            msg.setInformativeText(detail)
            msg.exec()

        # Limpiar y cargar
        self.list_widget.clear()
        self._pixmap_cache.clear()

        size = self.thumb_slider.value()
        for p in image_paths:
            if not os.path.exists(p):
                continue
            icon = self._make_icon(p, size)
            if icon is None:
                continue
            item = QListWidgetItem()
            item.setIcon(icon)
            item.setText(os.path.basename(p))
            item.setData(PATH_ROLE, p)
            item.setData(MTIME_ROLE, os.path.getmtime(p))
            self.list_widget.addItem(item)

        # Restaurar fondo
        if bg_path and os.path.exists(bg_path):
            self._bg_path = bg_path
            pixmap = QPixmap(bg_path)
            if not pixmap.isNull():
                preview_w = self.lbl_bg_preview.width() - 4
                preview_h = self.lbl_bg_preview.height() - 4
                self.lbl_bg_preview.setPixmap(pixmap.scaled(
                    preview_w, preview_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
        else:
            self._bg_path = None
            self.lbl_bg_preview.clear()
            self.lbl_bg_preview.setText("Sin fondo")

        self._project_path = path
        self._mark_clean()
        self._update_count()
        self._update_buttons()

    # -------------------------------------------------------- Fondo
    def _load_background(self):
        last_dir = self.settings.value("last_dir", "")
        path, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar imagen de fondo", last_dir, IMAGE_FILTER,
        )
        if not path:
            return
        self.settings.setValue("last_dir", os.path.dirname(path))
        self._bg_path = path

        # Mostrar preview
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            preview_w = self.lbl_bg_preview.width() - 4
            preview_h = self.lbl_bg_preview.height() - 4
            self.lbl_bg_preview.setPixmap(pixmap.scaled(
                preview_w, preview_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        self._update_buttons()
        self._mark_dirty()

    # -------------------------------------------------------- Zoom thumbnails
    def _on_thumb_resize(self, size: int):
        self.list_widget.setIconSize(QSize(size, size))
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            icon = self._make_icon(item.data(PATH_ROLE), size)
            if icon:
                item.setIcon(icon)

    # -------------------------------------------------------- Persistencia
    def _save_state(self):
        paths = []
        for i in range(self.list_widget.count()):
            paths.append(self.list_widget.item(i).data(PATH_ROLE))
        self.settings.setValue("image_paths", json.dumps(paths))
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("thumb_size", self.thumb_slider.value())

    def _restore_state(self):
        # Geometría de ventana
        geom = self.settings.value("geometry")
        if geom:
            self.restoreGeometry(geom)

        # Tamaño de thumbnails
        saved_size = self.settings.value("thumb_size", type=int)

        # Fotos guardadas
        raw = self.settings.value("image_paths")
        if not raw:
            return
        try:
            paths = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        if not paths:
            return

        found = []
        missing = []
        for p in paths:
            if os.path.isfile(p):
                found.append(p)
            else:
                missing.append(os.path.basename(p))

        # Cargar las que existen
        size = saved_size or THUMB_SIZE
        for path in found:
            icon = self._make_icon(path, size)
            if icon is None:
                continue
            item = QListWidgetItem()
            item.setIcon(icon)
            item.setText(os.path.basename(path))
            item.setData(PATH_ROLE, path)
            item.setData(MTIME_ROLE, os.path.getmtime(path))
            self.list_widget.addItem(item)

        self._update_count()

        if saved_size:
            self.thumb_slider.setValue(saved_size)
            self.list_widget.setIconSize(QSize(saved_size, saved_size))

        # Avisar de las faltantes
        if missing:
            n = len(missing)
            if n <= 5:
                detail = "\n".join(f"  • {name}" for name in missing)
                msg = f"Las siguientes {n} fotos ya no están disponibles:\n\n{detail}"
            else:
                detail = "\n".join(f"  • {name}" for name in missing[:5])
                msg = f"{n} fotos ya no están disponibles:\n\n{detail}\n  … y {n - 5} más"
            QMessageBox.information(self, "Fotos no encontradas", msg)

    def _ask_save_before_action(self) -> QMessageBox.StandardButton:
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle("Cambios sin guardar")
        msg.setText("El proyecto tiene cambios sin guardar.")
        msg.setInformativeText("¿Guardar antes de continuar?")
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
        )
        msg.setDefaultButton(QMessageBox.StandardButton.Yes)
        return msg.exec()

    def closeEvent(self, event):
        if self._project_dirty:
            resp = self._ask_save_before_action()
            if resp == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if resp == QMessageBox.StandardButton.Yes:
                self._save_project()
        self._save_state()
        super().closeEvent(event)

    # -------------------------------------------------------- Procesamiento común
    def _get_selected_paths(self) -> list[str]:
        """Retorna los paths de las fotos seleccionadas en orden."""
        items = self.list_widget.selectedItems()
        rows = sorted(self.list_widget.row(item) for item in items)
        return [self.list_widget.item(r).data(PATH_ROLE) for r in rows]

    def _ask_output_dir(self) -> str | None:
        last_out = self.settings.value("last_output_dir", "")
        path = QFileDialog.getExistingDirectory(self, "Carpeta de salida", last_out)
        if path:
            self.settings.setValue("last_output_dir", path)
        return path or None

    def _check_overwrite(self, out_names: list[str], out_dir: str) -> bool:
        """Verifica si hay archivos existentes y pide confirmación."""
        existing = [n for n in out_names if os.path.exists(os.path.join(out_dir, n))]
        if not existing:
            return True
        n = len(existing)
        if n <= 5:
            detail = "\n".join(f"  • {name}" for name in existing)
        else:
            detail = "\n".join(f"  • {name}" for name in existing[:5])
            detail += f"\n  … y {n - 5} más"
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Archivos existentes")
        msg.setText(f"{n} archivo(s) ya existen en la carpeta de salida:")
        msg.setInformativeText(f"{detail}\n\n¿Sobreescribir?")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.No)
        return msg.exec() == QMessageBox.StandardButton.Yes

    def _run_in_thread(self, func, paths, out_dir, suffix="_3x4.jpg", **kwargs):
        """Ejecuta el procesamiento en hilo separado con barra de progreso."""
        total = len(paths)
        self.progress.setMaximum(total)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._set_buttons_enabled(False)

        def worker():
            for i, path in enumerate(paths):
                name = os.path.splitext(os.path.basename(path))[0]
                out_path = os.path.join(out_dir, f"{i+1:04d}_{name}{suffix}")
                func(path, out_path, **kwargs)
                self._progress_signal.emit(i + 1)
            self._done_signal.emit(out_dir)

        threading.Thread(target=worker, daemon=True).start()

    def _run_pairs_in_thread(self, func, pairs, out_dir):
        """Ejecuta procesamiento de pares en hilo separado."""
        total = len(pairs)
        self.progress.setMaximum(total)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._set_buttons_enabled(False)

        def worker():
            for i, pair in enumerate(pairs):
                name1 = os.path.splitext(os.path.basename(pair[0]))[0]
                name2 = os.path.splitext(os.path.basename(pair[1]))[0] if len(pair) > 1 else ""
                names = f"{name1}+{name2}" if name2 else name1
                out_path = os.path.join(out_dir, f"{i+1:04d}_{names}_3x4.jpg")
                func(pair, out_path)
                self._progress_signal.emit(i + 1)
            self._done_signal.emit(out_dir)

        threading.Thread(target=worker, daemon=True).start()

    def _on_progress(self, value: int):
        self.progress.setValue(value)

    def _set_buttons_enabled(self, enabled: bool):
        for btn in (self.btn_load, self.btn_centrada,
                    self.btn_doble, self.btn_foto_fondo, self.btn_load_bg,
                    self.btn_new_project, self.btn_load_project,
                    self.btn_save_project, self.btn_close_project):
            btn.setEnabled(enabled)

    def _on_processing_done(self, out_dir: str):
        self.progress.setVisible(False)
        self._update_buttons()
        self.btn_load.setEnabled(True)
        self._update_count()
        open_folder(out_dir)

    # -------------------------------------------------------- Acción 1: Foto centrada
    def _action_foto_centrada(self):
        paths = self._get_selected_paths()
        if not paths:
            return
        out_dir = self._ask_output_dir()
        if not out_dir:
            return
        out_names = [f"{i+1:04d}_{os.path.splitext(os.path.basename(p))[0]}_3x4.jpg" for i, p in enumerate(paths)]
        if not self._check_overwrite(out_names, out_dir):
            return
        self._run_in_thread(lambda p, o: convert_centered(p, o), paths, out_dir)

    # -------------------------------------------------------- Acción 2: Doble foto
    def _action_doble_foto(self):
        paths = self._get_selected_paths()
        if len(paths) < 2:
            return
        out_dir = self._ask_output_dir()
        if not out_dir:
            return

        # Agrupar de a 2 (si impar, la última queda sola)
        pairs = []
        for i in range(0, len(paths), 2):
            pairs.append(paths[i:i + 2])

        out_names = []
        for i, pair in enumerate(pairs):
            n1 = os.path.splitext(os.path.basename(pair[0]))[0]
            n2 = os.path.splitext(os.path.basename(pair[1]))[0] if len(pair) > 1 else ""
            names = f"{n1}+{n2}" if n2 else n1
            out_names.append(f"{i+1:04d}_{names}_3x4.jpg")
        if not self._check_overwrite(out_names, out_dir):
            return

        self._run_pairs_in_thread(convert_double, pairs, out_dir)

    # -------------------------------------------------------- Acción 3: Foto y fondo
    def _action_foto_fondo(self):
        paths = self._get_selected_paths()
        if not paths or not self._bg_path:
            return

        out_dir = self._ask_output_dir()
        if not out_dir:
            return

        bg_path = self._bg_path
        out_names = [f"{i+1:04d}_{os.path.splitext(os.path.basename(p))[0]}_fondo_3x4.jpg" for i, p in enumerate(paths)]
        if not self._check_overwrite(out_names, out_dir):
            return

        self._run_in_thread(
            lambda p, o: convert_on_background(p, bg_path, o), paths, out_dir,
            suffix="_fondo_3x4.jpg",
        )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE)
    window = App()
    window.show()
    sys.exit(app.exec())
