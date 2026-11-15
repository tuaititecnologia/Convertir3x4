"""Tests de Convertir con fotos y videos: armado de trabajos, descargas y la pestaña.

Los videos de prueba los genera el ffmpeg de la app. Nada baja de internet: la
descarga real se probó a mano (ver docs/convertir-fotos-y-videos.md).
"""

import os

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

    def test_photos_as_video_and_aspect(self):
        jobs = batch.plan(["/a/1.jpg"], batch.CENTRADA,
                          batch.Options(aspect=(3, 4), photos_as_video=True))
        assert jobs[0].out_name == "0001_1_3x4.mp4" and jobs[0].is_video

    def test_describe_in_plain_words(self):
        assert batch.describe(["/a/1.jpg"], batch.CENTRADA, batch.Options()) == "Sale 1 foto."
        assert batch.describe(["/a/1.jpg", "/a/2.mp4"], batch.CENTRADA,
                              batch.Options()) == "Salen 1 foto y 1 video."
        text = batch.describe(["/a/1.jpg", "/a/2.mp4", "/a/3.jpg"], batch.DOBLE, batch.Options())
        assert text == "Salen 1 foto y 1 video (la última queda sola)."
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
        assert "no está soportado" in download._explain("ERROR: Unsupported URL: x")
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
        assert tab.btn_convert.text() == "Convertir 1"
        assert "1 no se pueden convertir" in tab.lbl_summary.text()

    def test_convert_batch_end_to_end(self, app, tab, media, tmp_path):
        from convertir3x4.ui.widgets.media_grid import STATE_ROLE
        tab._add_paths([media["photo"], media["video"]])
        items = tab._items()
        _wait(app, lambda: items[1].data(STATE_ROLE) is None)
        for it in items:
            it.setSelected(True)
        tab._mode_buttons[batch.DOBLE].click()
        tab._refresh_panel()
        assert tab.lbl_summary.text() == "Sale 1 video."
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
