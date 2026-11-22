"""Tema de la app: igual en Windows, Mac y Linux.

Tres piezas, todas dentro de la app (no dependen del sistema):
- estilo base `Fusion` de Qt, con una paleta oscura propia;
- la tipografía Inter (ui/assets/fonts);
- la hoja de estilos `DARK_STYLE`, armada desde los colores de `T`.

Criterio: grises neutros y un solo color de acento, como Lightroom. Lo único con
color fuerte en pantalla son las fotos y lo seleccionado.
"""

import os

from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPalette

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")


# Dos paletas con los mismos nombres. Contrastes medidos (WCAG, ver tests_ui.py)
# sobre `bg`, `panel` y `raised`: todo texto que hay que leer da 4,5:1 o más;
# bordes de campos y foco, 3:1 o más. `text_disabled` es lo único por debajo,
# porque lo deshabilitado no se lee.
DARK = {
    "bg": "#161616",            # fondo de la ventana
    "panel": "#1e1e1e",         # paneles, barra lateral
    "raised": "#262626",        # campos, celdas de la grilla
    "hover": "#303030",
    "border": "#2c2c2c",        # divisiones entre zonas (decorativo)
    "border_strong": "#3a3a3a",
    "field_border": "#6b6b6b",  # borde de campos: 3:1 contra bg y panel
    "text": "#e8e8e8",
    "text_2": "#a6a6a6",        # secundario
    "text_3": "#8f8f8f",        # ayudas y contadores
    "text_disabled": "#5c5c5c",
    "accent": "#2563eb",        # rellenos: el texto blanco encima da 5,2:1
    "accent_hover": "#1d4ed8",
    "accent_press": "#1e40af",
    "accent_text": "#6aa5ff",   # acento sobre el fondo: texto, bordes, selección, foco
    "accent_soft": "rgba(59, 130, 246, 0.18)",
    "accent_line": "rgba(106, 165, 255, 0.45)",
    "warn": "#f5a524",
    "warn_soft": "rgba(245, 165, 36, 0.20)",
    "warn_line": "rgba(245, 165, 36, 0.45)",
    "ia": "#b79cf7",
    "ia_soft": "rgba(150, 110, 240, 0.16)",
    "ia_hover": "rgba(150, 110, 240, 0.28)",
    "danger": "#f87171",
    "ok": "#4ade80",            # terminado bien (Discos: "Copiado completo")
    "on_image": "#ffffff",      # texto de insignias sobre la miniatura (fondo negro translúcido)
}

LIGHT = {
    "bg": "#f2f2f2",
    "panel": "#fafafa",
    "raised": "#ffffff",
    "hover": "#e6e6e6",
    "border": "#e2e2e2",
    "border_strong": "#cfcfcf",
    "field_border": "#8a8a8a",
    "text": "#1a1a1a",
    "text_2": "#474747",
    "text_3": "#666666",
    "text_disabled": "#a8a8a8",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "accent_press": "#1e40af",
    "accent_text": "#1d4ed8",
    "accent_soft": "rgba(37, 99, 235, 0.12)",
    "accent_line": "rgba(29, 78, 216, 0.45)",
    "warn": "#9a4a00",
    "warn_soft": "rgba(217, 119, 6, 0.14)",
    "warn_line": "rgba(154, 74, 0, 0.45)",
    "ia": "#6d28d9",
    "ia_soft": "rgba(109, 40, 217, 0.10)",
    "ia_hover": "rgba(109, 40, 217, 0.20)",
    "danger": "#c81e1e",
    "ok": "#166534",
    "on_image": "#ffffff",
}


class T:
    """Colores y medidas del modo activo. Cambiar los valores en DARK/LIGHT.

    `use_palette()` copia una de las dos paletas acá. Los widgets leen `T` al
    construirse o al dibujar, así que hay que elegir el modo antes de crear la
    ventana (lo hace `apply_theme`).
    """
    scheme = "dark"
    radius = 8
    radius_sm = 6
    font_size = 13


