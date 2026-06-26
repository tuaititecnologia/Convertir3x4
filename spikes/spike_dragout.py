"""
Spike Fase 0 — Drag-OUT de un archivo real desde PyQt6 hacia ProPresenter.

OBJETIVO (riesgo técnico nº1, bloqueante del plan):
    Validar que un QDrag con QMimeData.setUrls([QUrl.fromLocalFile(ruta)]) es
    aceptado por ProPresenter como un archivo LINKEADO (no copiado), con el
    setting "Manage Media Automatically" en OFF.

Esta es la mecánica exacta que después vivirá en ui/widgets/media_grid.py.

CÓMO USARLO
    1. Correr:   python spikes/spike_dragout.py [ruta_opcional_a_imagen_o_video]
       (sin argumento, abre un selector de archivo)
    2. Aparece una ventana con el archivo como ítem arrastrable.
    3. Arrastrar el ítem y soltarlo dentro de ProPresenter.
    4. Verificar en el inspector de medios de PP que el archivo quedó LINKEADO
       a su ruta original (no copiado a la carpeta Media del workspace).

SANITY-CHECK EN MAC (sin PP): arrastrar el ítem a Finder. Debería ofrecer
copiar/mover el archivo real — eso confirma que la plomería de QUrl funciona,
independiente de ProPresenter.

VALIDACIÓN REAL: se hace en la PC Windows con ProPresenter, porque el
comportamiento de drop de PP puede diferir entre plataformas.
"""

import os
import sys

from PyQt6.QtCore import Qt, QMimeData, QUrl, QSize
from PyQt6.QtGui import QDrag, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QFileDialog, QAbstractItemView,
)

PATH_ROLE = Qt.ItemDataRole.UserRole


class DragOutList(QListWidget):
    """QListWidget que arrastra el archivo real hacia fuera (otras apps)."""

    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setIconSize(QSize(160, 160))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    def startDrag(self, supportedActions):
        items = self.selectedItems()
        if not items:
            return
        urls = [QUrl.fromLocalFile(it.data(PATH_ROLE)) for it in items]

        mime = QMimeData()
        mime.setUrls(urls)

        drag = QDrag(self)
        drag.setMimeData(mime)

        # Vista previa que sigue al cursor mientras se arrastra.
        icon = items[0].icon()
        if not icon.isNull():
            drag.setPixmap(icon.pixmap(128, 128))

        # Ofrecemos Copy y Link; el destino (PP) elige. Con "Manage Media
        # Automatically" en OFF, PP debería linkear.
        drag.exec(
            Qt.DropAction.CopyAction | Qt.DropAction.LinkAction,
            Qt.DropAction.LinkAction,
        )


class SpikeWindow(QMainWindow):
    def __init__(self, initial_path=None):
        super().__init__()
        self.setWindowTitle("Spike drag-out → ProPresenter")
        self.resize(520, 480)

        central = QWidget()
        layout = QVBoxLayout(central)

        info = QLabel(
            "Arrastrá el ítem de abajo hacia ProPresenter.\n"
            "Verificá en PP que el archivo quede LINKEADO (no copiado a Media).\n\n"
            "Sanity-check en Mac: arrastralo a Finder."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.list = DragOutList()
        layout.addWidget(self.list, 1)

        btn = QPushButton("Elegir archivo…")
        btn.clicked.connect(self._pick_file)
        layout.addWidget(btn)

        self.setCentralWidget(central)

        if initial_path and os.path.isfile(initial_path):
            self._add_file(initial_path)

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Elegí un archivo para arrastrar", "",
            "Medios (*.jpg *.jpeg *.png *.bmp *.webp *.mp4 *.mov);;Todos (*.*)",
        )
        if path:
            self._add_file(path)

    def _add_file(self, path):
        item = QListWidgetItem(os.path.basename(path))
        item.setData(PATH_ROLE, path)
        pix = QPixmap(path)
        if not pix.isNull():
            item.setIcon(QIcon(pix))
        self.list.addItem(item)


def main():
    app = QApplication(sys.argv)
    initial = sys.argv[1] if len(sys.argv) > 1 else None
    win = SpikeWindow(initial)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
