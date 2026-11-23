"""«Catalogar ▾» de la pestaña Catalogar (definido el 22/11/2026).

Hacer clic en «Catalogar» pide todo, como el viejo «Catalogar ahora». La flecha
abre una ventana para elegir qué se hace (casillas), dónde (todas las carpetas
o una sola) y si se rehace lo ya hecho. Sirve sobre todo para catalogar rápido
una carpeta recién copiada. Lo atiende el catalogador del NAS
(`catalogador.pasada`); acá solo se arma el pedido.
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QLabel, QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from ...catalog import rutas
from .. import icons

# Lo que se puede pedir, en palabras del operador, y cuánto tarda rehacerlo en
# todo el archivo (medido en el NAS, docs/catalogado-en-el-nas.md y plan-v0.8.md).
PASOS = (
    ("archivos", "Archivos nuevos, movidos o borrados",
     "Recorre las carpetas: agrega lo nuevo, sigue lo que se movió y marca lo que ya no "
     "está.", 15),
    ("miniaturas", "Miniaturas",
     "Rehace las miniaturas con una regla vieja y arma las vistas previas de los videos "
     "que no tienen.", 180),
    ("escenas", "Escenas",
     "Etiquetas que sugiere la IA según el tipo de foto (culto, alabanza, bautismo…).", 120),
    ("personas", "Personas",
     "Busca a las personas de la lista en las fotos y en los videos.", 60),
    ("texto", "Texto en las fotos",
     "Lee el texto que aparece en las fotos (carteles, flyers, pantallas) para poder "
     "encontrarlas buscándolo.", 105),
)


def _duracion(minutos: int) -> str:
    if minutos < 60:
        return f"unos {minutos} minutos"
    h, m = divmod(minutos, 60)
    if m >= 45:
        h, m = h + 1, 0
    medio = " y media" if 15 <= m < 45 else ""
    return f"una hora{medio}" if h == 1 else f"unas {h} horas{medio}"


class BotonCatalogar(QWidget):
    """«Catalogar» (todo) pegado a una flecha que abre la ventana de partes."""
    todo = pyqtSignal()
    partes = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(1)
        self.principal = QPushButton("Catalogar")
        self.principal.setObjectName("btn_primary")
        self.principal.setToolTip("Catalogar todo ahora")
        self.principal.clicked.connect(self.todo)
        self.flecha = QPushButton()
        self.flecha.setObjectName("btn_primary")
        icons.set_icon(self.flecha, "chevron-down", 16, "on_image", active="text_disabled")
        self.flecha.setFixedWidth(34)
        self.flecha.setToolTip("Elegir qué catalogar")
        self.flecha.setAccessibleName("Elegir qué catalogar")
        self.flecha.clicked.connect(self.partes)
        h.addWidget(self.principal)
        h.addWidget(self.flecha)

    def setEnabled(self, si: bool):        # noqa: N802 — como Qt
        self.principal.setEnabled(si)
        self.flecha.setEnabled(si)


class CatalogarPorPartes(QDialog):
    """La ventana de la flecha. Al aceptar: `pasos` (None = todo), `carpeta`
    (relativa, None = todas) y `rehacer`."""

    def __init__(self, parent=None, carpetas=(), raiz: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Catalogar")
        self.setMinimumWidth(520)
        self._carpetas = tuple(carpetas)
        self._raiz = raiz
        self.carpeta: str | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(8)

        t = QLabel("Qué hacer")
        t.setObjectName("section_title")
        lay.addWidget(t)
        self.chk_todo = QCheckBox("Todo")
        self.chk_todo.setChecked(True)
        self.chk_todo.toggled.connect(self._actualizar)
        lay.addWidget(self.chk_todo)
        self.casillas: dict[str, QCheckBox] = {}
        for clave, nombre, ayuda, _min in PASOS:
            c = QCheckBox(nombre)
            c.setToolTip(ayuda)
            c.toggled.connect(self._actualizar)
            fila = QVBoxLayout()
            fila.setSpacing(0)
            fila.addWidget(c)
            d = QLabel(ayuda)
            d.setObjectName("muted")
            d.setWordWrap(True)
            d.setContentsMargins(26, 0, 0, 4)
            fila.addWidget(d)
            lay.addLayout(fila)
            self.casillas[clave] = c
        lay.addSpacing(10)

        t = QLabel("Dónde")
        t.setObjectName("section_title")
        lay.addWidget(t)
        self.rb_todas = QRadioButton("Todas las carpetas que se catalogan")
        self.rb_una = QRadioButton("Una carpeta")
        self.rb_todas.setChecked(True)
        grupo = QButtonGroup(self)
        grupo.addButton(self.rb_todas)
        grupo.addButton(self.rb_una)
        lay.addWidget(self.rb_todas)
        fila = QHBoxLayout()
        fila.addWidget(self.rb_una)
        self.btn_carpeta = QPushButton("Elegir carpeta…")
        self.btn_carpeta.clicked.connect(self._elegir_carpeta)
        fila.addWidget(self.btn_carpeta)
        fila.addStretch()
        lay.addLayout(fila)
        self.lbl_carpeta = QLabel("")
        self.lbl_carpeta.setWordWrap(True)
        self.lbl_carpeta.setObjectName("muted")
        self.lbl_carpeta.setVisible(False)
        lay.addWidget(self.lbl_carpeta)
        self.rb_una.toggled.connect(self._actualizar)
        lay.addSpacing(10)

        self.chk_rehacer = QCheckBox("Rehacer también lo que ya está hecho")
        self.chk_rehacer.toggled.connect(self._actualizar)
        lay.addWidget(self.chk_rehacer)
        self.lbl_tiempo = QLabel("")
        self.lbl_tiempo.setWordWrap(True)
        self.lbl_tiempo.setContentsMargins(26, 0, 0, 0)
        lay.addWidget(self.lbl_tiempo)

        lay.addSpacing(10)
        botones = QDialogButtonBox()
        botones.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(
            self.reject)
        self.btn_ok = botones.addButton("Catalogar", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_ok.setObjectName("btn_primary")
        self.btn_ok.clicked.connect(self.accept)
        lay.addWidget(botones)
        self._actualizar()

    # ------------------------------------------------------------- lo elegido
    @property
    def pasos(self) -> list[str] | None:
        if self.chk_todo.isChecked():
            return None
        return [k for k, c in self.casillas.items() if c.isChecked()]

    @property
    def rehacer(self) -> bool:
        return self.chk_rehacer.isChecked()

    def carpeta_elegida(self) -> str | None:
        return self.carpeta if self.rb_una.isChecked() else None

    def elegir(self, rel: str | None):
        """Deja elegida una carpeta (relativa a la raíz), o vuelve a «todas»."""
        if rel is None:
            self.carpeta = None
            self.rb_todas.setChecked(True)
        else:
            self.carpeta = rel
            self.rb_una.setChecked(True)
        self._actualizar()

    # ------------------------------------------------------------- interacción
    def _elegir_carpeta(self):
        ruta = QFileDialog.getExistingDirectory(self, "Elija la carpeta del NAS", self._raiz)
        if not ruta:
            return
        rel = rutas.a_relativa(ruta, self._raiz) if self._raiz else None
        if rel is None or not any(rutas.dentro_de(rel, c) for c in self._carpetas):
            self.carpeta = None
            self.lbl_carpeta.setText("Esa carpeta no está entre las que se catalogan. Elija una "
                                     "carpeta dentro de: " + ", ".join(self._carpetas) + ".")
            self.lbl_carpeta.setVisible(True)
            self._actualizar()
            return
        self.elegir(rel)

    def _actualizar(self):
        todo = self.chk_todo.isChecked()
        for c in self.casillas.values():
            c.setEnabled(not todo)
            if todo:
                c.blockSignals(True)
                c.setChecked(False)
                c.blockSignals(False)
        if self.rb_una.isChecked() and self.carpeta:
            self.lbl_carpeta.setText(f"Carpeta: {self.carpeta}")
            self.lbl_carpeta.setVisible(True)
        elif self.rb_todas.isChecked() and self.carpeta is None:
            self.lbl_carpeta.setVisible(self.lbl_carpeta.isVisible()
                                        and "no está entre" in self.lbl_carpeta.text())
        hay_pasos = todo or any(c.isChecked() for c in self.casillas.values())
        hay_lugar = self.rb_todas.isChecked() or bool(self.carpeta)
        self.btn_ok.setEnabled(hay_pasos and hay_lugar)
        self._mostrar_tiempo(todo)

    def _mostrar_tiempo(self, todo: bool):
        if not self.chk_rehacer.isChecked():
            self.lbl_tiempo.setText("Sin marcar, solo se hace lo que falta (lo nuevo o lo que "
                                    "quedó de una versión anterior).")
            self.lbl_tiempo.setObjectName("muted")
        else:
            elegidos = [p for p in PASOS if todo or self.casillas[p[0]].isChecked()]
            minutos = sum(p[3] for p in elegidos)
            if self.rb_una.isChecked():
                texto = (f"Con todo el archivo tardaría {_duracion(minutos)}; con una carpeta, "
                         "mucho menos, según cuántas fotos y videos tenga.")
            else:
                texto = (f"Puede tardar {_duracion(minutos)}. Mientras tanto el NAS no atiende "
                         "otros pedidos de catalogar.") if minutos else ""
            self.lbl_tiempo.setText(texto)
            self.lbl_tiempo.setObjectName("nonstandard_text")
        self.lbl_tiempo.style().unpolish(self.lbl_tiempo)
        self.lbl_tiempo.style().polish(self.lbl_tiempo)
