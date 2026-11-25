"""Las carpetas del NAS en árbol (docs/carpetas-y-conexion.md).

El árbol lo arma el NAS: el catalogador lee las carpetas de `multimedia` y las
deja en la base (`carpeta_nas`). Esta ventana nunca mira el disco de la PC, así
que se ve igual en Windows, Mac o Linux, esté donde esté montado el share.

- `ElegirCarpetas`: qué se cataloga. Una casilla por carpeta con el estado de
  esa carpeta (☑ se cataloga con sus fotos sueltas, ☐ no). Tildar o destildar
  cambia también lo de adentro; con el filtro puesto, solo lo que se ve.
  «Guardar» muestra un resumen y, si algo catalogado queda afuera, pregunta si
  se elimina del catálogo.
- `ElegirUnaCarpeta`: una sola carpeta para catalogar ahora («Catalogar ▾»).
  Solo las que se catalogan.

Al abrir pide al NAS que relea las carpetas y muestra enseguida lo que ya hay;
cuando el NAS termina (aviso `carpetas_nas_leidas`, ui/avisos_catalogo.py) se
redibuja sin perder lo desplegado ni lo que se cambió sin guardar.
"""

import datetime
import socket

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from ...catalog.alcance import Alcance, Arbol, nombre
from ..avisos_catalogo import hub
from .dialogos import confirmar
from ..servicio_catalogo import servicio, texto_para

RUTA = Qt.ItemDataRole.UserRole
SIN_RESPUESTA_MS = 10_000       # sin aviso del NAS en este tiempo: «El NAS no respondió»
RAIZ = "Toda la carpeta multimedia"


def _cantidad(fotos: int, videos: int) -> str:
    partes = []
    if fotos:
        partes.append("1 foto" if fotos == 1 else f"{fotos} fotos")
    if videos:
        partes.append("1 video" if videos == 1 else f"{videos} videos")
    return " y ".join(partes)


def _cuando(ts) -> str:
    if ts is None:
        return ""
    local = ts.astimezone()
    hoy = datetime.datetime.now().astimezone().date()
    if local.date() == hoy:
        return f"hoy a las {local:%H:%M}"
    return f"el {local:%d/%m} a las {local:%H:%M}"


