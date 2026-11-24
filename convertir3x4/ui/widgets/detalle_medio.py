"""Panel de detalles de Buscar, al costado de la grilla (pedidos del 24/11/2026).

Reemplaza el cartel que aparecía al dejar el puntero. Se abre y se cierra con la
tecla I o el botón «Detalles», y queda como el operador lo deje. Muestra lo
elegido en la grilla y lo sigue mientras se recorre:

- un elemento: vista previa, fecha de la toma, medidas, etiquetas, el texto que
  aparece en la foto y dónde está el archivo;
- varios: un resumen («5 seleccionados: 3 fotos y 2 videos», fechas, tamaño
  total, etiquetas en común) y la lista de todos.

Todo sale de lo que Buscar ya trajo (`Media`) y de la miniatura guardada en la
PC: no consulta la base. La foto original se lee aparte (puede estar en el NAS,
por SMB) y reemplaza la miniatura cuando llega.

Con un solo elemento se pueden corregir el texto de la foto y escribir notas
(pedido del 24/11/2026). Se guardan solos: al dejar de escribir un momento, al
salir del cuadro o al elegir otro elemento. El panel no guarda: avisa con
`guardar(id, campo, valor)` y Buscar lo manda al NAS y contesta con `guardado`.
Mientras el operador escribe, lo que llega del NAS no le pisa el cuadro.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QImage, QPixmap
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from ...utils import open_file
from ...workers import EngineWorker
from .. import icons

ANCHO = 360                      # ancho inicial del panel
ANCHO_MIN = 300
_ALTO_IMAGEN = 260
_LADO_ORIGINAL = 1200            # la foto original se achica a esto para mostrarla
_ESPERA_ORIGINAL_MS = 250        # recorriendo con las flechas no se lee cada foto
_LISTA_MAX = 200                 # con más elegidos, la lista muestra estos y «y N más»
_MINI_LISTA = 48
_ESPERA_GUARDAR_MS = 1200        # se guarda al dejar de escribir este tiempo

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
    if m.lugar:
        filas.append(("Lugar", m.lugar))
    if m.eventos:
        filas.append(("Eventos" if len(m.eventos) > 1 else "Evento", ", ".join(m.eventos)))
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



def _plural(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


def titulo_varios(medios) -> tuple[str, str]:
    """(«5 seleccionados», «3 fotos y 2 videos»)."""
    n = len(medios)
    videos = sum(m.kind == "video" for m in medios)
    fotos = n - videos
    if not videos:
        return f"{n} fotos seleccionadas", ""
    if not fotos:
        return f"{n} videos seleccionados", ""
    return f"{n} seleccionados", f"{_plural(fotos, 'foto', 'fotos')} y {_plural(videos, 'video', 'videos')}"


def resumen_de(medios, estados: dict) -> list[tuple[str, str]]:
    """Las filas de datos del resumen de varios elementos."""
    filas = []
    fechas = sorted(m.tomada for m in medios if m.tomada)
    if fechas:
        a, b = (f.astimezone() if f.tzinfo else f for f in (fechas[0], fechas[-1]))
        txt = f"{a:%d/%m/%Y}" if a.date() == b.date() else f"del {a:%d/%m/%Y} al {b:%d/%m/%Y}"
        sin = len(medios) - len(fechas)
        if sin:
            txt += f" ({_plural(sin, 'sin fecha', 'sin fecha')})"
        filas.append(("Fecha de la toma", txt))
    lugares = {m.lugar for m in medios}
    if lugares != {None}:
        if len(lugares) == 1:
            filas.append(("Lugar", next(iter(lugares))))
        else:
            sin = sum(m.lugar is None for m in medios)
            con = lugares - {None}
            txt = next(iter(con)) if len(con) == 1 else f"{len(con)} lugares distintos"
            filas.append(("Lugar", txt + (f" ({sin} sin lugar)" if sin else "")))
    cuantos: dict[str, int] = {}
    for m in medios:
        for e in m.eventos or []:
            cuantos[e] = cuantos.get(e, 0) + 1
    if cuantos:
        n = len(medios)
        filas.append(("Eventos", ", ".join(
            e if c == n else f"{e} ({c}/{n})"
            for e, c in sorted(cuantos.items(), key=lambda t: (-t[1], t[0])))))
    carpetas = {m.folder_origin or "" for m in medios}
    filas.append(("Carpeta", next(iter(carpetas)) or "—") if len(carpetas) == 1
                 else ("Carpetas", f"{len(carpetas)} distintas"))
    total = sum(m.size_bytes or 0 for m in medios)
    if total:
        filas.append(("Tamaño total", tamano_archivo(total)))
    seg = sum(m.duration or 0 for m in medios if m.kind == "video")
    if seg:
        filas.append(("Duración de los videos", duracion(seg)))
    return filas


def no_disponibles(medios, estados: dict) -> int:
    return sum(1 for m in medios if m.missing or estados.get(m.id))


def etiquetas_en_comun(medios) -> list[tuple[str, int, bool]]:
    """[(nombre, en cuántos, alguna sin confirmar)], las más repetidas primero."""
    cuantos, sin_confirmar = {}, set()
    for m in medios:
        for nombre, confirmada in m.etiquetas or []:
            cuantos[nombre] = cuantos.get(nombre, 0) + 1
            if not confirmada:
                sin_confirmar.add(nombre)
    return [(n, c, n in sin_confirmar)
            for n, c in sorted(cuantos.items(), key=lambda t: (-t[1], t[0]))]


# ---------------------------------------------------------------- panel
class PanelDetalles(QFrame):
    """El panel. `mostrar(medios, minis, estados)`: ninguno, uno o varios."""
    cerrar = pyqtSignal()
    ir_a = pyqtSignal(int)          # clic en un elemento de la lista: mostrarlo en la grilla
    guardar = pyqtSignal(int, str, str)     # (id, 'texto' | 'notas', lo escrito)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("detalle_panel")
        self.setMinimumWidth(ANCHO_MIN)
        self.media = None
        self._medios: list = []
        self._minis: dict = {}
        self._estados: dict = {}
        self._fuente: QPixmap | None = None
        self._ruta_original: str | None = None
        self._pedido = 0
        self._workers: set = set()
        self._espera = QTimer(self)
        self._espera.setSingleShot(True)
        self._espera.setInterval(_ESPERA_ORIGINAL_MS)
        self._espera.timeout.connect(self._leer_original)
        self._al_cambiar_ancho = QTimer(self)
        self._al_cambiar_ancho.setSingleShot(True)
        self._al_cambiar_ancho.setInterval(80)
        self._al_cambiar_ancho.timeout.connect(self._reacomodar)
        self._editable = True
        self._pendiente: dict[str, tuple[int, str]] = {}    # campo -> (id, valor) sin guardar
        self._enviado: dict[str, int] = {}                  # campo -> id, guardándose
        self._cargando = False
        self._espera_guardar = QTimer(self)
        self._espera_guardar.setSingleShot(True)
        self._espera_guardar.setInterval(_ESPERA_GUARDAR_MS)
        self._espera_guardar.timeout.connect(self.guardar_ya)

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(0, 0, 0, 0)
        raiz.setSpacing(0)

        cab = QHBoxLayout()
        cab.setContentsMargins(16, 10, 8, 6)
        t = QLabel("Detalles")
        t.setObjectName("section_title")
        cab.addWidget(t, 1)
        self.btn_cerrar = QPushButton()
        self.btn_cerrar.setObjectName("btn_icon")
        self.btn_cerrar.setToolTip("Cerrar los detalles (tecla I)")
        self.btn_cerrar.setAccessibleName("Cerrar los detalles")
        self.btn_cerrar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cerrar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        icons.set_icon(self.btn_cerrar, "x", 16, "text_2")
        self.btn_cerrar.clicked.connect(self.cerrar)
        cab.addWidget(self.btn_cerrar)
        raiz.addLayout(cab)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        raiz.addWidget(self._scroll, 1)
        self._cuerpo = QWidget()
        self._cuerpo.setObjectName("detalle_cuerpo")
        self._scroll.setWidget(self._cuerpo)
        self._lay = QVBoxLayout(self._cuerpo)
        self._lay.setContentsMargins(16, 4, 16, 16)
        self._lay.setSpacing(14)

        self.lbl_vacio = QLabel("Elija una foto o un video para ver sus detalles.")
        self.lbl_vacio.setObjectName("muted")
        self.lbl_vacio.setWordWrap(True)
        self.lbl_vacio.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lay.addWidget(self.lbl_vacio)

        self.imagen = QLabel()
        self.imagen.setObjectName("detalle_imagen")
        self.imagen.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.imagen.setFixedHeight(_ALTO_IMAGEN)
        self.imagen.setMinimumWidth(10)
        self.imagen.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._lay.addWidget(self.imagen)

        cabeza = QVBoxLayout()
        cabeza.setSpacing(2)
        self.lbl_nombre = _etiqueta("detalle_nombre")
        cabeza.addWidget(self.lbl_nombre)
        self.lbl_carpeta = _etiqueta("muted")
        cabeza.addWidget(self.lbl_carpeta)
        self._lay.addLayout(cabeza)

        self.aviso = _etiqueta("detalle_aviso", seleccionable=False)
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
        self.btn_mapa.setVisible(False)

        self.sec_etiquetas = _Seccion("Etiquetas")
        self._lay.addWidget(self.sec_etiquetas)
        self.sec_texto = _Seccion("Texto en la foto")
        self.txt_texto = self._editor(
            "No se encontró texto en esta foto. Si tiene, puede escribirlo aquí.",
            "El texto que aparece en la foto. Puede corregirlo: lo que usted escriba "
            "se usa en la búsqueda y ya no se vuelve a leer de la foto.")
        self.lbl_texto = _etiqueta("muted", seleccionable=False)
        self.sec_texto.cuerpo.addWidget(self.txt_texto)
        self.sec_texto.cuerpo.addWidget(self.lbl_texto)
        self._lay.addWidget(self.sec_texto)
        self.sec_notas = _Seccion("Notas")
        self.txt_notas = self._editor(
            "Escriba aquí lo que convenga recordar: quién aparece, para qué se usó…",
            "Notas sobre este elemento. Se guardan solas y se encuentran con la búsqueda.")
        self.lbl_notas = _etiqueta("muted", seleccionable=False)
        self.sec_notas.cuerpo.addWidget(self.txt_notas)
        self.sec_notas.cuerpo.addWidget(self.lbl_notas)
        self._lay.addWidget(self.sec_notas)
        self._campos = {"texto": (self.txt_texto, self.lbl_texto),
                        "notas": (self.txt_notas, self.lbl_notas)}
        for campo, (editor, _lbl) in self._campos.items():
            editor.textChanged.connect(lambda c=campo: self._escribio(c))
        self.sec_ubicacion = _Seccion("Ubicación del archivo")
        self.lbl_ruta = _etiqueta("muted")
        self.sec_ubicacion.cuerpo.addWidget(self.lbl_ruta)
        self._lay.addWidget(self.sec_ubicacion)
        self.sec_lista = _Seccion("Elegidos")
        self._lay.addWidget(self.sec_lista)
        self._lay.addStretch(1)

        self.pie = QFrame()
        self.pie.setObjectName("detalle_pie")
        pl = QHBoxLayout(self.pie)
        pl.setContentsMargins(16, 8, 16, 10)
        ayuda = QLabel("Tecla I: abrir o cerrar")
        ayuda.setObjectName("muted")
        pl.addWidget(ayuda, 1)
        self.btn_abrir = QPushButton("Abrir")
        self.btn_abrir.setToolTip("Abre el original en el visor de imágenes o el reproductor")
        self.btn_abrir.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_abrir.clicked.connect(self._abrir)
        pl.addWidget(self.btn_abrir)
        raiz.addWidget(self.pie)
        self.mostrar([])

    # ------------------------------------------------------------- contenido
    def mostrar(self, medios: list, minis: dict | None = None, estados: dict | None = None,
                actual=None):
        """`medios`: lo elegido (Media). `minis` {id: miniatura local}; `estados`
        {id: 'missing'/'offline'}; `actual`: el que se muestra si hay uno solo."""
        otro = len(medios) != 1 or self.media is None or medios[0].id != self.media.id
        if otro:
            self.guardar_ya()           # lo escrito en el anterior no se pierde
        self._medios, self._minis, self._estados = list(medios), minis or {}, estados or {}
        n = len(self._medios)
        uno = n == 1
        for w in (self.imagen, self.lbl_nombre, self.sec_ubicacion):
            w.setVisible(n > 0 and (uno or w is self.lbl_nombre))
        self.lbl_vacio.setVisible(n == 0)
        self.sec_lista.setVisible(n > 1)
        self.btn_abrir.setVisible(uno)
        if n == 0:
            self.media = None
            for w in (self.lbl_carpeta, self.aviso, self.sec_etiquetas, self.sec_texto,
                      self.sec_notas):
                w.setVisible(False)
            self._limpiar_datos()
            self.btn_mapa.setVisible(False)
        elif uno:
            self._mostrar_uno(self._medios[0])
        else:
            self.media = None
            self._pedido += 1
            self._mostrar_varios()
        self._ajustar_alto()
        QTimer.singleShot(0, self._ajustar_alto)       # ya con el estilo aplicado

    def _mostrar_uno(self, m):
        estado = self._estados.get(m.id)
        mismo = self.media is not None and self.media.id == m.id
        self.media = m
        self.lbl_nombre.setText(m.filename)
        self.lbl_carpeta.setText(m.folder_origin or "")
        self.lbl_carpeta.setVisible(bool(m.folder_origin))
        aviso = aviso_de(m, estado)
        self.aviso.setText(aviso)
        self.aviso.setVisible(bool(aviso))
        self._llenar_datos(datos_de(m), m.lat, m.lon)
        self._llenar_etiquetas([(nombre, 1, not conf) for nombre, conf in m.etiquetas or []], 1)
        self._cargar("texto", m.texto or "", mismo)
        self._cargar("notas", m.notas or "", mismo)
        self.sec_texto.setVisible(m.kind != "video")
        self.sec_notas.setVisible(True)
        self.lbl_ruta.setText(m.path)
        disponible = not estado and not m.missing
        self.btn_abrir.setEnabled(disponible)
        if not mismo:
            self._poner_imagen(self._minis.get(m.id))
            self._pedido += 1
            self._ruta_original = m.path if (disponible and m.kind == "image") else None
            self._espera.start()

    def _mostrar_varios(self):
        medios = self._medios
        titulo, sub = titulo_varios(medios)
        self.lbl_nombre.setText(titulo)
        self.lbl_carpeta.setText(sub)
        self.lbl_carpeta.setVisible(bool(sub))
        faltan = no_disponibles(medios, self._estados)
        self.aviso.setText("" if not faltan else (
            "1 de los elegidos no está disponible: no puede usarse en ProPresenter."
            if faltan == 1 else
            f"{faltan} de los elegidos no están disponibles: no pueden usarse en ProPresenter."))
        self.aviso.setVisible(bool(faltan))
        self._llenar_datos(resumen_de(medios, self._estados))
        self._llenar_etiquetas(etiquetas_en_comun(medios), len(medios))
        self.sec_texto.setVisible(False)
        self.sec_notas.setVisible(False)
        self._llenar_lista()

    # ------------------------------------------------------------- texto y notas
    def _editor(self, vacio: str, ayuda: str) -> QPlainTextEdit:
        e = QPlainTextEdit()
        e.setObjectName("detalle_texto")
        e.setPlaceholderText(vacio)
        e.setToolTip(ayuda)
        e.setTabChangesFocus(True)
        e.setFixedHeight(96)
        e.installEventFilter(self)
        return e

    def _cargar(self, campo: str, valor: str, mismo: bool):
        """Pone lo guardado en el cuadro, salvo que el operador esté escribiendo
        en ese mismo elemento (un aviso del NAS no le pisa lo que escribe)."""
        editor, lbl = self._campos[campo]
        if mismo and (editor.hasFocus() or campo in self._pendiente
                      or self._enviado.get(campo) == self.media.id):
            return
        if not mismo:
            lbl.setVisible(False)
        if editor.toPlainText() != valor:
            self._cargando = True
            editor.setPlainText(valor)
            self._cargando = False

    def _escribio(self, campo: str):
        if self._cargando or self.media is None:
            return
        editor, lbl = self._campos[campo]
        self._pendiente[campo] = (self.media.id, editor.toPlainText())
        lbl.setVisible(False)
        self._espera_guardar.start()

    def guardar_ya(self):
        """Manda a guardar lo escrito que falte (al elegir otro, al salir del cuadro…)."""
        self._espera_guardar.stop()
        pendiente, self._pendiente = self._pendiente, {}
        for campo, (mid, valor) in pendiente.items():
            self._enviado[campo] = mid
            self.guardar.emit(mid, campo, valor)

    def guardado(self, mid: int, campo: str, error: str | None):
        """Respuesta de Buscar: se guardó (error None) o no se pudo."""
        if self._enviado.get(campo) == mid:
            del self._enviado[campo]
        if self.media is None or self.media.id != mid:
            return
        editor, lbl = self._campos[campo]
        if error and campo not in self._pendiente:
            # Queda pendiente: se reintenta al salir del cuadro o al elegir otro,
            # y mientras tanto un aviso del NAS no lo borra del cuadro
            self._pendiente[campo] = (mid, editor.toPlainText())
        lbl.setObjectName("detalle_aviso_texto" if error else "muted")
        lbl.style().unpolish(lbl)
        lbl.style().polish(lbl)
        lbl.setText(("No se pudo guardar. " + error) if error else "Guardado.")
        lbl.setVisible(True)
        if not error:
            QTimer.singleShot(2500, lambda: lbl.text() == "Guardado." and lbl.setVisible(False))

    def set_editable(self, si: bool):
        """Sin conexión con el NAS no se puede escribir (no quedaría guardado)."""
        self._editable = si
        for editor, _lbl in self._campos.values():
            editor.setReadOnly(not si)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.FocusOut and obj in (self.txt_texto, self.txt_notas):
            self.guardar_ya()
        return super().eventFilter(obj, event)

    def hideEvent(self, event):
        self.guardar_ya()
        super().hideEvent(event)

    def _ajustar_alto(self):
        # Con textos en varias líneas el área de desplazamiento apretaba todo
        # (la imagen tapaba el nombre): el alto mínimo es el que necesita.
        self._lay.invalidate()
        ancho = self._cuerpo.width() if self._cuerpo.width() > 100 else ANCHO
        self._cuerpo.setMinimumHeight(max(0, self._lay.heightForWidth(ancho)))

    def _ancho_util(self) -> int:
        ancho = self._scroll.viewport().width()
        return (ancho if ancho > 100 else ANCHO) - 32

    def _limpiar_datos(self):
        while self._datos.count():
            w = self._datos.takeAt(0).widget()
            if w is not None and w is not self.btn_mapa:
                w.setParent(None)          # que desaparezca ya (deleteLater es diferido)
                w.deleteLater()

    def _llenar_datos(self, filas, lat=None, lon=None):
        self._limpiar_datos()
        for i, (nombre, valor) in enumerate(filas):
            k = QLabel(nombre)
            k.setObjectName("muted")
            k.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            v = _etiqueta("detalle_aviso_texto" if nombre == "Calidad" else "detalle_valor")
            v.setText(valor)
            self._datos.addWidget(k, i, 0)
            self._datos.addWidget(v, i, 1)
        if lat is not None and lon is not None:
            k = QLabel("Lugar")
            k.setObjectName("muted")
            self._datos.addWidget(k, len(filas), 0)
            self._datos.addWidget(self.btn_mapa, len(filas), 1, Qt.AlignmentFlag.AlignLeft)
            self.btn_mapa.setVisible(True)
        else:
            self.btn_mapa.setVisible(False)

    def _llenar_etiquetas(self, etiquetas: list[tuple[str, int, bool]], total: int):
        """`etiquetas`: [(nombre, en cuántos, sin confirmar)]."""
        cuerpo = self.sec_etiquetas.cuerpo
        _vaciar(cuerpo)
        self.sec_etiquetas.setVisible(True)
        if not etiquetas:
            vacio = _etiqueta("muted", seleccionable=False)
            vacio.setText("Sin etiquetas. Agréguelas desde la barra «Etiquetas» de Buscar."
                          if total == 1 else "Ninguno tiene etiquetas.")
            cuerpo.addWidget(vacio)
            return
        chips = []
        for nombre, cuantos, ia in etiquetas:
            c = QLabel(nombre if cuantos == total else f"{nombre} ({cuantos}/{total})")
            c.setObjectName("detalle_chip_ia" if ia else "detalle_chip")
            if ia:
                c.setToolTip("Sugerida por la IA: confírmela o quítela desde la barra "
                             "«Etiquetas» de Buscar")
            chips.append(c)
        _en_renglones(cuerpo, chips, self._ancho_util())
        if any(ia for _n, _c, ia in etiquetas):
            nota = _etiqueta("muted", seleccionable=False)
            nota.setText("Las punteadas son sugerencias de la IA sin confirmar.")
            cuerpo.addWidget(nota)

    def _llenar_lista(self):
        cuerpo = self.sec_lista.cuerpo
        _vaciar(cuerpo)
        for m in self._medios[:_LISTA_MAX]:
            cuerpo.addWidget(_Fila(m, self._minis.get(m.id), self._estados.get(m.id),
                                   self.ir_a))
        resto = len(self._medios) - _LISTA_MAX
        if resto > 0:
            mas = _etiqueta("muted", seleccionable=False)
            mas.setText(f"y {resto} más")
            cuerpo.addWidget(mas)

    # ------------------------------------------------------------- imagen
    def _poner_imagen(self, ruta: str | None):
        px = QPixmap(ruta) if ruta else QPixmap()
        self._fuente = None if px.isNull() else px
        self._escalar()

    def _escalar(self):
        if self._fuente is None:
            self.imagen.setPixmap(QPixmap())
            self.imagen.setText("Sin vista previa")
            return
        dpr = self.devicePixelRatioF() or 1.0
        caja = QSize(self._ancho_util() - 2, _ALTO_IMAGEN - 2)      # dentro del borde
        px = self._fuente.scaled(caja * dpr, Qt.AspectRatioMode.KeepAspectRatio,
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
            self._fuente = QPixmap.fromImage(img)
            self._escalar()

    # ------------------------------------------------------------- tamaño
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self._al_cambiar_ancho.start()

    def _reacomodar(self):
        """Cambió el ancho (el operador movió la división): imagen y etiquetas."""
        if self.media is not None:
            self._escalar()
            m = self.media
            self._llenar_etiquetas([(n, 1, not c) for n, c in m.etiquetas or []], 1)
        elif len(self._medios) > 1:
            self._llenar_etiquetas(etiquetas_en_comun(self._medios), len(self._medios))
        self._ajustar_alto()

    def showEvent(self, event):
        super().showEvent(event)
        if self.media is not None and self._ruta_original:
            self._espera.start()

    # ------------------------------------------------------------- acciones
    def _abrir(self):
        if self.media is not None:
            open_file(self.media.path)

    def _ver_mapa(self):
        m = self.media
        if m is not None and m.lat is not None and m.lon is not None:
            QDesktopServices.openUrl(QUrl(mapa(m.lat, m.lon)))


class _Fila(QFrame):
    """Un elegido en la lista: miniatura, nombre y datos. Clic: se muestra en la grilla."""

    def __init__(self, m, mini: str | None, estado: str | None, ir_a):
        super().__init__()
        self.setObjectName("detalle_fila")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Mostrarlo en la grilla")
        self._id, self._ir_a = m.id, ir_a
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(10)
        img = QLabel()
        img.setObjectName("detalle_fila_mini")
        img.setFixedSize(_MINI_LISTA, _MINI_LISTA)
        img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        px = QPixmap(mini) if mini else QPixmap()
        if not px.isNull():
            dpr = self.devicePixelRatioF() or 1.0
            px = px.scaled(QSize(_MINI_LISTA, _MINI_LISTA) * dpr,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
            px.setDevicePixelRatio(dpr)
            img.setPixmap(px)
        lay.addWidget(img)
        textos = QVBoxLayout()
        textos.setSpacing(1)
        nombre = QLabel(m.filename)
        nombre.setObjectName("detalle_valor")
        textos.addWidget(nombre)
        partes = []
        if m.tomada:
            f = m.tomada.astimezone() if m.tomada.tzinfo else m.tomada
            partes.append(f"{f:%d/%m/%Y}")
        if m.kind == "video" and m.duration:
            partes.append(duracion(m.duration))
        if m.size_bytes:
            partes.append(tamano_archivo(m.size_bytes))
        sub = QLabel(" · ".join(partes) or ("Video" if m.kind == "video" else "Foto"))
        sub.setObjectName("muted")
        textos.addWidget(sub)
        if estado or m.missing:
            falta = QLabel("No está disponible")
            falta.setObjectName("detalle_aviso_texto")
            textos.addWidget(falta)
        lay.addLayout(textos, 1)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._ir_a.emit(self._id)
        super().mousePressEvent(event)


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


def _etiqueta(nombre: str, seleccionable: bool = True) -> QLabel:
    lbl = QLabel()
    lbl.setObjectName(nombre)
    lbl.setWordWrap(True)
    if seleccionable:
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


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


def _vaciar(lay):
    while lay.count():
        _borrar(lay.takeAt(0))


def _borrar(item):
    w = item.widget()
    if w is not None:
        w.setParent(None)
        w.deleteLater()
        return
    sub = item.layout()
    if sub is not None:
        _vaciar(sub)
        sub.deleteLater()


def estado_de_item(item) -> str | None:
    """'missing'/'offline' según la celda de la grilla (None si se puede usar)."""
    from .media_grid import STATE_ROLE
    st = item.data(STATE_ROLE)
    return st if st in ("missing", "offline") else None
