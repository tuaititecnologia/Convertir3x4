"""Pestaña 'Buscar': busca en el catálogo (FTS5 + substring), muestra miniaturas,
permite taggear por lote y arrastrar a ProPresenter (drag-out como link).

Lee/escribe el catálogo en el hilo de UI con su propia conexión.
"""

import ntpath
import os
import re

from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QSizePolicy,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidgetItem, QFrame, QPushButton, QButtonGroup,
)

from ...appdata import db_path
from ...catalog.db import connect
from ...catalog.repo import Repo
from ..widgets.media_grid import (
    DragOutListWidget, PATH_ROLE, MEDIA_ID_ROLE, KIND_ROLE, DURATION_ROLE, STATE_ROLE,
)
from ..widgets.tag_bar import TagBar
from .. import icons
from ..style import T

_GRID_THUMB = 150


def storage_root(path: str) -> str:
    """Raíz de la ubicación de un archivo: el recurso del NAS, el disco o el volumen.

    \\\\NAS\\fotos\\a\\b.jpg -> \\\\NAS\\fotos   ·   D:\\x\\y.jpg -> D:\\
    /Volumes/SSD/x/y.jpg -> /Volumes/SSD   ·   /home/u/x.jpg -> /
    """
    # Rutas de Windows (unidad o recurso de red), con \\ o con /: ntpath las
    # entiende en cualquier sistema.
    if re.match(r"^[A-Za-z]:[\\/]", path) or path.startswith(("\\\\", "//")):
        drive, _ = ntpath.splitdrive(path)
        return drive + ("\\" if len(drive) == 2 else "")
    parts = path.split("/")
    if len(parts) > 2 and parts[1] in ("Volumes", "media", "mnt"):
        return "/".join(parts[:3])
    return "/"


_QUALITY = {"low_res": "Baja resolución: puede verse borrosa en la pantalla.",
            "corrupt": "El archivo parece dañado."}


def friendly_root(root: str) -> str:
    """Cómo nombra el operador una ubicación: "el NAS", "el disco D:", "el disco «SSD»"."""
    if root.startswith(("\\\\", "//")):
        return "el NAS"
    if re.match(r"^[A-Za-z]:", root):
        return f"el disco {root[:2].upper()}"
    if root.startswith(("/Volumes/", "/media/", "/mnt/")):
        return f"el disco «{root.rstrip('/').rsplit('/', 1)[-1]}»"
    return "esta PC"


def availability(paths: list[str]) -> dict[str, bool]:
    """Qué archivos están, sin congelar la app si el NAS está desconectado.

    En Windows, preguntar por un archivo de una carpeta de red caída puede
    tardar segundos POR ARCHIVO. Se pregunta una vez por cada raíz; solo si la
    raíz responde se pregunta archivo por archivo.
    """
    roots: dict[str, bool] = {}
    out = {}
    for p in paths:
        r = storage_root(p)
        if r not in roots:
            roots[r] = os.path.exists(r)
        out[p] = roots[r] and os.path.exists(p)
    return out


