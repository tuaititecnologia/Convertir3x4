"""Pestaña 'Convertir': la herramienta de conversión a vertical (imagen y video).

Es la `App` original convertida de QMainWindow a QWidget para vivir como una
pestaña del administrador. La geometría de ventana, el título y el cierre los
gestiona `convertir3x4.app.MainWindow`; esta pestaña le comunica el título por
la señal `titleChanged`.
"""

import json
import os
import threading

from PyQt6.QtCore import Qt, QSize, QSettings, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QFileDialog, QProgressBar,
    QAbstractItemView, QMessageBox, QSlider, QFrame, QComboBox, QCheckBox,
)

from ...config import (
    APP_TITLE, THUMB_SIZE, IMAGE_FILTER, VIDEO_FPS, VIDEO_DURATION,
    ASPECT_RATIOS, DEFAULT_ASPECT_W, DEFAULT_ASPECT_H,
)
from ...convert.images import convert_centered, convert_double, convert_on_background
from ...convert.video import convert_centered_video, convert_double_video
from ...convert.project import read_project_file
from ...utils import open_folder
from ..widgets.media_grid import ReorderListWidget, PATH_ROLE, MTIME_ROLE


class ConvertTab(QWidget):
    _progress_signal = pyqtSignal(int)
    _done_signal = pyqtSignal(str)
    titleChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.sort_field: str | None = None
        self.sort_asc: bool = True
        self._pixmap_cache: dict[str, QPixmap] = {}  # path -> pixmap original
        self._bg_path: str | None = None
        self._project_path: str | None = None
        self._project_dirty: bool = False
        self._clean_snapshot: tuple = ((), None)

        self.settings = QSettings("CDLM", "Convertir3x4")
        self._aspect_w = self.settings.value("aspect_w", DEFAULT_ASPECT_W, type=int)
        self._aspect_h = self.settings.value("aspect_h", DEFAULT_ASPECT_H, type=int)

        self._progress_signal.connect(self._on_progress)
        self._done_signal.connect(self._on_processing_done)

        self._build_ui()
        self._connect_signals()
        self.restore_state()
        self._clean_snapshot = self._current_snapshot()

    def _build_ui(self):
        main_layout = QHBoxLayout(self)
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

        self.btn_close_project = QPushButton("✖")
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

        # ==================== SIDEBAR DERECHA ====================
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(200)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(14, 16, 14, 16)
        sidebar_layout.setSpacing(0)

        # ── ACCIONES ──
        lbl_group_actions = QLabel("ACCIONES")
        lbl_group_actions.setObjectName("group_title")
        sidebar_layout.addWidget(lbl_group_actions)
        sidebar_layout.addSpacing(8)

        self.chk_video = QCheckBox("Exportar como video")
        self.chk_video.setToolTip("Genera un MP4 con fondo animado en lugar de imagen estática")
        sidebar_layout.addWidget(self.chk_video)
        sidebar_layout.addSpacing(12)

        # --- Foto centrada ---
        lbl1 = QLabel("Foto centrada")
        lbl1.setObjectName("section_title")
        sidebar_layout.addWidget(lbl1)
        sidebar_layout.addSpacing(6)

        self.btn_centrada = QPushButton("Convertir")
        self.btn_centrada.setObjectName("btn_action")
        self.btn_centrada.setEnabled(False)
        sidebar_layout.addWidget(self.btn_centrada)

        sidebar_layout.addSpacing(14)
        sep1 = QFrame()
        sep1.setObjectName("separator")
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setFixedHeight(1)
        sidebar_layout.addWidget(sep1)
        sidebar_layout.addSpacing(14)

        # --- Doble foto ---
        lbl2 = QLabel("Doble foto")
        lbl2.setObjectName("section_title")
        sidebar_layout.addWidget(lbl2)
        sidebar_layout.addSpacing(6)

        self.btn_doble = QPushButton("Convertir")
        self.btn_doble.setObjectName("btn_action")
        self.btn_doble.setEnabled(False)
        sidebar_layout.addWidget(self.btn_doble)

        sidebar_layout.addSpacing(14)
        sep2 = QFrame()
        sep2.setObjectName("separator")
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFixedHeight(1)
        sidebar_layout.addWidget(sep2)
        sidebar_layout.addSpacing(14)

        # --- Foto y fondo ---
        lbl3 = QLabel("Foto y fondo")
        lbl3.setObjectName("section_title")
        sidebar_layout.addWidget(lbl3)
        sidebar_layout.addSpacing(6)

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

        # ── CONFIGURACIÓN ──
        sep_config = QFrame()
        sep_config.setObjectName("separator")
        sep_config.setFrameShape(QFrame.Shape.HLine)
        sep_config.setFixedHeight(1)
        sidebar_layout.addWidget(sep_config)
        sidebar_layout.addSpacing(12)

        lbl_group_config = QLabel("CONFIGURACIÓN")
        lbl_group_config.setObjectName("group_title")
        sidebar_layout.addWidget(lbl_group_config)
        sidebar_layout.addSpacing(12)

        lbl_ratio = QLabel("Proporción")
        lbl_ratio.setObjectName("section_title")
        sidebar_layout.addWidget(lbl_ratio)
        sidebar_layout.addSpacing(6)

        self.combo_ratio = QComboBox()
        saved_key = f"{self._aspect_w}:{self._aspect_h}"
        current_idx = 0
        for i, (label, w, h) in enumerate(ASPECT_RATIOS):
            self.combo_ratio.addItem(label, (w, h))
            if f"{w}:{h}" == saved_key:
                current_idx = i
        self.combo_ratio.setCurrentIndex(current_idx)
        sidebar_layout.addWidget(self.combo_ratio)

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
        self.combo_ratio.currentIndexChanged.connect(self._on_aspect_changed)

    def _on_aspect_changed(self):
        w, h = self.combo_ratio.currentData()
        self._aspect_w = w
        self._aspect_h = h
        self.settings.setValue("aspect_w", w)
        self.settings.setValue("aspect_h", h)

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
        return read_project_file(path)

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
        self.titleChanged.emit(title)

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
    def save_state(self):
        paths = []
        for i in range(self.list_widget.count()):
            paths.append(self.list_widget.item(i).data(PATH_ROLE))
        self.settings.setValue("image_paths", json.dumps(paths))
        self.settings.setValue("thumb_size", self.thumb_slider.value())

    def restore_state(self):
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

    def confirm_close(self) -> bool:
        """Pide guardar si hay cambios. Retorna False si el usuario cancela."""
        if self._project_dirty:
            resp = self._ask_save_before_action()
            if resp == QMessageBox.StandardButton.Cancel:
                return False
            if resp == QMessageBox.StandardButton.Yes:
                self._save_project()
        return True

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

    def _run_pairs_in_thread(self, func, pairs, out_dir, aspect=None):
        """Ejecuta procesamiento de pares en hilo separado."""
        total = len(pairs)
        sfx = self._ratio_suffix()
        self.progress.setMaximum(total)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._set_buttons_enabled(False)

        def worker():
            for i, pair in enumerate(pairs):
                name1 = os.path.splitext(os.path.basename(pair[0]))[0]
                name2 = os.path.splitext(os.path.basename(pair[1]))[0] if len(pair) > 1 else ""
                names = f"{name1}+{name2}" if name2 else name1
                out_path = os.path.join(out_dir, f"{i+1:04d}_{names}{sfx}.jpg")
                func(pair, out_path, aspect=aspect)
                self._progress_signal.emit(i + 1)
            self._done_signal.emit(out_dir)

        threading.Thread(target=worker, daemon=True).start()

    def _run_video_in_thread(self, func, paths, out_dir, suffix="_3x4.mp4", aspect=None):
        """Ejecuta generación de video en hilo separado con progreso por frame."""
        total_frames = VIDEO_FPS * VIDEO_DURATION * len(paths)
        self.progress.setMaximum(total_frames)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._set_buttons_enabled(False)

        def worker():
            for i, path in enumerate(paths):
                name = os.path.splitext(os.path.basename(path))[0]
                out_path = os.path.join(out_dir, f"{i+1:04d}_{name}{suffix}")
                base_frames = i * VIDEO_FPS * VIDEO_DURATION

                def progress_cb(frame, _total, _base=base_frames):
                    self._progress_signal.emit(_base + frame)

                func(path, out_path, aspect=aspect, progress_cb=progress_cb)
            self._done_signal.emit(out_dir)

        threading.Thread(target=worker, daemon=True).start()

    def _run_video_pairs_in_thread(self, func, pairs, out_dir, aspect=None):
        """Ejecuta generación de video de pares en hilo separado."""
        total_frames = VIDEO_FPS * VIDEO_DURATION * len(pairs)
        sfx = self._ratio_suffix()
        self.progress.setMaximum(total_frames)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._set_buttons_enabled(False)

        def worker():
            for i, pair in enumerate(pairs):
                name1 = os.path.splitext(os.path.basename(pair[0]))[0]
                name2 = os.path.splitext(os.path.basename(pair[1]))[0] if len(pair) > 1 else ""
                names = f"{name1}+{name2}" if name2 else name1
                out_path = os.path.join(out_dir, f"{i+1:04d}_{names}{sfx}.mp4")
                base_frames = i * VIDEO_FPS * VIDEO_DURATION

                def progress_cb(frame, _total, _base=base_frames):
                    self._progress_signal.emit(_base + frame)

                func(pair, out_path, aspect=aspect, progress_cb=progress_cb)
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

    def _ratio_suffix(self):
        return f"_{self._aspect_w}x{self._aspect_h}"

    # -------------------------------------------------------- Acción 1: Foto centrada
    def _action_foto_centrada(self):
        paths = self._get_selected_paths()
        if not paths:
            return
        out_dir = self._ask_output_dir()
        if not out_dir:
            return
        sfx = self._ratio_suffix()
        video = self.chk_video.isChecked()
        ext = ".mp4" if video else ".jpg"
        out_names = [f"{i+1:04d}_{os.path.splitext(os.path.basename(p))[0]}{sfx}{ext}" for i, p in enumerate(paths)]
        if not self._check_overwrite(out_names, out_dir):
            return
        aspect = (self._aspect_w, self._aspect_h)
        if video:
            self._run_video_in_thread(convert_centered_video, paths, out_dir, suffix=f"{sfx}.mp4", aspect=aspect)
        else:
            self._run_in_thread(lambda p, o: convert_centered(p, o, aspect=aspect), paths, out_dir, suffix=f"{sfx}.jpg")

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

        sfx = self._ratio_suffix()
        video = self.chk_video.isChecked()
        ext = ".mp4" if video else ".jpg"
        out_names = []
        for i, pair in enumerate(pairs):
            n1 = os.path.splitext(os.path.basename(pair[0]))[0]
            n2 = os.path.splitext(os.path.basename(pair[1]))[0] if len(pair) > 1 else ""
            names = f"{n1}+{n2}" if n2 else n1
            out_names.append(f"{i+1:04d}_{names}{sfx}{ext}")
        if not self._check_overwrite(out_names, out_dir):
            return

        aspect = (self._aspect_w, self._aspect_h)
        if video:
            self._run_video_pairs_in_thread(convert_double_video, pairs, out_dir, aspect=aspect)
        else:
            self._run_pairs_in_thread(convert_double, pairs, out_dir, aspect=aspect)

    # -------------------------------------------------------- Acción 3: Foto y fondo
    def _action_foto_fondo(self):
        paths = self._get_selected_paths()
        if not paths or not self._bg_path:
            return

        out_dir = self._ask_output_dir()
        if not out_dir:
            return

        bg_path = self._bg_path
        sfx = self._ratio_suffix()
        out_names = [f"{i+1:04d}_{os.path.splitext(os.path.basename(p))[0]}_fondo{sfx}.jpg" for i, p in enumerate(paths)]
        if not self._check_overwrite(out_names, out_dir):
            return

        aspect = (self._aspect_w, self._aspect_h)
        self._run_in_thread(
            lambda p, o: convert_on_background(p, bg_path, o, aspect=aspect), paths, out_dir,
            suffix=f"_fondo{sfx}.jpg",
        )
