"""Pestaña 'Convertir': fotos y videos a la proporción de la pantalla LED.

Diseño en docs/convertir-fotos-y-videos.md (15/11/2026):
- Fotos y videos en la misma grilla, con filtro Todo · Fotos · Videos.
- A la derecha, solo las acciones: cómo armarla (Centrada, Doble, Sobre fondo)
  y un único botón "Convertir N". Si en la selección hay un video, sale video.
- Opciones avanzadas escondidas detrás del ⚙ y válidas hasta cerrar la app.
- Links de YouTube/Facebook: se bajan y entran a la grilla como un video más.
- Cada elemento muestra su estado: cargando, % de avance, listo, aviso, error,
  no disponible.

La lógica sin Qt está en convert/batch.py (qué sale y cómo se llama),
convert/clips.py (video) y convert/download.py (links). La ventana principal
maneja geometría y cierre.

Sin proyectos (se sacaron el 15/11/2026: nadie los usaba). La lista se guarda
sola en cada cambio y vuelve al abrir la app; "Vaciar" la deja en blanco sin
tocar ningún archivo.
"""

import json
import os

from PyQt6.QtCore import QSettings, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QIcon, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QSizePolicy,
    QAbstractItemView, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QProgressBar,
    QPushButton, QRadioButton, QSlider, QVBoxLayout, QWidget,
)

from ...appdata import clip_cache_dir, tools_dir
from ...config import THUMB_SIZE
from ...convert import batch, download
from ...utils import open_file, open_folder
from .. import icons
from ..style import T
from ..widgets.advanced_dialog import AdvancedDialog, SessionOptions
from ..widgets.media_grid import (
    DURATION_ROLE, KIND_ROLE, MTIME_ROLE, PATH_ROLE, PROGRESS_ROLE, STATE_ROLE,
    STORYBOARD_ROLE, ReorderListWidget,
)
from .convert_workers import ConvertWorker, DownloadWorker, ThumbWorker

MEDIA_FILTER = ("Fotos y videos (*.jpg *.jpeg *.png *.bmp *.webp *.mp4 *.mov *.m4v *.mkv *.webm *.avi)"
                ";;Fotos (*.jpg *.jpeg *.png *.bmp *.webp)"
                ";;Videos (*.mp4 *.mov *.m4v *.mkv *.webm *.avi)"
                ";;Todos (*.*)")
IMAGE_FILTER = "Imágenes (*.jpg *.jpeg *.png *.bmp *.webp);;Todos (*.*)"
LINK_ROLE = Qt.ItemDataRole.UserRole + 20   # link que se está bajando (sin archivo todavía)

MODES = [
    (batch.CENTRADA, "Centrada", "Ocupa toda la pantalla, con un fondo de sus colores."),
    (batch.DOBLE, "Doble", "De a dos: una arriba y otra abajo, en el orden de la grilla."),
    (batch.FONDO, "Sobre fondo", "Arriba de un fondo que elegís, con lugar abajo para texto."),
]


