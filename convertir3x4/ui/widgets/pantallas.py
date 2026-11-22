"""Pantallas guardadas de Convertir (pedido del 15/11/2026, definido el 22/11).

En lugar de escribir «Otra…» cada vez, una lista de pantallas con nombre y
medida («Evento de primavera · 4,5 × 1,2 m»), compartida por las PCs (tabla
`pantalla` del catálogo). Las de siempre (8:9 del LED…) están en el código y no
se tocan. La medida puede ser en metros, centímetros o píxeles: lo que importa
es la proporción. Se eligen en el ⚙ de Convertir; acá se crean, cambian y quitan.
"""

from fractions import Fraction

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)
from PyQt6.QtCore import Qt

from ..servicio_catalogo import texto_para
from .dialogos import confirmar


def proporcion(ancho: float, alto: float) -> tuple[int, int]:
    """4,5 × 1,2 → (15, 4). Se redondea a números chicos (hasta 100), como
    los que se escriben en «Otra…»."""
    r = (Fraction(str(ancho)) / Fraction(str(alto))).limit_denominator(100)
    while r.numerator > 100:                     # muy alargada: se achica la precisión
        r = Fraction(round(r.numerator / 2), max(1, round(r.denominator / 2)))
    return r.numerator, r.denominator


def _medida(v: float) -> str:
    return f"{v:g}".replace(".", ",")


def describir(p: dict) -> str:
    """«Evento de primavera · 4,5 × 1,2 (15:4)»."""
    w, h = proporcion(p["ancho"], p["alto"])
    medida = f"{_medida(p['ancho'])} × {_medida(p['alto'])}"
    return f"{p['nombre']} · {medida}" + ("" if medida == f"{w} × {h}" else f" ({w}:{h})")


class EditarPantalla(QDialog):
    """Nombre y medida de una pantalla (nueva o existente)."""

    def __init__(self, parent=None, nombre: str = "", ancho: float = 16, alto: float = 9):
        super().__init__(parent)
        self.setWindowTitle("Pantalla")
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        form = QFormLayout()
        self.txt_nombre = QLineEdit(nombre)
        self.txt_nombre.setPlaceholderText("Por ejemplo: Evento de primavera")
        form.addRow("Nombre", self.txt_nombre)
        self.spin_ancho = self._spin(ancho, "Ancho de la pantalla")
        self.spin_alto = self._spin(alto, "Alto de la pantalla")
        fila = QHBoxLayout()
        fila.addWidget(self.spin_ancho)
        fila.addWidget(QLabel("×"))
        fila.addWidget(self.spin_alto)
        fila.addStretch()
        form.addRow("Medida", fila)
        lay.addLayout(form)
        ayuda = QLabel("En metros, centímetros o píxeles: lo que importa es la proporción.")
        ayuda.setObjectName("muted")
        ayuda.setWordWrap(True)
        lay.addWidget(ayuda)
        self.lbl_prop = QLabel("")
        lay.addWidget(self.lbl_prop)
        botones = QDialogButtonBox()
        self.btn_ok = botones.addButton("Guardar", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_ok.setObjectName("btn_primary")
        botones.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)
        lay.addWidget(botones)
        self.txt_nombre.textChanged.connect(self._actualizar)
        self.spin_ancho.valueChanged.connect(self._actualizar)
        self.spin_alto.valueChanged.connect(self._actualizar)
        self._actualizar()

    @staticmethod
    def _spin(valor, nombre):
        s = QDoubleSpinBox()
        s.setDecimals(2)
        s.setRange(0.01, 100000)
        s.setValue(valor)
        s.setAccessibleName(nombre)
        return s

    def _actualizar(self, *_):
        w, h = proporcion(self.spin_ancho.value(), self.spin_alto.value())
        self.lbl_prop.setText(f"Proporción {w}:{h}")
        self.btn_ok.setEnabled(bool(self.txt_nombre.text().strip()))

    def datos(self) -> tuple[str, float, float]:
        return (" ".join(self.txt_nombre.text().split()), self.spin_ancho.value(),
                self.spin_alto.value())


