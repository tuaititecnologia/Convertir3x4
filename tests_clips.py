"""Tests de convert/clips.py: videos a la proporción del LED con el audio nivelado.

Los videos de prueba los genera el mismo ffmpeg que trae la app (lavfi), así que
no hace falta ningún archivo en el repo.
"""

import os
import shutil
import subprocess
import tempfile

import pytest
from PIL import Image

from convertir3x4.convert import clips
from convertir3x4.convert.clips import (
    AudioReport, Cancelled, ClipError, canvas_size, clip_info, convert_centered_clip,
    convert_clip_on_background, convert_double_clip, double_layout, make_storyboard,
    measure_final, parse_info,
)


def _ff(*args):
    r = subprocess.run([clips.ffmpeg_exe(), "-hide_banner", "-v", "error", "-y", *args],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def _video(path, w=640, h=360, dur=5, audio="sine=frequency=440:sample_rate=48000",
           volume_db=-30, extra=()):
    args = ["-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=25:duration={dur}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"{audio}:duration={dur}",
                 "-af", f"volume={volume_db}dB", "-c:a", "aac"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             *extra, "-shortest", path]
    _ff(*args)
    return path


@pytest.fixture(scope="module")
def tmp():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="module")
def wide(tmp):
    return _video(os.path.join(tmp, "ancho.mp4"))


@pytest.fixture(scope="module")
def vertical_mute(tmp):
    return _video(os.path.join(tmp, "vertical.mp4"), 360, 640, dur=3, audio=None)


@pytest.fixture(scope="module")
def photo(tmp):
    p = os.path.join(tmp, "foto.jpg")
    Image.new("RGB", (800, 600), (200, 40, 40)).save(p)
    return p


def _assert_audio_ok(report: AudioReport):
    assert report.before is not None
    assert abs(report.after - clips.LUFS) <= clips.LUFS_TOLERANCE, report
    assert report.peak <= clips.TP, report
    assert report.warnings == []


# ---------------------------------------------------------------- datos

class TestCanvasSize:
    def test_8x9_is_twice_the_led(self):
        assert canvas_size((8, 9)) == (896, 1008)

    def test_default_is_8x9(self):
        assert canvas_size() == (896, 1008)

    @pytest.mark.parametrize("aspect", [(3, 4), (9, 16), (1, 1), (4, 5)])
    def test_even_and_close_to_ratio(self, aspect):
        w, h = canvas_size(aspect)
        assert w % 2 == 0 and h % 2 == 0
        assert abs(w / h - aspect[0] / aspect[1]) < 0.01


SAMPLE_ROTATED = """\
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'IMG_1234.MOV':
  Duration: 00:01:02.50, start: 0.000000, bitrate: 8000 kb/s
  Stream #0:0[0x1](und): Video: hevc (Main) (hvc1 / 0x31637668), yuv420p(tv, bt709), 1920x1080, 7900 kb/s, 29.97 fps, 29.97 tbr, 600 tbn (default)
      Side data:
        displaymatrix: rotation of -90.00 degrees
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, stereo, fltp, 96 kb/s (default)
  Stream #0:2[0x3](und): Video: mjpeg (Baseline), yuvj420p, 320x240, 90k tbr (attached pic)
"""


class TestParseInfo:
    def test_phone_rotation_swaps_size(self):
        info = parse_info(SAMPLE_ROTATED)
        assert (info.width, info.height) == (1080, 1920)
        assert info.duration == pytest.approx(62.5)
        assert info.fps == pytest.approx(29.97)
        assert info.has_audio

    def test_codec_tag_is_not_taken_as_size(self):
        # "0x31637668" no es un tamaño
        assert parse_info(SAMPLE_ROTATED).width == 1080

    def test_no_video_stream(self):
        with pytest.raises(ClipError):
            parse_info("  Duration: 00:00:03.00\n  Stream #0:0: Audio: mp3, 44100 Hz\n")

    def test_real_file(self, wide, vertical_mute):
        a = clip_info(wide)
        assert (a.width, a.height, a.has_audio) == (640, 360, True)
        assert a.duration == pytest.approx(5, abs=0.2)
        b = clip_info(vertical_mute)
        assert (b.width, b.height, b.has_audio) == (360, 640, False)

    def test_unreadable_file(self, tmp):
        p = os.path.join(tmp, "roto.mp4")
        with open(p, "wb") as f:
            f.write(b"esto no es un video")
        with pytest.raises(ClipError):
            clip_info(p)


class TestDoubleLayout:
    def test_first_goes_on_top_in_grid_order(self):
        # Regla del 15/11/2026: el orden de la grilla manda (la vista previa lo
        # muestra y se puede invertir); antes iba arriba el más ancho.
        layout = double_layout((400, 800), (1600, 900), 896, 1008)
        assert [idx for idx, *_ in layout] == [0, 1]
        (_, _, y1, _, _), (_, _, y2, _, _) = layout
        assert y1 < y2

    def test_fits_canvas_even_sizes(self):
        (_, x1, y1, w1, h1), (_, x2, y2, w2, h2) = double_layout((1920, 1080), (1080, 1920),
                                                                 896, 1008)
        assert y1 >= 0 and y1 + h1 <= y2 and y2 + h2 <= 1008
        assert x1 >= 0 and x1 + w1 <= 896 and x2 >= 0 and x2 + w2 <= 896
        assert all(v % 2 == 0 for v in (w1, h1, w2, h2))


# ---------------------------------------------------------------- conversiones