class ConvertTab(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sort_field: str | None = None
        self.sort_asc = True
        self._pixmap_cache: dict[str, QPixmap] = {}
        self._bg_path: str | None = None
        self._mode = batch.CENTRADA
        # Opciones avanzadas: SOLO en memoria, vuelven a lo estándar al abrir la app.
        self._opts = SessionOptions()
        self._convert_worker: ConvertWorker | None = None
        self._thumb_workers: list[ThumbWorker] = []
        self._downloads: list[DownloadWorker] = []
        self._last_out_dir: str | None = None
        self._jobs_total = self._jobs_done = 0

        # Sin argumentos: usa el nombre de la app (__main__ fija CDLM/Convertir3x4;
        # los tests usan otro y no tocan la configuración real).
        self.settings = QSettings()
        self._build_ui()
        self._connect_signals()
        self.restore_state()
        self._refresh_panel()

    # ================================================================ UI
    def _build_ui(self):
        main = QHBoxLayout(self)
        main.setContentsMargins(16, 14, 16, 12)
        main.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(10)

        # --- barra superior: agregar · link · quitar · vaciar · filtro · contador
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.btn_load = QPushButton("Agregar")
        self.btn_load.setIcon(icons.icon("plus", 16, T.text))
        self.btn_load.setToolTip("Agregar fotos o videos de la PC o del NAS")
        bar.addWidget(self.btn_load)
        self.btn_link = QPushButton("Pegar link")
        self.btn_link.setIcon(icons.icon("link", 15, T.text))
        self.btn_link.setToolTip("Bajar un video de YouTube, Facebook u otro sitio")
        bar.addWidget(self.btn_link)
        self.btn_remove = self._icon_button("trash-2", "Quitar de la lista lo seleccionado (Supr). "
                                                       "Los archivos no se borran.")
        self.btn_remove.setEnabled(False)
        bar.addWidget(self.btn_remove)
        self.btn_clear = QPushButton("Vaciar")
        self.btn_clear.setToolTip("Sacar todo de la lista para empezar de nuevo. "
                                  "Los archivos no se borran.")
        self.btn_clear.setEnabled(False)
        bar.addWidget(self.btn_clear)
        bar.addSpacing(8)

        seg = QFrame()
        seg.setObjectName("segmented")
        seg_row = QHBoxLayout(seg)
        seg_row.setContentsMargins(3, 3, 3, 3)
        seg_row.setSpacing(2)
        self._kind_group = QButtonGroup(self)
        for kind, text in ((None, "Todo"), ("image", "Fotos"), ("video", "Videos")):
            b = QPushButton(text)
            b.setObjectName("segment")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAccessibleName(f"Mostrar: {text}")
            b.setProperty("kind", kind)
            # El filtro no se achica nunca (si no, los textos quedan cortados)
            b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self._kind_group.addButton(b)
            seg_row.addWidget(b)
            if kind is None:
                b.setChecked(True)
        bar.addWidget(seg)
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("muted")
        # El contador es lo primero que cede lugar cuando la ventana se angosta
        self.lbl_count.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_count.setMinimumWidth(0)
        self.lbl_count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar.addWidget(self.lbl_count, 1)
        left.addLayout(bar)

        # --- fila para pegar un link (aparece con "Pegar link")
        self.link_row = QFrame()
        self.link_row.setObjectName("panel")
        lr = QHBoxLayout(self.link_row)
        lr.setContentsMargins(10, 8, 10, 8)
        lr.setSpacing(8)
        self.txt_link = QLineEdit()
        self.txt_link.setPlaceholderText("Pegá el link de YouTube o Facebook y Enter…")
        self.txt_link.setAccessibleName("Link del video a bajar")
        lr.addWidget(self.txt_link, 1)
        self.btn_link_go = QPushButton("Bajar")
        self.btn_link_go.setObjectName("btn_primary")
        lr.addWidget(self.btn_link_go)
        self.btn_link_close = self._icon_button("x", "Cerrar")
        lr.addWidget(self.btn_link_close)
        self.link_row.setVisible(False)
        left.addWidget(self.link_row)

        # --- grilla
        self.list_widget = ReorderListWidget(thumb=THUMB_SIZE)
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setMovement(QListWidget.Movement.Static)  # setViewMode la pone en Free
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.set_thumb_size(THUMB_SIZE)
        self.list_widget.setAccessibleName("Fotos y videos para convertir")
        self.list_widget.set_empty_text(
            "Todavía no hay nada para convertir.\n"
            "Usá «Agregar» para elegir fotos o videos, o «Pegar link» para bajar uno de internet.")
        left.addWidget(self.list_widget, 1)

        # --- barra inferior: orden · tamaño
        bottom = QHBoxLayout()
        lbl_sort = QLabel("Ordenar:")
        lbl_sort.setObjectName("muted")
        bottom.addWidget(lbl_sort)
        self.btn_sort_nombre = QPushButton("Nombre")
        self.btn_sort_nombre.setObjectName("btn_sort")
        bottom.addWidget(self.btn_sort_nombre)
        self.btn_sort_fecha = QPushButton("Fecha")
        self.btn_sort_fecha.setObjectName("btn_sort")
        bottom.addWidget(self.btn_sort_fecha)
        bottom.addStretch()
        zoom = QLabel()
        zoom.setPixmap(icons.pixmap("image", 16, T.text_3))
        zoom.setToolTip("Tamaño de las miniaturas")
        bottom.addWidget(zoom)
        self.thumb_slider = QSlider(Qt.Orientation.Horizontal)
        self.thumb_slider.setRange(60, 300)
        self.thumb_slider.setValue(THUMB_SIZE)
        self.thumb_slider.setFixedWidth(150)
        self.thumb_slider.setAccessibleName("Tamaño de las miniaturas")
        bottom.addWidget(self.thumb_slider)
        left.addLayout(bottom)
        main.addLayout(left, 1)

        self._panel = self._build_panel()
        main.addWidget(self._panel)

        QShortcut(QKeySequence.StandardKey.Delete, self.list_widget, activated=self._remove_selected)
        QShortcut(QKeySequence(Qt.Key.Key_Backspace), self.list_widget, activated=self._remove_selected)

    def _build_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("sidebar")
        panel.setFixedWidth(self._PANEL_W)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)

        title = QLabel("Cómo armarla")
        title.setObjectName("section_title")
        lay.addWidget(title)

        # Una opción entre tres: botones de opción (el control estándar para
        # eso), con la descripción en gris debajo de cada uno.
        self._mode_group = QButtonGroup(self)
        self._mode_buttons: dict[str, QRadioButton] = {}
        for key, name, desc in MODES:
            b = QRadioButton(name)
            b.setObjectName("mode_option")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAccessibleDescription(desc)
            b.setProperty("mode", key)
            self._mode_group.addButton(b)
            self._mode_buttons[key] = b
            lay.addWidget(b)
            d = QLabel(desc)
            d.setObjectName("muted")
            d.setWordWrap(True)
            d.setContentsMargins(26, 0, 0, 6)
            lay.addWidget(d)
        self._mode_buttons[batch.CENTRADA].setChecked(True)

        # Fondo (solo en "Sobre fondo")
        self.bg_box = QWidget()
        bg_lay = QVBoxLayout(self.bg_box)
        bg_lay.setContentsMargins(0, 4, 0, 0)
        bg_lay.setSpacing(6)
        self.lbl_bg_preview = QLabel("Sin fondo")
        self.lbl_bg_preview.setObjectName("bg_preview")
        self.lbl_bg_preview.setFixedHeight(110)
        self.lbl_bg_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bg_lay.addWidget(self.lbl_bg_preview)
        self.btn_load_bg = QPushButton("Elegir fondo…")
        bg_lay.addWidget(self.btn_load_bg)
        lay.addWidget(self.bg_box)

        lay.addStretch(1)

        # Aviso naranja cuando algo está fuera de lo estándar
        self.nonstd_box = QFrame()
        self.nonstd_box.setObjectName("nonstandard")
        nl = QVBoxLayout(self.nonstd_box)
        nl.setContentsMargins(10, 8, 10, 8)
        nl.setSpacing(4)
        t = QLabel("Fuera de lo estándar")
        t.setObjectName("nonstandard_title")
        nl.addWidget(t)
        self.lbl_nonstd = QLabel("")
        self.lbl_nonstd.setObjectName("nonstandard_text")
        self.lbl_nonstd.setWordWrap(True)
        nl.addWidget(self.lbl_nonstd)
        self.btn_reset_opts = QPushButton("Volver a lo estándar")
        self.btn_reset_opts.setObjectName("btn_link")
        self.btn_reset_opts.setIcon(icons.icon("rotate-ccw", 14, T.warn))
        nl.addWidget(self.btn_reset_opts, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.nonstd_box)

        self.lbl_summary = QLabel("")
        self.lbl_summary.setWordWrap(True)
        lay.addWidget(self.lbl_summary)

        # Avance del lote
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("muted")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setVisible(False)
        lay.addWidget(self.lbl_status)
        self.btn_open_out = QPushButton("Abrir la carpeta")
        self.btn_open_out.setObjectName("btn_link")
        self.btn_open_out.setVisible(False)
        lay.addWidget(self.btn_open_out, 0, Qt.AlignmentFlag.AlignLeft)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_convert = QPushButton("Convertir")
        self.btn_convert.setObjectName("btn_primary")
        self.btn_convert.setMinimumHeight(36)
        row.addWidget(self.btn_convert, 1)
        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.setMinimumHeight(36)
        self.btn_cancel.setVisible(False)
        row.addWidget(self.btn_cancel, 1)
        self.btn_advanced = self._icon_button("settings", "Opciones avanzadas")
        self.btn_advanced.setFixedSize(36, 36)
        row.addWidget(self.btn_advanced)
        lay.addLayout(row)
        return panel

    _COMPACT_BELOW = 640   # ancho de la zona izquierda por debajo del cual la barra se compacta
    _PANEL_W, _PANEL_W_COMPACT = 272, 240

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # En ventanas angostas, "Agregar" y "Pegar link" quedan solo con ícono
        # (el texto sigue en el tooltip y en el nombre accesible) y el panel de
        # acciones se angosta: así el filtro Todo · Fotos · Videos entra entero.
        compact = self.width() - self._PANEL_W - 46 < self._COMPACT_BELOW
        self._panel.setFixedWidth(self._PANEL_W_COMPACT if compact else self._PANEL_W)
        for b, text in ((self.btn_load, "Agregar"), (self.btn_link, "Pegar link")):
            b.setText("" if compact else text)
            b.setAccessibleName(text)

    @staticmethod
    def _icon_button(name: str, tip: str) -> QPushButton:
        b = QPushButton()
        b.setObjectName("btn_icon")
        b.setIcon(icons.icon(name, 17, T.text_2))
        b.setIconSize(QSize(17, 17))
        b.setFixedSize(32, 32)
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        return b

    def _connect_signals(self):
        self.btn_load.clicked.connect(self._add_files)
        self.btn_link.clicked.connect(self._show_link_row)
        self.btn_link_go.clicked.connect(self._start_download)
        self.txt_link.returnPressed.connect(self._start_download)
        self.btn_link_close.clicked.connect(lambda: self.link_row.setVisible(False))
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_clear.clicked.connect(self._clear_list)
        self.btn_load_bg.clicked.connect(self._load_background)
        self.btn_sort_nombre.clicked.connect(lambda: self._toggle_sort("nombre"))
        self.btn_sort_fecha.clicked.connect(lambda: self._toggle_sort("fecha"))
        self.list_widget.itemSelectionChanged.connect(self._refresh_panel)
        self.list_widget.orderChanged.connect(self._save_list)
        self.list_widget.itemDoubleClicked.connect(self._open_item)
        self.thumb_slider.valueChanged.connect(self._on_thumb_resize)
        self._kind_group.buttonClicked.connect(lambda _: self._apply_filter())
        self._mode_group.buttonClicked.connect(self._on_mode)
        self.btn_convert.clicked.connect(self._convert)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_advanced.clicked.connect(self._open_advanced)
        self.btn_reset_opts.clicked.connect(self._reset_options)
        self.btn_open_out.clicked.connect(lambda: self._last_out_dir and open_folder(self._last_out_dir))

    # ================================================================ elementos
    def _items(self):
        return [self.list_widget.item(i) for i in range(self.list_widget.count())]

    def _paths(self) -> list[str]:
        return [it.data(PATH_ROLE) for it in self._items() if it.data(PATH_ROLE)]

    def _cached_pixmap(self, path: str) -> QPixmap | None:
        if path not in self._pixmap_cache:
            px = QPixmap(path)
            if px.isNull():
                return None
            if px.width() > 600 or px.height() > 600:
                px = px.scaled(600, 600, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            self._pixmap_cache[path] = px
        return self._pixmap_cache[path]

    def _make_icon(self, path: str, size: int) -> QIcon | None:
        px = self._cached_pixmap(path)
        if px is None:
            return None
        return QIcon(px.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation))

    def _add_paths(self, paths: list[str], missing_ok: bool = False) -> int:
        """Agrega rutas a la grilla. Los videos piden su miniatura en segundo plano.

        `missing_ok`: los que no existen entran igual, marcados "No disponible"
        (al abrir un proyecto o al volver a abrir la app con el NAS desconectado).
        """
        existing = set(self._paths())
        size = self.thumb_slider.value()
        videos, added = [], 0
        for path in paths:
            if path in existing:
                continue
            kind = batch.kind_of(path)
            if kind is None:
                continue
            item = QListWidgetItem(os.path.basename(path))
            item.setData(PATH_ROLE, path)
            item.setData(KIND_ROLE, kind)
            if not os.path.exists(path):
                if not missing_ok:
                    continue
                item.setData(MTIME_ROLE, 0)
                item.setData(STATE_ROLE, "missing")
                item.setToolTip(f"No se encuentra:\n{path}\n¿Se desconectó el disco o el NAS?")
            else:
                item.setData(MTIME_ROLE, os.path.getmtime(path))
                item.setToolTip(path)
                if kind == "image":
                    icon = self._make_icon(path, size)
                    if icon is None:
                        item.setData(STATE_ROLE, "error")
                        item.setToolTip(f"No se pudo abrir la imagen:\n{path}")
                    else:
                        item.setIcon(icon)
                else:
                    item.setData(STATE_ROLE, "loading")
                    videos.append(path)
            self.list_widget.addItem(item)
            existing.add(path)
            added += 1
        if videos:
            self._load_video_thumbs(videos)
        self._apply_filter()
        self._update_count()
        return added

    def _load_video_thumbs(self, paths: list[str]):
        w = ThumbWorker(paths, clip_cache_dir(), self)
        w.ready.connect(self._on_thumb_ready)
        w.failed.connect(self._on_thumb_failed)
        w.finished.connect(lambda: self._thumb_workers.remove(w) if w in self._thumb_workers else None)
        self._thumb_workers.append(w)
        w.start()

    def _item_for(self, path: str) -> QListWidgetItem | None:
        for it in self._items():
            if it.data(PATH_ROLE) == path:
                return it
        return None

    def _on_thumb_ready(self, path: str, thumbs):
        it = self._item_for(path)
        if it is None:
            return
        px = QPixmap(thumbs.poster)
        if not px.isNull():
            it.setIcon(QIcon(px))
        it.setData(DURATION_ROLE, thumbs.info.duration)
        it.setData(STORYBOARD_ROLE, thumbs.storyboard)
        if it.data(STATE_ROLE) == "loading":
            it.setData(STATE_ROLE, None)
        info = thumbs.info
        it.setToolTip(f"{path}\n{info.width}×{info.height} · "
                      f"{'con audio' if info.has_audio else 'sin audio'}")
        self._refresh_panel()

    def _on_thumb_failed(self, path: str, reason: str):
        it = self._item_for(path)
        if it is not None:
            it.setData(STATE_ROLE, "error")
            it.setToolTip(reason)

    def _add_files(self):
        last_dir = self.settings.value("last_dir", "")
        paths, _ = QFileDialog.getOpenFileNames(self, "Agregar fotos o videos", last_dir, MEDIA_FILTER)
        if not paths:
            return
        self.settings.setValue("last_dir", os.path.dirname(paths[0]))
        if self._add_paths(paths):
            self._save_list()

    def _open_item(self, item):
        """Doble clic: el video se ve en el reproductor de la PC (la foto, en su visor)."""
        path = item.data(PATH_ROLE)
        if not path:           # un link que todavía se está bajando
            return
        if not open_file(path):
            self._show_status(f"No se puede abrir {os.path.basename(path)}: no se encuentra "
                              "(¿se desconectó el disco o el NAS?).")

    def _remove_selected(self):
        if self._busy():
            return
        rows = sorted((self.list_widget.row(it) for it in self.list_widget.selectedItems()),
                      reverse=True)
        for r in rows:
            self.list_widget.takeItem(r)
        if rows:
            self._update_count()
            self._save_list()
            self._refresh_panel()

    # ================================================================ links
    def _show_link_row(self):
        self.link_row.setVisible(True)
        clip = QGuiApplication.clipboard().text().strip()
        if download.is_link(clip) and not self.txt_link.text():
            self.txt_link.setText(clip)
        self.txt_link.setFocus()
        self.txt_link.selectAll()

    def _start_download(self):
        url = self.txt_link.text().strip()
        if not download.is_link(url):
            self.txt_link.clear()
            self.txt_link.setPlaceholderText("Eso no parece un link: tiene que empezar con http…")
            return
        self.txt_link.clear()
        self.link_row.setVisible(False)
        item = QListWidgetItem(url)
        item.setData(LINK_ROLE, url)
        item.setData(KIND_ROLE, "video")
        item.setData(STATE_ROLE, "working")
        item.setData(PROGRESS_ROLE, 0.0)
        item.setToolTip(f"Bajando…\n{url}")
        self.list_widget.addItem(item)
        w = DownloadWorker(url, self._opts.download_dir, tools_dir(), self)
        w.progress.connect(self._on_download_progress)
        w.done.connect(self._on_download_done)
        w.failed.connect(self._on_download_failed)
        w.finished.connect(lambda: self._downloads.remove(w) if w in self._downloads else None)
        self._downloads.append(w)
        w.start()
        self._update_count()

    def _link_item(self, url: str) -> QListWidgetItem | None:
        for it in self._items():
            if it.data(LINK_ROLE) == url and not it.data(PATH_ROLE):
                return it
        return None

    def _on_download_progress(self, url: str, frac: float, msg: str):
        it = self._link_item(url)
        if it is not None:
            it.setData(PROGRESS_ROLE, frac)
            it.setToolTip(f"{msg}\n{url}")

    def _on_download_done(self, url: str, path: str):
        it = self._link_item(url)
        if it is not None:
            self.list_widget.takeItem(self.list_widget.row(it))
        self._add_paths([path])
        self._save_list()

    def _on_download_failed(self, url: str, reason: str):
        it = self._link_item(url)
        if it is None:
            return
        if not reason:   # cancelado
            self.list_widget.takeItem(self.list_widget.row(it))
        else:
            it.setData(STATE_ROLE, "error")
            it.setToolTip(f"{reason}\n{url}")
        self._update_count()

    # ================================================================ filtro y orden
    def _apply_filter(self):
        btn = self._kind_group.checkedButton()
        kind = btn.property("kind") if btn else None
        for it in self._items():
            hide = kind is not None and it.data(KIND_ROLE) != kind
            it.setHidden(hide)
            if hide:
                it.setSelected(False)

    def _toggle_sort(self, field: str):
        if self.list_widget.count() == 0:
            return
        if self.sort_field == field:
            self.sort_asc = not self.sort_asc
        else:
            self.sort_field, self.sort_asc = field, True
        items = [self.list_widget.takeItem(0) for _ in range(self.list_widget.count())]
        if field == "nombre":
            items.sort(key=lambda it: it.text().lower(), reverse=not self.sort_asc)
        else:
            items.sort(key=lambda it: it.data(MTIME_ROLE) or 0, reverse=not self.sort_asc)
        for it in items:
            self.list_widget.addItem(it)
        arrow = " ↑" if self.sort_asc else " ↓"
        for b, f, label in ((self.btn_sort_nombre, "nombre", "Nombre"),
                            (self.btn_sort_fecha, "fecha", "Fecha")):
            b.setText(label + (arrow if field == f else ""))
            b.setProperty("active", field == f)
            b.style().unpolish(b)
            b.style().polish(b)
        self._apply_filter()
        self._save_list()

    def _on_thumb_resize(self, size: int):
        self.list_widget.set_thumb_size(size)
        for it in self._items():
            if it.data(KIND_ROLE) == "image" and it.data(PATH_ROLE) and not it.data(STATE_ROLE):
                icon = self._make_icon(it.data(PATH_ROLE), size)
                if icon:
                    it.setIcon(icon)

    # ================================================================ panel
    def _selected_paths(self) -> list[str]:
        """Rutas seleccionadas, en el orden de la grilla, que se pueden convertir."""
        rows = sorted(self.list_widget.row(it) for it in self.list_widget.selectedItems())
        out = []
        for r in rows:
            it = self.list_widget.item(r)
            if it.data(PATH_ROLE) and it.data(STATE_ROLE) not in ("missing", "loading", "error"):
                out.append(it.data(PATH_ROLE))
        return out

    def _on_mode(self, button):
        self._mode = button.property("mode")
        self._refresh_panel()

    def _batch_options(self) -> batch.Options:
        return batch.Options(aspect=self._opts.aspect, photos_as_video=self._opts.photos_as_video,
                             background=self._bg_path)

    def _refresh_panel(self):
        busy = self._busy()
        paths = self._selected_paths()
        n_sel = len(self.list_widget.selectedItems())
        self.bg_box.setVisible(self._mode == batch.FONDO)
        self.btn_remove.setEnabled(n_sel > 0 and not busy)

        changes = self._opts.changes()
        self.nonstd_box.setVisible(bool(changes))
        self.lbl_nonstd.setText(" · ".join(changes))

        if busy:
            return
        if not paths:
            self.lbl_summary.setText("Seleccioná en la grilla lo que querés convertir."
                                     if self.list_widget.count() else "")
            self.btn_convert.setText("Convertir")
            self.btn_convert.setEnabled(False)
            return
        skipped = n_sel - len(paths)
        summary = batch.describe(paths, self._mode, self._batch_options())
        if skipped:
            summary += (" 1 no se puede convertir (no está disponible o tuvo un error)." if skipped == 1
                        else f" {skipped} no se pueden convertir (no están disponibles o tuvieron un error).")
        self.lbl_summary.setText(summary)
        ok = not (self._mode == batch.FONDO and not self._bg_path)
        self.btn_convert.setText(f"Convertir {len(paths)}")
        self.btn_convert.setEnabled(ok)

    def _open_advanced(self):
        dlg = AdvancedDialog(self._opts, self)
        if dlg.exec():
            self._opts = dlg.options()
            self._refresh_panel()

    def _reset_options(self):
        self._opts = SessionOptions()
        self._refresh_panel()

    # ================================================================ fondo
    def _load_background(self):
        last_dir = self.settings.value("last_dir", "")
        path, _ = QFileDialog.getOpenFileName(self, "Elegir fondo", last_dir, IMAGE_FILTER)
        if not path:
            return
        self.settings.setValue("last_dir", os.path.dirname(path))
        self._set_background(path)
        self._save_list()

    def _set_background(self, path: str | None):
        self._bg_path = path if path and os.path.exists(path) else None
        if self._bg_path:
            px = QPixmap(self._bg_path)
            self.lbl_bg_preview.setPixmap(px.scaled(
                236, 106, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:
            self.lbl_bg_preview.clear()
            self.lbl_bg_preview.setText("Sin fondo")
        self._refresh_panel()

    # ================================================================ convertir
    def _busy(self) -> bool:
        return self._convert_worker is not None

    def _ask_output_dir(self, first_path: str) -> str | None:
        # Se propone la carpeta del original: aceptar = el resultado queda al lado.
        start = os.path.dirname(first_path)
        path = QFileDialog.getExistingDirectory(self, "¿Dónde guardar lo convertido?", start)
        return path or None

    def _check_overwrite(self, names: list[str], out_dir: str) -> bool:
        existing = [n for n in names if os.path.exists(os.path.join(out_dir, n))]
        if not existing:
            return True
        detail = "\n".join(f"  • {n}" for n in existing[:5])
        if len(existing) > 5:
            detail += f"\n  … y {len(existing) - 5} más"
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Archivos repetidos")
        n = len(existing)
        msg.setText("Ya hay un archivo con el mismo nombre en esa carpeta:" if n == 1 else
                    f"Ya hay {n} archivos con el mismo nombre en esa carpeta:")
        msg.setInformativeText(f"{detail}\n\n¿Reemplazarlos?")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.No)
        return msg.exec() == QMessageBox.StandardButton.Yes

    def _convert(self):
        paths = self._selected_paths()
        if not paths or self._busy():
            return
        opts = self._batch_options()
        try:
            jobs = batch.plan(paths, self._mode, opts)
        except ValueError as e:
            self._show_status(str(e))
            return
        out_dir = self._ask_output_dir(paths[0])
        if not out_dir or not self._check_overwrite([j.out_name for j in jobs], out_dir):
            return
        self.start_jobs(jobs, out_dir, opts)

    def start_jobs(self, jobs, out_dir: str, opts: batch.Options):
        """Arranca el lote (separado de los diálogos para poder probarlo)."""
        paths = {p for j in jobs for p in j.inputs}
        for it in self._items():
            if it.data(PATH_ROLE) in paths:
                it.setData(STATE_ROLE, "working")
                it.setData(PROGRESS_ROLE, 0.0)
        self._jobs_total = len(jobs)
        self._jobs_done = 0
        self._last_out_dir = out_dir
        w = ConvertWorker(jobs, out_dir, opts, self)
        w.job_progress.connect(self._on_job_progress)
        w.job_done.connect(self._on_job_done)
        w.finished_all.connect(self._on_batch_done)
        self._convert_worker = w
        self._set_busy(True)
        w.start()

    def _set_busy(self, busy: bool):
        for b in (self.btn_load, self.btn_link, self.btn_clear, self.btn_advanced,
                  self.btn_load_bg):
            b.setEnabled(not busy)
        for b in self._mode_buttons.values():
            b.setEnabled(not busy)
        self.btn_convert.setVisible(not busy)
        self.btn_cancel.setVisible(busy)
        self.btn_cancel.setEnabled(True)
        self.progress.setVisible(busy)
        self.btn_open_out.setVisible(False)
        if busy:
            self.progress.setValue(0)
            self._show_status(f"Convirtiendo 1 de {self._jobs_total}…")
        self._update_count()

    def _show_status(self, text: str):
        self.lbl_status.setText(text)
        self.lbl_status.setVisible(bool(text))

    def _on_job_progress(self, job, frac: float):
        for p in job.inputs:
            it = self._item_for(p)
            if it is not None:
                it.setData(PROGRESS_ROLE, frac)
        overall = (self._jobs_done + frac) / max(1, self._jobs_total)
        self.progress.setValue(int(overall * 1000))
        self._show_status(f"Convirtiendo {self._jobs_done + 1} de {self._jobs_total}…")

    def _on_job_done(self, result):
        self._jobs_done += 1
        if result.error:
            state, tip = "error", result.error
        elif result.audio and result.audio.warnings:
            state, tip = "warning", "Listo, pero revisá el audio:\n" + "\n".join(result.audio.warnings)
        else:
            state, tip = "done", f"Listo: {os.path.basename(result.out_path)}"
            if result.audio and result.audio.before is not None:
                tip += "\nEl volumen quedó nivelado para la consola."
        for p in result.job.inputs:
            it = self._item_for(p)
            if it is not None:
                it.setData(STATE_ROLE, state)
                it.setToolTip(tip)

    def _on_batch_done(self, results: list, cancelled: bool):
        if self._convert_worker is not None:
            self._convert_worker.wait()
        self._convert_worker = None
        for it in self._items():   # lo que quedó sin terminar (cancelado)
            if it.data(STATE_ROLE) == "working" and it.data(PATH_ROLE):
                it.setData(STATE_ROLE, None)
        self._set_busy(False)
        ok = sum(1 for r in results if not r.error)
        err = len(results) - ok
        warn = sum(1 for r in results if not r.error and r.audio and r.audio.warnings)
        parts = [f"{ok} listo{'s' if ok != 1 else ''}"]
        if warn:
            parts.append(f"{warn} para revisar")
        if err:
            parts.append(f"{err} con error (pasá el mouse para ver el motivo)")
        prefix = "Cancelado. " if cancelled else ""
        self._show_status(prefix + " · ".join(parts) + ".")
        self.btn_open_out.setVisible(ok > 0)
        self._refresh_panel()
        if ok and not cancelled and not err and self.auto_open_folder:
            open_folder(self._last_out_dir)

    auto_open_folder = True   # los tests lo apagan

    def _cancel(self):
        if self._convert_worker is not None:
            self._convert_worker.cancel()
            self.btn_cancel.setEnabled(False)
            self._show_status("Cancelando…")

    def shutdown(self):
        """Corta lo que está corriendo (al cerrar la app)."""
        workers = self._thumb_workers + self._downloads
        if self._convert_worker is not None:
            workers.append(self._convert_worker)
        for w in workers:
            w.cancel()
        for w in workers:
            w.wait(5000)

    # ================================================================ estado y proyecto
    def _update_count(self):
        items = self._items()
        n_img = sum(1 for it in items if it.data(KIND_ROLE) == "image")
        n_vid = sum(1 for it in items if it.data(KIND_ROLE) == "video")
        parts = []
        if n_img:
            parts.append("1 foto" if n_img == 1 else f"{n_img} fotos")
        if n_vid:
            parts.append("1 video" if n_vid == 1 else f"{n_vid} videos")
        self.lbl_count.setText(" · ".join(parts))
        self.btn_clear.setEnabled(bool(items) and not self._busy())

    def _clear_list(self):
        """Vaciar la lista para arrancar de nuevo. No borra ningún archivo."""
        if not self.list_widget.count() or self._busy():
            return
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle("Vaciar la lista")
        msg.setText("¿Sacar todo de la lista?")
        msg.setInformativeText("Las fotos y los videos no se borran: siguen en su carpeta.")
        vaciar = msg.addButton("Vaciar", QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if msg.clickedButton() is vaciar:
            self.clear_list()

    def clear_list(self):
        self.list_widget.clear()
        self._pixmap_cache.clear()
        self._show_status("")
        self.btn_open_out.setVisible(False)
        self._update_count()
        self._refresh_panel()
        self._save_list()

    def _save_list(self):
        """Se guarda en cada cambio: si la app se cierra de golpe, la lista sigue."""
        self.settings.setValue("image_paths", json.dumps(self._paths()))
        self.settings.setValue("bg_path", self._bg_path or "")

    def save_state(self):
        self._save_list()
        self.settings.setValue("thumb_size", self.thumb_slider.value())

    def restore_state(self):
        saved_size = self.settings.value("thumb_size", type=int)
        if saved_size:
            self.thumb_slider.setValue(saved_size)
            self.list_widget.set_thumb_size(saved_size)
        raw = self.settings.value("image_paths")
        try:
            paths = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError):
            paths = []
        if paths:
            self._add_paths([p for p in paths if isinstance(p, str)], missing_ok=True)
        bg = self.settings.value("bg_path", "")
        if bg:
            self._set_background(bg)

    def confirm_close(self) -> bool:
        """Solo pregunta si hay una conversión en curso. Retorna False si se cancela."""
        if self._busy():
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("Hay una conversión en curso")
            msg.setText("Si cerrás ahora, se cancela la conversión.")
            msg.setStandardButtons(QMessageBox.StandardButton.Close | QMessageBox.StandardButton.Cancel)
            msg.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if msg.exec() != QMessageBox.StandardButton.Close:
                return False
        return True
