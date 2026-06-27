"""Tests del catálogo SQLite y el motor Etapa 0 (Fase 2)."""

import os
import tempfile

import numpy as np
import imageio
import pytest
from PIL import Image

from convertir3x4.catalog.db import connect, SCHEMA_VERSION
from convertir3x4.catalog.repo import Repo
from convertir3x4.catalog.models import Media
from convertir3x4.engine import inventory, probe, dedup
from convertir3x4.engine.pipeline import run_stage0


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def repo(conn):
    return Repo(conn)


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _img(path, w, h, color="red"):
    Image.new("RGB", (w, h), color=color).save(path, "JPEG")


def _mp4(path, frames=8, w=160, h=120):
    wri = imageio.get_writer(path, fps=4, codec="libx264",
                             macro_block_size=1, output_params=["-pix_fmt", "yuv420p"])
    for _ in range(frames):
        wri.append_data((np.random.rand(h, w, 3) * 255).astype("uint8"))
    wri.close()


# --------------------------------------------------------------- DB / schema
class TestSchema:
    def test_schema_version_set(self, conn):
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row["version"] == SCHEMA_VERSION

    def test_fts_table_exists(self, conn):
        # media_fts responde a una query MATCH sin error
        conn.execute("SELECT * FROM media_fts WHERE media_fts MATCH 'x' LIMIT 1").fetchall()


# --------------------------------------------------------------- Repo
class TestRepo:
    def test_upsert_and_get(self, repo):
        mid = repo.upsert_media(Media(path="/a.jpg", filename="a.jpg", kind="image"))
        assert mid > 0
        m = repo.get_media_by_path("/a.jpg")
        assert m.filename == "a.jpg" and m.id == mid

    def test_upsert_is_idempotent_by_path(self, repo):
        repo.upsert_media(Media(path="/a.jpg", filename="a.jpg", kind="image", width=100))
        repo.upsert_media(Media(path="/a.jpg", filename="a.jpg", kind="image", width=200))
        assert repo.count_media() == 1
        assert repo.get_media_by_path("/a.jpg").width == 200

    def test_dedup_by_sha256(self, repo):
        repo.upsert_media(Media(path="/a.jpg", filename="a.jpg", kind="image", sha256="deadbeef"))
        repo.upsert_media(Media(path="/b.jpg", filename="b.jpg", kind="image", sha256="deadbeef"))
        groups = repo.duplicate_groups()
        assert len(groups) == 1 and len(groups[0]) == 2

    def test_tag_normalization(self, repo):
        mid = repo.upsert_media(Media(path="/a.jpg", filename="a.jpg", kind="image"))
        repo.apply_tag(mid, "  Pichanal  ")
        repo.apply_tag(mid, "pichanal")  # mismo tag tras normalizar
        assert repo.tags_for(mid) == ["pichanal"]
        assert repo.all_tag_names() == ["pichanal"]

    def test_search_by_filename(self, repo):
        repo.upsert_media(Media(path="/x/render_patio.jpg", filename="render_patio.jpg", kind="image"))
        repo.upsert_media(Media(path="/x/logo.png", filename="logo.png", kind="image"))
        res = repo.search("patio")
        assert len(res) == 1 and res[0].filename == "render_patio.jpg"

    def test_search_by_tag(self, repo):
        mid = repo.upsert_media(Media(path="/x/foto.jpg", filename="foto.jpg", kind="image"))
        repo.apply_tag(mid, "pichanal")
        res = repo.search("pichanal")
        assert len(res) == 1 and res[0].id == mid

    def test_remove_tag(self, repo):
        mid = repo.upsert_media(Media(path="/x/foto.jpg", filename="foto.jpg", kind="image"))
        repo.apply_tag(mid, "pichanal")
        assert repo.tags_for(mid) == ["pichanal"]
        repo.remove_tag(mid, "  Pichanal ")  # normaliza antes de borrar
        assert repo.tags_for(mid) == []
        assert repo.search("pichanal") == []  # FTS se actualizó

    def test_tag_counts_partial_vs_full(self, repo):
        a = repo.upsert_media(Media(path="/a.jpg", filename="a.jpg", kind="image"))
        b = repo.upsert_media(Media(path="/b.jpg", filename="b.jpg", kind="image"))
        repo.apply_tag(a, "obra")
        repo.apply_tag(b, "obra")
        repo.apply_tag(a, "pichanal")  # solo en a
        counts = dict(repo.tag_counts([a, b]))
        assert counts["obra"] == 2 and counts["pichanal"] == 1

    def test_search_ignores_diacritics(self, repo):
        repo.upsert_media(Media(path="/x/construcción.jpg", filename="construcción.jpg", kind="image"))
        assert len(repo.search("construccion")) == 1

    def test_empty_search_returns_all(self, repo):
        repo.upsert_media(Media(path="/a.jpg", filename="a.jpg", kind="image"))
        repo.upsert_media(Media(path="/b.jpg", filename="b.jpg", kind="image"))
        assert len(repo.search("")) == 2

    def test_search_substring_in_middle_of_filename(self, repo):
        """Pedazo en el MEDIO de un nombre opaco (caso WhatsApp/FB)."""
        repo.upsert_media(Media(
            path="/x/FB_IMG_1761480478237.jpg",
            filename="FB_IMG_1761480478237.jpg", kind="image"))
        repo.upsert_media(Media(path="/x/otra.jpg", filename="otra.jpg", kind="image"))
        res = repo.search("4782")
        assert len(res) == 1 and res[0].filename == "FB_IMG_1761480478237.jpg"

    def test_search_substring_no_duplicate_with_fts(self, repo):
        """Un nombre que matchea por FTS y por substring no se duplica."""
        repo.upsert_media(Media(
            path="/x/patio_4782.jpg", filename="patio_4782.jpg", kind="image"))
        assert len(repo.search("patio")) == 1
        assert len(repo.search("4782")) == 1

    def test_search_underscore_is_literal(self, repo):
        """El '_' del LIKE no debe actuar como comodín."""
        repo.upsert_media(Media(path="/x/ab_cd.jpg", filename="ab_cd.jpg", kind="image"))
        repo.upsert_media(Media(path="/x/abxcd.jpg", filename="abxcd.jpg", kind="image"))
        res = repo.search("b_c")
        names = {m.filename for m in res}
        assert "ab_cd.jpg" in names and "abxcd.jpg" not in names


