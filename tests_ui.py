"""Tests de la interfaz: tema, accesibilidad y grilla. Corren sin pantalla.

Cubren lo que se arregló al pasar al tema nuevo (docs: ui/style.py), para que no
vuelva a romperse sin que nadie lo note.
"""

import datetime
import os
import time
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QPoint, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QListWidgetItem, QPushButton, QStyleOptionViewItem

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
        from convertir3x4.catalog import ubicacion
        calls = []

        def fake_exists(p):
            calls.append(p)
            return False
        monkeypatch.setattr(ubicacion.os.path, "exists", fake_exists)
        paths = [f"\\\\NAS\\fotos\\evento\\{i}.jpg" for i in range(500)]
        result = ubicacion.availability(paths)
        assert not any(result.values())
        assert calls == ["\\\\NAS\\fotos"]

    def test_no_pregunta_al_nas_por_cada_archivo(self, monkeypatch, tmp_path):
        # Por Netbird cada consulta al NAS tarda ~80 ms: 500 resultados no pueden
        # ser 500 consultas. Lo que falta lo marca el NAS (missing_since).
        from convertir3x4.catalog import ubicacion
        preguntas = []
        monkeypatch.setattr(ubicacion.os.path, "exists",
                            lambda p: preguntas.append(p) or True)
        monkeypatch.setattr(ubicacion.os, "listdir", lambda d: pytest.fail("listó " + d))
        paths = [f"/Volumes/multimedia/evento{i % 24}/{i}.jpg" for i in range(500)]
        assert all(ubicacion.availability(paths).values())
        assert preguntas == ["/Volumes/multimedia"]

    def test_missing_originals_are_marked_in_buscar(self, app, tmp_path, sin_catalogo):
        from convertir3x4.ui.tabs.tab_buscar import BuscarTab
        from convertir3x4.catalog.models import Media
        from convertir3x4.ui.widgets.media_grid import STATE_ROLE
        real = tmp_path / "esta.jpg"
        real.write_bytes(b"x")
        tab = BuscarTab(srv=sin_catalogo)
        tab._populate([Media(path=str(real), filename="esta.jpg", kind="image", id=1),
                       Media(path="/Volumes/NO_EXISTE/a.jpg", filename="a.jpg", kind="image", id=2)])
        states = [tab.grid.item(i).data(STATE_ROLE) for i in range(2)]
        assert states == [None, "offline"]   # el volumen entero no está: lo explica el aviso
        assert tab.offline_box.isVisibleTo(tab)
        assert "el disco «NO_EXISTE»" in tab.lbl_offline.text()
        assert "/Volumes" not in tab.lbl_offline.text()   # sin rutas del sistema
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


def _esperar(app, cond, segundos=20):
    import time
    t = time.time()
    while not cond() and time.time() - t < segundos:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return cond()


class TestLimpiarCatalogo:
    def test_ver_y_quitar_faltantes(self, app, pg_cfg, tmp_path):
        from convertir3x4.catalog.db import conectar
        from convertir3x4.catalog.models import Media
        from convertir3x4.catalog.repo import Repo
        from convertir3x4.ui.widgets.catalogo_avanzado import CatalogoAvanzado

        def abrir():
            return Repo(conectar(pg_cfg), str(tmp_path))

        with abrir() as r:
            for nombre in ("esta.jpg", "se_movio.jpg"):
                r.upsert_media(Media(path=str(tmp_path / nombre), filename=nombre, kind="image"))
            r.marcar_faltantes([r.get_media_by_path(str(tmp_path / "se_movio.jpg")).id])

        dlg = CatalogoAvanzado(abrir=abrir)
        cambios = []
        dlg.catalogChanged.connect(lambda: cambios.append(1))
        dlg.buscar()
        assert _esperar(app, lambda: dlg._worker is None)
        assert "Hay 1 foto o video cuyo archivo ya no está en su carpeta" in dlg.lbl_resultado.text()
        assert dlg.btn_quitar.isVisibleTo(dlg) and dlg.btn_quitar.text() == "Quitar 1 del catálogo…"
        dlg.quitar()                        # (la confirmación se prueba a mano)
        assert _esperar(app, lambda: dlg._worker is None)
        assert cambios == [1]
        with abrir() as r:
            assert [m.filename for m in r.all_media()] == ["esta.jpg"]
        dlg.deleteLater()


