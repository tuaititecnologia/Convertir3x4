"""Procesamiento de imágenes: recorte a aspect ratio con fondos texturizados.

Funciones puras, sin estado ni dependencia de Qt — reutilizables por el resto
del administrador (motor, miniaturas, etc.).
"""

import random

from PIL import Image, ImageDraw, ImageFilter

from ..config import JPEG_QUALITY, DEFAULT_ASPECT_W, DEFAULT_ASPECT_H
from .layout import dos_por_pantalla, es_horizontal, lienzo_para, sobre_fondo


def _darken(color: tuple[int, int, int], factor: float = 0.6) -> tuple[int, int, int]:
    """Oscurece un color RGB multiplicando cada canal por *factor* (0-1)."""
    return tuple(int(c * factor) for c in color)


def _dominant_colors(img: Image.Image, n: int = 5) -> list[tuple[int, int, int]]:
    """Extrae los N colores más frecuentes de una imagen (reducida), oscurecidos."""
    small = img.copy()
    small.thumbnail((80, 80), Image.LANCZOS)
    # Cuantizar a pocos colores
    quantized = small.quantize(colors=n, method=Image.Quantize.FASTOCTREE)
    palette = quantized.getpalette()
    # Contar frecuencia de cada índice
    counts = sorted(quantized.getcolors(), reverse=True)
    colors = []
    for _, idx in counts[:n]:
        r, g, b = palette[idx * 3], palette[idx * 3 + 1], palette[idx * 3 + 2]
        colors.append(_darken((r, g, b)))
    return colors


def _vary_color(color: tuple[int, int, int], amount: int = 35) -> tuple[int, int, int]:
    """Varía ligeramente un color RGB."""
    return tuple(max(0, min(255, c + random.randint(-amount, amount))) for c in color)


