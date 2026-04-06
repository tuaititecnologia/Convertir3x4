"""Tests para las funciones de procesamiento de imagen."""

import json
import os
import tempfile

import pytest
from PIL import Image

from Convertir3x4 import convert_centered, convert_double, convert_on_background, App, DEFAULT_ASPECT_W as ASPECT_W, DEFAULT_ASPECT_H as ASPECT_H, JPEG_QUALITY


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


class TestConvertDouble:
    def test_two_images_ratio(self, temp_dir):
        src1 = os.path.join(temp_dir, "img1.jpg")
        src2 = os.path.join(temp_dir, "img2.jpg")
        out = os.path.join(temp_dir, "out.jpg")
        _make_test_image(src1, 1200, 800)
        _make_test_image(src2, 1000, 700)

        convert_double([src1, src2], out)

        img = Image.open(out)
        assert abs(img.size[0] / img.size[1] - ASPECT_W / ASPECT_H) < 0.01

    def test_two_images_created(self, temp_dir):
        src1 = os.path.join(temp_dir, "img1.jpg")
        src2 = os.path.join(temp_dir, "img2.jpg")
        out = os.path.join(temp_dir, "out.jpg")
        _make_test_image(src1, 800, 600)
        _make_test_image(src2, 800, 600)

        convert_double([src1, src2], out)

        assert os.path.exists(out)
        assert os.path.getsize(out) > 0

    def test_single_image_ratio(self, temp_dir):
        """Foto impar sola: debe ser 3:4."""
        src = os.path.join(temp_dir, "solo.jpg")
        out = os.path.join(temp_dir, "out.jpg")
        _make_test_image(src, 1200, 800)

        convert_double([src], out)

        img = Image.open(out)
        assert abs(img.size[0] / img.size[1] - ASPECT_W / ASPECT_H) < 0.01

    def test_different_sizes(self, temp_dir):
        """Dos fotos de tamaños muy distintos."""
        src1 = os.path.join(temp_dir, "big.jpg")
        src2 = os.path.join(temp_dir, "small.jpg")
        out = os.path.join(temp_dir, "out.jpg")
        _make_test_image(src1, 2000, 1500)
        _make_test_image(src2, 400, 300)

        convert_double([src1, src2], out)

        img = Image.open(out)
        assert abs(img.size[0] / img.size[1] - ASPECT_W / ASPECT_H) < 0.01

    def test_output_is_jpeg(self, temp_dir):
        src1 = os.path.join(temp_dir, "img1.jpg")
        src2 = os.path.join(temp_dir, "img2.jpg")
        out = os.path.join(temp_dir, "out.jpg")
        _make_test_image(src1, 800, 600)
        _make_test_image(src2, 800, 600)

        convert_double([src1, src2], out)

        assert Image.open(out).format == "JPEG"