class TestBuscarConCatalogo:
    def _servicio(self, pg_cfg, raiz):
        from convertir3x4.catalog.db import conectar
        from convertir3x4.catalog.repo import Repo
        from convertir3x4.ui.servicio_catalogo import ServicioCatalogo
        return ServicioCatalogo(abrir=lambda: Repo(conectar(pg_cfg), raiz))

    def test_busca_baja_miniaturas_y_pone_en_gris_lo_que_falta(self, app, pg_cfg, tmp_path,
                                                                 monkeypatch):
        import io
        from PIL import Image
        from convertir3x4.catalog.db import conectar
        from convertir3x4.catalog.models import Media
        from convertir3x4.catalog.repo import Repo
        from convertir3x4.ui.tabs import tab_buscar
        from convertir3x4.ui.widgets.media_grid import STATE_ROLE

        cache = tmp_path / "cache"
        monkeypatch.setattr(tab_buscar, "catalog_thumb_dir", lambda: str(cache))
        nas = tmp_path / "nas"
        nas.mkdir()
        (nas / "culto_rojo.jpg").write_bytes(b"x")       # el archivo está
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (220, 30, 30)).save(buf, "JPEG")
        with Repo(conectar(pg_cfg), str(nas)) as r:
            for nombre in ("culto_rojo.jpg", "culto_movido.jpg"):
                r.upsert_media(Media(path=str(nas / nombre), filename=nombre, kind="image"),
                               miniatura=buf.getvalue())
            r.marcar_faltantes([r.get_media_by_path(str(nas / "culto_movido.jpg")).id])

        srv = self._servicio(pg_cfg, str(nas))
        t = tab_buscar.BuscarTab(srv=srv)
        t.resize(900, 600)
        t.show()
        t.txt_query.setText("culto")
        t._do_search()
        assert _esperar(app, lambda: t.grid.count() == 2 and all(
            not t.grid.item(i).icon().isNull() for i in range(2)))
        assert not t.conn_box.isVisible()
        nombres = [t.grid.item(i).text() for i in range(2)]
        assert nombres == ["culto_rojo.jpg", "culto_movido.jpg"]   # lo que falta, al final
        assert [t.grid.item(i).data(STATE_ROLE) for i in range(2)] == [None, "missing"]
        assert len(list(cache.glob("*.jpg"))) == 2                  # quedaron en la caché

        # La celda del que falta se dibuja en gris; la otra, en color.
        img = t.grid.viewport().grab().toImage()
        def saturacion(i):
            rect = t.grid.visualItemRect(t.grid.item(i))
            c = img.pixelColor(rect.center().x(), rect.top() + rect.height() // 3)
            return c.hsvSaturation()
        assert saturacion(0) > 100 and saturacion(1) < 20
        t.close()
        t.deleteLater()
        srv.cerrar()

    def test_sin_servidor_avisa_y_no_congela(self, app):
        import time
        from convertir3x4.catalog.db import ConfigCatalogo, conectar
        from convertir3x4.catalog.repo import Repo
        from convertir3x4.ui.servicio_catalogo import ServicioCatalogo
        from convertir3x4.ui.tabs.tab_buscar import BuscarTab
        cfg = ConfigCatalogo(servidor="127.0.0.1", puerto=1, clave="x")
        srv = ServicioCatalogo(abrir=lambda: Repo(conectar(cfg), "/"))
        inicio = time.monotonic()
        t = BuscarTab(srv=srv)
        assert time.monotonic() - inicio < 0.5          # crear la pestaña no espera a la red
        t.show()
        assert _esperar(app, lambda: t.conn_box.estado == "error", 10)
        assert "el NAS no responde" in t.conn_box.lbl.text()
        assert t.conn_box.btn_retry.isVisibleTo(t)
        t.close()
        t.deleteLater()
        srv.cerrar()


class TestBuscarCargando:
    """Mientras se trae la búsqueda del NAS se ve que está trabajando."""

    def test_muestra_que_busca_solo_si_tarda(self, app, monkeypatch):
        from convertir3x4.ui.tabs import tab_buscar

        class Lento:            # el NAS contesta cuando la prueba quiere
            estado, texto = "ok", ""

            class _S:
                def connect(self, *_a):
                    pass
            estado_cambiado = _S()

            def __init__(self):
                self.pendientes = []

            def pedir(self, fn, ok=None, error=None):
                self.pendientes.append((fn, ok))
                return len(self.pendientes)

        monkeypatch.setattr(tab_buscar, "_CARGANDO_MS", 50)
        srv = Lento()
        t = tab_buscar.BuscarTab(srv=srv)
        t.resize(900, 600)
        t.show()
        t.txt_query.setText("culto")
        t._do_search()
        assert not t.barra_cargando.isVisible()          # rápido: no parpadea
        assert _esperar(app, lambda: t.barra_cargando.isVisible(), 2)
        assert t.lbl_count.text() == "Buscando…"
        assert "Buscando en el catálogo" in t.grid._empty_text
        altura = t.grid.geometry().top()
        # llega la respuesta de la última búsqueda
        fn, ok = srv.pendientes[-1]
        ok(([], {}, [], {}, {}))
        assert not t.barra_cargando.isVisible()
        assert t.lbl_count.text() == "0 resultados"
        assert t.grid.geometry().top() == altura          # la grilla no salta
        t.close()
        t.deleteLater()


class TestBuscarPorPaginas:
    def test_trae_de_a_paginas_al_bajar(self, app, pg_cfg, tmp_path, monkeypatch):
        from convertir3x4.catalog.db import conectar
        from convertir3x4.catalog.models import Media
        from convertir3x4.catalog.repo import Repo
        from convertir3x4.ui.servicio_catalogo import ServicioCatalogo
        from convertir3x4.ui.tabs import tab_buscar

        monkeypatch.setattr(tab_buscar, "catalog_thumb_dir", lambda: str(tmp_path / "cache"))
        monkeypatch.setattr(tab_buscar, "_PAGINA", 40)
        abrir = lambda: Repo(conectar(pg_cfg), str(tmp_path))      # noqa: E731
        with abrir() as r:
            for i in range(100):
                r.upsert_media(Media(path=str(tmp_path / f"f{i:03}.jpg"), filename=f"f{i:03}.jpg",
                                     kind="video" if i % 10 == 0 else "image"))
        srv = ServicioCatalogo(abrir=abrir)
        t = tab_buscar.BuscarTab(srv=srv)
        try:
            t.resize(700, 500)
            t.show()
            assert _esperar(app, lambda: t.grid.count() == 40)
            assert t.lbl_count.text() == "40 de 100"
            t.grid.verticalScrollBar().setValue(t.grid.verticalScrollBar().maximum())
            assert _esperar(app, lambda: t.grid.count() >= 80)
            nombres = [t.grid.item(i).text() for i in range(t.grid.count())]
            assert nombres == [f"f{i:03}.jpg" for i in range(len(nombres))]   # en orden, sin repetir
            t.grid.verticalScrollBar().setValue(t.grid.verticalScrollBar().maximum())
            assert _esperar(app, lambda: t.grid.count() == 100)
            assert t.lbl_count.text() == "100 resultados"
            # el filtro va a la base: están los 10 videos, no solo los de la 1.ª página
            next(b for b in t._kind_group.buttons() if b.property("kind") == "video").click()
            assert _esperar(app, lambda: t.lbl_count.text() == "10 resultados")
        finally:
            t.close()
            t.deleteLater()
            srv.cerrar()


class TestBuscarEnVivo:
    """Cliente-servidor: lo que cataloga el NAS aparece en Buscar sin tocar nada."""

    def test_lo_nuevo_aparece_solo_y_se_conserva_la_seleccion(self, app, pg_cfg, tmp_path,
                                                             monkeypatch):
        from convertir3x4.catalog.db import conectar
        from convertir3x4.catalog.models import Media
        from convertir3x4.catalog.repo import Repo
        from convertir3x4.ui import avisos_catalogo
        from convertir3x4.ui.servicio_catalogo import ServicioCatalogo
        from convertir3x4.ui.tabs import tab_buscar
        from convertir3x4.ui.widgets.media_grid import MEDIA_ID_ROLE

        monkeypatch.setattr(tab_buscar, "catalog_thumb_dir", lambda: str(tmp_path / "cache"))
        monkeypatch.setattr(tab_buscar, "_VIVO_ESPERA_MS", 50)
        monkeypatch.setattr(tab_buscar, "_VIVO_MINIMO_MS", 50)
        nas = tmp_path / "nas"
        nas.mkdir()
        abrir = lambda: Repo(conectar(pg_cfg), str(nas))      # noqa: E731

        def catalogar(nombre):          # lo que hace el catalogador del NAS
            (nas / nombre).write_bytes(b"x")
            with abrir() as r:
                return r.upsert_media(Media(path=str(nas / nombre), filename=nombre,
                                            kind="image"))

        b_id = catalogar("b.jpg")
        srv = ServicioCatalogo(abrir=abrir)
        avisos = avisos_catalogo.AvisosCatalogo(abrir=abrir)
        t = tab_buscar.BuscarTab(srv=srv)
        avisos.cambio.connect(t.cambio_en_el_catalogo)
        avisos.start()
        try:
            t.resize(900, 600)
            t.show()
            assert _esperar(app, lambda: t.grid.count() == 1)
            t.grid.item(0).setSelected(True)
            time.sleep(0.3)                 # el oyente ya está escuchando
            catalogar("a.jpg")
            assert _esperar(app, lambda: t.grid.count() == 2, 10)
            assert [t.grid.item(i).text() for i in range(2)] == ["a.jpg", "b.jpg"]
            assert [it.data(MEDIA_ID_ROLE) for it in t.grid.selectedItems()] == [b_id]
            assert t.lbl_seleccion.text() == "1 seleccionado"

            # Con la pestaña escondida no consulta; al volver a verla, se pone al día.
            t.hide()
            catalogar("c.jpg")
            time.sleep(1.5)
            app.processEvents()
            assert t.grid.count() == 2
            t.show()
            assert _esperar(app, lambda: t.grid.count() == 3, 10)
        finally:
            avisos.detener()
            t.close()
            t.deleteLater()
            srv.cerrar()

    def test_el_oyente_vuelve_solo_si_se_corta(self, app, pg_cfg, monkeypatch):
        from convertir3x4.catalog.db import ConfigCatalogo, conectar
        from convertir3x4.catalog.repo import Repo
        from convertir3x4.ui import avisos_catalogo
        monkeypatch.setattr(avisos_catalogo, "REINTENTO", 0.2)
        intentos = []

        def abrir():
            intentos.append(1)
            if len(intentos) == 2:      # la segunda vez el NAS no responde
                raise ConnectionError("sin red")
            return Repo(conectar(pg_cfg), "/")

        avisos = avisos_catalogo.AvisosCatalogo(abrir=abrir)
        cambios = []
        avisos.cambio.connect(lambda: cambios.append(1))
        avisos.start()
        try:
            assert _esperar(app, lambda: intentos == [1])
            time.sleep(0.3)
            # se corta la conexión del oyente desde el servidor
            with Repo(conectar(pg_cfg), "/") as r:
                r.conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                               "WHERE query = 'LISTEN catalogo'")
            # vuelve (al tercer intento) y avisa, por si se perdió algo
            assert _esperar(app, lambda: len(intentos) >= 3 and cambios, 10)
        finally:
            avisos.detener()
        assert not avisos.isRunning()

    def test_el_oyente_consulta_cada_tanto_para_no_quedar_sordo(self, app, pg_cfg, monkeypatch):
        from convertir3x4.catalog.db import conectar
        from convertir3x4.catalog.repo import Repo
        from convertir3x4.ui import avisos_catalogo
        monkeypatch.setattr(avisos_catalogo, "PING", 0.2)
        avisos = avisos_catalogo.AvisosCatalogo(abrir=lambda: Repo(conectar(pg_cfg), "/"))
        avisos.start()
        try:
            with Repo(conectar(pg_cfg), "/") as r:
                def ultima_del_oyente():
                    fila = r.conn.execute(
                        "SELECT query FROM pg_stat_activity WHERE datname = current_database() "
                        "AND pid <> pg_backend_pid() AND query IN ('LISTEN catalogo', 'SELECT 1')"
                    ).fetchone()
                    return fila and fila["query"]
                assert _esperar(app, lambda: ultima_del_oyente() == "SELECT 1", 5)
        finally:
            avisos.detener()