# --------------------------------------------------------------- inventory
class TestInventory:
    def test_scan_finds_media_only(self, temp_dir):
        _img(os.path.join(temp_dir, "a.jpg"), 100, 100)
        _img(os.path.join(temp_dir, "b.png"), 100, 100)
        with open(os.path.join(temp_dir, "notes.txt"), "w") as f:
            f.write("x")
        found = inventory.scan(temp_dir)
        assert len(found) == 2
        assert all(p.endswith((".jpg", ".png")) for p in found)

    def test_scan_ignores_hidden(self, temp_dir):
        _img(os.path.join(temp_dir, ".hidden.jpg"), 100, 100)
        _img(os.path.join(temp_dir, "visible.jpg"), 100, 100)
        found = inventory.scan(temp_dir)
        assert len(found) == 1 and found[0].endswith("visible.jpg")

    def test_kind_for(self):
        assert inventory.kind_for("x.JPG") == "image"
        assert inventory.kind_for("x.mp4") == "video"
        assert inventory.kind_for("x.txt") is None

    def test_folder_origin(self, temp_dir):
        sub = os.path.join(temp_dir, "Pichanal", "obras")
        os.makedirs(sub)
        p = os.path.join(sub, "a.jpg")
        _img(p, 100, 100)
        assert inventory.folder_origin(p, temp_dir) == os.path.join("Pichanal", "obras")


# --------------------------------------------------------------- probe
class TestProbe:
    def test_probe_image(self, temp_dir):
        p = os.path.join(temp_dir, "a.jpg")
        _img(p, 1600, 900)
        pr = probe.probe(p, "image")
        assert pr.kind == "image" and pr.width == 1600 and pr.height == 900
        assert abs(pr.aspect - 1600 / 900) < 0.01 and pr.quality_flag == "ok"

    def test_probe_low_res(self, temp_dir):
        p = os.path.join(temp_dir, "small.jpg")
        _img(p, 320, 240)
        assert probe.probe(p, "image").quality_flag == "low_res"

    def test_probe_corrupt(self, temp_dir):
        p = os.path.join(temp_dir, "bad.jpg")
        with open(p, "wb") as f:
            f.write(b"not an image")
        assert probe.probe(p, "image").quality_flag == "corrupt"

    def test_probe_video(self, temp_dir):
        p = os.path.join(temp_dir, "v.mp4")
        _mp4(p)
        pr = probe.probe(p, "video")
        assert pr.kind == "video" and pr.width == 160 and pr.height == 120
        assert pr.duration and pr.duration > 0 and pr.codec


