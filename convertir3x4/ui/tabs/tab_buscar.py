"""Pestaña 'Buscar': busca en el catálogo (FTS5 + substring), muestra miniaturas,
permite taggear por lote y arrastrar a ProPresenter (drag-out como link).

Lee/escribe el catálogo en el hilo de UI con su propia conexión.
"""

import os

from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidgetItem, QFrame,
)


class SearchLineEdit(QLineEdit):
    """Campo de búsqueda: botón X para limpiar + ESC vacía el campo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setClearButtonEnabled(True)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.text():
            self.clear()
            return
        super().keyPressEvent(event)

from ...appdata import db_path
from ...catalog.db import connect
from ...catalog.repo import Repo
from ..widgets.media_grid import DragOutListWidget, PATH_ROLE, MEDIA_ID_ROLE
from ..widgets.tag_bar import TagBar

_GRID_THUMB = 150


class BuscarTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._conn = None
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(200)
        self._debounce.timeout.connect(self._do_search)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        row = QHBoxLayout()
        self.txt_query = SearchLineEdit()
        self.txt_query.setPlaceholderText("Buscar por nombre, carpeta o tag…")
        self.txt_query.textChanged.connect(lambda _: self._debounce.start())
        row.addWidget(self.txt_query, 1)
        self.lbl_count = QLabel("")
        row.addWidget(self.lbl_count)
        layout.addLayout(row)

        self.grid = DragOutListWidget()
        self.grid.setIconSize(QSize(_GRID_THUMB, _GRID_THUMB))
        self.grid.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.grid, 1)

        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        self.tag_bar = TagBar()
        self.tag_bar.tagApplied.connect(self._apply_tag_to_selection)
        self.tag_bar.tagRemoved.connect(self._remove_tag_from_selection)
        layout.addWidget(self.tag_bar)

    # ------------------------------------------------------------- datos
    def _repo(self) -> Repo | None:
        if self._conn is None:
            try:
                self._conn = connect(db_path())
            except Exception:
                return None
        return Repo(self._conn)

    def refresh(self):
        """Reabre la conexión (para ver lo recién catalogado) y re-busca."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._do_search()
        self._reload_known_tags()

    def _do_search(self):
        repo = self._repo()
        if repo is None:
            self.lbl_count.setText("Sin catálogo todavía")
            self.grid.clear()
            self.tag_bar.set_enabled(False)
            return
        results = repo.search(self.txt_query.text())
        self._populate(results)

    def _populate(self, results):
        self.grid.clear()
        for m in results:
            item = QListWidgetItem(m.filename)
            item.setData(PATH_ROLE, m.path)
            item.setData(MEDIA_ID_ROLE, m.id)
            icon = self._icon_for(m)
            if icon:
                item.setIcon(icon)
            tip = m.folder_origin or ""
            if m.width and m.height:
                tip += f"\n{m.width}×{m.height}"
            if m.quality_flag and m.quality_flag != "ok":
                tip += f"\n⚠ {m.quality_flag}"
            item.setToolTip(tip.strip())
            self.grid.addItem(item)
        self.lbl_count.setText(f"{len(results)} resultados")
        self._on_selection_changed()

    def _icon_for(self, media) -> QIcon | None:
        thumb = media.thumb_path
        if thumb and os.path.exists(thumb):
            px = QPixmap(thumb)
            if not px.isNull():
                return QIcon(px)
        # Fallback: la imagen original (si existe)
        if media.kind == "image" and os.path.exists(media.path):
            px = QPixmap(media.path)
            if not px.isNull():
                return QIcon(px.scaled(
                    _GRID_THUMB, _GRID_THUMB,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
        return None

    # ------------------------------------------------------------- tags
    def _selected_ids(self) -> list[int]:
        ids = []
        for it in self.grid.selectedItems():
            mid = it.data(MEDIA_ID_ROLE)
            if mid is not None:
                ids.append(mid)
        return ids

    def _on_selection_changed(self):
        ids = self._selected_ids()
        repo = self._repo()
        self.tag_bar.set_enabled(bool(ids))
        if not ids or repo is None:
            self.tag_bar.set_chips([], 0)
            return
        self.tag_bar.set_chips(repo.tag_counts(ids), len(ids))

    def _apply_tag_to_selection(self, name: str):
        repo = self._repo()
        ids = self._selected_ids()
        if repo is None or not ids:
            return
        for mid in ids:
            repo.apply_tag(mid, name)
        self._reload_known_tags()
        self._on_selection_changed()

    def _remove_tag_from_selection(self, name: str):
        repo = self._repo()
        ids = self._selected_ids()
        if repo is None or not ids:
            return
        for mid in ids:
            repo.remove_tag(mid, name)
        self._on_selection_changed()

    def _reload_known_tags(self):
        repo = self._repo()
        if repo is not None:
            self.tag_bar.set_known_tags(repo.all_tag_names())
