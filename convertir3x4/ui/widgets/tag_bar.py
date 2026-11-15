"""Barra de tags estilo Filament/Netbird para taggeo por lote.

Un campo de texto con autocompletado contra los tags existentes: si lo que
escribís ya existe, lo elegís; si no, al confirmar lo crea. Arriba, chips con
los tags de la selección actual, cada uno removible. Normaliza
(minúsculas/espacios) antes de emitir.

El área de chips tiene ALTURA FIJA reservada y scroll horizontal si hay muchos:
así el input nunca se solapa con los chips (un FlowLayout de altura dinámica
daba overlap en macOS) y el layout es 100% predecible.

Los tags que propuso la IA y nadie confirmó se ven violeta y punteados, con
dos botones: el tag (confirma) y ✕ (lo saca, y la IA no lo vuelve a poner).

El widget NO toca la base: emite `tagApplied(str)` / `tagRemoved(str)` /
`tagConfirmed(str)` y la
pestaña los aplica vía el repo. La pestaña le alimenta el vocabulario con
`set_known_tags()` y los chips con `set_chips()`.
"""

from PyQt6.QtCore import Qt, QStringListModel, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel,
    QPushButton, QCompleter, QScrollArea,
)

_CHIP_HEIGHT = 28
_CHIPS_AREA_HEIGHT = 44  # altura reservada (chip + aire arriba/abajo)


class TagBar(QWidget):
    tagApplied = pyqtSignal(str)
    tagRemoved = pyqtSignal(str)
    tagConfirmed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selection_total = 0
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 4)
        layout.setSpacing(8)

        # Área de chips: altura fija + scroll horizontal. La altura reservada
        # garantiza que el input de abajo nunca se solape con los chips.
        self._chips_area = QScrollArea()
        self._chips_area.setFixedHeight(_CHIPS_AREA_HEIGHT)
        self._chips_area.setWidgetResizable(True)
        self._chips_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self._chips_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._chips_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._chips_area.viewport().setStyleSheet("background: transparent;")

        self._chips_inner = QWidget()
        self._chips_row = QHBoxLayout(self._chips_inner)
        self._chips_row.setContentsMargins(0, 0, 0, 0)
        self._chips_row.setSpacing(6)
        self._chips_row.addStretch()
        self._chips_area.setWidget(self._chips_inner)
        layout.addWidget(self._chips_area)

        # Input con autocompletado
        row = QHBoxLayout()
        self.lbl = QLabel("Tag:")
        row.addWidget(self.lbl)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Escribí un tag y Enter (lo crea si no existe)…")
        self._completer = QCompleter(self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer_model = QStringListModel(self)
        self._completer.setModel(self._completer_model)
        self.input.setCompleter(self._completer)
        self.input.returnPressed.connect(self._on_return)
        row.addWidget(self.input, 1)
        layout.addLayout(row)

        self.set_enabled(False)

    # ------------------------------------------------------------- API
    def set_enabled(self, on: bool):
        self.input.setEnabled(on)
        self.lbl.setText("Tag:" if on else "Seleccioná fotos para taggear")

    def set_known_tags(self, names: list[str]):
        self._completer_model.setStringList(names)

    def set_chips(self, tag_counts: list[tuple[str, int]], total: int,
                  pending: dict[str, int] | None = None):
        """Pinta los chips de la selección. `total` = cuántas fotos seleccionadas.

        `pending`: tag -> en cuántas de la selección está sin confirmar (IA).
        """
        pending = pending or {}
        self._selection_total = total
        # Limpiar chips previos (todos menos el stretch final). setParent(None)
        # los saca de la vista YA (deleteLater es asíncrono y dejaría fantasmas).
        while self._chips_row.count() > 1:
            item = self._chips_row.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        # Insertar chips antes del stretch final
        for i, (name, count) in enumerate(tag_counts):
            if pending.get(name):
                chip = self._make_ia_chip(name, count, total)
            else:
                chip = self._make_chip(name, count, total)
            self._chips_row.insertWidget(i, chip)

    def _make_chip(self, name: str, count: int, total: int) -> QPushButton:
        partial = total > 1 and count < total
        text = f"{name} ({count}/{total}) ✕" if partial else f"{name} ✕"
        chip = QPushButton(text)
        chip.setObjectName("tag_chip_partial" if partial else "tag_chip")
        chip.setFixedHeight(_CHIP_HEIGHT)
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.setToolTip(
            f"En {count} de {total} seleccionadas — clic para quitarlo de todas"
            if partial else "Clic para quitar el tag"
        )
        chip.clicked.connect(lambda _=False, n=name: self.tagRemoved.emit(n))
        return chip

    def _make_ia_chip(self, name: str, count: int, total: int) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        suffix = f" ({count}/{total})" if total > 1 and count < total else ""
        ok = QPushButton(f"✨ {name}{suffix} ✓")
        ok.setObjectName("tag_chip_ia")
        ok.setFixedHeight(_CHIP_HEIGHT)
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setToolTip("Sugerido por la IA — clic para confirmarlo")
        ok.clicked.connect(lambda _=False, n=name: self.tagConfirmed.emit(n))
        x = QPushButton("✕")
        x.setObjectName("tag_chip_ia_x")
        x.setFixedHeight(_CHIP_HEIGHT)
        x.setCursor(Qt.CursorShape.PointingHandCursor)
        x.setToolTip("No corresponde — quitarlo (la IA no lo vuelve a sugerir)")
        x.clicked.connect(lambda _=False, n=name: self.tagRemoved.emit(n))
        row.addWidget(ok)
        row.addWidget(x)
        return box

    def _on_return(self):
        raw = self.input.text()
        norm = " ".join(raw.strip().lower().split())
        if not norm:
            return
        self.input.clear()
        self.tagApplied.emit(norm)
