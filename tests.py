"""Tests para las funciones de procesamiento de imagen."""

import os
import tempfile

import pytest
from PIL import Image

from Convertir3x4 import convert_centered, ASPECT_W, ASPECT_H, JPEG_QUALITY


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _make_test_image(path: str, width: int, height: int):
    """Crea una imagen de prueba sólida."""
    img = Image.new("RGB", (width, height), color="red")
    img.save(path, "JPEG")


class TestConvertCentered:
    def test_horizontal_image_ratio(self, temp_dir):
        src = os.path.join(temp_dir, "horizontal.jpg")
        out = os.path.join(temp_dir, "out.jpg")
        _make_test_image(src, 1600, 900)

        convert_centered(src, out)

        img = Image.open(out)
        w, h = img.size
        assert abs(w / h - ASPECT_W / ASPECT_H) < 0.01

    def test_vertical_image_ratio(self, temp_dir):
        src = os.path.join(temp_dir, "vertical.jpg")
        out = os.path.join(temp_dir, "out.jpg")
        _make_test_image(src, 900, 1600)

        convert_centered(src, out)

        img = Image.open(out)
        w, h = img.size
        assert abs(w / h - ASPECT_W / ASPECT_H) < 0.01

    def test_square_image_ratio(self, temp_dir):
        src = os.path.join(temp_dir, "square.jpg")
        out = os.path.join(temp_dir, "out.jpg")
        _make_test_image(src, 1000, 1000)

        convert_centered(src, out)

        img = Image.open(out)
        w, h = img.size
        assert abs(w / h - ASPECT_W / ASPECT_H) < 0.01

    def test_already_3x4_ratio(self, temp_dir):
        src = os.path.join(temp_dir, "already34.jpg")
        out = os.path.join(temp_dir, "out.jpg")
        _make_test_image(src, 900, 1200)

        convert_centered(src, out)

        img = Image.open(out)
        w, h = img.size
        assert abs(w / h - ASPECT_W / ASPECT_H) < 0.01

    def test_output_file_created(self, temp_dir):
        src = os.path.join(temp_dir, "input.jpg")
        out = os.path.join(temp_dir, "out.jpg")
        _make_test_image(src, 800, 600)

        convert_centered(src, out)

        assert os.path.exists(out)
        assert os.path.getsize(out) > 0

    def test_output_is_jpeg(self, temp_dir):
        src = os.path.join(temp_dir, "input.jpg")
        out = os.path.join(temp_dir, "out.jpg")
        _make_test_image(src, 800, 600)

        convert_centered(src, out)

        img = Image.open(out)
        assert img.format == "JPEG"

    def test_preserves_width_for_horizontal(self, temp_dir):
        src = os.path.join(temp_dir, "wide.jpg")
        out = os.path.join(temp_dir, "out.jpg")
        _make_test_image(src, 2000, 500)

        convert_centered(src, out)

        img = Image.open(out)
        assert img.size[0] == 2000

    def test_preserves_height_for_vertical(self, temp_dir):
        src = os.path.join(temp_dir, "tall.jpg")
        out = os.path.join(temp_dir, "out.jpg")
        _make_test_image(src, 500, 2000)

        convert_centered(src, out)

        img = Image.open(out)
        assert img.size[1] == 2000

    def test_png_input(self, temp_dir):
        src = os.path.join(temp_dir, "input.png")
        out = os.path.join(temp_dir, "out.jpg")
        img = Image.new("RGB", (800, 600), color="blue")
        img.save(src, "PNG")

        convert_centered(src, out)

        result = Image.open(out)
        assert abs(result.size[0] / result.size[1] - ASPECT_W / ASPECT_H) < 0.01