class AdministrarPantallas(QDialog):
    """Lista de pantallas guardadas: agregar, cambiar y quitar. Cada cambio se
    guarda enseguida en el catálogo (lo ve la otra PC)."""

    def __init__(self, srv, parent=None):
        super().__init__(parent)
        self._srv = srv
        self.cambio = False
        self.setWindowTitle("Pantallas guardadas")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(8)
        ayuda = QLabel("Las pantallas guardadas aparecen en «Proporción» de las opciones "
                       "avanzadas, en las dos PCs.")
        ayuda.setObjectName("muted")
        ayuda.setWordWrap(True)
        lay.addWidget(ayuda)
        self.lista = QListWidget()
        self.lista.setAccessibleName("Pantallas guardadas")
        self.lista.itemSelectionChanged.connect(self._botones)
        self.lista.itemDoubleClicked.connect(lambda _it: self._editar())
        lay.addWidget(self.lista)
        fila = QHBoxLayout()
        self.btn_agregar = QPushButton("Agregar…")
        self.btn_agregar.clicked.connect(self._agregar)
        self.btn_editar = QPushButton("Cambiar…")
        self.btn_editar.clicked.connect(self._editar)
        self.btn_quitar = QPushButton("Quitar…")
        self.btn_quitar.clicked.connect(self._quitar)
        for b in (self.btn_agregar, self.btn_editar, self.btn_quitar):
            fila.addWidget(b)
        fila.addStretch()
        lay.addLayout(fila)
        self.lbl_estado = QLabel("")
        self.lbl_estado.setWordWrap(True)
        lay.addWidget(self.lbl_estado)
        botones = QDialogButtonBox()
        botones.addButton("Cerrar", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(
            self.reject)
        lay.addWidget(botones)
        self._botones()
        self.releer()

    def releer(self):
        self._srv.pedir(lambda repo: repo.pantallas(), ok=self._mostrar,
                        error=lambda e: self.lbl_estado.setText(texto_para(e)))

    def _mostrar(self, pantallas):
        self.lista.clear()
        for p in pantallas:
            it = QListWidgetItem(describir(p))
            it.setData(Qt.ItemDataRole.UserRole, p)
            self.lista.addItem(it)
        if not pantallas:
            self.lbl_estado.setText("Todavía no hay pantallas guardadas.")
        self._botones()

    def _elegida(self) -> dict | None:
        its = self.lista.selectedItems()
        return its[0].data(Qt.ItemDataRole.UserRole) if its else None

    def _botones(self):
        hay = self._elegida() is not None
        self.btn_editar.setEnabled(hay)
        self.btn_quitar.setEnabled(hay)

    def _agregar(self):
        d = EditarPantalla(self)
        if d.exec():
            self.guardar(*d.datos())

    def _editar(self):
        p = self._elegida()
        if p is None:
            return
        d = EditarPantalla(self, p["nombre"], p["ancho"], p["alto"])
        if d.exec():
            self.guardar(*d.datos(), pantalla_id=p["id"])

    def guardar(self, nombre, ancho, alto, pantalla_id=None):
        def ok(_id):
            self.cambio = True
            self.lbl_estado.setText(f"Se guardó «{nombre}».")
            self.releer()

        def error(e):
            self.lbl_estado.setText(
                f"Ya hay una pantalla «{nombre}». Elija otro nombre." if "ya hay" in str(e)
                else texto_para(e))
        self._srv.pedir(lambda repo: repo.guardar_pantalla(nombre, ancho, alto, pantalla_id),
                        ok=ok, error=error)

    def _quitar(self):
        p = self._elegida()
        if p is None or not confirmar(self, "Quitar pantalla", f"¿Quitar «{p['nombre']}»?",
                                      "Deja de aparecer en las dos PCs. Lo ya convertido no "
                                      "cambia.", "Quitar", peligro=True):
            return

        def ok(_):
            self.cambio = True
            self.lbl_estado.setText(f"Se quitó «{p['nombre']}».")
            self.releer()
        self._srv.pedir(lambda repo: repo.quitar_pantalla(p["id"]), ok=ok,
                        error=lambda e: self.lbl_estado.setText(texto_para(e)))