class TestCentered:
    def test_size_duration_and_audio(self, tmp, wide):
        out = os.path.join(tmp, "centrada.mp4")
        steps = []
        report = convert_centered_clip(wide, out, progress_cb=steps.append)
        info = clip_info(out)
        assert (info.width, info.height) == (896, 1008)
        assert info.duration == pytest.approx(5, abs=0.2)
        _assert_audio_ok(report)
        assert report.before < -35            # la fuente estaba a -30 dB de volumen
        assert steps and steps[-1] == 1.0
        assert steps == sorted(steps)

    def test_no_audio(self, tmp, vertical_mute):
        out = os.path.join(tmp, "muda.mp4")
        report = convert_centered_clip(vertical_mute, out)
        assert report.before is None and report.warnings == []
        assert not clip_info(out).has_audio

    def test_other_aspect(self, tmp, vertical_mute):
        out = os.path.join(tmp, "9x16.mp4")
        convert_centered_clip(vertical_mute, out, aspect=(9, 16))
        info = clip_info(out)
        assert (info.width, info.height) == canvas_size((9, 16))

    def test_cancel_leaves_no_files(self, tmp, wide):
        out = os.path.join(tmp, "cancelada.mp4")
        with pytest.raises(Cancelled):
            convert_centered_clip(wide, out, cancel=lambda: True)
        leftovers = [f for f in os.listdir(tmp) if f.startswith("cancelada")]
        assert leftovers == []


class TestOnBackground:
    def test_video_over_background(self, tmp, wide, photo):
        out = os.path.join(tmp, "fondo.mp4")
        report = convert_clip_on_background(wide, photo, out)
        info = clip_info(out)
        assert (info.width, info.height) == (896, 1008)
        assert info.duration == pytest.approx(5, abs=0.3)
        _assert_audio_ok(report)


class TestDouble:
    def test_photo_and_video_gives_video(self, tmp, wide, photo):
        out = os.path.join(tmp, "doble_mixto.mp4")
        report = convert_double_clip([photo, wide], out)
        info = clip_info(out)
        assert (info.width, info.height) == (896, 1008)
        assert info.duration == pytest.approx(5, abs=0.3)
        _assert_audio_ok(report)

    def test_two_videos_last_as_long_as_the_longest(self, tmp, wide, vertical_mute):
        out = os.path.join(tmp, "doble_videos.mp4")
        report = convert_double_clip([vertical_mute, wide], out)
        assert clip_info(out).duration == pytest.approx(5, abs=0.3)
        _assert_audio_ok(report)              # audio del único que tiene

    def test_two_photos_is_not_a_clip(self, tmp, photo):
        with pytest.raises(ValueError):
            convert_double_clip([photo, photo], os.path.join(tmp, "x.mp4"))


SPIKES = "aevalsrc=0.02*sin(2*PI*440*t)+0.97*lt(mod(t\\,1)\\,0.003):sample_rate=48000"


class TestHorizontal:
    def test_photo_and_video_side_by_side_in_a_wide_screen(self, tmp, wide, photo):
        out = os.path.join(tmp, "doble_21x9.mp4")
        convert_double_clip([photo, wide], out, aspect=(21, 9))
        info = clip_info(out)
        assert abs(info.width / info.height - 21 / 9) < 0.02
        assert info.width % 2 == 0 and info.height % 2 == 0


class TestPeakFix:
    def test_quiet_audio_with_spikes_stays_under_peak(self, tmp):
        # Audio bajo con golpes casi a 0 dBFS, de punta a punta.
        src = _video(os.path.join(tmp, "picos.mp4"), dur=8, audio=SPIKES, volume_db=0)
        out = os.path.join(tmp, "picos_8x9.mp4")
        report = convert_centered_clip(src, out)
        assert report.peak <= clips.TP, report

    def test_peak_over_limit_triggers_audio_redo(self, tmp):
        # Simula lo que pasó con HISTORIA 37: la primera pasada deja el pico arriba
        # de -1. Acá el "resultado" es el mismo archivo con golpes a -0,3 dBFS.
        src = _video(os.path.join(tmp, "picos2.mp4"), dur=8, audio=SPIKES, volume_db=0)
        out = os.path.join(tmp, "picos2_resultado.mp4")
        shutil.copy(src, out)
        assert measure_final(out)[1] > clips.TP
        before = float(clips.measure_loudness(src)["input_i"])

        report = clips._check_audio(out, AudioReport(before=before), src, 0)

        assert report.fixed
        assert report.peak <= clips.TP, report
        assert clip_info(out).width == 640        # el video se copió, no se tocó


class TestStoryboard:
    def test_grid_of_frames(self, tmp, wide):
        out = os.path.join(tmp, "storyboard.jpg")
        make_storyboard(wide, out)
        with Image.open(out) as im:
            assert im.width == clips.STORYBOARD_WIDTH * clips.STORYBOARD_COLS
            rows = clips.STORYBOARD_FRAMES // clips.STORYBOARD_COLS
            assert im.height == 136 * rows   # 640x360 -> 240x135, redondeado a par


class TestAvisosDeAudio:
    """Qué le dice la grilla al operador cuando el volumen no quedó en el objetivo."""

    def _revisar(self, monkeypatch, despues, pico=-3.0):
        monkeypatch.setattr(clips, "measure_final", lambda p: (despues, pico))
        return clips._check_audio("x.mp4", AudioReport(before=-28.8), "src.mp4", 0)

    def test_bajo_dice_que_quedo_bajo_y_que_hacer(self, monkeypatch):
        r = self._revisar(monkeypatch, -20.9)
        assert len(r.warnings) == 1
        assert "más bajo" in r.warnings[0] and "consola" in r.warnings[0]

    def test_alto_dice_que_quedo_alto(self, monkeypatch):
        r = self._revisar(monkeypatch, -13.5)
        assert len(r.warnings) == 1 and "más alto" in r.warnings[0]

    def test_dentro_del_margen_no_avisa(self, monkeypatch):
        assert self._revisar(monkeypatch, -16.4).warnings == []
