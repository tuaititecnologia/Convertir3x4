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
    app.setStyleSheet(build_stylesheet())


def build_stylesheet() -> str:
    """Hoja de estilos para el modo activo de `T`."""
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
QComboBox::drop-down {{ border: none; width: 20px; }}
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
QCheckBox::indicator:checked {{ background-color: {T.accent}; border-color: {T.accent}; }}

/* ---------- paneles y textos de sección ---------- */
QFrame#sidebar, QFrame#panel {{
    background-color: {T.panel}; border: 1px solid {T.border}; border-radius: {T.radius}px;
}}
QFrame#separator {{ background-color: {T.border}; border: none; }}
QLabel#group_title {{ color: {T.text_3}; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
QLabel#section_title {{ color: {T.text}; font-weight: 600; }}
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