def _make_textured_bg(width: int, height: int, colors: list[tuple[int, int, int]]) -> Image.Image:
    """Genera un fondo con textura suave usando los colores dominantes de la foto."""
    base_color = colors[0]
    bg = Image.new("RGB", (width, height), base_color)
    draw = ImageDraw.Draw(bg)

    # Dibujar manchas suaves con los colores de la paleta
    n_blobs = random.randint(8, 15)
    for _ in range(n_blobs):
        color = _vary_color(random.choice(colors), 30)
        # Elipses grandes en posiciones aleatorias
        cx = random.randint(-width // 4, width + width // 4)
        cy = random.randint(-height // 4, height + height // 4)
        rx = random.randint(width // 4, width)
        ry = random.randint(height // 4, height)
        draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=color)

    # Blur fuerte para suavizar las manchas
    bg = bg.filter(ImageFilter.GaussianBlur(radius=min(width, height) // 4))
    return bg


def _make_zoned_bg(
    width: int, height: int,
    colors_top: list[tuple[int, int, int]],
    colors_bottom: list[tuple[int, int, int]],
) -> Image.Image:
    """Genera un fondo donde los colores de arriba dominan arriba y los de abajo dominan abajo."""
    bg = Image.new("RGB", (width, height), colors_top[0])
    draw = ImageDraw.Draw(bg)

    # Manchas con colores de arriba en la mitad superior
    for _ in range(random.randint(5, 8)):
        color = _vary_color(random.choice(colors_top), 30)
        cx = random.randint(-width // 4, width + width // 4)
        cy = random.randint(-height // 4, height // 2)
        rx = random.randint(width // 4, width)
        ry = random.randint(height // 6, height // 3)
        draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=color)

    # Manchas con colores de abajo en la mitad inferior
    for _ in range(random.randint(5, 8)):
        color = _vary_color(random.choice(colors_bottom), 30)
        cx = random.randint(-width // 4, width + width // 4)
        cy = random.randint(height // 2, height + height // 4)
        rx = random.randint(width // 4, width)
        ry = random.randint(height // 6, height // 3)
        draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=color)

    # Blur fuerte para fusionar todo suavemente
    bg = bg.filter(ImageFilter.GaussianBlur(radius=min(width, height) // 4))
    return bg


def convert_centered(src_path: str, out_path: str, aspect: tuple[int, int] = None):
    """Convierte una imagen al aspect ratio configurado con fondo de color dominante texturizado."""
    aw, ah = aspect or (DEFAULT_ASPECT_W, DEFAULT_ASPECT_H)
    img = Image.open(src_path).convert("RGB")
    src_w, src_h = img.size

    if src_w / src_h > aw / ah:
        canvas_w = src_w
        canvas_h = int(src_w * ah / aw)
    else:
        canvas_h = src_h
        canvas_w = int(src_h * aw / ah)

    # Fondo: textura con colores dominantes de la foto
    colors = _dominant_colors(img)
    bg = _make_textured_bg(canvas_w, canvas_h, colors)

    # Centrar la imagen original
    x = (canvas_w - src_w) // 2
    y = (canvas_h - src_h) // 2
    bg.paste(img, (x, y))

    bg.save(out_path, "JPEG", quality=JPEG_QUALITY)


def cubrir(img: Image.Image, w: int, h: int) -> Image.Image:
    """`img` agrandada para cubrir w x h sin deformar, recortada al centro."""
    s = max(w / img.width, h / img.height)
    r = img.resize((max(w, round(img.width * s)), max(h, round(img.height * s))), Image.LANCZOS)
    x, y = (r.width - w) // 2, (r.height - h) // 2
    return r.crop((x, y, x + w, y + h))


def convert_centered_on(src_path: str, bg_path: str, out_path: str,
                        aspect: tuple[int, int] = None):
    """Foto entera y centrada sobre un fondo elegido (la que queda sola en
    "2 por pantalla" con fondo elegido)."""
    aw, ah = aspect or (DEFAULT_ASPECT_W, DEFAULT_ASPECT_H)
    img = Image.open(src_path).convert("RGB")
    canvas_w, canvas_h = lienzo_para([img.size], (aw, ah))
    s = min(canvas_w / img.width, canvas_h / img.height)
    fw, fh = round(img.width * s), round(img.height * s)
    bg = cubrir(Image.open(bg_path).convert("RGB"), canvas_w, canvas_h)
    bg.paste(img.resize((fw, fh), Image.LANCZOS), ((canvas_w - fw) // 2, (canvas_h - fh) // 2))
    bg.save(out_path, "JPEG", quality=JPEG_QUALITY)


def convert_double(paths: list[str], out_path: str, aspect: tuple[int, int] = None,
                   bg_path: str | None = None):
    """2 fotos en la misma pantalla (o 1 sola), sobre un fondo de sus colores o,
    si se pasa `bg_path`, sobre ese fondo."""
    aw, ah = aspect or (DEFAULT_ASPECT_W, DEFAULT_ASPECT_H)
    images = [Image.open(p).convert("RGB") for p in paths]

    if len(images) == 1:
        # La que queda sola (cantidad impar) sale centrada, como en la vista previa
        if bg_path:
            convert_centered_on(paths[0], bg_path, out_path, aspect=(aw, ah))
        else:
            convert_centered(paths[0], out_path, aspect=(aw, ah))
        return

    # 2 fotos: dónde va cada una lo decide convert/layout.py (igual que en la
    # vista previa): arriba/abajo en vertical, lado a lado en horizontal.
    img_a, img_b = images[0], images[1]
    canvas_w, canvas_h = lienzo_para([img_a.size, img_b.size], (aw, ah))
    (x1, y1, w1, h1), (x2, y2, w2, h2) = dos_por_pantalla(img_a.size, img_b.size,
                                                          canvas_w, canvas_h)
    bg = (cubrir(Image.open(bg_path).convert("RGB"), canvas_w, canvas_h) if bg_path else
          fondo_dos_zonas(canvas_w, canvas_h, _dominant_colors(img_a), _dominant_colors(img_b)))
    bg.paste(img_a.resize((w1, h1), Image.LANCZOS), (x1, y1))
    bg.paste(img_b.resize((w2, h2), Image.LANCZOS), (x2, y2))
    bg.save(out_path, "JPEG", quality=JPEG_QUALITY)


def fondo_dos_zonas(w: int, h: int, colores_a, colores_b) -> Image.Image:
    """Fondo de colores repartido en dos zonas: arriba/abajo en una pantalla
    vertical, izquierda/derecha en una horizontal (se arma vertical y se gira)."""
    if not es_horizontal(w, h):
        return _make_zoned_bg(w, h, colores_a, colores_b)
    # Girado 90° a la izquierda, lo de "arriba" queda a la izquierda.
    return _make_zoned_bg(h, w, colores_a, colores_b).rotate(90, expand=True)


def convert_on_background(src_path: str, bg_path: str, out_path: str, aspect: tuple[int, int] = None):
    """Monta una foto sobre un fondo elegido, dejando lugar para texto (abajo en
    vertical, a la derecha en horizontal)."""
    aw, ah = aspect or (DEFAULT_ASPECT_W, DEFAULT_ASPECT_H)
    img = Image.open(src_path).convert("RGB")
    bg_img = Image.open(bg_path).convert("RGB")

    canvas_w, canvas_h = lienzo_para([img.size, bg_img.size], (aw, ah))

    # Escalar fondo para cubrir el canvas
    bg_scale = max(canvas_w / bg_img.size[0], canvas_h / bg_img.size[1])
    bg_resized = bg_img.resize(
        (int(bg_img.size[0] * bg_scale), int(bg_img.size[1] * bg_scale)), Image.LANCZOS)
    # Crop centrado al canvas
    bx = (bg_resized.size[0] - canvas_w) // 2
    by = (bg_resized.size[1] - canvas_h) // 2
    bg = bg_resized.crop((bx, by, bx + canvas_w, by + canvas_h))

    # Arriba en vertical, a la izquierda en horizontal (convert/layout.py)
    x, y, fw, fh = sobre_fondo(img.size, canvas_w, canvas_h)
    bg.paste(img.resize((fw, fh), Image.LANCZOS), (x, y))

    bg.save(out_path, "JPEG", quality=JPEG_QUALITY)