def use_palette(scheme: str) -> None:
    pal = LIGHT if scheme == "light" else DARK
    T.scheme = "light" if scheme == "light" else "dark"
    for k, v in pal.items():
        setattr(T, k, v)


use_palette("dark")


def system_scheme(app) -> str:
    """'light' u 'dark' según el sistema (Windows 10/11, macOS). Si no se sabe, oscuro."""
    try:
        from PyQt6.QtCore import Qt
        hints = app.styleHints()
        if hints.colorScheme() == Qt.ColorScheme.Light:
            return "light"
    except Exception:  # noqa: BLE001 — Qt viejo o app de prueba
        pass
    return "dark"


FONT_FAMILY = "Inter"


def _load_fonts() -> str:
    """Registra Inter. Si algo falla, cae en la fuente del sistema (no rompe)."""
    fonts = os.path.join(ASSETS_DIR, "fonts")
    family = None
    for name in ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf"):
        fid = QFontDatabase.addApplicationFont(os.path.join(fonts, name))
        if fid >= 0 and family is None:
            fams = QFontDatabase.applicationFontFamilies(fid)
            family = fams[0] if fams else None
    return family or QFont().defaultFamily()


def _palette() -> QPalette:
    """Paleta de Fusion: cubre lo que la hoja de estilos no alcanza (menús, tooltips)."""
    p = QPalette()
    c = QColor
    p.setColor(QPalette.ColorRole.Window, c(T.bg))
    p.setColor(QPalette.ColorRole.WindowText, c(T.text))
    p.setColor(QPalette.ColorRole.Base, c(T.raised))
    p.setColor(QPalette.ColorRole.AlternateBase, c(T.panel))
    p.setColor(QPalette.ColorRole.Text, c(T.text))
    p.setColor(QPalette.ColorRole.PlaceholderText, c(T.text_3))
    p.setColor(QPalette.ColorRole.Button, c(T.raised))
    p.setColor(QPalette.ColorRole.ButtonText, c(T.text))
    p.setColor(QPalette.ColorRole.Highlight, c(T.accent))
    p.setColor(QPalette.ColorRole.HighlightedText, c("#ffffff"))
    p.setColor(QPalette.ColorRole.ToolTipBase, c(T.raised))
    p.setColor(QPalette.ColorRole.ToolTipText, c(T.text))
    p.setColor(QPalette.ColorRole.Link, c(T.accent_text))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, c(T.text_disabled))
    return p


_CHECK_IMG = ""   # PNG de la tilde de las casillas (lo arma apply_theme)


def _make_check_image() -> str:
    """Tilde blanca para las casillas marcadas: el estado no puede ser solo color.

    La hoja de estilos de Qt solo acepta imágenes por ruta, así que se escribe
    una vez en una carpeta temporal.
    """
    import tempfile
    from PyQt6.QtCore import QByteArray, QRectF, Qt
    from PyQt6.QtGui import QPainter, QPixmap
    from PyQt6.QtSvg import QSvgRenderer
    with open(os.path.join(ASSETS_DIR, "icons", "check.svg"), encoding="utf-8") as f:
        svg = f.read().replace("currentColor", T.on_image).replace('stroke-width="2"', 'stroke-width="3"')
    px = QPixmap(32, 32)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    QSvgRenderer(QByteArray(svg.encode())).render(p, QRectF(4, 4, 24, 24))
    p.end()
    path = os.path.join(tempfile.gettempdir(), "cdlm-convertir-check.png")
    px.save(path)
    return path.replace("\\", "/")


