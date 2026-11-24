"""Detalles de una foto o un video de Buscar, con la tecla I (pedido del 24/11/2026).

Reemplaza el cartel que aparecía al dejar el puntero: una ventana chica, al lado
de la principal, que muestra el elemento actual de la grilla y lo sigue mientras
se recorre con las flechas (como la vista rápida de la Mac). I o Esc la cierran.

Todo sale de lo que Buscar ya trajo (`Media`) y de la miniatura guardada en la
PC: no consulta la base. La foto original se lee aparte (puede estar en el NAS,
por SMB) y reemplaza la miniatura cuando llega.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ...utils import open_file
from ...workers import EngineWorker
from ..style import T

ANCHO = 440
_ALTO_IMAGEN = 300
_LADO_ORIGINAL = 1200            # la foto original se achica a esto para mostrarla
_ESPERA_ORIGINAL_MS = 250        # recorriendo con las flechas no se lee cada foto

_CALIDAD = {"low_res": "Baja resolución: puede verse borrosa en la pantalla.",
            "corrupt": "El archivo parece dañado.",
            "en_negro": "La imagen está en negro: no muestra nada."}


def leer_original(path: str, lado: int = _LADO_ORIGINAL) -> QImage | None:
    """La foto derecha (EXIF) y achicada, como QImage (se puede crear fuera
    del hilo de la interfaz; QPixmap no). None si no se pudo abrir."""
    from PIL import Image, ImageOps
    try:
        img = Image.open(path)
        img.draft("RGB", (lado, lado))
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((lado, lado))
    except Exception:  # noqa: BLE001 — sin original se queda la miniatura
        return None
    datos = img.tobytes()
    return QImage(datos, img.width, img.height, 3 * img.width,
                  QImage.Format.Format_RGB888).copy()


# ---------------------------------------------------------------- textos
def tamano_archivo(n: int | None) -> str:
    if not n:
        return ""
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB".replace(".", ",")
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB".replace(".", ",")
    return f"{max(1, n // 1024)} KB"


def duracion(seg: float | None) -> str:
    if not seg:
        return ""
    s = int(round(seg))
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fecha_de_toma(f) -> str:
    local = f.astimezone() if f.tzinfo else f
    return f"{local:%d/%m/%Y} a las {local:%H:%M}"


def datos_de(m) -> list[tuple[str, str]]:
    """Las filas de datos del elemento, en el orden en que se muestran."""
    filas = [("Tipo", "Video" if m.kind == "video" else "Foto")]
    if m.tomada:
        filas.append(("Fecha de la toma", fecha_de_toma(m.tomada)))
    if m.width and m.height:
        filas.append(("Medidas", f"{m.width} × {m.height} px"))
    if m.kind == "video" and m.duration:
        filas.append(("Duración", duracion(m.duration)))
    if m.size_bytes:
        filas.append(("Tamaño del archivo", tamano_archivo(m.size_bytes)))
    if m.quality_flag and m.quality_flag != "ok" and m.quality_flag in _CALIDAD:
        filas.append(("Calidad", _CALIDAD[m.quality_flag]))
    return filas


def aviso_de(m, estado: str | None) -> str:
    """El aviso de arriba si el original no se puede usar ('' si se puede)."""
    if estado == "missing" or m.missing:
        return ("El archivo ya no está en su carpeta: puede haberse movido o borrado. "
                "Esta era su ubicación anterior. Mientras no aparezca, no puede usarse en "
                "ProPresenter.")
    if estado == "offline":
        return ("No se encuentra el original: el NAS o el disco no están conectados. "
                "Mientras tanto, no puede usarse en ProPresenter.")
    return ""


def mapa(lat: float, lon: float) -> str:
    return f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=17/{lat:.6f}/{lon:.6f}"


# ---------------------------------------------------------------- ventana
class DetalleMedio(QDialog):
    """Una sola ventana que se reutiliza. `mostrar(m, mini, estado)` la llena."""
    cerrada = pyqtSignal()

    def __init__(self, parent=None, grid=None):
        super().__init__(parent)
        self.setObjectName("detalle_medio")
        self.setWindowTitle("Detalles")
        self.setModal(False)
        self.setFixedWidth(ANCHO)
        self.setMinimumHeight(360)
        self._grid = grid
        self.media = None
        self._ruta_original: str | None = None
        self._pedido = 0
        self._workers: set = set()
        self._espera = QTimer(self)
        self._espera.setSingleShot(True)
        self._espera.setInterval(_ESPERA_ORIGINAL_MS)
        self._espera.timeout.connect(self._leer_original)

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(0, 0, 0, 0)
        raiz.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        raiz.addWidget(scroll, 1)
        self._cuerpo = QWidget()
        self._cuerpo.setObjectName("detalle_cuerpo")
        scroll.setWidget(self._cuerpo)
        self._lay = QVBoxLayout(self._cuerpo)
        self._lay.setContentsMargins(20, 20, 20, 16)
        self._lay.setSpacing(14)

        self.imagen = QLabel()
        self.imagen.setObjectName("detalle_imagen")
        self.imagen.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.imagen.setFixedHeight(_ALTO_IMAGEN)
        self._lay.addWidget(self.imagen)

        cabeza = QVBoxLayout()
        cabeza.setSpacing(2)
        self.lbl_nombre = QLabel()
        self.lbl_nombre.setObjectName("detalle_nombre")
        self.lbl_nombre.setWordWrap(True)
        self.lbl_nombre.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cabeza.addWidget(self.lbl_nombre)
        self.lbl_carpeta = QLabel()
        self.lbl_carpeta.setObjectName("muted")
        self.lbl_carpeta.setWordWrap(True)
        self.lbl_carpeta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cabeza.addWidget(self.lbl_carpeta)
        self._lay.addLayout(cabeza)

        self.aviso = QLabel()
        self.aviso.setObjectName("detalle_aviso")
        self.aviso.setWordWrap(True)
        self._lay.addWidget(self.aviso)

        self._datos = QGridLayout()
        self._datos.setHorizontalSpacing(16)
        self._datos.setVerticalSpacing(8)
        self._datos.setColumnStretch(1, 1)
        self._lay.addLayout(self._datos)

        self.btn_mapa = QPushButton("Ver en el mapa")
        self.btn_mapa.setObjectName("btn_link")
        self.btn_mapa.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mapa.clicked.connect(self._ver_mapa)

        self.sec_etiquetas = _Seccion("Etiquetas")
        self._lay.addWidget(self.sec_etiquetas)
        self.sec_texto = _Seccion("Texto en la foto")
        self.txt_texto = QPlainTextEdit()
        self.txt_texto.setObjectName("detalle_texto")
        self.txt_texto.setReadOnly(True)
        self.txt_texto.setMaximumHeight(140)
        self.sec_texto.cuerpo.addWidget(self.txt_texto)
        self._lay.addWidget(self.sec_texto)
        self.sec_ubicacion = _Seccion("Ubicación del archivo")
        self.lbl_ruta = QLabel()
        self.lbl_ruta.setObjectName("muted")
        self.lbl_ruta.setWordWrap(True)
        self.lbl_ruta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sec_ubicacion.cuerpo.addWidget(self.lbl_ruta)
        self._lay.addWidget(self.sec_ubicacion)
        self._lay.addStretch(1)

        pie = QFrame()
        pie.setObjectName("detalle_pie")
        pl = QHBoxLayout(pie)
        pl.setContentsMargins(20, 10, 20, 12)
        ayuda = QLabel("Flechas: recorrer · I o Esc: cerrar")
        ayuda.setObjectName("muted")
        pl.addWidget(ayuda, 1)
        self.btn_abrir = QPushButton("Abrir")
        self.btn_abrir.setToolTip("Abre el original en el visor de imágenes o el reproductor")
        self.btn_abrir.clicked.connect(self._abrir)
        pl.addWidget(self.btn_abrir)
        raiz.addWidget(pie)

    # ------------------------------------------------------------- contenido
    def mostrar(self, m, miniatura: str | None = None, estado: str | None = None):
        """Llena la ventana con `m` (Media). `estado`: None, 'missing' u 'offline'."""
        mismo = self.media is not None and self.media.id == m.id
        self.media, self._estado = m, estado
        self.setWindowTitle(m.filename)
        self.lbl_nombre.setText(m.filename)
        self.lbl_carpeta.setText(m.folder_origin or "")
        self.lbl_carpeta.setVisible(bool(m.folder_origin))
        aviso = aviso_de(m, estado)
        self.aviso.setText(aviso)
        self.aviso.setVisible(bool(aviso))
        self._llenar_datos(m)
        self._llenar_etiquetas(m.etiquetas or [])
        texto = m.texto or ""
        if self.txt_texto.toPlainText() != texto:
            self.txt_texto.setPlainText(texto)
        self.sec_texto.setVisible(bool(texto) and m.kind != "video")
        self.lbl_ruta.setText(m.path)
        disponible = not estado and not m.missing
        self.btn_abrir.setEnabled(disponible)
        # Con textos en varias líneas el área de desplazamiento apretaba todo
        # (la imagen tapaba el nombre): el alto mínimo es el que necesita.
        self._ajustar_alto()
        QTimer.singleShot(0, self._ajustar_alto)       # ya con el estilo aplicado
        if not mismo:
            self._poner_imagen(miniatura)
            self._pedido += 1
            self._ruta_original = m.path if (disponible and m.kind == "image") else None
            self._espera.start()

    def _ajustar_alto(self):
        self._lay.invalidate()
        ancho = self._cuerpo.width() if self._cuerpo.width() > 100 else ANCHO
        self._cuerpo.setMinimumHeight(max(0, self._lay.heightForWidth(ancho)))

    def _llenar_datos(self, m):
        while self._datos.count():
            w = self._datos.takeAt(0).widget()
            if w is not None and w is not self.btn_mapa:
                w.setParent(None)          # que desaparezca ya (deleteLater es diferido)
                w.deleteLater()
        filas = datos_de(m)
        for i, (nombre, valor) in enumerate(filas):
            k = QLabel(nombre)
            k.setObjectName("muted")
            k.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            v = QLabel(valor)
            v.setObjectName("detalle_aviso_texto" if nombre == "Calidad" else "detalle_valor")
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._datos.addWidget(k, i, 0)
            self._datos.addWidget(v, i, 1)
        if m.lat is not None and m.lon is not None:
            k = QLabel("Lugar")
            k.setObjectName("muted")
            self._datos.addWidget(k, len(filas), 0)
            self._datos.addWidget(self.btn_mapa, len(filas), 1,
                                  Qt.AlignmentFlag.AlignLeft)
            self.btn_mapa.setVisible(True)
        else:
            self.btn_mapa.setVisible(False)

    def _llenar_etiquetas(self, etiquetas):
        cuerpo = self.sec_etiquetas.cuerpo
        while cuerpo.count():
            item = cuerpo.takeAt(0)
            _borrar_layout(item)
        if not etiquetas:
            vacio = QLabel("Sin etiquetas. Agréguelas desde la barra «Etiquetas» de Buscar.")
            vacio.setObjectName("muted")
            vacio.setWordWrap(True)
            cuerpo.addWidget(vacio)
            return
        chips = []
        for nombre, confirmada in etiquetas:
            c = QLabel(nombre)
            c.setObjectName("detalle_chip" if confirmada else "detalle_chip_ia")
            if not confirmada:
                c.setToolTip("Sugerida por la IA: confírmela o quítela desde la barra "
                             "«Etiquetas» de Buscar")
            chips.append(c)
        _en_renglones(cuerpo, chips, ANCHO - 40)
        if any(not c for _n, c in etiquetas):
            nota = QLabel("Las punteadas son sugerencias de la IA sin confirmar.")
            nota.setObjectName("muted")
            nota.setWordWrap(True)
            cuerpo.addWidget(nota)

    # ------------------------------------------------------------- imagen
    def _poner_imagen(self, ruta: str | None):
        px = QPixmap(ruta) if ruta else QPixmap()
        if px.isNull():
            self.imagen.setPixmap(QPixmap())
            self.imagen.setText("Sin vista previa")
            return
        self._poner_pixmap(px)

    def _poner_pixmap(self, px: QPixmap):
        dpr = self.devicePixelRatioF() or 1.0
        ancho = self.imagen.width() if self.imagen.width() > 100 else ANCHO - 40
        caja = QSize(ancho - 2, _ALTO_IMAGEN - 2)         # dentro del borde
        px = px.scaled(caja * dpr, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
        px.setDevicePixelRatio(dpr)
        self.imagen.setText("")
        self.imagen.setPixmap(px)

    def _leer_original(self):
        ruta, pedido = self._ruta_original, self._pedido
        if not ruta or not self.isVisible():
            return
        w = EngineWorker(lambda _p: leer_original(ruta), self)
        w.finished_ok.connect(lambda img: self._llego_original(pedido, img))
        w.finished.connect(lambda: self._workers.discard(w))
        self._workers.add(w)
        w.start()

    def _llego_original(self, pedido: int, img):
        if pedido == self._pedido and img is not None and not img.isNull():
            self._poner_pixmap(QPixmap.fromImage(img))

    # ------------------------------------------------------------- acciones
    def _abrir(self):
        if self.media is not None:
            open_file(self.media.path)

    def _ver_mapa(self):
        m = self.media
        if m is not None and m.lat is not None and m.lon is not None:
            QDesktopServices.openUrl(QUrl(mapa(m.lat, m.lon)))

    def keyPressEvent(self, event):
        k = event.key()
        if k in (Qt.Key.Key_Escape, Qt.Key.Key_I) and not (
                event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier):
            self.close()
            return
        if self._grid is not None and k in (Qt.Key.Key_Left, Qt.Key.Key_Right,
                                            Qt.Key.Key_Up, Qt.Key.Key_Down,
                                            Qt.Key.Key_Home, Qt.Key.Key_End,
                                            Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            QApplication.sendEvent(self._grid, event)      # recorrer desde acá también
            return
        super().keyPressEvent(event)

    def mostrar_con_foco(self):
        """Se abre con el foco (así anda Esc); las flechas siguen recorriendo la grilla."""
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def closeEvent(self, event):
        self._espera.stop()
        self._pedido += 1
        super().closeEvent(event)
        self.cerrada.emit()


class _Seccion(QWidget):
    def __init__(self, titulo: str):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(8)
        t = QLabel(titulo)
        t.setObjectName("subtitulo")
        lay.addWidget(t)
        self.cuerpo = QVBoxLayout()
        self.cuerpo.setSpacing(6)
        lay.addLayout(self.cuerpo)


def _en_renglones(destino: QVBoxLayout, widgets: list, ancho: int, sep: int = 6):
    """Chips en renglones que entran en `ancho` (sin FlowLayout: ver tag_bar.py)."""
    fila, usado = None, 0
    for w in widgets:
        w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        w.ensurePolished()                  # el ancho con el relleno del estilo
        ancho_w = w.sizeHint().width()
        if fila is None or usado + sep + ancho_w > ancho:
            fila = QHBoxLayout()
            fila.setSpacing(sep)
            fila.addStretch(1)
            destino.addLayout(fila)
            usado = 0
        fila.insertWidget(fila.count() - 1, w)
        usado += ancho_w + sep


def _borrar_layout(item):
    w = item.widget()
    if w is not None:
        w.setParent(None)
        w.deleteLater()
        return
    lay = item.layout()
    if lay is not None:
        while lay.count():
            _borrar_layout(lay.takeAt(0))
        lay.deleteLater()


def estado_de_item(item) -> str | None:
    """'missing'/'offline' según la celda de la grilla (None si se puede usar)."""
    from .media_grid import STATE_ROLE
    st = item.data(STATE_ROLE)
    return st if st in ("missing", "offline") else None