class _ArbolBase(QDialog):
    def __init__(self, parent=None, srv=None, titulo: str = "", ayuda: str = ""):
        super().__init__(parent)
        self.setWindowTitle(titulo)
        self.resize(640, 620)
        self._srv = srv or servicio()
        self._arbol: Arbol | None = None
        self._leidas = None
        self._items: dict[str, QTreeWidgetItem] = {}
        self._visibles: set[str] | None = None
        self._pintando = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(8)
        if ayuda:
            lbl = QLabel(ayuda)
            lbl.setObjectName("muted")
            lbl.setWordWrap(True)
            lay.addWidget(lbl)

        fila = QHBoxLayout()
        self.txt_filtro = QLineEdit()
        self.txt_filtro.setPlaceholderText("Buscar carpeta por nombre")
        self.txt_filtro.setClearButtonEnabled(True)
        self.txt_filtro.setAccessibleName("Buscar carpeta por nombre")
        self.txt_filtro.textChanged.connect(self._filtrar)
        fila.addWidget(self.txt_filtro, 1)
        self.btn_desplegar = QPushButton("Desplegar todo")
        self.btn_desplegar.clicked.connect(self._desplegar_todo)
        fila.addWidget(self.btn_desplegar)
        self.btn_plegar = QPushButton("Plegar todo")
        self.btn_plegar.clicked.connect(self._plegar_todo)
        fila.addWidget(self.btn_plegar)
        lay.addLayout(fila)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Carpeta", "En el catálogo"])
        self.tree.setAccessibleName("Carpetas del NAS")
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setUniformRowHeights(True)
        lay.addWidget(self.tree, 1)

        fila = QHBoxLayout()
        self.lbl_lectura = QLabel("Pidiendo las carpetas al NAS…")
        self.lbl_lectura.setObjectName("muted")
        self.lbl_lectura.setWordWrap(True)
        fila.addWidget(self.lbl_lectura, 1)
        self.btn_actualizar = QPushButton("Actualizar")
        self.btn_actualizar.setToolTip("Vuelve a pedir al NAS la lista de carpetas")
        self.btn_actualizar.clicked.connect(self.actualizar)
        fila.addWidget(self.btn_actualizar)
        lay.addLayout(fila)
        self._lay = lay

        self._sin_respuesta = QTimer(self)
        self._sin_respuesta.setSingleShot(True)
        self._sin_respuesta.setInterval(SIN_RESPUESTA_MS)
        self._sin_respuesta.timeout.connect(self._nas_no_respondio)
        hub().carpetas.connect(self._nas_leyo)
        self._esperando = False

    # ------------------------------------------------------------- datos
    def actualizar(self):
        """Pide al NAS que relea las carpetas y dibuja lo que ya hay mientras
        tanto, sin dejar tocarlo hasta que llegue la lectura nueva (o se vea que
        el NAS no responde): el árbol puede cambiar debajo del operador."""
        self._esperando = True
        self._bloquear()
        self._sin_respuesta.start()
        self._srv.pedir(lambda repo: repo.pedir_carpetas_nas(), ok=lambda _: None,
                        error=self._error)
        self._consultar()

    def _consultar(self):
        self._srv.pedir(lambda repo: repo.arbol_carpetas(), ok=self._cargar, error=self._error)

    def _nas_leyo(self):
        try:
            if not self.isVisible() and not self._esperando:
                return
        except RuntimeError:
            return              # la ventana ya se cerró
        self._sin_respuesta.stop()
        self._srv.pedir(lambda repo: repo.arbol_carpetas(), ok=self._cargar_lectura_nueva,
                        error=self._error)

    def _cargar_lectura_nueva(self, datos: dict):
        self._esperando = False
        self._cargar(datos)

    def _nas_no_respondio(self):
        self._esperando = False
        self._bloquear()
        self._mostrar_lectura(no_respondio=True)

    def _error(self, e):
        self._sin_respuesta.stop()
        self._esperando = False
        self._bloquear()
        self.lbl_lectura.setText(texto_para(e))

    def _bloquear(self):
        """Mientras se espera al NAS no se puede tocar nada del árbol."""
        libre = not self._esperando and self._arbol is not None
        for w in (self.tree, self.txt_filtro, self.btn_desplegar, self.btn_plegar):
            w.setEnabled(libre)
        self.btn_actualizar.setEnabled(not self._esperando)
        self._botones()

    def _botones(self):
        """Para las subclases: Guardar / Elegir."""

    def _marcas_actuales(self, datos) -> dict[str, bool]:
        return datos["marcas"]

    def _cargar(self, datos: dict):
        desplegadas = {r for r, it in self._items.items() if it.isExpanded()}
        actual = self.tree.currentItem()
        elegida = actual.data(0, RUTA) if actual is not None else None
        scroll = self.tree.verticalScrollBar().value()
        primera = self._arbol is None
        self._leidas = datos["leidas"]
        self._arbol = Arbol(datos["carpetas"], self._marcas_actuales(datos), datos["conteos"])
        self._construir()
        if primera:
            # La raíz desplegada; y las de primer nivel con excepciones adentro
            self._items[""].setExpanded(True)
            contrarias = self._arbol.contrarias_adentro()
            for c in self._arbol.hijos[""]:
                if contrarias.get(c):
                    self._items[c].setExpanded(True)
        else:
            for r in desplegadas:
                if r in self._items:
                    self._items[r].setExpanded(True)
            if elegida in self._items:
                self.tree.setCurrentItem(self._items[elegida])
            self.tree.verticalScrollBar().setValue(scroll)
        self._filtrar(self.txt_filtro.text())
        self._mostrar_lectura()
        self._cargado()
        self._bloquear()

    def _cargado(self):
        """Para las subclases: después de cada carga."""

    def _mostrar_lectura(self, no_respondio: bool = False):
        if self._arbol is None:
            return
        cuando = _cuando(self._leidas)
        if not self._leidas:
            texto = ("El NAS todavía no leyó las carpetas. Se muestran solo las que ya "
                     "estaban elegidas.")
        elif no_respondio:
            texto = f"El NAS no respondió. Se muestran las carpetas leídas {cuando}."
        elif self._esperando:
            texto = f"Pidiendo las carpetas al NAS… (se muestran las leídas {cuando})"
        else:
            texto = f"Carpetas del NAS leídas {cuando}."
        self.lbl_lectura.setText(texto)

    # ------------------------------------------------------------- dibujo
    def _construir(self):
        self._pintando = True
        self.tree.clear()
        self._items = {}
        for c in self._arbol.carpetas():
            it = QTreeWidgetItem([RAIZ if not c else nombre(c), ""])
            it.setData(0, RUTA, c)
            if not c:
                self.tree.addTopLevelItem(it)
            else:
                self._items[c.rsplit("/", 1)[0] if "/" in c else ""].addChild(it)
            self._items[c] = it
            self._preparar_item(it, c)
        self._pintar()
        self._pintando = False

    def _preparar_item(self, it: QTreeWidgetItem, carpeta: str):
        """Para las subclases: casilla, si se puede elegir…"""

    def _texto_info(self, carpeta: str, contrarias: dict[str, int]) -> str:
        partes = []
        if carpeta and carpeta not in self._arbol.en_nas:
            partes.append("No está en el NAS")
        cant = _cantidad(*self._arbol.totales(carpeta))
        if cant:
            partes.append(cant)
        return " · ".join(partes)

    def _pintar(self):
        contrarias = self._arbol.contrarias_adentro()
        for c, it in self._items.items():
            it.setText(1, self._texto_info(c, contrarias))

    # ------------------------------------------------------------- filtro
    def _filtrar(self, texto: str):
        if self._arbol is None:
            return
        self._visibles = self._arbol.filtrar(texto)
        for c, it in self._items.items():
            it.setHidden(self._visibles is not None and c not in self._visibles)
        if self._visibles is None:
            return
        # Lo encontrado se ve con su camino desplegado; lo de adentro, plegado
        encontradas = [c for c in self._visibles if self._arbol.coincide(c, texto)]
        for c in self._items:
            self._items[c].setExpanded(False)
        for c in encontradas:
            m = c
            while m:
                m = m.rsplit("/", 1)[0] if "/" in m else ""
                self._items[m].setExpanded(True)
        if encontradas:
            self.tree.scrollToItem(self._items[min(encontradas)])

    def _desplegar_todo(self):
        self.tree.expandAll()

    def _plegar_todo(self):
        self.tree.collapseAll()
        if "" in self._items:
            self._items[""].setExpanded(True)

    # ------------------------------------------------------------- ciclo
    def showEvent(self, event):
        super().showEvent(event)
        if self._arbol is None:
            self.actualizar()

    def done(self, r):
        self._sin_respuesta.stop()
        try:
            hub().carpetas.disconnect(self._nas_leyo)
        except TypeError:
            pass
        super().done(r)


