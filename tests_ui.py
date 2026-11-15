"""Tests de la interfaz: tema, accesibilidad y grilla. Corren sin pantalla.

Cubren lo que se arregló al pasar al tema nuevo (docs: ui/style.py), para que no
vuelva a romperse sin que nadie lo note.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QPoint, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QListWidgetItem, QStyleOptionViewItem

from convertir3x4.ui.style import ASSETS_DIR, DARK, DARK_STYLE, LIGHT, T, apply_theme


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setOrganizationName("CDLM-tests")
    a.setApplicationName("Convertir3x4-tests")
    apply_theme(a, "dark")
    return a


# ---------------------------------------------------------------- contraste

def _lum(hex_color: str) -> float:
    c = QColor(hex_color)
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _blend(rgba: str, base: str) -> str:
    """Color resultante de un rgba(...) translúcido sobre un fondo opaco."""
    r, g, b, a = (float(x) for x in rgba[rgba.index("(") + 1:-1].split(","))
    bc = QColor(base)
    mix = [round(c * a + bb * (1 - a)) for c, bb in zip((r, g, b), (bc.red(), bc.green(), bc.blue()))]
    return QColor(*mix).name()


PALETTES = {"oscuro": DARK, "claro": LIGHT}
SURFACE_KEYS = ("bg", "panel", "raised")


@pytest.mark.parametrize("pal_name", list(PALETTES))
class TestContrast:
    @pytest.mark.parametrize("name", ["text", "text_2", "text_3", "accent_text",
                                      "ok", "warn", "danger"])
    @pytest.mark.parametrize("surface", SURFACE_KEYS)
    def test_readable_text_is_aa(self, pal_name, name, surface):
        pal = PALETTES[pal_name]
        ratio = contrast(pal[name], pal[surface])
        assert ratio >= 4.5, f"{pal_name}: {name} sobre {surface}: {ratio:.2f}:1"

    def test_white_on_primary_button(self, pal_name):
        pal = PALETTES[pal_name]
        assert contrast(pal["on_image"], pal["accent"]) >= 4.5
        assert contrast(pal["on_image"], pal["accent_hover"]) >= 4.5

    @pytest.mark.parametrize("surface", ["bg", "panel"])
    def test_field_border_is_visible(self, pal_name, surface):
        # WCAG 1.4.11: el borde que identifica un campo necesita 3:1
        pal = PALETTES[pal_name]
        assert contrast(pal["field_border"], pal[surface]) >= 3.0

    @pytest.mark.parametrize("fg, soft", [("warn", "warn_soft"), ("ia", "ia_soft"),
                                          ("text", "accent_soft")])
    def test_chips_are_readable(self, pal_name, fg, soft):
        pal = PALETTES[pal_name]
        chip_bg = _blend(pal[soft], pal["panel"])
        ratio = contrast(pal[fg], chip_bg)
        assert ratio >= 4.5, f"{pal_name}: chip {fg}: {ratio:.2f}:1"


# ---------------------------------------------------------------- tema

class TestTheme:
    def test_same_style_on_every_os(self, app):
        # Con hoja de estilos, Qt envuelve el estilo en QStyleSheetStyle y no deja
        # ver el de abajo: se verifica que apply_theme pida Fusion.
        calls = []

        class FakeApp:
            def setStyle(self, s): calls.append(("style", s))
            def setFont(self, f): pass
            def setPalette(self, p): pass
            def setStyleSheet(self, s): calls.append(("qss", bool(s)))

        apply_theme(FakeApp())
        assert ("style", "Fusion") in calls and ("qss", True) in calls

    def test_light_and_dark_use_their_palette(self, app):
        from convertir3x4.ui.style import build_stylesheet, use_palette
        try:
            use_palette("light")
            assert T.bg == LIGHT["bg"] and LIGHT["bg"] in build_stylesheet()
        finally:
            use_palette("dark")
        assert T.bg == DARK["bg"] and DARK["bg"] in build_stylesheet()

    def test_no_hardcoded_colors_in_rules(self):
        # Todo color de la hoja sale de la paleta: si no, el modo claro queda roto
        import re
        from convertir3x4.ui.style import build_stylesheet, use_palette
        use_palette("light")
        try:
            qss = build_stylesheet()
        finally:
            use_palette("dark")
        dark_only = {v for k, v in DARK.items() if v != LIGHT[k]}
        leaked = [c for c in re.findall(r"#[0-9a-fA-F]{6}|rgba\([^)]*\)", qss) if c in dark_only]
        assert not leaked, leaked

    def test_inter_is_loaded(self, app):
        assert app.font().family() == "Inter"

    def test_no_background_on_every_widget(self):
        # Pintar el fondo de todo QWidget dibujaba recuadros detrás de cada texto
        assert "QMainWindow, QWidget {" not in DARK_STYLE

    def test_assets_present_and_packaged(self):
        for f in ("fonts/Inter-Regular.ttf", "fonts/OFL-Inter.txt",
                  "icons/search.svg", "icons/LICENSE-lucide.txt"):
            assert os.path.exists(os.path.join(ASSETS_DIR, f)), f
        spec = open(os.path.join(os.path.dirname(__file__), "Convertir3x4.spec")).read()
        assert "convertir3x4/ui/assets" in spec and "PyQt6.QtSvg" in spec

    def test_icons_take_the_requested_color(self, app):
        from convertir3x4.ui import icons
        img = icons.pixmap("x", 24, "#ff0000").toImage()
        colors = {img.pixelColor(x, y).name() for x in range(img.width())
                  for y in range(img.height()) if img.pixelColor(x, y).alpha() > 200}
        assert colors and all(c.startswith("#f") for c in colors), colors  # rojo, no negro


# ---------------------------------------------------------------- grilla

def _grid_with_items(cls, n=8, **kw):
    from convertir3x4.ui.widgets.media_grid import PATH_ROLE
    g = cls(**kw)
    px = QPixmap(200, 120)
    px.fill(QColor("#888888"))
    for i in range(n):
        it = QListWidgetItem(f"foto_{i:02d}.jpg")
        it.setIcon(QIcon(px))
        it.setData(PATH_ROLE, f"/x/foto_{i:02d}.jpg")
        g.addItem(it)
    return g


def _columns(g) -> int:
    y0 = g.visualItemRect(g.item(0)).y()
    return sum(1 for i in range(g.count()) if g.visualItemRect(g.item(i)).y() == y0)


class TestGrid:
    @pytest.mark.parametrize("width", [700, 956, 1274])
    @pytest.mark.parametrize("kind", ["drag", "reorder"])
    def test_leftover_width_is_shared(self, app, width, kind):
        from convertir3x4.ui.widgets.media_grid import DragOutListWidget, ReorderListWidget
        cls = DragOutListWidget if kind == "drag" else ReorderListWidget
        g = _grid_with_items(cls, n=12, thumb=130)
        g.resize(width, 900)
        g.show()
        app.processEvents()
        cell = g.gridSize().width()
        cols = _columns(g)
        last = max(g.visualItemRect(g.item(i)).right() for i in range(cols))
        gap = g.viewport().width() - last
        assert gap < cell, f"queda una columna sin usar: hueco {gap}px, celda {cell}px"
        g.close()

    def test_video_badge_does_not_change_name_font(self, app):
        from convertir3x4.ui.widgets.media_grid import (
            DragOutListWidget, KIND_ROLE, DURATION_ROLE)
        # Se dibuja la misma celda como foto y como video: la franja del nombre
        # (debajo de la imagen) tiene que salir idéntica. Antes, la insignia
        # dejaba la letra chica y en negrita para el nombre.
        g = _grid_with_items(DragOutListWidget, n=1, thumb=120)
        d = g.itemDelegate()
        idx = g.model().index(0, 0)
        size = d.cell_size()

        def render(kind):
            g.item(0).setData(KIND_ROLE, kind)
            g.item(0).setData(DURATION_ROLE, 44)
            img = QImage(size, QImage.Format.Format_ARGB32)
            img.fill(QColor("#000000"))
            p = QPainter(img)
            p.setFont(app.font())
            opt = QStyleOptionViewItem()
            opt.rect = img.rect()
            opt.font = app.font()
            d.paint(p, opt, idx)
            p.end()
            top = size.height() - d.LABEL_H
            return img.copy(0, top, size.width(), d.LABEL_H)

        photo, video = render("image"), render("video")
        assert photo == video, "el nombre de un video se dibuja con otra letra"

    def test_format_duration(self):
        from convertir3x4.ui.widgets.media_grid import format_duration
        assert format_duration(44) == "0:44"
        assert format_duration(336) == "5:36"
        assert format_duration(3725) == "1:02:05"
        assert format_duration(None) == ""

    def test_reorder_by_drag_still_works(self, app):
        from convertir3x4.ui.widgets.media_grid import ReorderListWidget, PATH_ROLE
        g = _grid_with_items(ReorderListWidget, n=4, thumb=100)
        g.resize(900, 400)
        g.show()
        app.processEvents()
        moved = []
        g.orderChanged.connect(lambda: moved.append(True))
        src = g.visualItemRect(g.item(0)).center()
        dst = g.visualItemRect(g.item(3)).center() + QPoint(30, 0)  # mitad derecha del último
        vp = g.viewport()
        QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=src)
        QTest.mouseMove(vp, src + QPoint(40, 0))
        QTest.mouseMove(vp, dst)
        QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=dst)
        app.processEvents()
        assert moved
        assert g.item(3).data(PATH_ROLE) == "/x/foto_00.jpg"
        g.close()


# ---------------------------------------------------------------- teclado

@pytest.fixture
def window(app):
    from convertir3x4.app import MainWindow
    w = MainWindow()
    w.resize(1100, 700)
    w.show()
    w.activateWindow()
    app.processEvents()
    yield w
    w.hide()
    w.deleteLater()


class TestKeyboard:
    def test_module_shortcuts(self, app, window):
        window.buscar_tab.txt_query.setFocus()
        for key, expected in ((Qt.Key.Key_3, 2), (Qt.Key.Key_2, 1), (Qt.Key.Key_1, 0)):
            QTest.keyClick(app.focusWidget(), key, Qt.KeyboardModifier.ControlModifier)
            app.processEvents()
            assert window.stack.currentIndex() == expected
            assert window._nav.checkedId() == expected

    def test_tab_order_has_no_dead_stops(self, app, window):
        window.buscar_tab.txt_query.setFocus()
        app.processEvents()
        seen = []
        for _ in range(6):
            seen.append(type(app.focusWidget()).__name__)
            window.focusNextChild()
            app.processEvents()
        assert "QScrollArea" not in seen
        assert "DragOutListWidget" in seen

    def test_kind_filter_with_arrows(self, app, window):
        b = window.buscar_tab._kind_group.buttons()
        b[0].setFocus()
        QTest.keyClick(b[0], Qt.Key.Key_Right)
        assert app.focusWidget() is b[1]
        QTest.keyClick(b[1], Qt.Key.Key_Space)
        assert window.buscar_tab._kind_group.checkedButton() is b[1]


# ---------------------------------------------------------------- archivos no disponibles

class TestAvailability:
    @pytest.mark.parametrize("path, root", [
        ("\\\\NAS\\fotos\\2024\\a.jpg", "\\\\NAS\\fotos"),
        ("//NAS/fotos/2024/a.jpg", "//NAS/fotos"),
        ("D:\\fotos\\a.jpg", "D:\\"),
        ("/Volumes/SSD_EXT/x/a.jpg", "/Volumes/SSD_EXT"),
        ("/home/u/a.jpg", "/"),
    ])
    def test_storage_root(self, path, root):
        from convertir3x4.ui.tabs.tab_buscar import storage_root
        assert storage_root(path) == root

    def test_offline_nas_is_checked_once_not_per_file(self, monkeypatch):
        # Con el NAS caído, cada consulta en Windows puede tardar segundos:
        # 500 resultados no pueden ser 500 consultas.
        from convertir3x4.ui.tabs import tab_buscar
        calls = []

        def fake_exists(p):
            calls.append(p)
            return False
        monkeypatch.setattr(tab_buscar.os.path, "exists", fake_exists)
        paths = [f"\\\\NAS\\fotos\\evento\\{i}.jpg" for i in range(500)]
        result = tab_buscar.availability(paths)
        assert not any(result.values())
        assert calls == ["\\\\NAS\\fotos"]

    def test_missing_originals_are_marked_in_buscar(self, app, tmp_path):
        from convertir3x4.ui.tabs.tab_buscar import BuscarTab
        from convertir3x4.catalog.models import Media
        from convertir3x4.ui.widgets.media_grid import STATE_ROLE
        real = tmp_path / "esta.jpg"
        real.write_bytes(b"x")
        tab = BuscarTab()
        tab._populate([Media(path=str(real), filename="esta.jpg", kind="image", id=1),
                       Media(path="/Volumes/NO_EXISTE/a.jpg", filename="a.jpg", kind="image", id=2)])
        states = [tab.grid.item(i).data(STATE_ROLE) for i in range(2)]
        assert states == [None, "offline"]   # el volumen entero no está: lo explica el aviso
        assert tab.offline_box.isVisibleTo(tab)
        assert "/Volumes/NO_EXISTE" in tab.lbl_offline.text()
        assert "No se encuentra el original" in tab.grid.item(1).toolTip()
        tab.deleteLater()


class TestHelp:
    def test_opens_alone_the_first_time_only(self, app):
        from PyQt6.QtCore import QSettings
        from convertir3x4.app import MainWindow
        QSettings().remove("help_seen")
        w = MainWindow()
        w.show()
        app.processEvents()
        assert w.help_panel.isVisible()
        w.set_help_visible(False)
        w2 = MainWindow()
        w2.show()
        app.processEvents()
        assert not w2.help_panel.isVisible()
        for x in (w, w2):
            x.hide()
            x.deleteLater()

    def test_follows_the_current_module(self, app):
        from convertir3x4.app import MainWindow
        w = MainWindow()
        w.show_module(2)
        assert w.help_panel.lbl_title.text() == "Ayuda · Convertir"
        w.deleteLater()

    def test_does_not_squeeze_the_work_area(self, app):
        from convertir3x4.app import MainWindow
        w = MainWindow()
        w.resize(900, 700)
        w.show()
        w.set_help_visible(False)
        app.processEvents()
        before = w.stack.width()
        w.set_help_visible(True)
        app.processEvents()
        assert w.stack.width() == before
        w.hide()
        w.deleteLater()
