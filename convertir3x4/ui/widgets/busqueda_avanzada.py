"""Formulario de búsqueda avanzada de Buscar, como el de Gmail (24/11/2026).

Se abre con el botón de la derecha del campo de búsqueda. Al abrirse se llena
con lo que ya está escrito; «Buscar» escribe en el campo la búsqueda armada
(`etiqueta:placa tipo:video …`), así el operador ve y aprende cómo se escribe.
Lo que el formulario no tiene (p. ej. `-carpeta:Entrada`) queda en «Contiene
las palabras», tal cual: nada de lo escrito se pierde.
"""

from __future__ import annotations

from PyQt6.QtCore import QStringListModel, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from ...catalog import consulta
from ...catalog.consulta import Consulta, Filtro, analizar, armar, periodo_de

_SIMPLES = ("carpeta", "nombre", "texto", "notas", "desde", "hasta")
_CASILLAS = (("tiene", "texto"), ("tiene", "notas"), ("sin", "etiquetas"))
ANCHO_MIN = 640


class _CompletarLista(QCompleter):
    """Completa la última de una lista separada por comas («placa, santa c…»)."""

    def splitPath(self, path: str) -> list[str]:
        return [path.split(",")[-1].strip()]

    def pathFromIndex(self, index) -> str:
        antes = self.widget().text().split(",")[:-1]
        return ", ".join([a.strip() for a in antes if a.strip()] + [index.data()])


