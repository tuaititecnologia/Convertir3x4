"""Pestaña 'Discos': copia de discos externos al NAS, vista y manejada desde acá.

Habla con panel-copias (la API del NAS, ver nas/copias.py). Muestra los discos USB
enchufados al NAS con el avance de su copia, y permite copiar, detener, verificar
y formatear. Las reglas de seguridad las aplica el NAS (copiar-disco.py): la app
solo pide; si el NAS se niega, se muestra el motivo.

Consulta el estado cada pocos segundos, y solo mientras la pestaña está visible.
Toda llamada de red corre en un EngineWorker para no trabar la UI.
"""

import html
import os

from PyQt6.QtCore import QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFontDatabase
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ...nas.copias import (
    URLS_POR_DEFECTO, PanelClient, acciones_para, explicar, fecha, ficha, lineas_contenido,
    nota_operador, resumir, resumir_contenido, tipo_disco,
)
from ...workers import EngineWorker
from .. import icons
from ..style import T
from ..widgets.dialogos import confirmar

_POLL_MS = 3000          # mientras se ve el módulo
_POLL_FONDO_MS = 20000   # escondido: solo para avisar en la barra lateral
_CONTENT_MAX_W = 860
# Tono de cada estado -> color de la paleta activa (claro u oscuro), no fijo.
_TONOS = {"activo": "accent_text", "ok": "ok", "aviso": "warn", "error": "danger",
          "neutro": "text_3"}


def _tono(tono: str) -> str:
    return getattr(T, _TONOS.get(tono, "text_3"))
_UUID_ROLE = Qt.ItemDataRole.UserRole
_COPIA_ROLE = Qt.ItemDataRole.UserRole + 1      # el estado completo de la copia (dict)


def _cliente_desde(settings: QSettings) -> PanelClient:
    urls = os.environ.get("CDLM_PANEL_URLS") or settings.value("nas/panel_urls", "") \
        or ",".join(URLS_POR_DEFECTO)
    token = os.environ.get("CDLM_PANEL_TOKEN") or settings.value("nas/panel_token", "")
    return PanelClient([u.strip() for u in str(urls).split(",")], str(token))


