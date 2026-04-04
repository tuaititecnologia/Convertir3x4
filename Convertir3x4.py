"""Convertir a 3:4 — CDLM: Convierte fotos a formato 3:4 portrait."""

import json
import os
import sys

from PyQt6.QtCore import Qt, QSize, QRect, QPoint, QSettings, QStandardPaths
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QListWidget, QListWidgetItem, QFileDialog,
    QProgressBar, QAbstractItemView, QMessageBox, QSlider,
)

# --- Constantes ---
APP_TITLE = "Convertir a 3:4 — CDLM"
THUMB_SIZE = 130
IMAGE_FILTER = "Imágenes (*.jpg *.jpeg *.png *.bmp *.webp);;Todos (*.*)"

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
QPushButton#btn_clear { background-color: #3a2030; border-color: #5a3048; color: #e08090; }
QPushButton#btn_clear:hover { background-color: #4a2840; border-color: #7a4060; }
QPushButton#btn_load { background-color: #1a3550; border-color: #2a5070; color: #70b8ff; }
QPushButton#btn_load:hover { background-color: #204060; border-color: #3070a0; }
QPushButton#btn_centrada { background-color: #1a3a2a; border-color: #2a5a3a; color: #70d090; }
QPushButton#btn_centrada:hover { background-color: #204a34; border-color: #30704a; }
QPushButton#btn_doble { background-color: #1a3a2a; border-color: #2a5a3a; color: #70d090; }
QPushButton#btn_doble:hover { background-color: #204a34; border-color: #30704a; }
QPushButton#btn_foto_fondo { background-color: #1a3a2a; border-color: #2a5a3a; color: #70d090; }
QPushButton#btn_foto_fondo:hover { background-color: #204a34; border-color: #30704a; }
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
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(960, 640)
        self.setMinimumSize(700, 450)

        self.sort_field: str | None = None
        self.sort_asc: bool = True
        self._pixmap_cache: dict[str, QPixmap] = {}  # path -> pixmap original

        self.settings = QSettings("CDLM", "Convertir3x4")

        self._build_ui()
        self._connect_signals()
        self._restore_state()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # --- Toolbar superior ---
        toolbar_layout = QHBoxLayout()

        self.btn_load = QPushButton("Cargar imágenes")
        self.btn_load.setObjectName("btn_load")
        toolbar_layout.addWidget(self.btn_load)

        self.btn_clear = QPushButton("Quitar todas")
        self.btn_clear.setObjectName("btn_clear")
        self.btn_clear.setEnabled(False)
        toolbar_layout.addWidget(self.btn_clear)

        self.lbl_count = QLabel("0 fotos cargadas")
        toolbar_layout.addWidget(self.lbl_count)

        toolbar_layout.addStretch()

        self.btn_centrada = QPushButton("Foto centrada")
        self.btn_centrada.setObjectName("btn_centrada")
        self.btn_centrada.setEnabled(False)
        toolbar_layout.addWidget(self.btn_centrada)

        self.btn_doble = QPushButton("Doble foto")
        self.btn_doble.setObjectName("btn_doble")
        self.btn_doble.setEnabled(False)
        toolbar_layout.addWidget(self.btn_doble)

        self.btn_foto_fondo = QPushButton("Foto y fondo")
        self.btn_foto_fondo.setObjectName("btn_foto_fondo")
        self.btn_foto_fondo.setEnabled(False)
        toolbar_layout.addWidget(self.btn_foto_fondo)

        layout.addLayout(toolbar_layout)

        # --- Grilla de imágenes ---
        self.list_widget = ReorderListWidget()
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.setSpacing(6)
        self.list_widget.setWordWrap(True)
        layout.addWidget(self.list_widget)

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
        layout.addLayout(bottom_layout)

        # --- Barra de progreso (oculta) ---
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

    def _connect_signals(self):
        self.btn_load.clicked.connect(self._load_images)
        self.btn_clear.clicked.connect(self._clear_images)
        self.btn_centrada.clicked.connect(self._action_foto_centrada)
        self.btn_doble.clicked.connect(self._action_doble_foto)
        self.btn_foto_fondo.clicked.connect(self._action_foto_fondo)
        self.btn_sort_nombre.clicked.connect(lambda: self._toggle_sort("nombre"))
        self.btn_sort_fecha.clicked.connect(lambda: self._toggle_sort("fecha"))
        self.list_widget.itemSelectionChanged.connect(self._update_buttons)
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

    # -------------------------------------------------------- Vaciar
    def _clear_images(self):
        n = self.list_widget.count()
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Quitar todas")
        msg.setText(f"¿Quitar las {n} fotos de la grilla?")
        msg.setInformativeText("Las fotos originales no se modifican.")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.No)
        if msg.exec() == QMessageBox.StandardButton.Yes:
            self.list_widget.clear()
            self._pixmap_cache.clear()
            self._update_count()
            self._update_buttons()

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

    # -------------------------------------------------------- Estado
    def _update_count(self):
        n = self.list_widget.count()
        self.lbl_count.setText(f"{n} fotos cargadas")
        self.btn_clear.setEnabled(n > 0)

    def _update_buttons(self):
        n = len(self.list_widget.selectedItems())
        self.btn_centrada.setEnabled(n >= 1)
        self.btn_doble.setEnabled(n >= 2 and n % 2 == 0)
        self.btn_foto_fondo.setEnabled(n >= 1)

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

    def closeEvent(self, event):
        self._save_state()
        super().closeEvent(event)

    # -------------------------------------------------------- Acciones (placeholder)
    def _action_foto_centrada(self):
        pass  # Fase 2

    def _action_doble_foto(self):
        pass  # Fase 3

    def _action_foto_fondo(self):
        pass  # Fase 4


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE)
    window = App()
    window.show()
    sys.exit(app.exec())