def test_confirmaciones_nombran_la_accion():
    # Criterio de textos (impeccable/clarify): nada de Sí/No/Cerrar genéricos en
    # confirmaciones; se usa ui/widgets/dialogos.confirmar() con la acción en el botón.
    import glob
    malos = []
    for f in glob.glob(os.path.join(os.path.dirname(__file__), "convertir3x4", "ui", "**", "*.py"),
                       recursive=True):
        texto = open(f, encoding="utf-8").read()
        for patron in ("StandardButton.Yes", "StandardButton.No)", "QMessageBox.question("):
            if patron in texto:
                malos.append(f"{os.path.basename(f)}: {patron}")
    assert not malos, malos


class TestCambioDeTemaEnVivo:
    def test_al_cambiar_el_sistema_cambian_colores_e_iconos(self, app):
        from PyQt6.QtWidgets import QPushButton
        from convertir3x4.ui import icons, style

        def color_del_icono(b):
            img = b.icon().pixmap(32, 32).toImage()
            for y in range(img.height()):
                for x in range(img.width()):
                    c = img.pixelColor(x, y)
                    if c.alpha() > 200:
                        return c.name()
            return None

        style._reaplicar(app, "dark")
        b = QPushButton()
        icons.set_icon(b, "x", 24, "text")
        oscuro = color_del_icono(b)
        try:
            style._reaplicar(app, "light")             # la Mac pasa a modo claro
            assert style.T.scheme == "light"
            assert style.LIGHT["bg"] in app.styleSheet()
            assert app.palette().window().color().name() == style.LIGHT["bg"]
            claro = color_del_icono(b)
            assert claro != oscuro                     # el icono se repintó solo
            assert QColor(claro).lightness() < QColor(oscuro).lightness()
        finally:
            style._reaplicar(app, "dark")
        assert color_del_icono(b) == oscuro