class DiscosTab(QWidget):
    # Discos enchufados al NAS que el operador todavía no vio (para la barra lateral)
    pendientes = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Sin argumentos: el nombre lo fija __main__; los tests usan otro.
        self.settings = QSettings()
        self.client = _cliente_desde(self.settings)
        self._estado: dict = {}
        self._cards: dict[str, "_DiscoCard"] = {}
        self._workers: set[EngineWorker] = set()
        self._polling = False
        self._log_uuid: str | None = None
        self._vistos: set[str] = set()        # discos que ya se vieron en pantalla
        self._contenido: dict[str, dict] = {}  # uuid -> qué tiene en la raíz
        self._leyendo: set[str] = set()
        self._fondo = False

        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self.refresh)
        self._build_ui()

    # ------------------------------------------------------------- UI
    def _build_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 24)
        content = QWidget()
        content.setMaximumWidth(_CONTENT_MAX_W)
        outer.addWidget(content, 1)
        outer.addStretch(0)
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # En Multimedia le dicen "la grabadora": este módulo es para sus discos, no
        # para cualquier disco (decisión del 15/11/2026).
        lay.addWidget(_title("Discos de la grabadora"))
        lay.addWidget(_text(
            "Para guardar en el NAS lo grabado, conecte el disco de la grabadora al NAS. "
            "El NAS lo reconoce porque contiene únicamente grabaciones; con la copia "
            "automática activada, lo copia y lo deja vacío para volver a grabar. Los demás "
            "discos aparecen en la lista, pero no se copian automáticamente."))

        row = QHBoxLayout()
        # Estado de la conexión, bien visible mientras se conecta o si falla
        # (ver _set_conexion). Mientras tanto, todo lo de abajo queda en gris.
        self.conn_box = QFrame()
        cb = QHBoxLayout(self.conn_box)
        cb.setContentsMargins(12, 8, 12, 8)
        cb.setSpacing(10)
        self.lbl_conexion = QLabel()
        self.lbl_conexion.setWordWrap(True)
        cb.addWidget(self.lbl_conexion, 1)
        self.btn_retry = QPushButton("Reintentar")
        self.btn_retry.clicked.connect(self._reintentar)
        cb.addWidget(self.btn_retry, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.conn_box, 1)
        # La conexión la configura quien administra el NAS: queda en un ⚙ chico,
        # no como un botón más para el operador.
        self.btn_config = QPushButton()
        self.btn_config.setObjectName("btn_icon")
        icons.set_icon(self.btn_config, "settings", 16, "text_2")
        self.btn_config.setFixedSize(30, 30)
        self.btn_config.setToolTip("Conexión con el NAS (la configura el administrador del NAS)")
        self.btn_config.setAccessibleName("Conexión con el NAS")
        self.btn_config.clicked.connect(self._configurar)
        row.addWidget(self.btn_config)
        lay.addLayout(row)

        # Todo lo que depende del NAS: deshabilitado (gris) hasta que conecta
        self.body = QWidget()
        blay = QVBoxLayout(self.body)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.setSpacing(10)
        lay.addWidget(self.body, 1)

        self.chk_auto = QCheckBox("Copia automática")
        self.chk_auto.setToolTip(
            "Cuando se conecta un disco de la grabadora, se copia, se verifica que la copia "
            "esté completa y se borra para volver a grabar. Si ocurre un error, no se borra.")
        self.chk_auto.setEnabled(False)
        self.chk_auto.clicked.connect(self._toggle_auto)
        blay.addWidget(self.chk_auto)

        blay.addSpacing(14)
        blay.addWidget(_title("Discos conectados al NAS"))
        self.cards_box = QVBoxLayout()
        self.cards_box.setSpacing(8)
        self.lbl_sin_discos = QLabel("No hay discos conectados al NAS. Conecte el disco de la grabadora para copiarlo.")
        self.lbl_sin_discos.setObjectName("muted")
        self.cards_box.addWidget(self.lbl_sin_discos)
        blay.addLayout(self.cards_box)

        blay.addSpacing(14)
        blay.addWidget(_title("Historial de copias"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Disco", "Estado", "Carpeta en el NAS", "Actualizado"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAccessibleName("Historial de copias")
        # Ancho inicial: vacía (mientras conecta) los títulos no quedan cortados
        for col, ancho in enumerate((170, 170, 290)):
            self.tree.setColumnWidth(col, ancho)
        self.tree.itemSelectionChanged.connect(self._on_historial_sel)
        blay.addWidget(self.tree, 2)

        # La copia elegida, completa: estado, qué pasó y qué hacer, y sus datos
        self.panel_copia = QFrame()
        self.panel_copia.setObjectName("panel")
        self.panel_copia.setAccessibleName("Detalle de la copia elegida")
        pc = QVBoxLayout(self.panel_copia)
        pc.setContentsMargins(14, 10, 14, 12)
        pc.setSpacing(6)
        self.lbl_copia_estado = QLabel("")
        self.lbl_copia_estado.setObjectName("section_title")
        pc.addWidget(self.lbl_copia_estado)
        self.lbl_copia_que = QLabel("")
        self.lbl_copia_que.setWordWrap(True)
        pc.addWidget(self.lbl_copia_que)
        self.lbl_copia_hacer = QLabel("")
        self.lbl_copia_hacer.setWordWrap(True)
        pc.addWidget(self.lbl_copia_hacer)
        self.grid_copia = QGridLayout()
        self.grid_copia.setHorizontalSpacing(16)
        self.grid_copia.setVerticalSpacing(4)
        self.grid_copia.setColumnStretch(1, 1)
        pc.addLayout(self.grid_copia)
        self.panel_copia.setVisible(False)
        blay.addWidget(self.panel_copia)

        # El registro del NAS es técnico: le sirve a quien administra el NAS, no al
        # operador. Queda escondido detrás de un botón.
        self.btn_log = QPushButton("Ver el detalle técnico")
        self.btn_log.setObjectName("btn_link")
        self.btn_log.setCheckable(True)
        self.btn_log.setToolTip("Registro de la copia, para el administrador del NAS")
        self.btn_log.toggled.connect(self._toggle_log)
        blay.addWidget(self.btn_log, 0, Qt.AlignmentFlag.AlignLeft)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setPlaceholderText("Seleccione una copia de la lista para ver el detalle.")
        self.txt_log.setAccessibleName("Detalle técnico de la copia")
        self.txt_log.setVisible(False)
        # Monoespaciada del sistema: existe en cualquier plataforma (es un registro)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setPixelSize(12)
        self.txt_log.setFont(mono)
        blay.addWidget(self.txt_log, 1)

        self.lbl_sin_discos.setVisible(False)   # no se sabe hasta que el NAS responde
        self._puntos = 0
        self._anim = QTimer(self)
        self._anim.setInterval(450)
        self._anim.timeout.connect(self._animar_conectando)
        self._set_conexion("conectando")

    # ------------------------------------------------------------- conexión
    def _set_conexion(self, estado: str, texto: str = ""):
        """'conectando' (azul, animado), 'ok' (línea discreta) o 'error' (naranja,
        con Reintentar). Fuera de 'ok', lo que depende del NAS queda en gris."""
        self._estado_conexion = estado
        self.conn_box.setObjectName(f"conn_{estado}")
        self.conn_box.style().unpolish(self.conn_box)
        self.conn_box.style().polish(self.conn_box)
        self.lbl_conexion.style().unpolish(self.lbl_conexion)
        self.lbl_conexion.style().polish(self.lbl_conexion)
        self.btn_retry.setVisible(estado == "error")
        # Conectado no hay recuadro: sin relleno, el texto se alinea con el resto
        self.conn_box.layout().setContentsMargins(*((0, 6, 0, 6) if estado == "ok" else (12, 8, 12, 8)))
        self.body.setEnabled(estado == "ok")
        if estado == "conectando":
            self._puntos = 0
            self._animar_conectando()
            self._anim.start()
        else:
            self._anim.stop()
            self.lbl_conexion.setText(texto)

    def _animar_conectando(self):
        self._puntos = (self._puntos % 3) + 1
        self.lbl_conexion.setText("Conectando con el NAS" + "." * self._puntos)

    def _reintentar(self):
        self._set_conexion("conectando")
        self.refresh()

    # ------------------------------------------------------------- ciclo
    def start_background(self):
        """Sigue mirando el NAS aunque el módulo no se vea, para avisar en la barra
        lateral cuando se enchufa un disco. Lo llama __main__ (no los tests)."""
        self._fondo = True
        if not self.isVisible():
            self._timer.start(_POLL_FONDO_MS)
            self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._timer.start(_POLL_MS)

    def hideEvent(self, event):
        super().hideEvent(event)
        if self._fondo:
            self._timer.start(_POLL_FONDO_MS)
        else:
            self._timer.stop()

    def _run(self, fn, on_ok=None, on_fail=None):
        # Las respuestas llegan a MÉTODOS de la pestaña (no a lambdas): si la
        # pestaña ya no existe, Qt corta la conexión solo y no se ejecuta nada
        # sobre una pantalla destruida.
        w = EngineWorker(lambda _cb: fn())
        w._al_terminar = on_ok
        w._al_fallar = on_fail or self._error_accion
        self._workers.add(w)
        w.finished_ok.connect(self._respuesta_ok)
        w.failed.connect(self._respuesta_fallo)
        w.finished.connect(self._worker_terminado)
        w.start()
        return w

    def _respuesta_ok(self, resultado):
        cb = getattr(self.sender(), "_al_terminar", None)
        if cb:
            cb(resultado)

    def _respuesta_fallo(self, msg):
        cb = getattr(self.sender(), "_al_fallar", None)
        if cb:
            cb(msg)

    def _worker_terminado(self):
        self._workers.discard(self.sender())

    def refresh(self):
        if self._polling:
            return
        self._polling = True
        client = self.client
        uuid_log = self._log_uuid

        def fn():
            est = client.estado()
            log = None
            if uuid_log and any(c.get("uuid") == uuid_log and c.get("corriendo")
                                for c in est.get("copias", [])):
                log = client.log(uuid_log)
            return est, log, client.url_activa

        def ok(res):
            self._polling = False
            est, log, url = res
            self._aplicar(est, url)
            if log is not None and uuid_log == self._log_uuid:
                self._set_log(log)

        def fail(msg):
            self._polling = False
            self._sin_conexion(msg)

        self._run(fn, ok, fail)

    # ------------------------------------------------------------- render
    def _aplicar(self, est: dict, url: str | None):
        self._estado = est
        hora = (est.get("hora") or "")[11:16]
        self._set_conexion("ok", f"Conectado al NAS · actualizado a las {hora}")
        self.chk_auto.setEnabled(True)
        self.chk_auto.setChecked(bool(est.get("auto")))

        copias = {c["uuid"]: c for c in est.get("copias", [])}
        tareas = est.get("tareas", {})
        discos = est.get("discos", [])
        vistos = set()
        for d in discos:
            u = d["uuid"]
            vistos.add(u)
            card = self._cards.get(u)
            if card is None:
                card = _DiscoCard(self, u)
                self._cards[u] = card
                self.cards_box.addWidget(card)
            card.actualizar(d, copias.get(u), tareas.get(u))
        for u in list(self._cards):
            if u not in vistos:
                self._cards.pop(u).deleteLater()
        self.lbl_sin_discos.setVisible(not discos)
        self._llenar_historial(est.get("copias", []))

        # Aviso para la barra lateral: discos enchufados que todavía no se vieron
        if self.isVisible():
            self._vistos |= vistos
        self.pendientes.emit(len(vistos - self._vistos))

        # Qué tiene cada disco: se pide una vez (el NAS lo guarda mientras siga
        # enchufado). No mientras se copia: el NAS no lo monta dos veces.
        for u in list(self._contenido):
            if u not in vistos:
                del self._contenido[u]
        if self.isVisible():
            for d in discos:
                u = d["uuid"]
                copia = copias.get(u) or {}
                if d.get("admitido") and not copia.get("corriendo") and \
                        u not in self._contenido and u not in self._leyendo:
                    self._leer_contenido(u)
        for u, card in self._cards.items():
            card.set_contenido(self._contenido.get(u), u in self._leyendo)

    def _leer_contenido(self, uuid: str):
        self._leyendo.add(uuid)
        client = self.client

        def ok(datos):
            self._leyendo.discard(uuid)
            if not datos.get("ocupado"):
                self._contenido[uuid] = datos
            card = self._cards.get(uuid)
            if card:
                card.set_contenido(self._contenido.get(uuid), False)

        def fail(_msg):
            self._leyendo.discard(uuid)
            self._contenido[uuid] = {"error": True}
            card = self._cards.get(uuid)
            if card:
                card.set_contenido(self._contenido[uuid], False)

        self._run(lambda: client.contenido(uuid), ok, fail)

    def _llenar_historial(self, copias):
        sel = self._log_uuid
        self.tree.blockSignals(True)
        self.tree.clear()
        for c in copias:
            r = resumir(c)
            disco = c.get("modelo") or "Disco"
            it = QTreeWidgetItem([disco, r.titulo, self._carpeta(c.get("destino")),
                                  fecha(c.get("actualizado"))])
            it.setData(0, _UUID_ROLE, c["uuid"])
            it.setData(0, _COPIA_ROLE, c)
            it.setForeground(1, _color(r.tono))
            self.tree.addTopLevelItem(it)
            if c["uuid"] == sel:
                it.setSelected(True)
                self._mostrar_copia(c)
        if copias:   # vacía, se quedan los anchos iniciales (si no, los títulos se cortan)
            for i in range(3):
                self.tree.resizeColumnToContents(i)
        self.tree.blockSignals(False)

    def _sin_conexion(self, msg):
        self.chk_auto.setEnabled(False)
        self._set_conexion("error", _amigable(msg))

    def _on_historial_sel(self):
        items = self.tree.selectedItems()
        if not items:
            self.panel_copia.setVisible(False)
            return
        self._mostrar_copia(items[0].data(0, _COPIA_ROLE) or {})
        uuid = items[0].data(0, _UUID_ROLE)
        self._log_uuid = uuid
        self.txt_log.setPlainText("Cargando…")
        client = self.client
        self._run(lambda: client.log(uuid),
                  lambda texto: uuid == self._log_uuid and self._set_log(texto))

    def _mostrar_copia(self, c: dict):
        """El panel de la copia elegida: estado, qué pasó y qué hacer, y sus datos."""
        r = resumir(c)
        self.lbl_copia_estado.setText(r.titulo)
        self.lbl_copia_estado.setStyleSheet(f"color: {_color(r.tono).color().name()};")
        exp = explicar(c)
        que, hacer = exp if exp else (r.detalle, "")
        self.lbl_copia_que.setText(que)
        self.lbl_copia_que.setVisible(bool(que))
        self.lbl_copia_hacer.setText(f"<b>Qué hacer:</b> {html.escape(hacer)}" if hacer else "")
        self.lbl_copia_hacer.setVisible(bool(hacer))
        while self.grid_copia.count():
            w = self.grid_copia.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        filas = ficha(c)
        if c.get("destino"):      # después del disco: dónde quedó
            filas.insert(1 if filas and filas[0][0] == "Disco" else 0,
                         ("Carpeta en el NAS", self._carpeta(c["destino"])))
        for i, (dato, valor) in enumerate(filas):
            k = QLabel(dato)
            k.setObjectName("muted")
            v = QLabel(valor)
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.grid_copia.addWidget(k, i, 0, Qt.AlignmentFlag.AlignTop)
            self.grid_copia.addWidget(v, i, 1)
        self.panel_copia.setVisible(True)

    def _set_log(self, texto):
        bar = self.txt_log.verticalScrollBar()
        al_final = bar.value() >= bar.maximum() - 4
        self.txt_log.setPlainText(texto or "(sin detalle)")
        if al_final:
            bar.setValue(bar.maximum())

    def _toggle_log(self, visible: bool):
        self.txt_log.setVisible(visible)
        self.btn_log.setText("Ocultar el detalle técnico" if visible else "Ver el detalle técnico")

    def _raiz(self) -> str:
        return self._estado.get("destinos_raiz", "/mnt/tank/multimedia")

    def _carpeta(self, destino: str | None) -> str:
        """Carpeta del NAS como la ve el operador: relativa a Multimedia."""
        if not destino:
            return ""
        raiz = self._raiz().rstrip("/") + "/"
        return destino[len(raiz):] if destino.startswith(raiz) else destino

    # ------------------------------------------------------------- acciones
    def _accion(self, fn):
        def ok(msg):
            self.lbl_conexion.setText(msg or "Listo.")
            self.refresh()
        self._run(fn, ok)

    def _error_accion(self, msg):
        QMessageBox.warning(self, "Grabadora", _amigable(msg))
        self.refresh()

    def copiar(self, disco: dict, reintentar=False):
        if reintentar:
            if not confirmar(
                    self, "Volver a intentar", "¿Intentar copiar de nuevo lo que falló?",
                    "El disco tiene partes dañadas y cada intento puede empeorarlo. Se "
                    "recomienda solicitar al administrador del NAS que recupere esos archivos.",
                    "Intentar de nuevo", peligro=True):
                return
        # El operador ve y escribe la carpeta dentro de Multimedia
        # ("Historial/Reuniones/2026-11-15"), no la ruta del sistema del NAS.
        raiz = self._raiz()
        previo = self.settings.value("nas/ultimo_destino", "") or \
            self._estado.get("destino_auto", raiz)
        rel, ok = QInputDialog.getText(
            self, "Copiar disco",
            f"¿En qué carpeta de Multimedia desea copiar {_nombre(disco)}?\n"
            "Si no existe, se crea.", QLineEdit.EchoMode.Normal, self._carpeta(previo))
        rel = rel.strip().strip("/").replace("\\", "/")
        if not ok or not rel:
            return
        if ".." in rel.split("/"):
            QMessageBox.warning(self, "Copiar disco", "Elija una carpeta dentro de Multimedia.")
            return
        destino = raiz.rstrip("/") + "/" + rel
        self.settings.setValue("nas/ultimo_destino", destino)
        client, uuid = self.client, disco["uuid"]
        self._accion(lambda: client.copiar(uuid, destino, reintentar))

    def detener(self, disco: dict):
        if confirmar(self, "Detener copia", f"¿Detener la copia de {_nombre(disco)}?",
                     "Lo que ya se copió queda en el NAS. Con Copiar sigue desde donde quedó.",
                     "Detener copia", cancelar="Seguir copiando", peligro=True):
            client, uuid = self.client, disco["uuid"]
            self._accion(lambda: client.detener(uuid))

    def verificar(self, disco: dict):
        client, uuid = self.client, disco["uuid"]
        self._accion(lambda: client.verificar(uuid))

    def formatear(self, disco: dict, copia: dict | None):
        dlg = _FormatearDialog(self, disco, copia or {})
        if dlg.exec() == QDialog.DialogCode.Accepted:
            client, uuid, serie = self.client, disco["uuid"], disco.get("serie", "")
            self._accion(lambda: client.formatear(uuid, serie))

    def _toggle_auto(self, activo: bool):
        if not activo:
            if not confirmar(
                    self, "Copia automática", "¿Apagar la copia automática?",
                    "Sin ella, cada disco de la grabadora deberá copiarse y formatearse "
                    "manualmente desde aquí. Si un disco vuelve a la grabadora sin formatear, "
                    "se llena y la grabación se interrumpe.",
                    "Apagar", cancelar="Dejarla encendida", peligro=True):
                self.chk_auto.setChecked(True)
                return
        client = self.client
        self._accion(lambda: "Copia automática " + ("activada." if client.set_auto(activo)
                                                    else "desactivada."))

    def _configurar(self):
        dlg = _ConexionDialog(self, self.settings)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.client = _cliente_desde(self.settings)
            self._set_conexion("conectando")
            self.refresh()


# ------------------------------------------------------------------ tarjeta de disco
class _DiscoCard(QFrame):
    def __init__(self, tab: DiscosTab, uuid: str):
        super().__init__()
        self.tab = tab
        self.uuid = uuid
        self.disco: dict = {}
        self.copia: dict | None = None
        self.setObjectName("panel")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.lbl_nombre = QLabel()
        f = self.lbl_nombre.font()
        f.setBold(True)
        self.lbl_nombre.setFont(f)
        head.addWidget(self.lbl_nombre)
        # De la grabadora u otro disco: lo decide el NAS (ver copias.tipo_disco)
        self.lbl_tipo = QLabel()
        head.addWidget(self.lbl_tipo)
        head.addStretch()
        lay.addLayout(head)
        self.lbl_estado = QLabel()
        self.lbl_estado.setWordWrap(True)
        lay.addWidget(self.lbl_estado)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(True)
        lay.addWidget(self.bar)
        self.lbl_actual = QLabel()
        self.lbl_actual.setObjectName("muted")
        lay.addWidget(self.lbl_actual)

        # Qué tiene el disco: una frase siempre y la lista entera a pedido
        fila = QHBoxLayout()
        fila.setSpacing(10)
        self.lbl_contenido = QLabel()
        self.lbl_contenido.setWordWrap(True)
        fila.addWidget(self.lbl_contenido, 1)
        self.btn_archivos = QPushButton()
        self.btn_archivos.setObjectName("btn_link")
        self.btn_archivos.setCheckable(True)
        self.btn_archivos.toggled.connect(self._toggle_archivos)
        fila.addWidget(self.btn_archivos, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(fila)
        self.lst_archivos = QListWidget()
        self.lst_archivos.setObjectName("people_list")
        self.lst_archivos.setAccessibleName("Archivos del disco")
        self.lst_archivos.setMaximumHeight(180)
        self.lst_archivos.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.lst_archivos.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lst_archivos.setVisible(False)
        lay.addWidget(self.lst_archivos)
        self._n_archivos = 0
        self.lbl_contenido.setVisible(False)
        self.btn_archivos.setVisible(False)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.botones = {}
        for clave, texto, fn in (
                ("copiar", "Copiar…", lambda: tab.copiar(self.disco)),
                ("reintentar", "Volver a intentar lo que falló…",
                 lambda: tab.copiar(self.disco, True)),
                ("detener", "Detener", lambda: tab.detener(self.disco)),
                ("verificar", "Verificar", lambda: tab.verificar(self.disco)),
                ("formatear", "Formatear…", lambda: tab.formatear(self.disco, self.copia))):
            b = QPushButton(texto)
            b.clicked.connect(fn)
            row.addWidget(b)
            self.botones[clave] = b
        self.botones["copiar"].setObjectName("btn_primary")
        row.addStretch()
        lay.addLayout(row)

    def actualizar(self, disco, copia, tarea):
        self.disco, self.copia = disco, copia
        self.lbl_nombre.setText(_nombre(disco))
        tipo = tipo_disco(disco)
        self.lbl_tipo.setVisible(tipo is not None)
        if tipo:
            self.lbl_tipo.setText("Disco de la grabadora" if tipo == "grabadora" else "Otro disco")
            self.lbl_tipo.setObjectName("pill_grabadora" if tipo == "grabadora" else "pill_otro")
            self.lbl_tipo.setToolTip(
                "El NAS lo reconoce porque solo tiene grabaciones de la grabadora."
                if tipo == "grabadora" else
                "Contiene otros archivos además de grabaciones, por eso no se copia automáticamente.")
            self.lbl_tipo.style().unpolish(self.lbl_tipo)
            self.lbl_tipo.style().polish(self.lbl_tipo)
        r = resumir(copia, tarea, disco)
        color = _tono(r.tono)
        self.lbl_estado.setText(f'<span style="color:{color}; font-weight:600">{r.titulo}</span>'
                                + (f" — {r.detalle}" if r.detalle else ""))
        self.bar.setVisible(r.porcentaje is not None)
        if r.porcentaje is not None:
            self.bar.setValue(r.porcentaje)
        nota = ""
        if copia and copia.get("corriendo") and copia.get("actual"):
            nota = f"Copiando ahora: {copia['actual']}"
        elif not copia and not disco.get("formateado"):
            # Por qué la copia automática no lo tomó, en palabras del operador.
            nota = nota_operador(disco)
        self.lbl_actual.setText(nota)
        self.lbl_actual.setWordWrap(True)
        self.lbl_actual.setVisible(bool(nota))
        # Copiar es la acción principal solo en los discos de la grabadora; en
        # otro disco se puede, pero no se la invita.
        principal = "btn_primary" if tipo != "otro" else ""
        if self.botones["copiar"].objectName() != principal:
            self.botones["copiar"].setObjectName(principal)
            self.botones["copiar"].style().unpolish(self.botones["copiar"])
            self.botones["copiar"].style().polish(self.botones["copiar"])
        acciones = acciones_para(disco, copia, tarea)
        for clave, b in self.botones.items():
            b.setVisible(clave in acciones)
        if copia and copia.get("fase") == "TERMINADO":
            self.botones["copiar"].setText("Copiar lo nuevo…")
            self.botones["copiar"].setToolTip(
                "Si el disco se volvió a usar, copia solo lo que se grabó después.")
        else:
            self.botones["copiar"].setText("Copiar…")
            self.botones["copiar"].setToolTip("")


    def set_contenido(self, datos: dict | None, leyendo: bool):
        if leyendo and not datos:
            self.lbl_contenido.setText("Revisando qué tiene el disco…")
            self.lbl_contenido.setObjectName("muted")
        elif datos and datos.get("error"):
            self.lbl_contenido.setText("No se pudo ver qué tiene el disco.")
            self.lbl_contenido.setObjectName("muted")
        else:
            self.lbl_contenido.setText(resumir_contenido(datos))
            self.lbl_contenido.setObjectName("")
        self.lbl_contenido.style().unpolish(self.lbl_contenido)
        self.lbl_contenido.style().polish(self.lbl_contenido)
        self.lbl_contenido.setVisible(bool(self.lbl_contenido.text()))

        lineas = lineas_contenido(datos) if datos and not datos.get("error") else []
        if len(lineas) != self._n_archivos or self.lst_archivos.count() != len(lineas):
            self.lst_archivos.clear()
            self.lst_archivos.addItems(lineas)
            self._n_archivos = len(lineas)
        self.btn_archivos.setVisible(bool(lineas))
        self._toggle_archivos(self.btn_archivos.isChecked())

    def _toggle_archivos(self, visible: bool):
        n = self._n_archivos
        self.lst_archivos.setVisible(visible and n > 0)
        # Sin número: la lista es la raíz (archivos sueltos y carpetas), y un número
        # distinto del total de la frase de arriba confundía.
        self.btn_archivos.setText("Ocultar archivos" if visible else "Mostrar archivos")


# ------------------------------------------------------------------ diálogos
class _FormatearDialog(QDialog):
    def __init__(self, parent, disco: dict, copia: dict):
        super().__init__(parent)
        self.setWindowTitle("Formatear disco")
        lay = QVBoxLayout(self)
        lay.addWidget(_text(
            f"<b>{_nombre(disco)}</b><br><br>"
            f"Está copiado en <b>{parent._carpeta(copia.get('destino'))}</b> "
            f"({copia.get('archivos', '?')} archivos).<br><br>"
            "Antes de borrar, el NAS revisa que la copia esté completa. Si falta algo, o el "
            "disco tiene algo grabado después de la copia, no lo borra.<br><br>"
            "<b>Se va a borrar todo lo que tiene el disco. No se puede deshacer.</b>"))
        self.txt = QLineEdit()
        self.txt.setPlaceholderText("Escriba FORMATEAR para confirmar")
        self.txt.setAccessibleName("Confirmación")
        lay.addWidget(self.txt)
        self.botones = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                        QDialogButtonBox.StandardButton.Cancel)
        ok = self.botones.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Formatear")
        ok.setEnabled(False)
        self.txt.textChanged.connect(lambda t: ok.setEnabled(t.strip() == "FORMATEAR"))
        self.botones.accepted.connect(self.accept)
        self.botones.rejected.connect(self.reject)
        lay.addWidget(self.botones)
        self.setMinimumWidth(460)


class _ConexionDialog(QDialog):
    def __init__(self, parent, settings: QSettings):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Conexión con el NAS")
        lay = QVBoxLayout(self)
        lay.addWidget(_text(
            "Esta configuración corresponde al administrador del NAS. Si hay más de una "
            "dirección, sepárelas con comas: se prueban en orden."))
        form = QFormLayout()
        self.txt_urls = QLineEdit(settings.value("nas/panel_urls", "") or ",".join(URLS_POR_DEFECTO))
        form.addRow("Dirección del NAS:", self.txt_urls)
        self.txt_token = QLineEdit(settings.value("nas/panel_token", ""))
        self.txt_token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Clave de acceso:", self.txt_token)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._guardar)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.setMinimumWidth(480)

    def _guardar(self):
        self.settings.setValue("nas/panel_urls", self.txt_urls.text().strip())
        self.settings.setValue("nas/panel_token", self.txt_token.text().strip())
        self.accept()


# ------------------------------------------------------------------ helpers
def _amigable(msg: str) -> str:
    """Los errores del NAS ya vienen en palabras del operador; cualquier otro
    (un error interno de Python) se reemplaza por una frase entendible."""
    if msg.startswith(("PanelError", "PanelSinConexion")):
        return msg.split(": ", 1)[1]
    return "Error de comunicación con el NAS. Intente nuevamente en unos minutos."

def _nombre(disco: dict) -> str:
    modelo = disco.get("modelo") or "Disco"
    etiqueta = f' · «{disco["etiqueta"]}»' if disco.get("etiqueta") else ""
    try:
        gb = float(disco.get("disco_gb"))
        tam = f"{gb:.0f} GB" if gb >= 10 else f"{gb:.1f} GB".replace(".", ",")
    except (TypeError, ValueError):
        tam = "? GB"
    return f"{modelo} · {tam}{etiqueta}"


def _color(tono):
    return QBrush(QColor(_tono(tono)))


def _title(text):
    lbl = QLabel(text)
    lbl.setObjectName("section_title")
    return lbl


def _text(text):
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    return lbl
