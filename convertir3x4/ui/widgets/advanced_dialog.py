"""Opciones avanzadas de Convertir: escondidas a propósito.

Decisión del 15/11/2026 (docs/convertir-fotos-y-videos.md): lo que casi nunca se
toca no va en el panel, para que un operador sin experiencia no cambie por
accidente lo que tiene que quedar igual todos los domingos.

- Se abren desde el ⚙, en esta ventana aparte.
- Lo que se cambia acá dura hasta cerrar la app: `SessionOptions` no se guarda.
- El nivel de audio (-16 LUFS) no está acá: es una constante (convert/clips.py),
  porque la consola se calibra una sola vez contra ese valor.
"""

import os
from dataclasses import dataclass, field, replace

from PyQt6.QtCore import QStandardPaths, Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from ...config import ASPECT_RATIOS, DEFAULT_ASPECT_H, DEFAULT_ASPECT_W


def default_download_dir() -> str:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    return os.path.join(base or os.path.expanduser("~"), "CDLM Videos")


@dataclass
class SessionOptions:
    aspect: tuple[int, int] = (DEFAULT_ASPECT_W, DEFAULT_ASPECT_H)
    photos_as_video: bool = False
    download_dir: str = field(default_factory=default_download_dir)

    def changes(self) -> list[str]:
        """Lo que está fuera de lo estándar, en palabras cortas (para el aviso naranja)."""
        std = SessionOptions()
        out = []
        if self.aspect != std.aspect:
            out.append(f"Proporción {self.aspect[0]}:{self.aspect[1]}")
        if self.photos_as_video != std.photos_as_video:
            out.append("Fotos como video")
        if os.path.normpath(self.download_dir) != os.path.normpath(std.download_dir):
            out.append("Otra carpeta de descargas")
        return out


class AdvancedDialog(QDialog):
    def __init__(self, opts: SessionOptions, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Opciones avanzadas")
        self.setMinimumWidth(460)
        self._opts = replace(opts)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(8)

        note = QLabel("Lo que cambies acá vale hasta cerrar la app. "
                      "Al volver a abrirla, todo vuelve a lo estándar.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addSpacing(10)

        lay.addWidget(self._title("Proporción"))
        self.combo_ratio = QComboBox()
        self.combo_ratio.setAccessibleName("Proporción")
        for label, w, h in ASPECT_RATIOS:
            self.combo_ratio.addItem(label, (w, h))
            if (w, h) == self._opts.aspect:
                self.combo_ratio.setCurrentIndex(self.combo_ratio.count() - 1)
        lay.addWidget(self.combo_ratio)
        lay.addWidget(self._help("La pantalla de la iglesia usa 8:9. Cambiala solo si lo convertido "
                                "va a otro lado."))
        lay.addSpacing(10)

        lay.addWidget(self._title("Fotos"))
        self.chk_video = QCheckBox("Sacar las fotos como video de 30 segundos, con el fondo en movimiento")
        self.chk_video.setChecked(self._opts.photos_as_video)
        lay.addWidget(self.chk_video)
        lay.addSpacing(10)

        lay.addWidget(self._title("Carpeta de descargas"))
        row = QHBoxLayout()
        self.txt_dir = QLineEdit(self._opts.download_dir)
        self.txt_dir.setAccessibleName("Carpeta de descargas")
        row.addWidget(self.txt_dir, 1)
        b = QPushButton("Elegir…")
        b.clicked.connect(self._browse)
        row.addWidget(b)
        lay.addLayout(row)
        lay.addWidget(self._help("Dónde quedan los videos que se bajan de internet."))
        lay.addSpacing(14)

        buttons = QDialogButtonBox()
        self.btn_reset = buttons.addButton("Volver a lo estándar", QDialogButtonBox.ButtonRole.ResetRole)
        ok = buttons.addButton("Aceptar", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setObjectName("btn_primary")
        ok.setDefault(True)
        buttons.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_reset.clicked.connect(self._reset)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    @staticmethod
    def _title(text):
        lbl = QLabel(text)
        lbl.setObjectName("section_title")
        return lbl

    @staticmethod
    def _help(text):
        lbl = QLabel(text)
        lbl.setObjectName("muted")
        lbl.setWordWrap(True)
        return lbl

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Carpeta de descargas", self.txt_dir.text())
        if d:
            self.txt_dir.setText(d)

    def _reset(self):
        std = SessionOptions()
        for i in range(self.combo_ratio.count()):
            if self.combo_ratio.itemData(i) == std.aspect:
                self.combo_ratio.setCurrentIndex(i)
        self.chk_video.setChecked(std.photos_as_video)
        self.txt_dir.setText(std.download_dir)

    def options(self) -> SessionOptions:
        return SessionOptions(
            aspect=tuple(self.combo_ratio.currentData()),
            photos_as_video=self.chk_video.isChecked(),
            download_dir=self.txt_dir.text().strip() or default_download_dir(),
        )