class TestBuscarAConvertir:
    def test_mandar_cambia_a_convertir(self, app, window, tmp_path):
        from PIL import Image
        f = str(tmp_path / "culto.jpg")
        Image.new("RGB", (80, 60), (10, 20, 30)).save(f)
        window.show_module(0)
        window.buscar_tab.enviarAConvertir.emit([f])
        app.processEvents()
        assert window.stack.currentWidget() is window.convert_tab
        assert f in window.convert_tab._paths()


class TestRegistroDeLosTextos:
    """Los textos de la aplicación van en usted y con vocabulario formal
    (decisión del 15/11/2026). Revisa todos los textos del código, sin los
    comentarios ni la documentación interna."""

    PROHIBIDAS = re.compile(
        r"\b(tocá|elegí|probá|revisá|pegá|usá|andá|escribí|seleccioná|arrastrá|enchufá|"
        r"cargá|corré|agregá|pasá|fijate|avisale|pedile|querés|podés|tenés|"
        r"bajar|bajando|bajarlo|mandar|mandarlos|enchufad[oa]s?|la app|el mouse)\b", re.I)

    def test_sin_voseo_ni_palabras_coloquiales(self):
        import ast
        raiz = os.path.join(os.path.dirname(__file__), "convertir3x4")
        encontrados = []
        for carpeta, _, archivos in os.walk(raiz):
            for nombre in archivos:
                if not nombre.endswith(".py"):
                    continue
                ruta = os.path.join(carpeta, nombre)
                arbol = ast.parse(open(ruta, encoding="utf-8").read())
                docs = {id(n.body[0].value) for n in ast.walk(arbol)
                        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef))
                        and n.body and isinstance(n.body[0], ast.Expr)
                        and isinstance(n.body[0].value, ast.Constant)}
                for n in ast.walk(arbol):
                    if (isinstance(n, ast.Constant) and isinstance(n.value, str)
                            and id(n) not in docs and self.PROHIBIDAS.search(n.value)):
                        encontrados.append(f"{nombre}:{n.lineno}: {n.value[:70]!r}")
        assert not encontrados, "\n".join(encontrados)


