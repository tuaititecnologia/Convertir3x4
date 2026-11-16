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
    QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
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

        note = QLabel("Estos cambios se mantienen hasta cerrar la aplicación. "
                      "Al volver a abrirla, se restablecen los valores estándar.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addSpacing(10)

        lay.addWidget(self._title("Proporción"))
        self.combo_ratio = QComboBox()
        self.combo_ratio.setAccessibleName("Proporción")
        for label, w, h in ASPECT_RATIOS:
            self.combo_ratio.addItem(label, (w, h))
        # Para pantallas que no son de ninguna medida conocida (p. ej. la pantalla
        # alargada del evento de primavera, 15/11/2026): se escribe ancho y alto.
        self.combo_ratio.addItem("Otra…", None)
        lay.addWidget(self.combo_ratio)

        self.otra = QWidget()
        fila = QHBoxLayout(self.otra)
        fila.setContentsMargins(0, 0, 0, 0)
        fila.setSpacing(8)
        fila.addWidget(QLabel("Ancho"))
        self.spin_w = QSpinBox()
        self.spin_w.setRange(1, 100)
        self.spin_w.setAccessibleName("Ancho de la pantalla")
        fila.addWidget(self.spin_w)
        fila.addWidget(QLabel(":"))
        fila.addWidget(QLabel("Alto"))
        self.spin_h = QSpinBox()
        self.spin_h.setRange(1, 100)
        self.spin_h.setAccessibleName("Alto de la pantalla")
        fila.addWidget(self.spin_h)
        fila.addStretch()
        lay.addWidget(self.otra)
        lay.addWidget(self._help(
            "La pantalla de la iglesia usa 8:9. Cámbiela solo si el material es para otra "
            "pantalla. Para otra medida, elija «Otra…» e indique el ancho y el alto (en "
            "metros, centímetros o píxeles: lo que importa es la proporción)."))
        self.combo_ratio.currentIndexChanged.connect(self._on_ratio)
        self._mostrar_aspecto(self._opts.aspect)
        lay.addSpacing(10)

        lay.addWidget(self._title("Fotos"))
        self.chk_video = QCheckBox("Exportar las fotos como video de 30 segundos, con fondo en movimiento")
        self.chk_video.setChecked(self._opts.photos_as_video)
        lay.addWidget(self.chk_video)
        lay.addSpacing(10)

        lay.addWidget(self._title("Carpeta de descargas"))
        row = QHBoxLayout()
        self.txt_dir = QLineEdit(self._opts.download_dir)
        self.txt_dir.setAccessibleName("Carpeta de descargas")
        row.addWidget(self.txt_dir, 1)
        b = QPushButton("Elegir carpeta…")
        b.clicked.connect(self._browse)
        row.addWidget(b)
        lay.addLayout(row)
        lay.addWidget(self._help("Carpeta donde se guardan los videos descargados de internet."))
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

    def _mostrar_aspecto(self, aspect):
        for i in range(self.combo_ratio.count()):
            if self.combo_ratio.itemData(i) == tuple(aspect):
                self.combo_ratio.setCurrentIndex(i)
                break
        else:
            self.combo_ratio.setCurrentIndex(self.combo_ratio.count() - 1)   # Otra…
        self.spin_w.setValue(aspect[0])
        self.spin_h.setValue(aspect[1])
        self._on_ratio()

    def _on_ratio(self, *_):
        self.otra.setVisible(self.combo_ratio.currentData() is None)

    def _reset(self):
        std = SessionOptions()
        self._mostrar_aspecto(std.aspect)
        self.chk_video.setChecked(std.photos_as_video)
        self.txt_dir.setText(std.download_dir)

    def _aspecto(self) -> tuple[int, int]:
        elegido = self.combo_ratio.currentData()
        if elegido is not None:
            return tuple(elegido)
        from math import gcd
        w, h = self.spin_w.value(), self.spin_h.value()
        g = gcd(w, h)
        return (w // g, h // g)

    def options(self) -> SessionOptions:
        return SessionOptions(
            aspect=self._aspecto(),
            photos_as_video=self.chk_video.isChecked(),
            download_dir=self.txt_dir.text().strip() or default_download_dir(),
        )
