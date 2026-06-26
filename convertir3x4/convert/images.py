"""Procesamiento de imágenes: recorte a aspect ratio con fondos texturizados.

Funciones puras, sin estado ni dependencia de Qt — reutilizables por el resto
del administrador (motor, miniaturas, etc.).
"""

import random

from PIL import Image, ImageDraw, ImageFilter

from ..config import JPEG_QUALITY, DEFAULT_ASPECT_W, DEFAULT_ASPECT_H


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


def convert_double(paths: list[str], out_path: str, aspect: tuple[int, int] = None):
    """Apila 2 fotos (o 1) verticalmente en un canvas con fondo texturizado."""
    aw, ah = aspect or (DEFAULT_ASPECT_W, DEFAULT_ASPECT_H)
    images = [Image.open(p).convert("RGB") for p in paths]

    if len(images) == 1:
        img = images[0]
        src_w, src_h = img.size
        if src_w / src_h > aw / ah:
            canvas_w = src_w
            canvas_h = int(src_w * ah / aw)
        else:
            canvas_h = src_h
            canvas_w = int(src_h * aw / ah)

        colors = _dominant_colors(img)
        bg = _make_textured_bg(canvas_w, canvas_h, colors)
        x = (canvas_w - src_w) // 2
        bg.paste(img, (x, 0))
        bg.save(out_path, "JPEG", quality=JPEG_QUALITY)
        return

    # 2 fotos: apilar verticalmente
    img1, img2 = images[0], images[1]

    # Determinar cuál es más ancha (por aspect ratio)
    ar1 = img1.size[0] / img1.size[1]
    ar2 = img2.size[0] / img2.size[1]

    # La más ancha va primero (arriba), la más alta abajo
    if ar1 >= ar2:
        img_wide, img_tall = img1, img2
    else:
        img_wide, img_tall = img2, img1

    # Canvas basado en la imagen más grande
    canvas_w = max(img_wide.size[0], img_tall.size[0])
    canvas_h = int(canvas_w * ah / aw)
    margin = int(canvas_h * 0.02)  # 2% de margen mínimo

    def fit_image(img, max_w, max_h):
        w, h = img.size
        scale = min(max_w / w, max_h / h)
        return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    # La más ancha ocupa el ancho disponible, limitada al 45% de alto
    wide_max_w = canvas_w - margin * 2
    wide_max_h = int(canvas_h * 0.45)
    img_wide_fit = fit_image(img_wide, wide_max_w, wide_max_h)

    # La más alta usa el espacio restante
    tall_max_w = canvas_w - margin * 2
    tall_max_h = canvas_h - img_wide_fit.size[1] - margin * 3
    img_tall_fit = fit_image(img_tall, tall_max_w, tall_max_h)

    # Márgenes dinámicos con el espacio sobrante
    used_h = img_wide_fit.size[1] + img_tall_fit.size[1]
    remaining = canvas_h - used_h
    margin = remaining // 3

    # Fondo zonificado: colores de arriba para arriba, colores de abajo para abajo
    colors_top = _dominant_colors(img_wide)
    colors_bottom = _dominant_colors(img_tall)
    bg = _make_zoned_bg(canvas_w, canvas_h, colors_top, colors_bottom)

    # Foto ancha centrada arriba
    x1 = (canvas_w - img_wide_fit.size[0]) // 2
    y1 = margin
    bg.paste(img_wide_fit, (x1, y1))

    # Foto alta centrada abajo
    x2 = (canvas_w - img_tall_fit.size[0]) // 2
    y2 = margin + img_wide_fit.size[1] + margin
    bg.paste(img_tall_fit, (x2, y2))

    bg.save(out_path, "JPEG", quality=JPEG_QUALITY)


def convert_on_background(src_path: str, bg_path: str, out_path: str, aspect: tuple[int, int] = None):
    """Monta una foto en la mitad superior de un canvas con fondo personalizado."""
    aw, ah = aspect or (DEFAULT_ASPECT_W, DEFAULT_ASPECT_H)
    img = Image.open(src_path).convert("RGB")
    bg_img = Image.open(bg_path).convert("RGB")

    canvas_w = max(img.size[0], bg_img.size[0])
    canvas_h = int(canvas_w * ah / aw)

    # Escalar fondo para cubrir el canvas
    bg_scale = max(canvas_w / bg_img.size[0], canvas_h / bg_img.size[1])
    bg_resized = bg_img.resize(
        (int(bg_img.size[0] * bg_scale), int(bg_img.size[1] * bg_scale)), Image.LANCZOS)
    # Crop centrado al canvas
    bx = (bg_resized.size[0] - canvas_w) // 2
    by = (bg_resized.size[1] - canvas_h) // 2
    bg = bg_resized.crop((bx, by, bx + canvas_w, by + canvas_h))

    # Foto en la mitad superior con márgenes ~5%
    margin = int(canvas_w * 0.05)
    max_w = canvas_w - margin * 2
    max_h = int(canvas_h * 0.50) - margin

    w, h = img.size
    scale = min(max_w / w, max_h / h)
    img_fit = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    x = (canvas_w - img_fit.size[0]) // 2
    y = margin
    bg.paste(img_fit, (x, y))

    bg.save(out_path, "JPEG", quality=JPEG_QUALITY)