class TestCatalogarEnElNas:
    """Pestaña Catalogar: pide al NAS y muestra lo que informa (catalogado_estado)."""

    AHORA = datetime.datetime(2026, 11, 22, 10, 0).astimezone()

    def _est(self, **k):
        base = dict(fase="esperando", segundos_sin_latido=5, hechos=None, total=None,
                    ultima_fin=None, ultima_error=None, ultima_resumen=None, latido=None)
        base.update(k)
        return base

    def test_textos_del_estado(self):
        from convertir3x4.ui.tabs.tab_motor import texto_estado
        hoy = self.AHORA.replace(hour=2, minute=10)
        assert "no está preparado" in texto_estado(self._est(segundos_sin_latido=None), [])[0]
        t, trabaja = texto_estado(self._est(fase="catalogando", hechos=3, total=10), [])
        assert t == "El NAS está revisando las carpetas: 3 de 10." and trabaja
        t, _ = texto_estado(self._est(ultima_fin=hoy, ultima_resumen={
            "catalogado": {"added": 12, "missing": 1}, "ia": {"scene_tags": 3}}), [], self.AHORA)
        assert t.startswith("Última revisión: hoy a las 02:10. 12 nuevos.")
        assert "1 ya no está en su carpeta" in t and "La IA sugirió 3 etiquetas" in t
        t, _ = texto_estado(self._est(ultima_fin=hoy, ultima_error="x"), [], self.AHORA)
        assert "no pudo terminar" in t
        t, trabaja = texto_estado(self._est(segundos_sin_latido=600, latido=hoy),
                                  [{"id": 1}], self.AHORA)
        assert "no está catalogando" in t and "hoy a las 02:10" in t and "en espera" in t
        assert not trabaja
        assert "grabadora" in texto_estado(self._est(fase="esperando_copia"), [])[0]
        assert "versión anterior" in texto_estado(self._est(fase="detenido"), [])[0]
        assert texto_estado(self._est(), [{"id": 1}])[1]       # pedido en la fila

    def test_pide_al_nas_y_muestra_el_estado(self, app, pg_cfg, tmp_path):
        from convertir3x4.catalog.db import conectar
        from convertir3x4.catalog.repo import Repo
        from convertir3x4.ui.servicio_catalogo import ServicioCatalogo
        from convertir3x4.ui.tabs.tab_motor import MotorTab

        def abrir():
            return Repo(conectar(pg_cfg), str(tmp_path))
        with abrir() as r:           # lo que publica el catalogador al arrancar
            r.actualizar_estado(carpetas=["Publicar", "Entrada"], fase="esperando")
        srv = ServicioCatalogo(abrir=abrir)
        tab = MotorTab(srv=srv)
        try:
            assert _esperar(app, lambda: tab.btn_run.isEnabled())
            assert "Publicar" in tab.lbl_carpetas.text()
            assert "todavía no catalogó" in tab.lbl_status.text()
            tab.btn_run.click()
            assert not tab.btn_run.isEnabled()
            assert _esperar(app, lambda: "va a empezar" in tab.lbl_status.text())
            with abrir() as r:
                assert [p["carpeta"] for p in r.pedidos_abiertos()] == [None]
                p = r.tomar_pedido()
                r.actualizar_estado(fase="catalogando", hechos=5, total=20)
            tab.refresh()
            assert _esperar(app, lambda: "5 de 20" in tab.lbl_status.text())
            assert tab.progress.isVisibleTo(tab) and not tab.btn_run.isEnabled()
            cambios = []
            tab.catalogChanged.connect(lambda: cambios.append(1))
            with abrir() as r:
                r.cerrar_pedido(p["id"], {"catalogado": {"added": 2}})
                r.actualizar_estado(fase="esperando", ultima_fin=datetime.datetime.now(),
                                    ultima_resumen={"catalogado": {"added": 2}})
            tab.refresh()
            assert _esperar(app, lambda: "2 nuevos" in tab.lbl_status.text())
            assert tab.btn_run.isEnabled() and not tab.progress.isVisibleTo(tab)
            assert cambios == []      # primera pasada vista: no había una anterior
            # una subcarpeta fuera de la lista no se pide
            tab.catalogar_una_carpeta(str(tmp_path / "Historial"))
            assert "no está entre las que se catalogan" in tab.lbl_status.text()
        finally:
            tab.deleteLater()
            srv.cerrar()

    def test_fotos_desde_buscar_van_a_la_persona(self, app, pg_cfg, tmp_path, monkeypatch):
        from PIL import Image
        from convertir3x4.catalog.db import conectar
        from convertir3x4.catalog.repo import Repo
        from convertir3x4.ui.servicio_catalogo import ServicioCatalogo
        from convertir3x4.ui.tabs import tab_motor

        abrir = lambda: Repo(conectar(pg_cfg), str(tmp_path))      # noqa: E731
        with abrir() as r:
            r.add_person("Juan Carlos Bazán")
        fotos = []
        for nombre in ("culto1.jpg", "culto2.jpg"):
            fotos.append(str(tmp_path / nombre))
            Image.new("RGB", (800, 600), "blue").save(fotos[-1])
        ofrecidos = []

        def elegir(_self, nombres, n):
            ofrecidos.append((nombres, n))
            return "  juan carlos   BAZÁN "              # otras mayúsculas: la misma persona
        monkeypatch.setattr(tab_motor.MotorTab, "_elegir_persona", elegir)
        monkeypatch.setattr(tab_motor, "abrir_repo", abrir)
        srv = ServicioCatalogo(abrir=abrir)
        tab = tab_motor.MotorTab(srv=srv)
        try:
            tab.recibir_fotos_de_persona(fotos)
            assert _esperar(app, lambda: "se enviaron 2 fotos al NAS" in tab.lbl_people_status.text())
            assert ofrecidos == [(["Juan Carlos Bazán"], 2)]
            assert tab.lbl_people_status.text().startswith("Juan Carlos Bazán:")
            with abrir() as r:
                gente = r.list_persons()
            assert [(p["name"], p["pendientes"]) for p in gente] == [("Juan Carlos Bazán", 2)]
        finally:
            tab.deleteLater()
            srv.cerrar()

    def test_fotos_desde_buscar_sin_nombre_no_envia(self, app, pg_cfg, tmp_path, monkeypatch):
        from convertir3x4.catalog.db import conectar
        from convertir3x4.catalog.repo import Repo
        from convertir3x4.ui.servicio_catalogo import ServicioCatalogo
        from convertir3x4.ui.tabs import tab_motor

        abrir = lambda: Repo(conectar(pg_cfg), str(tmp_path))      # noqa: E731
        llamado = []
        monkeypatch.setattr(tab_motor.MotorTab, "_elegir_persona",
                            lambda *a: llamado.append(1) or None)   # canceló
        monkeypatch.setattr(tab_motor.MotorTab, "enviar_referencias",
                            lambda *a: pytest.fail("envió sin nombre"))
        srv = ServicioCatalogo(abrir=abrir)
        tab = tab_motor.MotorTab(srv=srv)
        try:
            tab.recibir_fotos_de_persona([str(tmp_path / "x.jpg")])
            assert _esperar(app, lambda: llamado == [1])
        finally:
            tab.deleteLater()
            srv.cerrar()

    def test_buscar_agregar_persona_abre_catalogar(self, app, window, monkeypatch):
        recibidas = []
        monkeypatch.setattr(window.motor_tab, "recibir_fotos_de_persona", recibidas.append)
        window.show_module(0)
        window.buscar_tab.enviarAPersona.emit(["/fotos/a.jpg"])
        app.processEvents()
        assert window.stack.currentWidget() is window.motor_tab
        assert recibidas == [["/fotos/a.jpg"]]

    def test_personas_la_pc_sube_y_el_nas_procesa(self, app, pg_cfg, tmp_path, monkeypatch):
        import numpy as np
        from PIL import Image
        from convertir3x4 import catalogador as cat
        from convertir3x4.catalog.db import ConfigCatalogo, conectar
        from convertir3x4.catalog.repo import Repo
        from convertir3x4.engine import ai_models, faces
        from convertir3x4.ui.servicio_catalogo import ServicioCatalogo
        from convertir3x4.ui.tabs import tab_motor

        abrir = lambda: Repo(conectar(pg_cfg), str(tmp_path))      # noqa: E731
        fotos = []
        for nombre, color in (("pastor1.jpg", "blue"), ("pastor2.jpg", "red")):
            fotos.append(str(tmp_path / nombre))
            Image.new("RGB", (3000, 2000), color).save(fotos[-1])
        monkeypatch.setattr(tab_motor.QFileDialog, "getOpenFileNames",
                            staticmethod(lambda *a, **k: (fotos, "")))
        monkeypatch.setattr(tab_motor, "abrir_repo", abrir)
        # la PC no baja ni carga modelos
        monkeypatch.setattr(ai_models, "ensure", lambda *a, **k: pytest.fail("bajó modelos"))
        srv = ServicioCatalogo(abrir=abrir)
        tab = tab_motor.MotorTab(srv=srv)
        try:
            tab._pick_and_add_refs(None, "Juan Carlos Bazán")
            assert _esperar(app, lambda: "se enviaron 2 fotos al NAS" in tab.lbl_people_status.text())
            assert _esperar(app, lambda: tab.lst_people.count() == 1 and "2 en proceso en el NAS"
                            in tab.lst_people.item(0).text())

            class Caras:                 # el NAS: hay cara solo en la foto azul
                def __init__(self, *a):
                    pass

                def revisar_referencia(self, img):
                    if img.getpixel((0, 0))[2] > 200:
                        return faces.RevisionReferencia(np.ones(128, np.float32), 1,
                                                        (100, 100, 400, 400), 0.95)
                    return faces.RevisionReferencia(None)
            monkeypatch.setattr(ai_models, "ensure", lambda *a, **k: None)
            monkeypatch.setattr(faces, "FaceEngine", Caras)
            with abrir() as r:
                cfg = cat.ConfigCatalogador(catalogo=ConfigCatalogo(servidor="x"))
                assert cat.procesar_referencias(r, cfg) == 2
            tab.cambio_en_el_catalogo()                     # el aviso del NAS
            assert _esperar(app, lambda: tab.lst_people.item(0).text()
                            == "Juan Carlos Bazán — 1 foto · 1 sin rostro visible")
            assert "pastor2.jpg" in tab.lst_people.item(0).toolTip()

            # El detalle de la persona: cada foto con lo que vio el NAS
            assert not tab.panel_persona.isVisibleTo(tab)
            tab.lst_people.item(0).setSelected(True)
            assert tab.panel_persona.isVisibleTo(tab)
            assert _esperar(app, lambda: tab.lista_fotos.count() == 2)
            assert tab.lbl_persona_estado.text() == "Conviene agregar más fotos"
            textos = [lbl.text() for lbl in tab.panel_persona.findChildren(QLabel)]
            assert "Sirve" in textos and "No se encontró un rostro" in textos
            assert "Todavía ninguna foto del catálogo" in tab.lbl_persona_catalogo.text()
            fila = tab.lista_fotos.itemAt(0).widget()
            assert any(not lbl.pixmap().isNull() for lbl in fila.findChildren(QLabel))
            # Quitar la foto sin rostro (confirmando)
            monkeypatch.setattr(tab_motor, "confirmar", lambda *a, **k: True)
            quitar = [b for b in tab.lista_fotos.itemAt(1).widget().findChildren(QPushButton)
                      if b.text() == "Quitar esta foto"]
            quitar[0].click()
            assert _esperar(app, lambda: tab.lista_fotos.count() == 1)
            assert tab.lst_people.item(0).text() == "Juan Carlos Bazán — 1 foto"
            assert tab.lst_people.item(0).isSelected() and tab.panel_persona.isVisibleTo(tab)
        finally:
            tab.deleteLater()
            srv.cerrar()