def _flecha_de_lista() -> str:
    """Flecha de las listas desplegables, en el color del tema. Con hoja de
    estilos, Qt deja de dibujar la suya y la lista parece un campo de texto."""
    import tempfile
    from PyQt6.QtCore import QByteArray, QRectF, Qt
    from PyQt6.QtGui import QGuiApplication, QPainter, QPixmap
    from PyQt6.QtSvg import QSvgRenderer
    if QGuiApplication.instance() is None:
        return ""
    with open(os.path.join(ASSETS_DIR, "icons", "chevron-down.svg"), encoding="utf-8") as f:
        svg = f.read().replace("currentColor", T.text_2)
    px = QPixmap(32, 32)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    QSvgRenderer(QByteArray(svg.encode())).render(p, QRectF(0, 0, 32, 32))
    p.end()
    path = os.path.join(tempfile.gettempdir(), f"cdlm-convertir-flecha-{T.scheme}.png")
    px.save(path)
    return path.replace("\\", "/")


_AL_CAMBIAR = []   # funciones a llamar cuando cambia el tema (p. ej. icons.refresh_all)


def al_cambiar_tema(fn):
    if fn not in _AL_CAMBIAR:
        _AL_CAMBIAR.append(fn)


def _reaplicar(app, scheme: str):
    """Cambia de tema con la app abierta (el sistema pasó a claro u oscuro)."""
    if scheme == T.scheme:
        return
    use_palette(scheme)
    app.setPalette(_palette())
    app.setStyleSheet(build_stylesheet())
    for fn in list(_AL_CAMBIAR):
        fn()
    # Lo que se dibuja a mano (grilla, avisos) lee T al pintar: alcanza con repintar
    for w in app.allWidgets():
        w.update()


def apply_theme(app, scheme: str | None = None) -> None:
    """Deja la app con el mismo aspecto en cualquier sistema operativo.

    `scheme`: 'light' | 'dark'. Sin indicar, sigue el modo del sistema de esa PC
    (decisión del 15/11/2026: la de la cabina y la de la oficina no tienen la
    misma luz). Se decide al abrir la app.
    """
    use_palette(scheme or system_scheme(app))
    app.setStyle("Fusion")
    family = _load_fonts()
    font = QFont(family)
    font.setPixelSize(T.font_size)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(font)
    app.setPalette(_palette())
    global _CHECK_IMG
    try:
        _CHECK_IMG = _make_check_image()
    except Exception:  # noqa: BLE001 — sin tilde, la casilla igual funciona
        _CHECK_IMG = ""
    app.setStyleSheet(build_stylesheet())
    # Sin modo fijo, se sigue al sistema también con la app abierta (decisión del
    # 15/11/2026: la Mac pasó a modo noche y la app había quedado clara).
    if scheme is None:
        try:
            hints = app.styleHints()
            hints.colorSchemeChanged.connect(lambda _s: _reaplicar(app, system_scheme(app)))
        except Exception:  # noqa: BLE001 — Qt viejo o app de prueba: queda fijo
            pass


