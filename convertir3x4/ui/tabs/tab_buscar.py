"""Pestaña 'Buscar': busca en el catálogo (FTS5 + substring), muestra miniaturas,
permite taggear por lote y arrastrar a ProPresenter (drag-out como link).

Lee/escribe el catálogo en el hilo de UI con su propia conexión.
"""

import ntpath
import os
import re

from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QSizePolicy,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidgetItem, QFrame, QPushButton, QButtonGroup, QMessageBox,
)

from ...appdata import db_path
from ...catalog.db import connect
from ...catalog.repo import Repo
from ...catalog.ubicacion import availability, friendly_root, storage_root  # noqa: F401
from ..widgets.media_grid import (
    DragOutListWidget, PATH_ROLE, MEDIA_ID_ROLE, KIND_ROLE, DURATION_ROLE, STATE_ROLE,
)
from ..widgets.tag_bar import TagBar
from ...utils import open_file
from .. import icons
from ..style import T

_GRID_THUMB = 150


_QUALITY = {"low_res": "Baja resolución: puede verse borrosa en la pantalla.",
            "corrupt": "El archivo parece dañado."}


class SearchLineEdit(QLineEdit):
    """Campo de búsqueda: botón X para limpiar + ESC vacía el campo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("search_field")
        self.setClearButtonEnabled(True)
        lupa = self.addAction(QIcon(), QLineEdit.ActionPosition.LeadingPosition)
        icons.set_icon(lupa, "search", 16, "text_3")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.text():
            self.clear()
            return
        super().keyPressEvent(event)


class BuscarTab(QWidget):
    # Rutas elegidas en el catálogo para convertir (MainWindow las pasa a Convertir)
    enviarAConvertir = pyqtSignal(list)

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
        self.txt_query.setPlaceholderText("Buscar por nombre, carpeta o etiqueta…")
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
        icons.set_icon(self.chk_pending, "sparkles", 15, "text_2", active="ia")
        self.chk_pending.setToolTip("Mostrar solo los elementos con etiquetas sugeridas por la IA pendientes de revisión")
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
        self.grid.itemDoubleClicked.connect(self._open_item)
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._menu_grilla)
        layout.addWidget(self.grid, 1)

        # Tags de la selección, en un panel abajo
        panel = QFrame()
        panel.setObjectName("panel")
        p_lay = QVBoxLayout(panel)
        p_lay.setContentsMargins(14, 10, 14, 10)
        # Lo elegido en el catálogo se manda a Convertir sin buscarlo de nuevo
        acciones = QHBoxLayout()
        acciones.setSpacing(10)
        self.lbl_seleccion = QLabel("")
        self.lbl_seleccion.setObjectName("muted")
        acciones.addWidget(self.lbl_seleccion, 1)
        self.btn_convertir = QPushButton("Enviar a Convertir")
        self.btn_convertir.setObjectName("btn_primary")
        icons.set_icon(self.btn_convertir, "wand-sparkles", 15, "on_image")
        self.btn_convertir.setToolTip("Agrega los elementos seleccionados a la grilla de Convertir (Ctrl+Enter)")
        self.btn_convertir.clicked.connect(self._mandar_a_convertir)
        acciones.addWidget(self.btn_convertir)
        p_lay.addLayout(acciones)
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
            self.grid.set_empty_text("Todavía no hay catálogo.\nVaya a Catalogar y elija una carpeta con fotos y videos.")
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
                       "Verifique que el NAS o el disco estén conectados. Mientras tanto, no "
                       "puede usarse en ProPresenter.")
            item.setToolTip(tip.strip())
            self.grid.addItem(item)
        n = len(results)
        self.lbl_count.setText("1 resultado" if n == 1 else f"{n} resultados")
        if self.txt_query.text().strip() or self.chk_pending.isChecked():
            filtro = self._kind_group.checkedButton()
            if filtro is not None and filtro.property("kind"):
                self.grid.set_empty_text("No hay nada que coincida con la búsqueda.\n"
                                         "Pruebe con otra palabra o elija «Todo» arriba.")
            else:
                self.grid.set_empty_text("No hay nada que coincida con la búsqueda.\n"
                                         "Pruebe con otra palabra o con parte del nombre.")
        else:
            self.grid.set_empty_text("No hay nada de este tipo en el catálogo.\n"
                                     "Elija «Todo» arriba para ver el resto.")
        self._on_selection_changed()

    def _open_item(self, item):
        """Doble clic: el video se ve en el reproductor de la PC (la foto, en su visor)."""
        path = item.data(PATH_ROLE)
        if path and not open_file(path):
            QMessageBox.information(
                self, "No se puede abrir",
                f"No se encuentra {os.path.basename(path)}.\n"
                "Verifique que el NAS o el disco donde está guardado estén conectados.")

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
            text = (f"{cuantos} {verbo} en {donde}, que no está conectado. Se muestran las "
                    "miniaturas, pero no pueden usarse en ProPresenter hasta que se conecte.")
        else:
            verbo = "ya no está" if n == 1 else "ya no están"
            text = (f"{cuantos} {verbo} en su carpeta: pueden haberse movido o borrado. "
                    "Pase el puntero sobre cada elemento para ver su ubicación anterior.")
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

    # ------------------------------------------------------------- a Convertir
    def _elegidos_disponibles(self) -> tuple[list[str], int]:
        """Rutas elegidas que se pueden convertir, y cuántas no están (disco o
        NAS desconectado, archivo movido)."""
        paths, faltan = [], 0
        for it in self.grid.selectedItems():
            p = it.data(PATH_ROLE)
            if not p:
                continue
            if it.data(STATE_ROLE) in ("missing", "offline") or not os.path.exists(p):
                faltan += 1
            else:
                paths.append(p)
        return paths, faltan

    def _mandar_a_convertir(self):
        paths, faltan = self._elegidos_disponibles()
        if faltan:
            self.lbl_seleccion.setText(
                ("1 elemento no está disponible y no se envió" if faltan == 1 else
                 f"{faltan} elementos no están disponibles y no se enviaron")
                + ". Verifique que el disco o el NAS estén conectados.")
        if paths:
            self.enviarAConvertir.emit(paths)

    def _menu_grilla(self, pos):
        from PyQt6.QtWidgets import QMenu
        if not self.grid.selectedItems():
            return
        menu = QMenu(self)
        accion = menu.addAction("Enviar a Convertir")
        accion.setEnabled(self.btn_convertir.isEnabled())
        if menu.exec(self.grid.viewport().mapToGlobal(pos)) is accion:
            self._mandar_a_convertir()

    def _actualizar_seleccion(self):
        paths, faltan = self._elegidos_disponibles()
        n = len(self.grid.selectedItems())
        self.btn_convertir.setEnabled(bool(paths))
        if not n:
            self.lbl_seleccion.setText("Seleccione fotos o videos para enviarlos a Convertir o asignarles etiquetas.")
        else:
            txt = "1 seleccionado" if n == 1 else f"{n} seleccionados"
            if faltan:
                txt += " · 1 no está disponible" if faltan == 1 else f" · {faltan} no están disponibles"
            self.lbl_seleccion.setText(txt)

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._mandar_a_convertir()
            return
        super().keyPressEvent(event)

    def _on_selection_changed(self):
        self._actualizar_seleccion()
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
