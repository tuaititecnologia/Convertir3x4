"""Pestaña 'Buscar': busca en el catálogo (FTS5) y muestra miniaturas.

Lee el catálogo en el hilo de UI con su propia conexión. El drag-OUT hacia
ProPresenter (spike ya validado) se suma en la Fase 3.
"""

import os

from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QAbstractItemView,
)

from ...appdata import db_path
from ...catalog.db import connect
from ...catalog.repo import Repo
from ..widgets.media_grid import PATH_ROLE

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
        self.txt_query = QLineEdit()
        self.txt_query.setPlaceholderText("Buscar por nombre, carpeta o tag…")
        self.txt_query.textChanged.connect(lambda _: self._debounce.start())
        row.addWidget(self.txt_query, 1)
        self.lbl_count = QLabel("")
        row.addWidget(self.lbl_count)
        layout.addLayout(row)

        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setIconSize(QSize(_GRID_THUMB, _GRID_THUMB))
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.grid.setSpacing(6)
        self.grid.setWordWrap(True)
        layout.addWidget(self.grid)

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

    def _do_search(self):
        repo = self._repo()
        if repo is None:
            self.lbl_count.setText("Sin catálogo todavía")
            self.grid.clear()
            return
        results = repo.search(self.txt_query.text())
        self._populate(results)

    def _populate(self, results):
        self.grid.clear()
        for m in results:
            item = QListWidgetItem(m.filename)
            item.setData(PATH_ROLE, m.path)
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