def build_stylesheet() -> str:
    """Hoja de estilos para el modo activo de `T`."""
    try:
        flecha = _flecha_de_lista()
    except Exception:  # noqa: BLE001 — sin flecha, la lista igual funciona
        flecha = ""
    return f"""
/* ---------- base ----------
   Solo la ventana lleva fondo: si se lo pone a todo QWidget, cada etiqueta
   dibuja un recuadro del color de la ventana encima de los paneles. */
QMainWindow, QStackedWidget > QWidget {{ background-color: {T.bg}; }}
QWidget {{ color: {T.text}; font-size: {T.font_size}px; }}
QLabel {{ background: transparent; color: {T.text_2}; }}
QToolTip {{
    background-color: {T.raised}; color: {T.text}; border: 1px solid {T.border_strong};
    border-radius: {T.radius_sm}px; padding: 6px 8px;
}}

/* ---------- navegación lateral ---------- */
QFrame#nav_rail {{ background-color: {T.panel}; border-right: 1px solid {T.border}; }}
QToolButton#nav_button {{
    background: transparent; border: none; border-radius: {T.radius}px;
    color: {T.text_2}; font-size: 11px; font-weight: 500; padding: 8px 2px 6px 2px;
}}
QToolButton#nav_button:hover {{ background-color: {T.hover}; color: {T.text_2}; }}
QToolButton#nav_button:checked {{ background-color: {T.accent_soft}; color: {T.text}; }}
QToolButton#nav_button:focus {{ border: 1px solid {T.accent_text}; }}

/* ---------- conexión con el NAS (Grabadora) ---------- */
QFrame#conn_conectando {{
    background-color: {T.accent_soft}; border: 1px solid {T.accent_line}; border-radius: {T.radius_sm}px;
}}
QFrame#conn_conectando QLabel {{ color: {T.accent_text}; font-weight: 600; }}
QFrame#conn_error {{
    background-color: {T.warn_soft}; border: 1px solid {T.warn_line}; border-radius: {T.radius_sm}px;
}}
QFrame#conn_error QLabel {{ color: {T.warn}; font-weight: 600; }}
QFrame#conn_ok {{ background: transparent; border: none; }}
QFrame#conn_ok QLabel {{ color: {T.text_3}; }}

/* Lo deshabilitado se ve gris en todos lados (no solo los botones) */
QLabel:disabled, QLabel#section_title:disabled, QLabel#muted:disabled {{ color: {T.text_disabled}; }}
QCheckBox:disabled {{ color: {T.text_disabled}; }}
QTreeView:disabled, QListWidget:disabled {{
    color: {T.text_disabled}; background-color: {T.panel}; border-color: {T.border};
}}
QHeaderView::section:disabled {{ color: {T.text_disabled}; }}
QPushButton#btn_link:disabled {{ color: {T.text_disabled}; }}

/* ---------- tipo de disco (Grabadora) ---------- */
/* Radio = mitad del alto (11 px de letra + 2 x 3 de relleno = 17): si es mayor, Qt lo ignora */
QLabel#pill_grabadora, QLabel#pill_otro {{
    border-radius: 8px; padding: 2px 9px; font-size: 11px; font-weight: 600;
}}
QLabel#pill_grabadora {{ background-color: {T.accent_soft}; color: {T.accent_text}; }}
QLabel#pill_otro {{ background-color: {T.hover}; color: {T.text_2}; }}

/* ---------- ayuda ---------- */
QFrame#help_panel {{ background-color: {T.panel}; border-left: 1px solid {T.border_strong}; }}
QLabel#help_title {{ color: {T.text}; font-size: 15px; font-weight: 600; }}
QLabel#help_text {{ color: {T.text_2}; }}
QFrame#help_panel QScrollArea, QFrame#help_panel QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ---------- botones ---------- */
QPushButton {{
    background-color: {T.raised}; color: {T.text}; border: 1px solid {T.border_strong};
    border-radius: {T.radius_sm}px; padding: 6px 14px;
}}
QPushButton:hover {{ background-color: {T.hover}; }}
QPushButton:pressed {{ background-color: {T.border_strong}; }}
QPushButton:disabled {{ color: {T.text_disabled}; background-color: {T.panel}; border-color: {T.border}; }}
QPushButton:checked {{ background-color: {T.accent_soft}; border-color: {T.accent_text}; color: {T.text}; }}
QPushButton:focus {{ border-color: {T.accent_text}; }}

/* acción principal (una por pantalla) */
QPushButton#btn_action, QPushButton#btn_load, QPushButton#btn_primary {{
    background-color: {T.accent}; border: 1px solid {T.accent}; color: {T.on_image}; font-weight: 500;
}}
QPushButton#btn_action:hover, QPushButton#btn_load:hover, QPushButton#btn_primary:hover {{
    background-color: {T.accent_hover}; border-color: {T.accent_hover};
}}
QPushButton#btn_action:pressed, QPushButton#btn_load:pressed, QPushButton#btn_primary:pressed {{
    background-color: {T.accent_press};
}}
QPushButton#btn_action:disabled, QPushButton#btn_primary:disabled {{
    background-color: {T.raised}; border-color: {T.border}; color: {T.text_disabled};
}}

/* botones chicos de ícono y de texto sin borde */
QPushButton#btn_icon {{
    background: transparent; border: 1px solid transparent; border-radius: {T.radius_sm}px;
    font-size: 15px; padding: 0px;
}}
QPushButton#btn_icon:hover {{ background-color: {T.hover}; }}
QPushButton#btn_load_bg, QPushButton#btn_sort {{
    background: transparent; border: none; color: {T.text_2}; padding: 4px 8px;
}}
QPushButton#btn_load_bg:hover, QPushButton#btn_sort:hover {{ color: {T.text}; }}
QPushButton#btn_sort[active="true"] {{ color: {T.accent_text}; }}

/* filtro segmentado (Todo · Fotos · Videos) y conmutadores tipo chip */
QPushButton#segment {{
    background: transparent; border: none; border-radius: {T.radius_sm}px;
    color: {T.text_2}; padding: 5px 12px;
}}
QPushButton#segment:hover {{ color: {T.text}; }}
QPushButton#segment:checked {{ background-color: {T.hover}; color: {T.text}; }}
QPushButton#segment:focus {{ border: 1px solid {T.accent_text}; }}
QFrame#segmented {{
    background-color: {T.panel}; border: 1px solid {T.border}; border-radius: {T.radius}px;
}}
QPushButton#toggle_chip {{
    background: transparent; border: 1px solid {T.border_strong}; border-radius: 14px;
    color: {T.text_2}; padding: 5px 12px;
}}
QPushButton#toggle_chip:hover {{ color: {T.text}; border-color: {T.text_3}; }}
QPushButton#toggle_chip:checked {{ background-color: {T.ia_soft}; border-color: {T.ia}; color: {T.ia}; }}
QPushButton#toggle_chip:focus {{ border-color: {T.accent_text}; }}

/* botón con aspecto de link (acciones secundarias dentro de un texto) */
QPushButton#btn_link {{
    background: transparent; border: none; color: {T.accent_text}; padding: 2px 0;
    text-align: left;
}}
QPushButton#btn_link:hover {{ text-decoration: underline; }}

/* ---------- opciones de Convertir ---------- */
QRadioButton#mode_option {{ color: {T.text}; font-weight: 600; spacing: 10px; padding-top: 4px; }}
QRadioButton#mode_option:disabled {{ color: {T.text_disabled}; }}
/* El borde se suma al tamaño: contenido + 2 x borde = 16 px siempre, y radio 8 = círculo */
QRadioButton::indicator {{
    width: 14px; height: 14px; border-radius: 8px;
    border: 1px solid {T.field_border}; background: {T.raised};
}}
QRadioButton::indicator:hover {{ border-color: {T.text_2}; }}
QRadioButton::indicator:checked {{
    width: 6px; height: 6px; border: 5px solid {T.accent}; background: {T.on_image};
}}
QRadioButton:focus {{ color: {T.accent_text}; }}
QFrame#nonstandard {{
    background-color: {T.warn_soft}; border: 1px solid {T.warn_line}; border-radius: {T.radius_sm}px;
}}
QLabel#nonstandard_title {{ color: {T.warn}; font-weight: 600; }}
QLabel#nonstandard_text {{ color: {T.warn}; }}
QFrame#nonstandard QPushButton#btn_link {{ color: {T.warn}; }}

/* Panel de Convertir: la parte que se desplaza no pinta fondo propio */
QScrollArea#panel_scroll, QWidget#panel_contenido {{ background: transparent; }}

/* "¿Cuántos en cada pantalla?" (Convertir): dos tarjetas con el dibujo */
QToolButton#por_pantalla {{
    background: transparent; border: 1px solid {T.border_strong}; border-radius: 10px;
    color: {T.text_2}; padding: 10px 6px 8px 6px; font-weight: 600;
}}
QToolButton#por_pantalla:hover {{ background-color: {T.hover}; }}
QToolButton#por_pantalla:checked {{
    background-color: {T.accent_soft}; border: 2px solid {T.accent_text}; color: {T.text};
}}
QToolButton#por_pantalla:focus {{ border-color: {T.accent_text}; }}
QToolButton#por_pantalla:disabled {{ color: {T.text_disabled}; border-color: {T.border}; }}

/* ---------- campos ---------- */
QLineEdit {{
    background-color: {T.raised}; color: {T.text}; border: 1px solid {T.field_border};
    border-radius: {T.radius}px; padding: 7px 10px;
    selection-background-color: {T.accent};
}}
QLineEdit:hover {{ border-color: {T.text_3}; }}
QLineEdit:focus {{ border: 2px solid {T.accent_text}; padding: 6px 9px; }}
QLineEdit:disabled {{ background-color: {T.panel}; color: {T.text_disabled}; border-color: {T.border_strong}; }}
QLineEdit#search_field {{ padding-left: 4px; font-size: 14px; }}
QLineEdit#search_field:focus {{ padding-left: 3px; }}

QComboBox {{
    background-color: {T.raised}; color: {T.text}; border: 1px solid {T.field_border};
    border-radius: {T.radius_sm}px; padding: 5px 10px;
}}
QComboBox:hover {{ border-color: {T.text_3}; }}
QComboBox:focus {{ border-color: {T.accent_text}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ {f"image: url({flecha});" if flecha else ""} width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{
    background-color: {T.raised}; color: {T.text}; border: 1px solid {T.border_strong};
    selection-background-color: {T.accent_soft}; outline: none;
}}

QCheckBox {{ color: {T.text_2}; spacing: 8px; background: transparent; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {T.field_border};
    border-radius: 4px; background-color: {T.raised};
}}
QCheckBox::indicator:hover {{ border-color: {T.text_2}; }}
QCheckBox:focus {{ color: {T.text}; }}
QCheckBox::indicator:checked {{
    background-color: {T.accent}; border-color: {T.accent};
    {f"image: url({_CHECK_IMG});" if _CHECK_IMG else ""}
}}

/* ---------- paneles y textos de sección ---------- */
QFrame#sidebar, QFrame#panel {{
    background-color: {T.panel}; border: 1px solid {T.border}; border-radius: {T.radius}px;
}}
QFrame#separator {{ background-color: {T.border}; border: none; }}
QLabel#group_title {{ color: {T.text_3}; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
QLabel#section_title {{ color: {T.text}; font-weight: 600; }}
QLabel#subtitulo {{ color: {T.text_2}; font-weight: 600; font-size: 12px; }}
QLabel#muted {{ color: {T.text_3}; }}
QLabel#bg_preview {{
    background-color: {T.raised}; border: 1px dashed {T.border_strong}; border-radius: {T.radius_sm}px;
    color: {T.text_3};
}}

/* ---------- grilla de medios ---------- */
QListWidget {{
    background-color: {T.bg}; border: none; outline: none;
}}
QListWidget::item {{
    background-color: {T.raised}; border: 2px solid transparent;
    border-radius: {T.radius_sm}px; padding: 4px; margin: 4px; color: {T.text_2};
}}
QListWidget::item:selected {{ border-color: {T.accent_text}; background-color: {T.accent_soft}; color: {T.text}; }}
QListWidget::item:hover:!selected {{ background-color: {T.hover}; }}
QListWidget#people_list {{
    background-color: {T.raised}; border: 1px solid {T.border_strong}; border-radius: {T.radius_sm}px;
    padding: 4px;
}}
QListWidget#people_list::item {{ background: transparent; border: none; margin: 0; padding: 6px 8px; }}
QListWidget#people_list::item:selected {{ background-color: {T.accent_soft}; color: {T.text}; }}
QListWidget#people_list::item:disabled {{ color: {T.text_3}; }}
QListWidget#media_grid::item {{ background: transparent; border: none; padding: 0; margin: 0; }}

/* ---------- tablas y texto largo (historial y registro de Discos) ---------- */
QTreeView, QPlainTextEdit {{
    background-color: {T.raised}; color: {T.text}; border: 1px solid {T.border_strong};
    border-radius: {T.radius_sm}px; selection-background-color: {T.accent_soft};
    selection-color: {T.text}; outline: none;
}}
QTreeView::item {{ padding: 4px 6px; }}
QTreeView::item:hover {{ background-color: {T.hover}; }}
QTreeView::item:selected {{ background-color: {T.accent_soft}; color: {T.text}; }}
QTreeView:focus, QPlainTextEdit:focus {{ border-color: {T.accent_text}; }}
QHeaderView::section {{
    background-color: {T.panel}; color: {T.text_2}; border: none;
    border-bottom: 1px solid {T.border_strong}; padding: 6px; font-weight: 600;
}}

/* ---------- progreso y slider ---------- */
QProgressBar {{
    background-color: {T.raised}; border: none; border-radius: 3px; max-height: 6px;
    color: transparent;
}}
QProgressBar::chunk {{ background-color: {T.accent_text}; border-radius: 3px; }}
QSlider::groove:horizontal {{ background: {T.border_strong}; height: 4px; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {T.text_3}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {T.text}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: {T.text_2}; }}

/* ---------- barras de desplazamiento finas, sin flechas ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {T.border_strong}; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:horizontal {{ background: {T.border_strong}; border-radius: 3px; min-width: 32px; }}
QScrollBar::handle:hover {{ background: {T.text_3}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QScrollArea {{ background: transparent; border: none; }}

/* ---------- chips de tags ---------- */
QPushButton#tag_chip {{
    background-color: {T.accent_soft}; border: 1px solid {T.accent_line}; color: {T.text};
    border-radius: 13px; padding: 4px 12px;
}}
QPushButton#tag_chip:hover {{ border-color: {T.danger}; color: {T.danger}; }}
QPushButton#tag_chip_partial {{
    background-color: {T.warn_soft}; border: 1px solid {T.warn_line}; color: {T.warn};
    border-radius: 13px; padding: 4px 12px;
}}
QPushButton#tag_chip_partial:hover {{ border-color: {T.danger}; color: {T.danger}; }}
QPushButton#tag_chip_ia {{
    background-color: {T.ia_soft}; border: 1px dashed {T.ia}; color: {T.ia};
    border-top-left-radius: 13px; border-bottom-left-radius: 13px;
    border-top-right-radius: 0; border-bottom-right-radius: 0;
    padding: 4px 10px;
}}
QPushButton#tag_chip_ia:hover {{ background-color: {T.ia_hover}; }}
QPushButton#tag_chip_ia_x {{
    background-color: {T.ia_soft}; border: 1px dashed {T.ia}; border-left: none; color: {T.ia};
    border-top-right-radius: 13px; border-bottom-right-radius: 13px;
    border-top-left-radius: 0; border-bottom-left-radius: 0;
    padding: 4px 9px;
}}
QPushButton#tag_chip_ia_x:hover {{ color: {T.danger}; }}

/* ---------- pestañas (quedan donde todavía se usen) ---------- */
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{
    background: transparent; color: {T.text_2}; border: none;
    border-bottom: 2px solid transparent; padding: 8px 14px;
}}
QTabBar::tab:selected {{ color: {T.text}; border-bottom-color: {T.accent_text}; }}
QTabBar::tab:hover:!selected {{ color: {T.text}; }}
"""


DARK_STYLE = build_stylesheet()  # compatibilidad (shim y tests viejos): modo oscuro


def qcolor(valor: str) -> "QColor":
    """Un color del tema como QColor. Los tokens con transparencia están escritos
    como en CSS («rgba(150, 110, 240, 0.16)»), que QColor no entiende (da negro)."""
    from PyQt6.QtGui import QColor
    import re as _re
    m = _re.fullmatch(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)", valor.strip())
    if m:
        r, g, b, a = m.groups()
        c = QColor(int(r), int(g), int(b))
        c.setAlphaF(float(a))
        return c
    return QColor(valor)