def test_los_nombres_de_la_barra_lateral_entran_enteros(window):
    # «Grabadora» se veía «Gra…ora»: el texto más el margen del botón no entraba.
    for b in window._nav.buttons() + [window.btn_help]:
        assert b.fontMetrics().horizontalAdvance(b.text()) <= b.width() - 8, b.text()


def test_buscar_nota_solo_que_se_conecto_el_nas(app, sin_catalogo, monkeypatch):
    from convertir3x4.catalog.models import Media
    from convertir3x4.ui.tabs import tab_buscar
    from convertir3x4.ui.widgets.media_grid import STATE_ROLE
    t = tab_buscar.BuscarTab(srv=sin_catalogo)
    t.show()
    m = Media(path="/Volumes/NAS_PRUEBA/evento/a.jpg", filename="a.jpg", kind="image", id=1)
    t._mostrar_busqueda(t._busqueda, [m], {}, [], {m.path: False},
                        {"/Volumes/NAS_PRUEBA": False})
    t.grid.item(0).setSelected(True)
    assert t.grid.item(0).data(STATE_ROLE) == "offline" and t.offline_box.isVisibleTo(t)
    conectado = []
    monkeypatch.setattr(tab_buscar, "_existe", lambda r: (conectado.append(r), True)[1])
    t._revisar_raices()                         # lo que hace el reloj cada 5 s
    assert _esperar(app, lambda: t.grid.item(0).data(STATE_ROLE) is None, 5)
    assert conectado == ["/Volumes/NAS_PRUEBA"]  # una pregunta por raíz, no por archivo
    assert not t.offline_box.isVisibleTo(t)
    assert t.grid.item(0).isSelected()           # no se pierde lo elegido
    t.close()
    t.deleteLater()