class TestConvertOnBackground:
    def test_ratio(self, temp_dir):
        src = os.path.join(temp_dir, "photo.jpg")
        bg = os.path.join(temp_dir, "bg.jpg")
        out = os.path.join(temp_dir, "out.jpg")
        _make_test_image(src, 1200, 800)
        _make_test_image(bg, 1600, 1200)

        convert_on_background(src, bg, out)

        img = Image.open(out)
        assert abs(img.size[0] / img.size[1] - ASPECT_W / ASPECT_H) < 0.01

    def test_output_created(self, temp_dir):
        src = os.path.join(temp_dir, "photo.jpg")
        bg = os.path.join(temp_dir, "bg.jpg")
        out = os.path.join(temp_dir, "out.jpg")
        _make_test_image(src, 800, 600)
        _make_test_image(bg, 1000, 1000)

        convert_on_background(src, bg, out)

        assert os.path.exists(out)
        assert os.path.getsize(out) > 0

    def test_photo_in_upper_half(self, temp_dir):
        """La foto debe estar en la mitad superior del canvas."""
        src = os.path.join(temp_dir, "photo.jpg")
        bg = os.path.join(temp_dir, "bg.jpg")
        out = os.path.join(temp_dir, "out.jpg")
        img = Image.new("RGB", (800, 600), color="red")
        img.save(src, "JPEG")
        bg_img = Image.new("RGB", (1000, 1000), color="blue")
        bg_img.save(bg, "JPEG")

        convert_on_background(src, bg, out)

        result = Image.open(out)
        w, h = result.size
        center_top = result.getpixel((w // 2, h // 4))
        assert center_top[0] > 200  # rojo (foto)
        center_bottom = result.getpixel((w // 2, int(h * 0.80)))
        assert center_bottom[2] > 200  # azul (fondo)

    def test_output_is_jpeg(self, temp_dir):
        src = os.path.join(temp_dir, "photo.jpg")
        bg = os.path.join(temp_dir, "bg.jpg")
        out = os.path.join(temp_dir, "out.jpg")
        _make_test_image(src, 800, 600)
        _make_test_image(bg, 1000, 1000)

        convert_on_background(src, bg, out)

        assert Image.open(out).format == "JPEG"

    def test_vertical_photo(self, temp_dir):
        src = os.path.join(temp_dir, "vertical.jpg")
        bg = os.path.join(temp_dir, "bg.jpg")
        out = os.path.join(temp_dir, "out.jpg")
        _make_test_image(src, 600, 1200)
        _make_test_image(bg, 1000, 1000)

        convert_on_background(src, bg, out)

        img = Image.open(out)
        assert abs(img.size[0] / img.size[1] - ASPECT_W / ASPECT_H) < 0.01


class TestReadProjectFile:
    def _write(self, temp_dir, content: str) -> str:
        path = os.path.join(temp_dir, "test.c3x4")
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_valid_project(self, temp_dir):
        data = {"version": 1, "images": ["/a.jpg", "/b.jpg"], "background": None}
        path = self._write(temp_dir, json.dumps(data))
        result = App._read_project_file(path)
        assert isinstance(result, dict)
        assert result["images"] == ["/a.jpg", "/b.jpg"]

    def test_valid_with_background(self, temp_dir):
        data = {"version": 1, "images": ["/a.jpg"], "background": "/bg.jpg"}
        path = self._write(temp_dir, json.dumps(data))
        result = App._read_project_file(path)
        assert result["background"] == "/bg.jpg"

    def test_empty_images(self, temp_dir):
        data = {"version": 1, "images": [], "background": None}
        path = self._write(temp_dir, json.dumps(data))
        result = App._read_project_file(path)
        assert isinstance(result, dict)
        assert result["images"] == []

    def test_broken_json(self, temp_dir):
        path = self._write(temp_dir, "{not valid json!!!")
        result = App._read_project_file(path)
        assert isinstance(result, str)

    def test_empty_file(self, temp_dir):
        path = self._write(temp_dir, "")
        result = App._read_project_file(path)
        assert isinstance(result, str)

    def test_json_array_instead_of_dict(self, temp_dir):
        path = self._write(temp_dir, '["/a.jpg", "/b.jpg"]')
        result = App._read_project_file(path)
        assert isinstance(result, str)

    def test_json_string_instead_of_dict(self, temp_dir):
        path = self._write(temp_dir, '"hello"')
        result = App._read_project_file(path)
        assert isinstance(result, str)

    def test_images_not_a_list(self, temp_dir):
        data = {"version": 1, "images": "not a list"}
        path = self._write(temp_dir, json.dumps(data))
        result = App._read_project_file(path)
        assert isinstance(result, str)

    def test_missing_images_key(self, temp_dir):
        data = {"version": 1}
        path = self._write(temp_dir, json.dumps(data))
        result = App._read_project_file(path)
        assert isinstance(result, dict)
        assert result["images"] == []

    def test_non_string_entries_filtered(self, temp_dir):
        data = {"version": 1, "images": ["/a.jpg", 123, None, "/b.jpg"]}
        path = self._write(temp_dir, json.dumps(data))
        result = App._read_project_file(path)
        assert isinstance(result, dict)
        assert result["images"] == ["/a.jpg", "/b.jpg"]

    def test_nonexistent_file(self, temp_dir):
        path = os.path.join(temp_dir, "no_existe.c3x4")
        result = App._read_project_file(path)
        assert isinstance(result, str)
