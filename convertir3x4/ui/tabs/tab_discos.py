"""Pestaña 'Discos': copia de discos externos al NAS, vista y manejada desde acá.

Habla con panel-copias (la API del NAS, ver nas/copias.py). Muestra los discos USB
enchufados al NAS con el avance de su copia, y permite copiar, detener, verificar
y formatear. Las reglas de seguridad las aplica el NAS (copiar-disco.py): la app
solo pide; si el NAS se niega, se muestra el motivo.

Consulta el estado cada pocos segundos, y solo mientras la pestaña está visible.
Toda llamada de red corre en un EngineWorker para no trabar la UI.
"""

import os

from PyQt6.QtCore import QSettings, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ...nas.copias import (
    URLS_POR_DEFECTO, PanelClient, acciones_para, nombre_fs, resumir,
)
from ...workers import EngineWorker

_POLL_MS = 3000
_CONTENT_MAX_W = 860
_TONOS = {"activo": "#6cb6ff", "ok": "#57c785", "aviso": "#e3b341",
          "error": "#f47067", "neutro": "#9aa0a6"}
_UUID_ROLE = Qt.ItemDataRole.UserRole


def _cliente_desde(settings: QSettings) -> PanelClient:
    urls = os.environ.get("CDLM_PANEL_URLS") or settings.value("nas/panel_urls", "") \
        or ",".join(URLS_POR_DEFECTO)
    token = os.environ.get("CDLM_PANEL_TOKEN") or settings.value("nas/panel_token", "")
    return PanelClient([u.strip() for u in str(urls).split(",")], str(token))


class DiscosTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = QSettings("CDLM", "Convertir3x4")
        self.client = _cliente_desde(self.settings)
        self._estado: dict = {}
        self._cards: dict[str, "_DiscoCard"] = {}
        self._workers: set[EngineWorker] = set()
        self._polling = False
        self._log_uuid: str | None = None

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

        lay.addWidget(_title("Discos externos"))
        lay.addWidget(_text(
            "Los discos se enchufan por USB al NAS y se copian a la carpeta que elijas. "
            "Desde acá ves cómo va cada copia y la manejás. El disco se lee una sola vez, "
            "en solo lectura, y nunca se pisa un archivo del NAS."))

        row = QHBoxLayout()
        self.lbl_conexion = QLabel("Conectando con el NAS…")
        self.lbl_conexion.setObjectName("muted")
        self.lbl_conexion.setWordWrap(True)
        row.addWidget(self.lbl_conexion, 1)
        self.btn_config = QPushButton("Conexión…")
        self.btn_config.clicked.connect(self._configurar)
        row.addWidget(self.btn_config)
        lay.addLayout(row)

        self.chk_auto = QCheckBox(
            "Copiar, verificar y formatear solos los discos de la HyperDeck al enchufarlos")
        self.chk_auto.setToolTip(
            "Un disco cuenta como de la HyperDeck si solo tiene archivos HyperDeck_NNNN. "
            "Si la copia tiene algún error, no se formatea.")
        self.chk_auto.setEnabled(False)
        self.chk_auto.clicked.connect(self._toggle_auto)
        lay.addWidget(self.chk_auto)

        lay.addSpacing(14)
        lay.addWidget(_title("Enchufados al NAS"))
        self.cards_box = QVBoxLayout()
        self.cards_box.setSpacing(8)
        self.lbl_sin_discos = QLabel("No hay discos USB enchufados al NAS.")
        self.lbl_sin_discos.setObjectName("muted")
        self.cards_box.addWidget(self.lbl_sin_discos)
        lay.addLayout(self.cards_box)

        lay.addSpacing(14)
        lay.addWidget(_title("Historial de copias"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Disco", "Estado", "Destino", "Última novedad"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAccessibleName("Historial de copias")
        self.tree.itemSelectionChanged.connect(self._on_historial_sel)
        lay.addWidget(self.tree, 2)

        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setPlaceholderText("Elegí una copia del historial para ver su registro.")
        self.txt_log.setAccessibleName("Registro de la copia")
        mono = QFont("Menlo" if os.name != "nt" else "Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(10)
        self.txt_log.setFont(mono)
        lay.addWidget(self.txt_log, 1)

    # ------------------------------------------------------------- ciclo
    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def _run(self, fn, on_ok=None, on_fail=None):
        w = EngineWorker(lambda _cb: fn())
        self._workers.add(w)
        if on_ok:
            w.finished_ok.connect(on_ok)
        w.failed.connect(on_fail or self._error_accion)
        w.finished.connect(lambda: self._workers.discard(w))
        w.start()
        return w

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
        hora = (est.get("hora") or "")[11:19]
        donde = (url or "").replace("http://", "").split(":")[0]
        via = "Netbird" if donde.startswith("100.") else "red local"
        self.lbl_conexion.setText(f"Conectado al NAS por {via} ({donde}) · actualizado {hora}")
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

    def _llenar_historial(self, copias):
        sel = self._log_uuid
        self.tree.blockSignals(True)
        self.tree.clear()
        for c in copias:
            r = resumir(c)
            disco = " ".join(x for x in (c.get("modelo"), c.get("serie")) if x) or c["uuid"]
            it = QTreeWidgetItem([disco, r.titulo, c.get("destino") or "", c.get("actualizado") or ""])
            it.setData(0, _UUID_ROLE, c["uuid"])
            it.setToolTip(0, f"UUID {c['uuid']}")
            it.setToolTip(1, r.detalle)
            it.setForeground(1, _color(r.tono))
            self.tree.addTopLevelItem(it)
            if c["uuid"] == sel:
                it.setSelected(True)
        for i in range(3):
            self.tree.resizeColumnToContents(i)
        self.tree.blockSignals(False)

    def _sin_conexion(self, msg):
        self.chk_auto.setEnabled(False)
        texto = msg.split(": ", 1)[1] if msg.startswith(("PanelError", "PanelSinConexion")) else msg
        self.lbl_conexion.setText(f"Sin conexión con el NAS. {texto}")

    def _on_historial_sel(self):
        items = self.tree.selectedItems()
        if not items:
            return
        uuid = items[0].data(0, _UUID_ROLE)
        self._log_uuid = uuid
        self.txt_log.setPlainText("Cargando…")
        client = self.client
        self._run(lambda: client.log(uuid),
                  lambda texto: uuid == self._log_uuid and self._set_log(texto))

    def _set_log(self, texto):
        bar = self.txt_log.verticalScrollBar()
        al_final = bar.value() >= bar.maximum() - 4
        self.txt_log.setPlainText(texto or "(sin registro)")
        if al_final:
            bar.setValue(bar.maximum())

    # ------------------------------------------------------------- acciones
    def _accion(self, fn):
        def ok(msg):
            self.lbl_conexion.setText(msg or "Listo.")
            self.refresh()
        self._run(fn, ok)

    def _error_accion(self, msg):
        texto = msg.split(": ", 1)[1] if msg.startswith(("PanelError", "PanelSinConexion")) else msg
        QMessageBox.warning(self, "Discos", texto)
        self.refresh()

    def copiar(self, disco: dict, reintentar=False):
        if reintentar:
            r = QMessageBox.warning(
                self, "Reintentar archivos con error",
                "Cada lectura sobre un sector dañado empeora el disco. Lo recomendado es "
                "rescatar esos archivos con ddrescue desde otra PC.\n\n"
                "¿Volver a intentar igual los archivos que fallaron?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if r != QMessageBox.StandardButton.Yes:
                return
        raiz = self._estado.get("destinos_raiz", "/mnt/tank/multimedia")
        previo = self.settings.value("nas/ultimo_destino", "") or \
            self._estado.get("destino_auto", raiz)
        destino, ok = QInputDialog.getText(
            self, "Copiar disco",
            f"Carpeta del NAS donde se copia {_nombre(disco)}\n(dentro de {raiz}; "
            "si no existe, se crea):", QLineEdit.EchoMode.Normal, previo)
        destino = destino.strip().rstrip("/")
        if not ok or not destino:
            return
        if not (destino + "/").startswith(raiz.rstrip("/") + "/"):
            QMessageBox.warning(self, "Copiar disco", f"La carpeta tiene que estar dentro de {raiz}.")
            return
        self.settings.setValue("nas/ultimo_destino", destino)
        client, uuid = self.client, disco["uuid"]
        self._accion(lambda: client.copiar(uuid, destino, reintentar))

    def detener(self, disco: dict):
        r = QMessageBox.question(
            self, "Detener copia",
            f"¿Detener la copia de {_nombre(disco)}?\n\nLo ya copiado queda en el NAS. "
            "El archivo que estaba a medias se copia entero al retomar.")
        if r == QMessageBox.StandardButton.Yes:
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
            r = QMessageBox.question(
                self, "Copia automática",
                "Si se apaga, un disco de la HyperDeck que se enchufe al NAS no se copia ni "
                "se formatea solo: hay que hacerlo desde acá.\n\n"
                "Ojo: si vuelve a la HyperDeck sin formatear, se llena y corta la grabación.\n\n"
                "¿Apagar la copia automática?")
            if r != QMessageBox.StandardButton.Yes:
                self.chk_auto.setChecked(True)
                return
        client = self.client
        self._accion(lambda: "Copia automática " + ("activada." if client.set_auto(activo)
                                                    else "desactivada."))

    def _configurar(self):
        dlg = _ConexionDialog(self, self.settings)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.client = _cliente_desde(self.settings)
            self.lbl_conexion.setText("Conectando con el NAS…")
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

        self.lbl_nombre = QLabel()
        f = self.lbl_nombre.font()
        f.setBold(True)
        self.lbl_nombre.setFont(f)
        lay.addWidget(self.lbl_nombre)
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

        row = QHBoxLayout()
        row.setSpacing(8)
        self.botones = {}
        for clave, texto, fn in (
                ("copiar", "Copiar…", lambda: tab.copiar(self.disco)),
                ("reintentar", "Reintentar errores…", lambda: tab.copiar(self.disco, True)),
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
        self.lbl_nombre.setToolTip(f"{disco.get('particion')} · UUID {disco.get('uuid')} · "
                                   f"serie {disco.get('serie') or '?'}")
        r = resumir(copia, tarea, disco)
        color = _TONOS.get(r.tono, _TONOS["neutro"])
        self.lbl_estado.setText(f'<span style="color:{color}; font-weight:600">{r.titulo}</span>'
                                + (f" — {r.detalle}" if r.detalle else ""))
        self.bar.setVisible(r.porcentaje is not None)
        if r.porcentaje is not None:
            self.bar.setValue(r.porcentaje)
        nota = ""
        if copia and copia.get("corriendo") and copia.get("actual"):
            nota = f"Archivo: {copia['actual']}"
        elif not copia and disco.get("nota_auto") and not disco.get("formateado"):
            # Por qué el modo automático no lo tomó (otros archivos, vacío…).
            nota = "Modo automático: " + disco["nota_auto"].split(": ", 1)[-1]
        self.lbl_actual.setText(nota)
        self.lbl_actual.setVisible(bool(nota))
        acciones = acciones_para(disco, copia, tarea)
        for clave, b in self.botones.items():
            b.setVisible(clave in acciones)
        if copia and copia.get("fase") == "TERMINADO":
            self.botones["copiar"].setText("Copiar lo nuevo…")
            self.botones["copiar"].setToolTip("Si el disco se volvió a grabar, baja solo lo nuevo.")
        else:
            self.botones["copiar"].setText("Copiar…")
            self.botones["copiar"].setToolTip("")


# ------------------------------------------------------------------ diálogos
class _FormatearDialog(QDialog):
    def __init__(self, parent, disco: dict, copia: dict):
        super().__init__(parent)
        self.setWindowTitle("Formatear disco")
        lay = QVBoxLayout(self)
        lay.addWidget(_text(
            f"<b>{_nombre(disco)}</b><br>"
            f"Serie {disco.get('serie') or '?'} · {disco.get('particion')} · UUID {disco.get('uuid')}<br><br>"
            f"Copiado a <b>{copia.get('destino', '?')}</b> ({copia.get('archivos', '?')} archivos).<br><br>"
            "Antes de borrar, el NAS compara muestras de cada archivo del disco contra su "
            "copia. Si algo no coincide, o hay algo grabado después de la copia, no formatea.<br><br>"
            "<b>Se va a borrar todo el contenido del disco. No se puede deshacer.</b>"))
        self.txt = QLineEdit()
        self.txt.setPlaceholderText("Escribí FORMATEAR para confirmar")
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
            "Direcciones del servicio de copias en el NAS, separadas por coma (se prueban "
            "en orden), y la clave de acceso, que está en el NAS en "
            "/mnt/tank/ingreso/_copias/panel.token."))
        form = QFormLayout()
        self.txt_urls = QLineEdit(settings.value("nas/panel_urls", "") or ",".join(URLS_POR_DEFECTO))
        form.addRow("Direcciones:", self.txt_urls)
        self.txt_token = QLineEdit(settings.value("nas/panel_token", ""))
        self.txt_token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Clave:", self.txt_token)
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
def _nombre(disco: dict) -> str:
    modelo = disco.get("modelo") or "Disco"
    etiqueta = f' "{disco["etiqueta"]}"' if disco.get("etiqueta") else ""
    return f"{modelo} · {str(disco.get('disco_gb', '?')).replace('.', ',')} GB ·{etiqueta} {nombre_fs(disco.get('fs'))}"


def _color(tono):
    return QBrush(QColor(_TONOS.get(tono, _TONOS["neutro"])))


def _title(text):
    lbl = QLabel(text)
    lbl.setObjectName("section_title")
    return lbl


def _text(text):
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    return lbl
