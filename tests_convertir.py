"""Tests de Convertir con fotos y videos: armado de trabajos, descargas y la pestaña.

Los videos de prueba los genera el ffmpeg de la app. Nada baja de internet: la
descarga real se probó a mano (ver docs/convertir-fotos-y-videos.md).
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import subprocess
import tempfile
import time

import pytest
from PIL import Image

from convertir3x4.convert import batch, clips, download


def _ff(*args):
    r = subprocess.run([clips.ffmpeg_exe(), "-hide_banner", "-v", "error", "-y", *args],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.fixture(scope="module")
def media():
    d = tempfile.mkdtemp()
    photo = os.path.join(d, "foto.jpg")
    Image.new("RGB", (800, 600), (30, 90, 160)).save(photo)
    photo2 = os.path.join(d, "foto2.jpg")
    Image.new("RGB", (600, 900), (160, 60, 40)).save(photo2)
    video = os.path.join(d, "video.mp4")
    _ff("-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
        "-af", "volume=-25dB", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", video)
    yield {"dir": d, "photo": photo, "photo2": photo2, "video": video}
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- batch: plan

class TestPlan:
    def test_centrada_one_output_each_numbered(self):
        jobs = batch.plan(["/a/uno.jpg", "/a/dos.mp4"], batch.CENTRADA, batch.Options())
        assert [j.out_name for j in jobs] == ["0001_uno_8x9.jpg", "0002_dos_8x9.mp4"]
        assert [j.is_video for j in jobs] == [False, True]

    def test_doble_with_a_video_gives_video(self):
        jobs = batch.plan(["/a/f.jpg", "/a/v.mov", "/a/g.jpg", "/a/h.jpg"], batch.DOBLE,
                          batch.Options())
        assert [j.out_name for j in jobs] == ["0001_f+v_8x9.mp4", "0002_g+h_8x9.jpg"]
        assert jobs[0].has_clip and not jobs[1].has_clip

    def test_doble_odd_last_goes_alone(self):
        jobs = batch.plan(["/a/1.jpg", "/a/2.jpg", "/a/3.jpg"], batch.DOBLE, batch.Options())
        assert [len(j.inputs) for j in jobs] == [2, 1]

    def test_fondo_needs_background(self):
        with pytest.raises(ValueError):
            batch.plan(["/a/1.jpg"], batch.FONDO, batch.Options())
        jobs = batch.plan(["/a/1.jpg"], batch.FONDO, batch.Options(background="/b.jpg"))
        assert jobs[0].out_name == "0001_1_fondo_8x9.jpg"

    def test_doble_con_fondo_elegido(self):
        opts = batch.Options(background="/b.jpg")
        jobs = batch.plan(["/a/1.jpg", "/a/2.jpg", "/a/3.jpg"], batch.DOBLE, opts)
        assert all(j.con_fondo for j in jobs)
        assert jobs[0].out_name == "0001_1+2_fondo_8x9.jpg"
        # Sin fondo elegido, de sus colores como siempre
        assert not batch.plan(["/a/1.jpg"], batch.DOBLE, batch.Options())[0].con_fondo
        # El fondo no cuenta en "1 por pantalla" sin fondo
        assert not batch.plan(["/a/1.jpg"], batch.CENTRADA, opts)[0].con_fondo

    def test_sobre_fondo_respeta_fotos_como_video(self):
        opts = batch.Options(background="/b.jpg", photos_as_video=True)
        job = batch.plan(["/a/1.jpg"], batch.FONDO, opts)[0]
        assert job.is_video and job.out_name.endswith(".mp4")

    def test_photos_as_video_and_aspect(self):
        jobs = batch.plan(["/a/1.jpg"], batch.CENTRADA,
                          batch.Options(aspect=(3, 4), photos_as_video=True))
        assert jobs[0].out_name == "0001_1_3x4.mp4" and jobs[0].is_video

    def test_describe_in_plain_words(self):
        assert batch.describe(["/a/1.jpg"], batch.CENTRADA, batch.Options()) == "Resultado: 1 foto."
        assert batch.describe(["/a/1.jpg", "/a/2.mp4"], batch.CENTRADA,
                              batch.Options()) == "Resultado: 1 foto y 1 video."
        text = batch.describe(["/a/1.jpg", "/a/2.mp4", "/a/3.jpg"], batch.DOBLE, batch.Options())
        assert text in ("Resultado: 1 video y 1 foto (el último elemento ocupa una pantalla propia).",
                        "Resultado: 1 foto y 1 video (el último elemento ocupa una pantalla propia).")
        # Dos videos: la diferencia entre "1 por pantalla" y "2 por pantalla" se lee
        dos = ["/a/1.mp4", "/a/2.mp4"]
        assert batch.describe(dos, batch.CENTRADA, batch.Options()) == "Resultado: 2 videos."
        assert batch.describe(dos, batch.DOBLE, batch.Options()) == "Resultado: 1 video."
        assert batch.describe(["/a/1.jpg"], batch.FONDO, batch.Options()) == "Falta elegir el fondo."


# ---------------------------------------------------------------- batch: run

class TestRun:
    def test_photo_and_video_centered(self, media, tmp_path):
        jobs = batch.plan([media["photo"], media["video"]], batch.CENTRADA, batch.Options())
        steps = []
        results = [batch.run(j, str(tmp_path), batch.Options(), steps.append) for j in jobs]
        assert all(r.error is None for r in results)
        assert Image.open(results[0].out_path).size[0] > 0
        info = clips.clip_info(results[1].out_path)
        assert (info.width, info.height) == (896, 1008)
        assert results[1].audio.before is not None and not results[1].audio.warnings
        assert steps[-1] == 1.0

    def test_doble_photo_and_video(self, media, tmp_path):
        job = batch.plan([media["photo"], media["video"]], batch.DOBLE, batch.Options())[0]
        r = batch.run(job, str(tmp_path), batch.Options())
        assert r.error is None and r.out_path.endswith(".mp4")
        assert clips.clip_info(r.out_path).duration == pytest.approx(3, abs=0.3)

    def test_dos_fotos_sobre_fondo_elegido(self, media, tmp_path):
        verde = str(tmp_path / "verde.jpg")
        Image.new("RGB", (400, 400), (20, 200, 40)).save(verde)
        opts = batch.Options(background=verde)
        for paths in ([media["photo"], media["photo2"]], [media["photo"]]):
            job = batch.plan(paths, batch.DOBLE, opts)[0]
            r = batch.run(job, str(tmp_path), opts)
            assert r.error is None
            esquina = Image.open(r.out_path).convert("RGB").getpixel((2, 2))
            assert esquina[1] > 150 and esquina[0] < 80      # se ve el fondo verde

    def test_foto_y_video_sobre_fondo_elegido(self, media, tmp_path):
        opts = batch.Options(background=media["photo2"])
        for paths in ([media["photo"], media["video"]], [media["video"]]):
            job = batch.plan(paths, batch.DOBLE, opts)[0]
            r = batch.run(job, str(tmp_path), opts)
            assert r.error is None and r.out_path.endswith(".mp4")
            assert clips.clip_info(r.out_path).duration == pytest.approx(3, abs=0.3)

    def test_foto_sobre_fondo_como_video(self, media, tmp_path):
        opts = batch.Options(background=media["photo2"], photos_as_video=True)
        job = batch.plan([media["photo"]], batch.FONDO, opts)[0]
        r = batch.run(job, str(tmp_path), opts)
        assert r.error is None and r.out_path.endswith(".mp4") and os.path.exists(r.out_path)

    def test_video_over_background(self, media, tmp_path):
        opts = batch.Options(background=media["photo2"])
        job = batch.plan([media["video"]], batch.FONDO, opts)[0]
        r = batch.run(job, str(tmp_path), opts)
        assert r.error is None and os.path.exists(r.out_path)

    def test_photos_as_video_can_be_cancelled(self, media, tmp_path):
        opts = batch.Options(photos_as_video=True)
        job = batch.plan([media["photo"]], batch.CENTRADA, opts)[0]
        with pytest.raises(clips.Cancelled):
            batch.run(job, str(tmp_path), opts, cancel=lambda: True)
        assert os.listdir(tmp_path) == []      # no queda el MP4 a medias

    def test_missing_input_is_reported_not_raised(self, tmp_path):
        job = batch.plan(["/no/existe.jpg"], batch.CENTRADA, batch.Options())[0]
        r = batch.run(job, str(tmp_path), batch.Options())
        assert r.error and "No se encontró" in r.error
        assert os.listdir(tmp_path) == []


# ---------------------------------------------------------------- descargas (sin red)

class TestDownloadHelpers:
    def test_is_link(self):
        assert download.is_link("https://www.youtube.com/watch?v=x")
        assert download.is_link("  http://fb.watch/abc  ")
        assert not download.is_link("hola")
        assert not download.is_link("https://a.com/x y")

    def test_facebook_prefix_is_removed(self):
        name = "4.7K views · 138 reactions ｜ Culto de jóvenes [123].mp4"
        assert download.clean_name(name) == "Culto de jóvenes [123].mp4"
        assert download.clean_name("Normal [1].mp4") == "Normal [1].mp4"

    def test_errors_in_plain_words(self):
        assert "privado" in download._explain("ERROR: Private video. Sign in")
        assert "de ese sitio" in download._explain("ERROR: Unsupported URL: x")
        assert "conexión" in download._explain("ERROR: Unable to download webpage")

    def test_every_platform_has_a_pinned_deno(self):
        assert download._deno_target() in download.DENO_ZIPS
        assert all(len(h) == 64 for h in download.DENO_ZIPS.values())


# ---------------------------------------------------------------- la pestaña

@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication
    from convertir3x4.ui.style import apply_theme
    a = QApplication.instance() or QApplication([])
    a.setOrganizationName("CDLM-tests")
    a.setApplicationName("Convertir3x4-tests")
    apply_theme(a, "dark")
    return a


def _wait(app, cond, timeout=60):
    t = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.02)
        assert time.time() - t < timeout, "se pasó el tiempo de espera"
    app.processEvents()


@pytest.fixture
def tab(app):
    from PyQt6.QtCore import QSettings
    QSettings().clear()
    from convertir3x4.ui.tabs.tab_convertir import ConvertTab
    t = ConvertTab()
    t.auto_open_folder = False
    t.resize(1200, 760)
    t.show()
    yield t
    t.shutdown()
    t.close()
    t.deleteLater()


class TestConvertTab:
    def test_mixed_grid_filter_and_video_thumbs(self, app, tab, media):
        from convertir3x4.ui.widgets.media_grid import STATE_ROLE, STORYBOARD_ROLE, DURATION_ROLE
        tab._add_paths([media["photo"], media["video"], "/NAS/no_esta.jpg"], missing_ok=True)
        items = tab._items()
        assert [it.data(STATE_ROLE) for it in items] == [None, "loading", "missing"]
        _wait(app, lambda: items[1].data(STATE_ROLE) is None)
        assert items[1].data(STORYBOARD_ROLE) and items[1].data(DURATION_ROLE) == pytest.approx(3, abs=0.2)
        assert tab.lbl_count.text() == "2 fotos · 1 video"
        tab._kind_group.buttons()[2].click()     # Videos
        assert [it.isHidden() for it in items] == [True, False, True]

    def test_missing_files_cannot_be_converted(self, app, tab, media):
        tab._add_paths([media["photo"], "/NAS/no_esta.jpg"], missing_ok=True)
        for it in tab._items():
            it.setSelected(True)
        tab._refresh_panel()
        assert tab.btn_convert.text() == "Crear 1 foto"
        assert "1 no se puede convertir" in tab.lbl_summary.text()

    def test_convert_batch_end_to_end(self, app, tab, media, tmp_path):
        from convertir3x4.ui.widgets.media_grid import STATE_ROLE
        tab._add_paths([media["photo"], media["video"]])
        items = tab._items()
        _wait(app, lambda: items[1].data(STATE_ROLE) is None)
        for it in items:
            it.setSelected(True)
        tab.set_por_pantalla(2)
        assert tab.btn_convert.text() == "Crear 1 video"
        jobs = batch.plan(tab._selected_paths(), tab._mode, tab._batch_options())
        tab.start_jobs(jobs, str(tmp_path), tab._batch_options())
        assert tab.btn_cancel.isVisible() and not tab.btn_convert.isVisible()
        _wait(app, lambda: not tab._busy(), timeout=120)
        assert [it.data(STATE_ROLE) for it in items] == ["done", "done"]
        assert os.listdir(tmp_path) == ["0001_foto+video_8x9.mp4"]
        assert tab.lbl_status.text().startswith("1 listo")

    def test_cancel_leaves_items_ready_again(self, app, tab, media, tmp_path):
        from convertir3x4.ui.widgets.media_grid import STATE_ROLE
        tab._add_paths([media["photo"], media["photo2"]])
        tab._opts.photos_as_video = True       # 30 s de video por foto: da tiempo a cancelar
        for it in tab._items():
            it.setSelected(True)
        jobs = batch.plan(tab._selected_paths(), batch.CENTRADA, tab._batch_options())
        tab.start_jobs(jobs, str(tmp_path), tab._batch_options())
        tab._cancel()
        _wait(app, lambda: not tab._busy(), timeout=120)
        assert all(it.data(STATE_ROLE) in (None, "done") for it in tab._items())
        assert tab.lbl_status.text().startswith("Cancelado")
        assert not any(f.endswith(".mp4") and os.path.getsize(tmp_path / f) == 0
                       for f in os.listdir(tmp_path))


class TestAdvancedOptions:
    def test_changes_are_listed_for_the_warning(self):
        from convertir3x4.ui.widgets.advanced_dialog import SessionOptions
        assert SessionOptions().changes() == []
        o = SessionOptions(aspect=(3, 4), photos_as_video=True)
        assert o.changes() == ["Proporción 3:4", "Fotos como video"]

    def test_options_do_not_survive_a_restart(self, app, tab):
        tab._opts.aspect = (3, 4)
        tab._refresh_panel()
        assert tab.nonstd_box.isVisibleTo(tab)
        tab.save_state()
        from convertir3x4.ui.tabs.tab_convertir import ConvertTab
        fresh = ConvertTab()          # "volver a abrir la app"
        try:
            assert fresh._opts.aspect == (8, 9)
            assert not fresh.nonstd_box.isVisibleTo(fresh)
        finally:
            fresh.shutdown()
            fresh.deleteLater()

    def test_audio_level_is_not_an_option(self):
        from convertir3x4.ui.widgets import advanced_dialog
        src = open(advanced_dialog.__file__, encoding="utf-8").read()
        assert "LUFS" not in src.split('"""', 2)[2]   # fuera del docstring no se menciona