class SearchLineEdit(QLineEdit):
    """Campo de búsqueda: botón X para limpiar + ESC vacía el campo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("search_field")
        self.setClearButtonEnabled(True)
        self.addAction(icons.icon("search", 16, T.text_3),
                       QLineEdit.ActionPosition.LeadingPosition)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.text():
            self.clear()
            return
        super().keyPressEvent(event)


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
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(14)

        # Barra superior: buscador · filtro por tipo · sugerencias IA · contador
        row = QHBoxLayout()
        row.setSpacing(12)
        self.txt_query = SearchLineEdit()
        self.txt_query.setPlaceholderText("Buscar por nombre, carpeta o tag…")
        self.txt_query.setAccessibleName("Buscar en el catálogo")
        self.txt_query.textChanged.connect(lambda _: self._debounce.start())
        row.addWidget(self.txt_query, 1)

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
        self._kind_group.buttonClicked.connect(lambda _: self._apply_kind_filter())
        row.addWidget(seg)

        self.chk_pending = QPushButton("Sugerencias de la IA")
        self.chk_pending.setObjectName("toggle_chip")
        self.chk_pending.setCheckable(True)
        self.chk_pending.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chk_pending.setIcon(icons.icon("sparkles", 15, T.text_2, active_color=T.ia))
        self.chk_pending.setToolTip("Mostrar solo lo que tiene tags propuestos por la IA sin revisar")
        self.chk_pending.toggled.connect(lambda _: self._do_search())
        row.addWidget(self.chk_pending)

        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("muted")
        self.lbl_count.setMinimumWidth(96)
        self.lbl_count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.lbl_count)
        layout.addLayout(row)

        # Un solo aviso con la causa, en vez de repetirla en cada miniatura
        self.offline_box = QFrame()
        self.offline_box.setObjectName("nonstandard")
        ob = QVBoxLayout(self.offline_box)
        ob.setContentsMargins(12, 8, 12, 8)
        ob.setSpacing(2)
        t = QLabel("No se encuentran los originales")
        t.setObjectName("nonstandard_title")
        ob.addWidget(t)
        self.lbl_offline = QLabel("")
        self.lbl_offline.setObjectName("nonstandard_text")
        self.lbl_offline.setWordWrap(True)
        ob.addWidget(self.lbl_offline)
        self.offline_box.setVisible(False)
        layout.addWidget(self.offline_box)

        self.grid = DragOutListWidget(thumb=_GRID_THUMB)
        self.grid.setAccessibleName("Resultados")
        self.grid.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.grid, 1)

        # Tags de la selección, en un panel abajo
        panel = QFrame()
        panel.setObjectName("panel")
        p_lay = QVBoxLayout(panel)
        p_lay.setContentsMargins(14, 10, 14, 10)
        self.tag_bar = TagBar()
        self.tag_bar.tagApplied.connect(self._apply_tag_to_selection)
        self.tag_bar.tagRemoved.connect(self._remove_tag_from_selection)
        self.tag_bar.tagConfirmed.connect(self._confirm_tag_on_selection)
        p_lay.addWidget(self.tag_bar)
        layout.addWidget(panel)

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
            self.lbl_count.setText("")
            self.grid.clear()
            self.grid.set_empty_text("Todavía no hay catálogo.\nAndá a Catalogar y elegí una carpeta con fotos y videos.")
            self.tag_bar.set_enabled(False)
            return
        self._results = repo.search(self.txt_query.text(),
                                    pending_only=self.chk_pending.isChecked())
        self._apply_kind_filter()

    def _apply_kind_filter(self):
        btn = self._kind_group.checkedButton()
        kind = btn.property("kind") if btn else None
        results = [m for m in getattr(self, "_results", []) if kind is None or m.kind == kind]
        self._populate(results)

    def _populate(self, results):
        self.grid.clear()
        present = availability([m.path for m in results])
        roots_ok: dict[str, bool] = {}
        self._show_offline([m.path for m in results if not present[m.path]])
        for m in results:
            item = QListWidgetItem(m.filename)
            item.setData(PATH_ROLE, m.path)
            item.setData(MEDIA_ID_ROLE, m.id)
            item.setData(KIND_ROLE, m.kind)
            item.setData(DURATION_ROLE, m.duration)
            if not present[m.path]:
                # No se puede arrastrar a ProPresenter. Si falta el disco/NAS
                # entero, el aviso de arriba lo explica una vez: solo se atenúa.
                # Si el disco está y el archivo no, la insignia va en la celda.
                root_ok = roots_ok.setdefault(storage_root(m.path),
                                              os.path.exists(storage_root(m.path)))
                item.setData(STATE_ROLE, "missing" if root_ok else "offline")
            icon = self._icon_for(m)
            if icon:
                item.setIcon(icon)
            tip = m.folder_origin or ""
            if m.width and m.height:
                tip += f"\n{m.width}×{m.height}"
            if m.quality_flag and m.quality_flag != "ok":
                tip += "\n" + _QUALITY.get(m.quality_flag, "")
            if not present[m.path]:
                tip = (f"No se encuentra el original:\n{m.path}\n"
                       "¿Se desconectó el NAS o el disco? Así no se puede llevar a ProPresenter.")
            item.setToolTip(tip.strip())
            self.grid.addItem(item)
        n = len(results)
        self.lbl_count.setText("1 resultado" if n == 1 else f"{n} resultados")
        if self.txt_query.text().strip() or self.chk_pending.isChecked():
            self.grid.set_empty_text("No hay nada que coincida con la búsqueda.")
        else:
            self.grid.set_empty_text("No hay nada de este tipo en el catálogo.")
        self._on_selection_changed()

    def _show_offline(self, missing: list[str]):
        if not missing:
            self.offline_box.setVisible(False)
            return
        roots = sorted({storage_root(p) for p in missing if not os.path.exists(storage_root(p))})
        n = len(missing)
        cuantos = "1 resultado" if n == 1 else f"{n} resultados"
        if roots:
            donde = " y ".join(friendly_root(r) for r in roots)
            verbo = "está" if n == 1 else "están"
            text = (f"{cuantos} {verbo} en {donde}, que no está conectado. Se ven las miniaturas, "
                    "pero no se pueden llevar a ProPresenter hasta que se conecte.")
        else:
            verbo = "ya no está" if n == 1 else "ya no están"
            text = (f"{cuantos} {verbo} en su carpeta (¿se movieron o se borraron?). "
                    "Pasá el mouse por arriba para ver dónde estaban.")
        self.lbl_offline.setText(text)
        self.offline_box.setVisible(True)

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
        self.tag_bar.set_chips(repo.tag_counts(ids), len(ids),
                               repo.pending_tag_counts(ids))

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

    def _confirm_tag_on_selection(self, name: str):
        repo = self._repo()
        ids = self._selected_ids()
        if repo is None or not ids:
            return
        for mid in ids:
            repo.confirm_tag(mid, name)
        self._on_selection_changed()

    def _reload_known_tags(self):
        repo = self._repo()
        if repo is not None:
            self.tag_bar.set_known_tags(repo.all_tag_names())