class ElegirCarpetas(_ArbolBase):
    """Qué carpetas del NAS se catalogan (⚙ de Catalogar)."""

    guardado = pyqtSignal()

    def __init__(self, parent=None, srv=None):
        super().__init__(
            parent, srv, "Carpetas que se catalogan",
            "Tilde las carpetas del NAS que quiere encontrar en Buscar. Tildar o destildar una "
            "carpeta cambia también todo lo de adentro, y después puede cambiar una por una. "
            "Una carpeta que se cree más adelante toma la marca de la carpeta que la contiene.")
        self._originales: dict[str, bool] | None = None
        self.tree.itemChanged.connect(self._al_tildar)

        # Solo para avisar si no se pudo guardar: lo que se cataloga ya lo dice el árbol
        self.lbl_aviso = QLabel("")
        self.lbl_aviso.setWordWrap(True)
        self.lbl_aviso.setVisible(False)
        self._lay.addWidget(self.lbl_aviso)

        botones = QDialogButtonBox()
        botones.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(
            self.reject)
        self.btn_guardar = botones.addButton("Guardar", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_guardar.setObjectName("btn_primary")
        self.btn_guardar.setEnabled(False)
        self.btn_guardar.clicked.connect(self.guardar)
        self._lay.addWidget(botones)

    def _marcas_actuales(self, datos) -> dict[str, bool]:
        if self._originales is None:
            self._originales = Alcance(datos["marcas"]).marcas
        # Lo cambiado y todavía sin guardar sigue; las carpetas nuevas heredan
        return self._arbol.marcas() if self._arbol is not None else datos["marcas"]

    def _preparar_item(self, it, carpeta):
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)

    def _texto_info(self, carpeta, contrarias):
        texto = super()._texto_info(carpeta, contrarias)
        n = contrarias.get(carpeta, 0)
        if n:
            propio = self._arbol.estados[carpeta]
            extra = (f"{n} excluida{'s' if n > 1 else ''} adentro" if propio else
                     f"{n} elegida{'s' if n > 1 else ''} adentro")
            texto = f"{texto} · {extra}" if texto else extra
        return texto

    def _pintar(self):
        self._pintando = True
        for c, it in self._items.items():
            it.setCheckState(0, Qt.CheckState.Checked if self._arbol.estados[c]
                             else Qt.CheckState.Unchecked)
        super()._pintar()
        self._pintando = False

    def _cargado(self):
        self._botones()

    def _al_tildar(self, it: QTreeWidgetItem, columna: int):
        if self._pintando or columna != 0 or self._arbol is None:
            return
        self.tildar(it.data(0, RUTA), it.checkState(0) == Qt.CheckState.Checked)

    def tildar(self, carpeta: str, valor: bool):
        """Lo que hace la casilla (con el filtro, solo lo que se ve)."""
        self._arbol.cambiar(carpeta, valor, self._visibles)
        self._pintar()
        self._botones()

    def _botones(self):
        if hasattr(self, "btn_guardar"):
            self.btn_guardar.setEnabled(not self._esperando and self._arbol is not None
                                        and self._arbol.marcas() != self._originales)

    # ------------------------------------------------------------- guardar
    def guardar(self):
        if self._arbol is None:
            return
        marcas = self._arbol.marcas()
        if marcas == self._originales:
            self.accept()
            return
        self.btn_guardar.setEnabled(False)
        self._srv.pedir(lambda repo: repo.medios_que_salen(marcas),
                        ok=lambda salen: self._confirmar_y_guardar(marcas, salen),
                        error=self._fallo_al_guardar)

    def _confirmar_y_guardar(self, marcas, salen):
        # Solo se pregunta si algo ya catalogado queda afuera; si no, se guarda
        eleccion = self.confirmar(salen) if salen else "dejar"
        if eleccion is None:
            self._botones()
            return
        quien = socket.gethostname()
        eliminar = eleccion == "eliminar"

        def hacer(repo):
            ids = repo.guardar_marcas(marcas, eliminar_medios=eliminar, por=quien)
            if ids:
                from ...appdata import catalog_thumb_dir
                from ...catalog.cache_miniaturas import CacheMiniaturas
                CacheMiniaturas(catalog_thumb_dir()).olvidar(ids)
            return ids

        self._srv.pedir(hacer, ok=lambda _ids: self._guardado(), error=self._fallo_al_guardar)

    def _guardado(self):
        self.guardado.emit()
        self.accept()

    def _fallo_al_guardar(self, e):
        self.lbl_aviso.setText(texto_para(e))
        self.lbl_aviso.setVisible(True)
        self._botones()

    def confirmar(self, salen: dict) -> str | None:
        """Algo ya catalogado queda afuera: ¿se elimina del catálogo o se deja?
        Devuelve "dejar", "eliminar" o None (cancelar)."""
        fotos = sum(f for f, _ in salen.values())
        videos = sum(v for _, v in salen.values())
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Guardar las carpetas")
        msg.setText(f"{_cantidad(fotos, videos).capitalize()} del catálogo quedan fuera de las "
                    "carpetas elegidas. ¿Los elimina del catálogo o los deja?")
        detalle = "\n".join(f"  • {c or RAIZ}: {_cantidad(*n)}"
                            for c, n in sorted(salen.items())[:8])
        if len(salen) > 8:
            detalle += f"\n  … y {len(salen) - 8} carpetas más"
        msg.setInformativeText(
            f"{detalle}\n\nSi los elimina, se pierden sus etiquetas. Si los deja, siguen en "
            "Buscar y se aprovechan si la carpeta vuelve a elegirse. Los archivos del NAS no "
            "se tocan.")
        dejar = msg.addButton("Dejarlos", QMessageBox.ButtonRole.AcceptRole)
        eliminar = msg.addButton("Eliminarlos del catálogo", QMessageBox.ButtonRole.DestructiveRole)
        cancelar = msg.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(dejar)
        msg.setEscapeButton(cancelar)
        msg.exec()
        elegido = msg.clickedButton()
        if elegido is dejar:
            return "dejar"
        if elegido is eliminar:
            return "eliminar"
        return None

    def reject(self):
        if self._arbol is not None and self._arbol.marcas() != self._originales:
            if not confirmar(self, "Carpetas que se catalogan",
                             "Hay cambios sin guardar. ¿Cerrar sin guardarlos?", "",
                             "Cerrar sin guardar", "Seguir eligiendo", peligro=True):
                return
        super().reject()