class TestListaSinProyectos:
    def test_la_lista_y_el_fondo_vuelven_al_abrir(self, app, tab, media):
        tab._add_paths([media["photo"], media["photo2"]])
        tab._save_list()                       # lo que pasa en cada cambio
        tab._set_background(media["photo2"])
        tab._save_list()
        from convertir3x4.ui.tabs.tab_convertir import ConvertTab
        otra = ConvertTab()                    # "volver a abrir la app", sin save_state
        try:
            assert otra._paths() == [media["photo"], media["photo2"]]
            assert otra._bg_path == media["photo2"]
        finally:
            otra.shutdown()
            otra.deleteLater()

    def test_vaciar_no_borra_archivos(self, app, tab, media):
        tab._add_paths([media["photo"]])
        assert tab.btn_clear.isEnabled()
        tab.clear_list()
        assert tab.list_widget.count() == 0 and not tab.btn_clear.isEnabled()
        assert os.path.exists(media["photo"])
        from convertir3x4.ui.tabs.tab_convertir import ConvertTab
        otra = ConvertTab()
        try:
            assert otra._paths() == []         # también quedó vacía la guardada
        finally:
            otra.shutdown()
            otra.deleteLater()

    def test_sin_botones_de_proyecto(self, tab):
        assert not hasattr(tab, "btn_new_project") and not hasattr(tab, "btn_save_project")
        assert not hasattr(tab, "titleChanged")