# --------------------------------------------------------------- dedup
class TestDedup:
    def test_sha256_matches_for_identical(self, temp_dir):
        a = os.path.join(temp_dir, "a.jpg")
        b = os.path.join(temp_dir, "b.jpg")
        _img(a, 100, 100, "blue")
        import shutil
        shutil.copy(a, b)
        assert dedup.sha256_file(a) == dedup.sha256_file(b)

    def test_sha256_differs(self, temp_dir):
        a = os.path.join(temp_dir, "a.jpg")
        b = os.path.join(temp_dir, "b.jpg")
        _img(a, 100, 100, "blue")
        _img(b, 100, 100, "green")
        assert dedup.sha256_file(a) != dedup.sha256_file(b)

    def test_dhash_near_for_rescaled(self, temp_dir):
        """La misma imagen reescalada da dHash con distancia chica."""
        base = Image.new("RGB", (400, 300))
        for x in range(400):
            for y in range(0, 300, 20):
                base.putpixel((x, y), (255, 255, 255))
        a = os.path.join(temp_dir, "a.jpg")
        b = os.path.join(temp_dir, "b.jpg")
        base.save(a, "JPEG")
        base.resize((200, 150)).save(b, "JPEG")
        ha, hb = dedup.dhash_image(a), dedup.dhash_image(b)
        assert dedup.hamming(ha, hb) <= 5

    def test_dhash_far_for_different(self, temp_dir):
        a = os.path.join(temp_dir, "a.jpg")
        b = os.path.join(temp_dir, "b.jpg")
        _img(a, 200, 200, "black")
        # gradiente bien distinto
        grad = Image.new("L", (200, 200))
        for x in range(200):
            for y in range(200):
                grad.putpixel((x, y), (x + y) % 256)
        grad.convert("RGB").save(b, "JPEG")
        assert dedup.hamming(dedup.dhash_image(a), dedup.dhash_image(b)) >= 10


# --------------------------------------------------------------- pipeline
class TestPipeline:
    def test_run_stage0_end_to_end(self, repo, temp_dir):
        media_dir = os.path.join(temp_dir, "Media")
        os.makedirs(os.path.join(media_dir, "Pichanal"))
        _img(os.path.join(media_dir, "render_patio.jpg"), 1920, 1080)
        _img(os.path.join(media_dir, "Pichanal", "obra1.jpg"), 800, 600)
        _mp4(os.path.join(media_dir, "fondo.mp4"))
        thumb_dir = os.path.join(temp_dir, "thumbs")

        calls = []
        stats = run_stage0(repo, media_dir, thumb_dir,
                           progress_cb=lambda d, t, p: calls.append((d, t)))

        assert stats.scanned == 3 and stats.added == 3
        assert repo.count_media() == 3
        assert calls[-1] == (3, 3)
        # metadata cargada
        m = repo.get_media_by_path(os.path.join(media_dir, "render_patio.jpg"))
        assert m.width == 1920 and m.kind == "image" and m.sha256
        assert m.folder_origin == ""
        # carpeta de origen para el de la subcarpeta
        sub = repo.get_media_by_path(os.path.join(media_dir, "Pichanal", "obra1.jpg"))
        assert sub.folder_origin == "Pichanal"
        # video catalogado con duración
        vid = repo.get_media_by_path(os.path.join(media_dir, "fondo.mp4"))
        assert vid.kind == "video" and vid.duration and vid.duration > 0
        # miniaturas generadas
        assert os.path.isdir(thumb_dir) and len(os.listdir(thumb_dir)) == 3

    def test_run_stage0_is_idempotent(self, repo, temp_dir):
        media_dir = os.path.join(temp_dir, "Media")
        os.makedirs(media_dir)
        _img(os.path.join(media_dir, "a.jpg"), 1000, 800)
        thumb_dir = os.path.join(temp_dir, "thumbs")

        run_stage0(repo, media_dir, thumb_dir)
        stats2 = run_stage0(repo, media_dir, thumb_dir)
        assert stats2.skipped == 1 and stats2.added == 0
        assert repo.count_media() == 1

    def test_run_stage0_detects_duplicate(self, repo, temp_dir):
        import shutil
        media_dir = os.path.join(temp_dir, "Media")
        os.makedirs(media_dir)
        a = os.path.join(media_dir, "a.jpg")
        _img(a, 1000, 800, "purple")
        shutil.copy(a, os.path.join(media_dir, "a_copy.jpg"))
        thumb_dir = os.path.join(temp_dir, "thumbs")

        stats = run_stage0(repo, media_dir, thumb_dir)
        assert stats.added == 2 and stats.duplicates == 1
        assert len(repo.duplicate_groups()) == 1