class ElegirUnaCarpeta(_ArbolBase):
    """Una carpeta (de las que se catalogan) para catalogar ahora."""

    def __init__(self, parent=None, srv=None):
        super().__init__(
            parent, srv, "Elija la carpeta",
            "Solo se pueden elegir carpetas que se catalogan. Las demás se ven en gris.")
        self.carpeta: str | None = None
        self.tree.currentItemChanged.connect(lambda *_: self._botones())
        self.tree.itemDoubleClicked.connect(lambda *_: self._aceptar())
        botones = QDialogButtonBox()
        botones.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(
            self.reject)
        self.btn_ok = botones.addButton("Elegir", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_ok.setObjectName("btn_primary")
        self.btn_ok.clicked.connect(self._aceptar)
        self._lay.addWidget(botones)
        self._botones()

    def _preparar_item(self, it, carpeta):
        if not self._arbol.estados[carpeta]:
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            it.setForeground(0, self.palette().placeholderText())
            it.setForeground(1, self.palette().placeholderText())

    def _elegible(self) -> str | None:
        it = self.tree.currentItem()
        if it is None or self._arbol is None:
            return None
        c = it.data(0, RUTA)
        return c if self._arbol.estados.get(c) else None

    def _botones(self):
        if hasattr(self, "btn_ok"):
            self.btn_ok.setEnabled(not self._esperando and self._elegible() is not None)

    def elegir(self, carpeta: str) -> bool:
        """Deja elegida una carpeta (para las pruebas y el doble clic)."""
        if carpeta not in self._items:
            return False
        self.tree.setCurrentItem(self._items[carpeta])
        return self._elegible() is not None

    def _aceptar(self):
        c = self._elegible()
        if c is None:
            return
        self.carpeta = c
        self.accept()
