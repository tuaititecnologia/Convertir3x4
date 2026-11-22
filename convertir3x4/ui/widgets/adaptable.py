"""Dos partes lado a lado si el ancho alcanza, o una debajo de la otra.

Lo usan la Grabadora (historial y detalle de la copia) y Catalogar (personas y
el detalle de sus fotos).
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QBoxLayout, QWidget

HISTERESIS = 24            # margen para no saltar de un modo al otro en el límite


class Adaptable(QWidget):
    """Dos partes lado a lado si el ancho alcanza; si no, una debajo de la otra.
    Decide por el ancho propio (no el de la ventana), así sirve en cualquier lugar."""
    cambio = pyqtSignal(bool)        # True: lado a lado

    def __init__(self, umbral: int, espacio: int = 16, espacio_vertical: int | None = None,
                 por_su_ancho: bool = True, parent=None):
        super().__init__(parent)
        self.umbral = umbral
        self.por_su_ancho = por_su_ancho
        self.espacio = (espacio, espacio if espacio_vertical is None else espacio_vertical)
        self.lado_a_lado = True
        self.lay = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(espacio)

    def agregar(self, w: QWidget, estirar: int = 0):
        self.lay.addWidget(w, estirar)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.por_su_ancho:
            self.ajustar(event.size().width())

    def ajustar(self, ancho: int):
        """Elige el modo para `ancho`. Con margen: si está lado a lado, recién pasa
        a apilado por debajo del umbral; para volver, tiene que sobrar HISTERESIS."""
        lado = ancho >= self.umbral + (0 if self.lado_a_lado else HISTERESIS)
        if lado != self.lado_a_lado:
            self.lado_a_lado = lado
            self.lay.setDirection(QBoxLayout.Direction.LeftToRight if lado
                                  else QBoxLayout.Direction.TopToBottom)
            self.lay.setSpacing(self.espacio[0] if lado else self.espacio[1])
            self.cambio.emit(lado)


def vaciar(grid):
    """Saca todo de una grilla ya mismo: si solo se borra «después», lo viejo se
    sigue dibujando encima de lo nuevo un instante."""
    while grid.count():
        w = grid.takeAt(0).widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