def test_la_grilla_de_buscar_muestra_las_etiquetas(app, sin_catalogo):
    from convertir3x4.catalog.models import Media
    from convertir3x4.ui.tabs.tab_buscar import BuscarTab
    from convertir3x4.ui.widgets.media_grid import TAGS_ROLE
    t = BuscarTab(srv=sin_catalogo)
    t.resize(700, 500)
    t.show()
    m = Media(path="/a.jpg", filename="a.jpg", kind="image", id=1,
              etiquetas=[("predicación", False)])
    t._populate([m], {}, {"/a.jpg": True}, {"/": True})
    app.processEvents()
    it = t.grid.item(0)
    assert it.data(TAGS_ROLE) == [("predicación", False)]
    rect = t.grid.visualItemRect(it)
    img = t.grid.viewport().grab().toImage()
    dpr = img.devicePixelRatio()
    # el renglón de las etiquetas, abajo de la celda: hay violeta de la IA
    colores = [img.pixelColor(int(x * dpr), int(y * dpr))
               for y in range(rect.bottom() - 22, rect.bottom() - 4)
               for x in range(rect.left(), rect.right())]
    # el texto en violeta (tono de T.ia) y el fondo del chip no negro (rgba mal leído)
    assert any(c.hslHue() in range(240, 290) and c.lightness() > 110 for c in colores)
    assert QColor(0, 0, 0) not in colores
    t.close()
    t.deleteLater()


def test_qcolor_entiende_los_colores_con_transparencia():
    from convertir3x4.ui.style import DARK, LIGHT, qcolor
    for paleta in (DARK, LIGHT):
        for nombre, valor in paleta.items():
            if isinstance(valor, str) and valor.startswith(("#", "rgba")):
                assert qcolor(valor).isValid(), nombre
    c = qcolor("rgba(150, 110, 240, 0.16)")
    assert (c.red(), c.green(), c.blue(), round(c.alphaF(), 2)) == (150, 110, 240, 0.16)
