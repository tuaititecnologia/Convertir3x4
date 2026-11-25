"""Aviso del estado de la conexión con el catálogo, igual al de la Grabadora.

- "conectando": recuadro azul con puntos animados.
- "error": recuadro naranja con el motivo y «Reintentar».
- "ok": no se muestra (el catálogo funciona y no hay nada que avisar).
"""

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton


class CajaConexion(QFrame):
    reintentar = pyqtSignal()
    configurar = pyqtSignal()          # abrir «Conexión al catálogo»

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)
        self.lbl = QLabel()
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl, 1)
        self.btn_retry = QPushButton("Reintentar")
        self.btn_retry.clicked.connect(self.reintentar.emit)
        lay.addWidget(self.btn_retry, 0, Qt.AlignmentFlag.AlignVCenter)
        self.btn_conexion = QPushButton("Conexión…")
        self.btn_conexion.setToolTip("Servidor, clave y carpeta multimedia de esta PC")
        self.btn_conexion.clicked.connect(self.configurar.emit)
        lay.addWidget(self.btn_conexion, 0, Qt.AlignmentFlag.AlignVCenter)
        self._puntos = 0
        self._anim = QTimer(self)
        self._anim.setInterval(450)
        self._anim.timeout.connect(self._animar)
        self.estado = ""
        self.set_estado("conectando")

    def set_estado(self, estado: str, texto: str = ""):
        self.estado = estado
        self.setObjectName(f"conn_{estado}")
        for w in (self, self.lbl):
            w.style().unpolish(w)
            w.style().polish(w)
        self.btn_retry.setVisible(estado == "error")
        self.btn_conexion.setVisible(estado == "error")
        self.setVisible(estado != "ok")
        if estado == "conectando":
            self._puntos = 0
            self._animar()
            self._anim.start()
        else:
            self._anim.stop()
            self.lbl.setText(texto)

    def _animar(self):
        self._puntos = (self._puntos % 3) + 1
        self.lbl.setText("Conectando con el catálogo" + "." * self._puntos)