class TestDobleClic:
    def test_open_file_no_abre_lo_que_no_esta(self):
        from convertir3x4.utils import open_file
        assert open_file("/no/existe/video.mp4") is False
        assert open_file("") is False

    def test_doble_clic_abre_el_video_en_convertir(self, app, tab, media, monkeypatch):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        from convertir3x4.ui.tabs import tab_convertir
        abiertos = []
        monkeypatch.setattr(tab_convertir, "open_file", lambda p: abiertos.append(p) or True)
        tab._add_paths([media["video"]])
        app.processEvents()
        rect = tab.list_widget.visualItemRect(tab.list_widget.item(0))
        # La grilla de Convertir maneja el mouse a mano (reordenar): el doble clic
        # igual tiene que llegar.
        vp = tab.list_widget.viewport()
        QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=rect.center())   # un doble clic real
        QTest.mouseDClick(vp, Qt.MouseButton.LeftButton, pos=rect.center())  # = clic + doble clic
        assert abiertos == [media["video"]]

    def test_avisa_si_no_esta(self, app, tab, monkeypatch):
        from convertir3x4.ui.tabs import tab_convertir
        monkeypatch.setattr(tab_convertir, "open_file", lambda p: False)
        tab._add_paths(["/NAS/no_esta.mp4"], missing_ok=True)
        tab._open_item(tab.list_widget.item(0))
        assert "no se encuentra" in tab.lbl_status.text()

    def test_link_bajando_no_hace_nada(self, app, tab, monkeypatch):
        from PyQt6.QtWidgets import QListWidgetItem
        from convertir3x4.ui.tabs import tab_convertir
        abiertos = []
        monkeypatch.setattr(tab_convertir, "open_file", lambda p: abiertos.append(p) or True)
        tab.list_widget.addItem(QListWidgetItem("https://youtu.be/x"))
        tab._open_item(tab.list_widget.item(0))
        assert abiertos == []

    def test_doble_clic_en_buscar(self, app, monkeypatch, tmp_path, sin_catalogo):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        from convertir3x4.catalog.models import Media
        from convertir3x4.ui.tabs import tab_buscar
        abiertos = []
        monkeypatch.setattr(tab_buscar, "open_file", lambda p: abiertos.append(p) or True)
        f = tmp_path / "culto.mp4"
        f.write_bytes(b"x")
        t = tab_buscar.BuscarTab(srv=sin_catalogo)
        t.resize(900, 600)
        t.show()
        t._populate([Media(path=str(f), filename="culto.mp4", kind="video", id=1)])
        app.processEvents()
        rect = t.grid.visualItemRect(t.grid.item(0))
        QTest.mouseClick(t.grid.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        QTest.mouseDClick(t.grid.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        assert abiertos == [str(f)]
        t.close()
        t.deleteLater()


# ---------------------------------------------------------------- ubicación (vertical y horizontal)
from convertir3x4.convert import layout as L


@pytest.mark.parametrize("w, h", [(896, 1008), (1008, 1008), (1920, 1080), (2560, 640), (720, 1280)])
@pytest.mark.parametrize("a, b", [((1920, 1080), (1080, 1920)), ((800, 600), (800, 600)),
                                  ((300, 2000), (2000, 300))])
def test_dos_por_pantalla_entra_y_respeta_el_orden(w, h, a, b):
    (x1, y1, w1, h1), (x2, y2, w2, h2) = L.dos_por_pantalla(a, b, w, h)
    for x, y, ww, hh in ((x1, y1, w1, h1), (x2, y2, w2, h2)):
        assert x >= 0 and y >= 0 and x + ww <= w and y + hh <= h
        assert ww % 2 == 0 and hh % 2 == 0
    if w > h:      # horizontal: lado a lado, el primero a la izquierda
        assert x1 + w1 <= x2
    else:          # vertical o cuadrada: uno arriba del otro, el primero arriba
        assert y1 + h1 <= y2


def test_sobre_fondo_deja_lugar_para_texto():
    x, y, w, h = L.sobre_fondo((1920, 1080), 896, 1008)
    assert y + h <= 1008 // 2                  # vertical: abajo queda libre
    x, y, w, h = L.sobre_fondo((1080, 1920), 2560, 640)
    assert x + w <= 2560 // 2                  # horizontal: la derecha queda libre


def test_foto_doble_horizontal_sale_lado_a_lado(tmp_path):
    from convertir3x4.convert.images import convert_double
    a, b, out = tmp_path / "a.jpg", tmp_path / "b.jpg", tmp_path / "out.jpg"
    Image.new("RGB", (800, 600), (220, 30, 30)).save(a)     # roja
    Image.new("RGB", (800, 600), (30, 30, 220)).save(b)     # azul
    convert_double([str(a), str(b)], str(out), aspect=(21, 9))
    img = Image.open(out).convert("RGB")
    assert abs(img.width / img.height - 21 / 9) < 0.01
    izq = img.getpixel((img.width // 4, img.height // 2))
    der = img.getpixel((img.width * 3 // 4, img.height // 2))
    assert izq[0] > 150 and izq[2] < 100         # la primera (roja) a la izquierda
    assert der[2] > 150 and der[0] < 100         # la segunda (azul) a la derecha


class TestPanelPorPantalla:
    def _cargar(self, app, tab, media, *paths):
        from convertir3x4.ui.widgets.media_grid import STATE_ROLE
        tab._add_paths(list(paths))
        _wait(app, lambda: all(it.data(STATE_ROLE) is None for it in tab._items()))
        for it in tab._items():
            it.setSelected(True)
        app.processEvents()

    def test_dos_videos_uno_o_dos_por_pantalla(self, app, tab, media, tmp_path):
        v2 = str(tmp_path / "otro.mp4")
        shutil.copy(media["video"], v2)
        self._cargar(app, tab, media, media["video"], v2)
        tab.set_por_pantalla(1)
        assert tab.btn_convert.text() == "Crear 2 videos"
        tab.set_por_pantalla(2)
        assert tab.btn_convert.text() == "Crear 1 video"
        assert "orden de la grilla" in tab.lbl_summary.text()

    def test_vista_previa_sigue_el_orden_de_la_grilla(self, app, tab, media):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        self._cargar(app, tab, media, media["photo"], media["video"])
        tab.set_por_pantalla(2)
        tab.resize(1200, 760)
        app.processEvents()
        antes = tab._paths()
        assert tab.vista_previa._jobs[0].inputs == antes
        # Tocar la vista previa no cambia nada
        centro = tab.vista_previa._celda(0).center().toPoint()
        QTest.mouseClick(tab.vista_previa, Qt.MouseButton.LeftButton, pos=centro)
        app.processEvents()
        assert tab._paths() == antes

    def test_fondo_elegido_con_uno_o_dos_por_pantalla(self, app, tab, media):
        self._cargar(app, tab, media, media["photo"], media["photo2"])
        tab._btn_fondo["elegido"].click()
        assert tab._mode == batch.FONDO and "Falta elegir la imagen de fondo" in tab.lbl_summary.text()
        assert not tab.btn_convert.isEnabled()
        # Con 2 por pantalla el fondo elegido se mantiene (antes se perdía)
        tab._btn_por_pantalla[2].click()
        assert tab._mode == batch.DOBLE and tab._fondo_elegido()
        assert tab.bg_box.isVisible() and not tab.btn_convert.isEnabled()
        tab._bg_path = media["photo"]
        tab._refresh_panel()
        assert tab.btn_convert.isEnabled() and tab.btn_convert.text() == "Crear 1 foto"
        assert tab._batch_options().background == media["photo"]
        # Volver a "De sus colores" no manda el fondo
        tab._btn_fondo["colores"].click()
        assert tab._batch_options().background is None

    def test_iconos_toman_la_forma_de_la_salida(self, app):
        from convertir3x4.ui.style import T
        from convertir3x4.ui.widgets.vista_previa import icono_pantalla

        def caja(px):
            img = px.toImage()
            xs = [x for x in range(img.width()) for y in range(img.height())
                  if img.pixelColor(x, y).alpha() > 200]
            ys = [y for x in range(img.width()) for y in range(img.height())
                  if img.pixelColor(x, y).alpha() > 200]
            return max(xs) - min(xs), max(ys) - min(ys)
        w, h = caja(icono_pantalla(1, (8, 9), 44, T.text))
        assert h > w                                   # vertical
        w, h = caja(icono_pantalla(1, (32, 9), 44, T.text))
        assert w > 2 * h                               # horizontal alargada


class TestDescargaCancelable:
    def _falsa(self, monkeypatch):
        import threading
        from convertir3x4.convert import download as dl
        llamada = threading.Event()

        def falsa(url, out, tools, cb, cancel):
            llamada.set()
            cb(0.1, "Buscando el video…")
            while not cancel():
                import time
                time.sleep(0.02)
            raise clips.Cancelled()
        monkeypatch.setattr(dl, "download", falsa)
        return llamada

    def test_barra_con_cancelar(self, app, tab, monkeypatch):
        self._falsa(monkeypatch)
        tab.txt_link.setText("https://www.youtube.com/watch?v=x")
        tab._start_download()
        _wait(app, lambda: "buscando" in tab.lbl_dl.text())
        assert tab.dl_bar.isVisible() or not tab.isVisible()
        assert tab.lbl_dl.text() == "Descargando 1 video: buscando el video…"
        tab.btn_dl_cancel.click()
        _wait(app, lambda: not tab._downloads)
        assert not tab.dl_bar.isVisible() and tab.list_widget.count() == 0

    def test_quitar_el_que_se_baja_lo_cancela(self, app, tab, monkeypatch):
        self._falsa(monkeypatch)
        tab.txt_link.setText("https://www.youtube.com/watch?v=y")
        tab._start_download()
        tab.list_widget.item(0).setSelected(True)
        tab._remove_selected()
        _wait(app, lambda: not tab._downloads)
        assert tab.list_widget.count() == 0


class TestHerramientasDescarga:
    def test_usa_la_version_descomprimida(self, tmp_path):
        assert download._ytdlp_asset().endswith(".zip")
        d = tmp_path / "yt-dlp-app"
        (d / "_internal").mkdir(parents=True)
        assert download.ytdlp_path(str(tmp_path)) is None
        (d / "yt-dlp_macos").write_text("x")
        assert download.ytdlp_path(str(tmp_path)).endswith("yt-dlp_macos")

    def test_cancelar_corta_al_instante(self):
        import subprocess, sys, threading, time
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        ev = threading.Event()
        threading.Timer(0.2, ev.set).start()
        t = time.time()
        download._cortar_si_cancelan(p, ev.is_set)
        p.wait(5)
        assert time.time() - t < 2 and p.returncode != 0


class TestRevisarHerramientas:
    def test_si_falta_ofrece_prepararla(self, app, tab, monkeypatch):
        from convertir3x4.convert import download as dl
        listo = {"v": False}
        monkeypatch.setattr(dl, "tools_ready", lambda d: listo["v"])

        def preparar(d, cb, cancel):
            cb(0.5)
            listo["v"] = True
        monkeypatch.setattr(dl, "preparar", preparar)
        tab.revisar_herramientas()
        assert tab.tools_box.isVisible() and "120 MB" in tab.lbl_tools.text()
        tab.btn_tools.click()
        _wait(app, lambda: tab._tools_worker is None)
        assert not tab.tools_box.isVisible()

    def test_si_falla_lo_dice_y_deja_reintentar(self, app, tab, monkeypatch):
        from convertir3x4.convert import download as dl
        monkeypatch.setattr(dl, "tools_ready", lambda d: False)

        def preparar(d, cb, cancel):
            raise dl.DownloadError("No se pudo conectar a internet. Revisá la conexión.")
        monkeypatch.setattr(dl, "preparar", preparar)
        tab.revisar_herramientas()
        tab.btn_tools.click()
        _wait(app, lambda: tab._tools_worker is None)
        assert tab.tools_box.isVisible() and "internet" in tab.lbl_tools.text()
        assert tab.btn_tools.text() == "Reintentar" and tab.btn_tools.isEnabled()

    def test_si_esta_actualiza_sin_molestar(self, app, tab, monkeypatch):
        from convertir3x4.convert import download as dl
        llamadas = []
        monkeypatch.setattr(dl, "tools_ready", lambda d: True)

        def preparar(d, cb, cancel):
            llamadas.append(d)
            raise dl.DownloadError("sin internet")
        monkeypatch.setattr(dl, "preparar", preparar)
        tab.revisar_herramientas()
        _wait(app, lambda: tab._tools_worker is None)
        assert llamadas and not tab.tools_box.isVisible()

    @pytest.mark.skipif(sys.platform == "win32", reason="usa un script de shell")
    def test_el_arranque_de_prueba_se_puede_cortar(self, tmp_path):
        import threading, time
        lento = tmp_path / "yt-dlp"
        lento.write_text("#!/bin/sh\nsleep 30\n")
        lento.chmod(0o755)
        ev = threading.Event()
        threading.Timer(0.2, ev.set).start()
        t = time.time()
        with pytest.raises(clips.Cancelled):
            download._calentar(str(lento), ev.is_set)
        assert time.time() - t < 2


class TestActualizarSiFallaLaDescarga:
    def _tocar_boton(self, app, tab, item):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        from convertir3x4.ui.widgets.media_grid import ACTION_ROLE
        lw = tab.list_widget
        r = lw._delegate.action_rect(lw.visualItemRect(item), item.data(ACTION_ROLE))
        QTest.mouseClick(lw.viewport(), Qt.MouseButton.LeftButton, pos=r.center().toPoint())
        app.processEvents()

    def test_cancelar_desde_la_miniatura(self, app, tab, monkeypatch):
        from convertir3x4.convert import download as dl
        from convertir3x4.ui.widgets.media_grid import ACTION_ROLE

        def falsa(url, out, tools, cb, cancel):
            while not cancel():
                time.sleep(0.02)
            raise clips.Cancelled()
        monkeypatch.setattr(dl, "download", falsa)
        tab.txt_link.setText("https://www.youtube.com/watch?v=c")
        tab._start_download()
        item = tab.list_widget.item(0)
        assert item.data(ACTION_ROLE) == "Cancelar"
        self._tocar_boton(app, tab, item)
        _wait(app, lambda: not tab._downloads)
        assert tab.list_widget.count() == 0

    def test_reintentar_actualiza_y_vuelve_a_bajar(self, app, tab, media, monkeypatch, tmp_path):
        from convertir3x4.convert import download as dl
        from convertir3x4.ui.widgets.media_grid import ACTION_ROLE, PATH_ROLE
        estado = {"actualizado": False, "intentos": 0}
        bajado = str(tmp_path / "bajado.mp4")
        shutil.copy(media["video"], bajado)

        def falsa(url, out, tools, cb, cancel):
            estado["intentos"] += 1
            if not estado["actualizado"]:
                raise dl.DownloadError(dl._GENERICO, por_el_programa=True)
            return bajado

        def actualizar(d, cb, cancel):
            estado["actualizado"] = True
        monkeypatch.setattr(dl, "download", falsa)
        monkeypatch.setattr(dl, "hay_version_nueva", lambda d, c=None: True)
        monkeypatch.setattr(dl, "actualizar", actualizar)
        tab.txt_link.setText("https://www.youtube.com/watch?v=z")
        tab._start_download()
        _wait(app, lambda: tab.list_widget.item(0).data(ACTION_ROLE) == "Reintentar")
        assert "versión nueva" in tab.list_widget.item(0).toolTip()
        # También el aviso amarillo de arriba, como cuando falta preparar el programa
        assert tab.tools_box.isVisible() and "versión nueva" in tab.lbl_tools.text()
        assert tab.btn_tools.text() == "Actualizar y reintentar"
        self._tocar_boton(app, tab, tab.list_widget.item(0))
        _wait(app, lambda: any(it.data(PATH_ROLE) == bajado for it in tab._items()))
        assert estado == {"actualizado": True, "intentos": 2}
        assert tab.list_widget.count() == 1 and not tab.tools_box.isVisible()

    def test_el_aviso_de_arriba_actualiza_y_reintenta(self, app, tab, media, monkeypatch, tmp_path):
        from convertir3x4.convert import download as dl
        from convertir3x4.ui.widgets.media_grid import PATH_ROLE
        estado = {"actualizado": False}
        bajado = str(tmp_path / "bajado2.mp4")
        shutil.copy(media["video"], bajado)

        def falsa(url, out, tools, cb, cancel):
            if not estado["actualizado"]:
                raise dl.DownloadError(dl._GENERICO, por_el_programa=True)
            return bajado
        monkeypatch.setattr(dl, "download", falsa)
        monkeypatch.setattr(dl, "hay_version_nueva", lambda d, c=None: True)
        monkeypatch.setattr(dl, "actualizar", lambda d, cb, c: estado.update(actualizado=True))
        tab.txt_link.setText("https://www.youtube.com/watch?v=b")
        tab._start_download()
        _wait(app, lambda: tab.tools_box.isVisible())
        tab.btn_tools.click()
        _wait(app, lambda: any(it.data(PATH_ROLE) == bajado for it in tab._items()))
        assert not tab.tools_box.isVisible()

    def test_si_no_se_puede_actualizar_lo_dice_arriba(self, app, tab, monkeypatch):
        from convertir3x4.convert import download as dl
        from convertir3x4.ui.widgets.media_grid import ACTION_ROLE

        def falsa(url, out, tools, cb, cancel):
            raise dl.DownloadError(dl._GENERICO, por_el_programa=True)

        def actualizar(d, cb, c):
            raise dl.DownloadError("No se pudo conectar a internet. Revisá la conexión.")
        monkeypatch.setattr(dl, "download", falsa)
        monkeypatch.setattr(dl, "hay_version_nueva", lambda d, c=None: True)
        monkeypatch.setattr(dl, "actualizar", actualizar)
        tab.txt_link.setText("https://www.youtube.com/watch?v=n")
        tab._start_download()
        _wait(app, lambda: tab.tools_box.isVisible())
        tab.btn_tools.click()
        _wait(app, lambda: "No se pudo actualizar" in tab.lbl_tools.text())
        assert tab.list_widget.item(0).data(ACTION_ROLE) == "Reintentar"

    def test_reintentar_sin_version_nueva_solo_reintenta(self, app, tab, monkeypatch):
        from convertir3x4.convert import download as dl
        from convertir3x4.ui.widgets.media_grid import ACTION_ROLE
        intentos = []

        def falsa(url, out, tools, cb, cancel):
            intentos.append(url)
            raise dl.DownloadError("No se pudo conectar. Revisá la conexión a internet.")
        monkeypatch.setattr(dl, "download", falsa)
        tab.txt_link.setText("https://www.youtube.com/watch?v=r")
        tab._start_download()
        _wait(app, lambda: tab.list_widget.item(0).data(ACTION_ROLE) == "Reintentar")
        self._tocar_boton(app, tab, tab.list_widget.item(0))
        _wait(app, lambda: len(intentos) == 2 and not tab._downloads)
        assert tab.list_widget.count() == 1

    def test_video_privado_no_ofrece_actualizar(self, app, tab, monkeypatch):
        from convertir3x4.convert import download as dl
        consultas = []

        def falsa(url, out, tools, cb, cancel):
            raise dl.DownloadError("El video es privado o pide iniciar sesión: no se puede bajar.")
        monkeypatch.setattr(dl, "download", falsa)
        monkeypatch.setattr(dl, "hay_version_nueva", lambda d, c=None: consultas.append(1) or True)
        tab.txt_link.setText("https://www.youtube.com/watch?v=p")
        tab._start_download()
        _wait(app, lambda: not tab._downloads)
        assert not consultas and not tab.tools_box.isVisible()

    def test_error_generico_marca_al_programa(self):
        assert download._explain("ERROR: algo raro") == download._GENERICO


class TestDeBuscarAConvertir:
    def _buscar(self, app, media, tmp_path):
        from convertir3x4.catalog.models import Media
        from convertir3x4.ui.tabs import tab_buscar
        from conftest import _SinCatalogo
        t = tab_buscar.BuscarTab(srv=_SinCatalogo())
        t.resize(900, 600)
        t.show()
        perdido = str(tmp_path / "no_esta.jpg")
        t._populate([Media(path=media["photo"], filename="foto.jpg", kind="image", id=1),
                     Media(path=media["video"], filename="video.mp4", kind="video", id=2),
                     Media(path=perdido, filename="no_esta.jpg", kind="image", id=3)])
        app.processEvents()
        return t

    def test_manda_lo_elegido_y_saltea_lo_que_no_esta(self, app, media, tmp_path):
        t = self._buscar(app, media, tmp_path)
        recibidos = []
        t.enviarAConvertir.connect(recibidos.append)
        assert not t.btn_convertir.isEnabled()
        for i in range(3):
            t.grid.item(i).setSelected(True)
        app.processEvents()
        assert t.btn_convertir.isEnabled() and "1 no está disponible" in t.lbl_seleccion.text()
        t.btn_convertir.click()
        assert recibidos == [[media["photo"], media["video"]]]
        t.close()
        t.deleteLater()

    def test_convertir_los_recibe_elegidos(self, app, tab, media):
        from convertir3x4.ui.widgets.media_grid import PATH_ROLE
        tab._add_paths([media["photo2"]])
        tab.recibir([media["photo"], media["photo2"]])
        elegidos = [it.data(PATH_ROLE) for it in tab.list_widget.selectedItems()]
        assert sorted(elegidos) == sorted([media["photo"], media["photo2"]])
        assert tab.list_widget.count() == 2
        assert tab.lbl_status.text() == "Se agregó 1 elemento desde Buscar. 1 ya estaba en la lista."
        assert tab.btn_convert.text() == "Crear 2 fotos"
