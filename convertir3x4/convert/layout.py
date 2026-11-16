"""Dónde va cada foto o video dentro de la pantalla. Sin Qt ni PIL.

Lo usan las fotos (images.py), los videos (video.py, clips.py) y la vista
previa del panel de Convertir: lo que muestra la vista previa es exactamente
lo que sale.

Reglas (15/11/2026):
- "2 por pantalla": en una pantalla vertical o cuadrada, uno arriba y otro
  abajo; en una horizontal, uno al lado del otro. El primero de la grilla va
  arriba (o a la izquierda). Los dos se agrandan lo más posible sin salirse.
- "Sobre un fondo": vertical, en la mitad de arriba (abajo queda lugar para
  texto); horizontal, en la mitad izquierda (a la derecha queda el lugar).

Las medidas que devuelve son enteras y pares (lo piden los videos).
"""

Rect = tuple[int, int, int, int]   # x, y, ancho, alto


def es_horizontal(w: float, h: float) -> bool:
    return w > h


def _par(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def lienzo_para(tamanos: list[tuple[int, int]], aspect: tuple[int, int]) -> tuple[int, int]:
    """Tamaño del lienzo para fotos: no achica la foto más grande.

    Vertical o cuadrado: el ancho es el de la foto más ancha. Horizontal: el
    alto es el de la foto más alta."""
    aw, ah = aspect
    if es_horizontal(aw, ah):
        h = max(t[1] for t in tamanos)
        return int(h * aw / ah), h
    w = max(t[0] for t in tamanos)
    return w, int(w * ah / aw)


def dos_por_pantalla(a: tuple[int, int], b: tuple[int, int], w: int, h: int) -> list[Rect]:
    """Lugar de `a` (primero) y `b` (segundo) en una pantalla de w x h."""
    m = round(min(w, h) * 0.03)
    if not es_horizontal(w, h):
        # Uno arriba y otro abajo: los dos al ancho disponible y, si no entran
        # en alto, se achican los dos en la misma proporción.
        ancho = w - 2 * m
        h1, h2 = ancho * a[1] / a[0], ancho * b[1] / b[0]
        s = min(1.0, (h - 3 * m) / (h1 + h2))
        r1 = (_par(ancho * s), _par(h1 * s))
        r2 = (_par(ancho * s), _par(h2 * s))
        hueco = (h - r1[1] - r2[1]) // 3
        return [((w - r1[0]) // 2, hueco, *r1),
                ((w - r2[0]) // 2, hueco * 2 + r1[1], *r2)]
    alto = h - 2 * m
    w1, w2 = alto * a[0] / a[1], alto * b[0] / b[1]
    s = min(1.0, (w - 3 * m) / (w1 + w2))
    r1 = (_par(w1 * s), _par(alto * s))
    r2 = (_par(w2 * s), _par(alto * s))
    hueco = (w - r1[0] - r2[0]) // 3
    return [(hueco, (h - r1[1]) // 2, *r1),
            (hueco * 2 + r1[0], (h - r2[1]) // 2, *r2)]


def sobre_fondo(t: tuple[int, int], w: int, h: int) -> Rect:
    """Lugar de la foto o el video sobre un fondo elegido, dejando lugar para texto."""
    if not es_horizontal(w, h):
        m = int(w * 0.05)
        caja_w, caja_h = w - 2 * m, h * 0.5 - m
        s = min(caja_w / t[0], caja_h / t[1])
        fw, fh = _par(t[0] * s), _par(t[1] * s)
        return ((w - fw) // 2, m, fw, fh)
    m = int(h * 0.05)
    caja_w, caja_h = w * 0.5 - m, h - 2 * m
    s = min(caja_w / t[0], caja_h / t[1])
    fw, fh = _par(t[0] * s), _par(t[1] * s)
    return (m, (h - fh) // 2, fw, fh)