class FormularioBusqueda(QFrame):
    buscar = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("busqueda_avanzada")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(ANCHO_MIN)
        self._etiquetas = QStringListModel(self)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(12)
        titulo = QLabel("Búsqueda avanzada")
        titulo.setObjectName("section_title")
        lay.addWidget(titulo)

        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(8)
        g.setColumnStretch(1, 1)
        fila = 0

        def campo(texto: str, ayuda: str, vacio: str = "") -> QLineEdit:
            nonlocal fila
            e = QLineEdit()
            e.setPlaceholderText(vacio)
            e.setToolTip(ayuda)
            e.returnPressed.connect(self._buscar)
            e.textChanged.connect(self._revisar)
            self._fila(g, fila, texto, e)
            fila += 1
            return e

        self.txt_palabras = campo(
            "Contiene las palabras", "Palabras del nombre, la carpeta, las etiquetas, las "
            "notas o el texto de la foto. Con un guion adelante se excluye: -ensayo")
        self.txt_etiquetas = campo("Con las etiquetas", "Todas las etiquetas escritas, "
                                   "separadas por comas", "placa, santa cena")
        self.txt_sin_etiquetas = campo("Sin las etiquetas", "Ninguna de estas etiquetas, "
                                       "separadas por comas")
        for e in (self.txt_etiquetas, self.txt_sin_etiquetas):
            c = _CompletarLista(self._etiquetas, e)
            c.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            c.setFilterMode(Qt.MatchFlag.MatchContains)
            e.setCompleter(c)

        self.cmb_tipo = QComboBox()
        for clave, texto in ((None, "Fotos y videos"), ("image", "Solo fotos"),
                             ("video", "Solo videos")):
            self.cmb_tipo.addItem(texto, clave)
        self._fila(g, fila, "Tipo", self.cmb_tipo, ancho_natural=True)
        fila += 1
        self.txt_carpeta = campo("Carpeta", "Parte del nombre de la carpeta", "Reuniones")
        self.txt_nombre = campo("Nombre del archivo", "Parte del nombre del archivo",
                                "DSC_0001")
        self.txt_texto = campo("Texto en la foto", "Lo que dice algún cartel o pantalla "
                               "de la foto")
        self.txt_notas = campo("Notas", "Parte de las notas escritas en los detalles")

        fechas = QHBoxLayout()
        fechas.setSpacing(8)
        self.txt_desde, self.txt_hasta = QLineEdit(), QLineEdit()
        for e, texto in ((self.txt_desde, "desde"), (self.txt_hasta, "hasta")):
            e.setPlaceholderText("dd/mm/aaaa")
            e.setToolTip("Un día (21/09/2025), un mes (09/2025) o un año (2025). "
                         "Es la fecha en que se tomó la foto o el video.")
            e.returnPressed.connect(self._buscar)
            e.textChanged.connect(self._revisar)
            lbl = QLabel(texto)
            lbl.setObjectName("muted")
            fechas.addWidget(lbl)
            fechas.addWidget(e, 1)
        k = QLabel("Fecha de la toma")
        k.setObjectName("muted")
        g.addWidget(k, fila, 0)
        g.addLayout(fechas, fila, 1)
        fila += 1

        casillas = QHBoxLayout()
        casillas.setSpacing(18)
        self.chk_texto = QCheckBox("Con texto en la foto")
        self.chk_notas = QCheckBox("Con notas")
        self.chk_sin_etiquetas = QCheckBox("Sin etiquetas")
        for c in (self.chk_texto, self.chk_notas, self.chk_sin_etiquetas):
            casillas.addWidget(c)
        casillas.addStretch(1)
        g.addLayout(casillas, fila, 1)
        lay.addLayout(g)

        self.lbl_aviso = QLabel("")
        self.lbl_aviso.setObjectName("aviso_error")
        self.lbl_aviso.setWordWrap(True)
        self.lbl_aviso.setVisible(False)
        lay.addWidget(self.lbl_aviso)

        pie = QHBoxLayout()
        pie.setSpacing(10)
        ayuda = QLabel("También puede escribirlo en el campo, por ejemplo: "
                       "etiqueta:placa tipo:video desde:09/2025")
        ayuda.setObjectName("muted")
        ayuda.setWordWrap(True)
        pie.addWidget(ayuda, 1)
        self.btn_borrar = QPushButton("Borrar")
        self.btn_borrar.setToolTip("Vacía el formulario (no busca todavía)")
        self.btn_borrar.clicked.connect(lambda: self.cargar(""))
        pie.addWidget(self.btn_borrar)
        self.btn_buscar = QPushButton("Buscar")
        self.btn_buscar.setObjectName("btn_primary")
        self.btn_buscar.setDefault(True)
        self.btn_buscar.clicked.connect(self._buscar)
        pie.addWidget(self.btn_buscar)
        lay.addLayout(pie)

    @staticmethod
    def _fila(g: QGridLayout, fila: int, texto: str, w, ancho_natural: bool = False):
        k = QLabel(texto)
        k.setObjectName("muted")
        g.addWidget(k, fila, 0)
        if ancho_natural:
            g.addWidget(w, fila, 1, Qt.AlignmentFlag.AlignLeft)
        else:
            g.addWidget(w, fila, 1)

    # ------------------------------------------------------------- datos
    def set_etiquetas(self, nombres: list[str]):
        self._etiquetas.setStringList(sorted(nombres))

    def cargar(self, texto: str):
        """Llena el formulario con lo escrito en el campo de búsqueda."""
        c = analizar(texto)
        resto: list[Filtro] = []
        con, sin, simples, casillas = [], [], {}, set()
        tipo = None
        for f in c.filtros:
            if f.clave == "etiqueta":
                (sin if f.negado else con).append(f.valor)
            elif f.clave == "tipo" and not f.negado:
                tipo = consulta.TIPOS[consulta._simple(f.valor)]
            elif f.clave in _SIMPLES and not f.negado and f.clave not in simples:
                simples[f.clave] = f.valor
            elif (f.clave, consulta.TIENE.get(consulta._simple(f.valor))) in _CASILLAS \
                    and not f.negado:
                casillas.add((f.clave, consulta.TIENE[consulta._simple(f.valor)]))
            else:
                resto.append(f)
        # Una fecha mal escrita va a su cuadro, en rojo, para corregirla ahí
        invalidos = []
        for bruto in c.invalidos:
            clave, _, valor = bruto.partition(":")
            clave = consulta.CLAVES.get(consulta._simple(clave))
            if clave in ("desde", "hasta") and clave not in simples:
                simples[clave] = consulta.terminos(valor)
            else:
                invalidos.append(bruto)
        palabras = armar(Consulta(c.palabras, c.excluidas, resto))
        self.txt_palabras.setText(" ".join(p for p in [palabras] + invalidos if p))
        self.txt_etiquetas.setText(", ".join(con))
        self.txt_sin_etiquetas.setText(", ".join(sin))
        self.cmb_tipo.setCurrentIndex(max(0, self.cmb_tipo.findData(tipo)))
        for clave in _SIMPLES:
            getattr(self, "txt_" + clave).setText(simples.get(clave, ""))
        self.chk_texto.setChecked(("tiene", "texto") in casillas)
        self.chk_notas.setChecked(("tiene", "notas") in casillas)
        self.chk_sin_etiquetas.setChecked(("sin", "etiquetas") in casillas)
        self._revisar()

    def texto(self) -> str:
        """La búsqueda armada con lo que dice el formulario."""
        base = analizar(self.txt_palabras.text())
        filtros: list[Filtro] = []

        def lista(e: QLineEdit) -> list[str]:
            return [" ".join(v.split()) for v in e.text().split(",") if v.strip()]

        filtros += [Filtro("etiqueta", v) for v in lista(self.txt_etiquetas)]
        filtros += [Filtro("etiqueta", v, True) for v in lista(self.txt_sin_etiquetas)]
        tipo = self.cmb_tipo.currentData()
        if tipo:
            filtros.append(Filtro("tipo", consulta.NOMBRE_TIPO[tipo]))
        for clave in _SIMPLES:
            v = " ".join(getattr(self, "txt_" + clave).text().split())
            if v and (clave not in consulta.FECHAS or periodo_de(v)):
                filtros.append(Filtro(clave, v))
        for chk, (clave, valor) in zip((self.chk_texto, self.chk_notas,
                                        self.chk_sin_etiquetas), _CASILLAS):
            if chk.isChecked():
                filtros.append(Filtro(clave, valor))
        armado = armar(Consulta(base.palabras, base.excluidas, filtros + base.filtros))
        return " ".join(p for p in [armado] + base.invalidos if p)

    def _revisar(self, *_):
        malas = []
        for e in (self.txt_desde, self.txt_hasta):
            mala = bool(e.text().strip()) and periodo_de(e.text()) is None
            if mala:
                malas.append(e.text().strip())
            if bool(e.property("error")) != mala:
                e.setProperty("error", mala)
                e.style().unpolish(e)
                e.style().polish(e)
        if malas:
            self.lbl_aviso.setText(f"La fecha «{malas[0]}» no se entiende: escríbala como "
                                   "21/09/2025, 09/2025 o 2025.")
        self.lbl_aviso.setVisible(bool(malas))
        self.btn_buscar.setEnabled(not malas)

    def _buscar(self):
        if self.btn_buscar.isEnabled():
            self.buscar.emit(self.texto())
            self.close()

    # ------------------------------------------------------------- mostrar
    def abrir_debajo_de(self, campo: QLineEdit, texto: str):
        self.cargar(texto)
        self.setFixedWidth(max(ANCHO_MIN, min(campo.width(), 760)))
        self.adjustSize()
        abajo = campo.mapToGlobal(campo.rect().bottomLeft())
        self.move(abajo.x(), abajo.y() + 4)
        self.show()
        self.txt_palabras.setFocus()
