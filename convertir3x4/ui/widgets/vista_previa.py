"""Vista previa de lo que va a crear Convertir, y los iconos de "por pantalla".

Rediseño del 15/11/2026: con dos videos no quedaba claro si salían dos videos o
uno con los dos. La vista previa dibuja cada pantalla que se va a crear, con la
proporción de salida y las fotos y videos ubicados con el MISMO convert/layout.py
que usa la conversión: lo que se ve acá es lo que sale.
"""

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ...convert import batch
from ...convert.layout import dos_por_pantalla, sobre_fondo
from ..style import DARK, T


def icono_pantalla(n: int, aspect: tuple[int, int], size: int, color: str) -> QPixmap:
    """Pantalla con la forma de la salida y `n` (1 o 2) fotos/videos adentro,
    ubicados como los arma la app (arriba/abajo o lado a lado)."""
    aw, ah = aspect
    ratio = 2
    px = QPixmap(size * ratio, size * ratio)
    px.setDevicePixelRatio(ratio)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    m = 2.5
    esc = min((size - 2 * m) / aw, (size - 2 * m) / ah)
    w, h = aw * esc, ah * esc
    pant = QRectF((size - w) / 2, (size - h) / 2, w, h)
    c = QColor(color)
    p.setPen(QPen(c, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(pant, 2.5, 2.5)
    relleno = QColor(c)
    relleno.setAlphaF(0.35)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(relleno)
    pad = min(w, h) * 0.16
    adentro = pant.adjusted(pad, pad, -pad, -pad)
    if n == 1:
        p.drawRoundedRect(adentro, 1.2, 1.2)
    else:
        sep = min(w, h) * 0.08
        if h >= w:
            hh = (adentro.height() - sep) / 2
            p.drawRoundedRect(QRectF(adentro.left(), adentro.top(), adentro.width(), hh), 1.2, 1.2)
            p.drawRoundedRect(QRectF(adentro.left(), adentro.top() + hh + sep,
                                     adentro.width(), hh), 1.2, 1.2)
        else:
            ww = (adentro.width() - sep) / 2
            p.drawRoundedRect(QRectF(adentro.left(), adentro.top(), ww, adentro.height()), 1.2, 1.2)
            p.drawRoundedRect(QRectF(adentro.left() + ww + sep, adentro.top(),
                                     ww, adentro.height()), 1.2, 1.2)
    p.end()
    return px


class VistaPrevia(QWidget):
    """Una miniatura por cada pantalla que se va a crear. Solo para mirar: el
    orden lo decide la grilla (15/11/2026: se sacó "tocar para invertir")."""

    HUECO = 8
    MAX = 12            # más que esto: "y N más"
    ALTO_MAX = 170      # una pantalla vertical sola no se estira de más

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs: list = []
        self._aspect = (8, 9)
        self._datos = None      # funciones: pixmap(path), tamano(path), fondo()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Vista previa de lo que se va a crear")

    def set_datos(self, jobs, aspect, pixmap_de, tamano_de, fondo=None):
        self._jobs = list(jobs)
        self._aspect = aspect
        self._datos = (pixmap_de, tamano_de, fondo)
        self.setFixedHeight(self._alto())
        self.updateGeometry()
        self.update()

    # ------------------------------------------------------------- geometría
    # El tamaño se adapta: con pocas pantallas, más grandes; una pantalla
    # horizontal ocupa todo el ancho del panel (si no, quedaba una tira finita).
    def _columnas(self) -> int:
        aw, ah = self._aspect
        n = len(self._jobs)
        if aw > ah:
            return 1 if n <= 3 else 2
        return 2 if n <= 2 else 3

    def _celda_tam(self) -> tuple[float, float]:
        aw, ah = self._aspect
        cols = self._columnas()
        ancho = max(60, self.width() or 240)
        cw = (ancho - (cols - 1) * self.HUECO) / cols
        ch = cw * ah / aw
        if ch > self.ALTO_MAX:
            ch = self.ALTO_MAX
            cw = ch * aw / ah
        return cw, ch

    def _visibles(self) -> int:
        return min(len(self._jobs), self.MAX)

    def _alto(self) -> int:
        n = self._visibles() + (1 if len(self._jobs) > self.MAX else 0)
        if not n:
            return 0
        filas = -(-n // self._columnas())
        _, ch = self._celda_tam()
        return int(filas * ch + (filas - 1) * self.HUECO)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.setFixedHeight(self._alto())

    def sizeHint(self) -> QSize:
        return QSize(240, self._alto())

    def _celda(self, i: int) -> QRectF:
        cols = self._columnas()
        cw, ch = self._celda_tam()
        f, c = divmod(i, cols)
        return QRectF(c * (cw + self.HUECO), f * (ch + self.HUECO), cw, ch)

    def _pantalla(self, celda: QRectF) -> QRectF:
        return celda.adjusted(1, 1, -1, -1)

    # ------------------------------------------------------------- dibujo
    def paintEvent(self, e):
        if not self._jobs or not self._datos:
            return
        pixmap_de, tamano_de, fondo = self._datos
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for i, job in enumerate(self._jobs[:self.MAX]):
            pant = self._pantalla(self._celda(i))
            marco = QPainterPath()
            marco.addRoundedRect(pant, 3, 3)
            # Fondo: el elegido (sobre un fondo) o un gris neutro (fondo de colores)
            if job.con_fondo and fondo is not None and not fondo.isNull():
                p.save()
                p.setClipPath(marco)
                p.drawPixmap(pant, fondo, _recorte_cubrir(fondo, pant))
                p.restore()
            else:
                p.fillPath(marco, QColor(T.hover))
            p.save()
            p.setClipPath(marco)
            for path, r in zip(job.inputs, self._lugares(job, pant, tamano_de)):
                pm = pixmap_de(path)
                if pm is not None and not pm.isNull():
                    p.drawPixmap(r, pm, QRectF(pm.rect()))
                else:
                    p.fillRect(r, QColor(T.text_3))
            p.restore()
            p.setPen(QPen(QColor(T.border_strong), 1))
            p.drawPath(marco)
            if job.is_video:
                self._insignia(p, pant)
        if len(self._jobs) > self.MAX:
            r = self._celda(self.MAX)
            p.setPen(QColor(T.text_3))
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), f"y {len(self._jobs) - self.MAX} más")
        p.end()

    def _lugares(self, job, pant: QRectF, tamano_de) -> list[QRectF]:
        """Dónde va cada foto/video, con la misma regla que la conversión."""
        w, h = max(2, int(pant.width())), max(2, int(pant.height()))
        tams = [tamano_de(x) or (16, 9) for x in job.inputs]
        if len(job.inputs) == 2:
            rects = dos_por_pantalla(tams[0], tams[1], w, h)
        elif job.mode == batch.FONDO:
            rects = [sobre_fondo(tams[0], w, h)]
        else:   # una sola, entera y centrada
            tw, th = tams[0]
            s = min(w / tw, h / th)
            fw, fh = tw * s, th * s
            rects = [((w - fw) / 2, (h - fh) / 2, fw, fh)]
        return [QRectF(pant.left() + x, pant.top() + y, rw, rh) for x, y, rw, rh in rects]

    def _insignia(self, p: QPainter, pant: QRectF):
        """▶ abajo a la derecha: esta pantalla sale como video."""
        r = QRectF(pant.right() - 17, pant.bottom() - 15, 14, 12)
        fondo = QPainterPath()
        fondo.addRoundedRect(r, 3, 3)
        p.fillPath(fondo, QColor(0, 0, 0, 170))
        tri = QPainterPath()
        cx, cy = r.center().x(), r.center().y()
        tri.moveTo(cx - 2.5, cy - 3.5)
        tri.lineTo(cx + 3.5, cy)
        tri.lineTo(cx - 2.5, cy + 3.5)
        tri.closeSubpath()
        p.fillPath(tri, QColor(DARK["text"]))


def _recorte_cubrir(pm: QPixmap, destino: QRectF) -> QRectF:
    """Parte de `pm` que cubre `destino` sin deformar (como el fondo real)."""
    pw, ph = pm.width(), pm.height()
    s = max(destino.width() / pw, destino.height() / ph)
    w, h = destino.width() / s, destino.height() / s
    return QRectF((pw - w) / 2, (ph - h) / 2, w, h)
