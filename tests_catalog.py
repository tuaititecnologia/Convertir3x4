"""Tests del catálogo (PostgreSQL) y el catalogado (Etapa 0).

Las pruebas que usan `repo`/`pg_cfg` necesitan el PostgreSQL de desarrollo
(ver conftest.py); sin él se saltean.
"""

import os
import shutil
import tempfile
import threading
import time

import numpy as np
import imageio
import psycopg
import pytest
from PIL import Image

from convertir3x4.catalog import db as catdb, rutas
from convertir3x4.catalog.repo import Repo
from convertir3x4.catalog.models import Media
from convertir3x4.engine import inventory, probe, dedup
from convertir3x4.engine.pipeline import catalogar, run_stage0, CatalogoOcupado


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


# --------------------------------------------------------------- rutas
class TestRutas:
    def test_relativa_y_completa_en_windows(self):
        raiz = r"\\truenas\multimedia"
        rel = rutas.a_relativa(r"\\TRUENAS\Multimedia\Publicar\2026\a.jpg", raiz)
        assert rel == "Publicar/2026/a.jpg"
        assert rutas.a_completa(rel, raiz) == r"\\truenas\multimedia\Publicar\2026\a.jpg"
        assert rutas.a_relativa(r"\\otro\recurso\a.jpg", raiz) is None
        assert rutas.a_relativa(r"\\truenas\multimedia2\a.jpg", raiz) is None

    def test_relativa_en_mac(self):
        assert rutas.a_relativa("/Volumes/multimedia/Entrada/x.mp4",
                                "/Volumes/multimedia") == "Entrada/x.mp4"
        assert rutas.a_relativa("/Volumes/otro/x.mp4", "/Volumes/multimedia") is None
        assert rutas.a_completa("Entrada/x.mp4", "/Volumes/multimedia/") == \
            "/Volumes/multimedia/Entrada/x.mp4"


# --------------------------------------------------------------- configuración
class TestConfig:
    def test_lee_catalogo_ini(self, tmp_path):
        ini = tmp_path / "catalogo.ini"
        ini.write_text(
            "[catalogo]\nservidor = truenas\nclave = s3creta\n"
            "raiz = \\\\truenas\\multimedia\n"
            "carpetas =\n    Entrada\n    Publicar\\2026\n    Entrada\n", encoding="utf-8")
        cfg = catdb.ConfigCatalogo.desde_archivo(str(ini))
        assert cfg.servidor == "truenas" and cfg.clave == "s3creta"
        assert cfg.raiz == r"\\truenas\multimedia"
        assert cfg.carpetas == ("Entrada", "Publicar/2026")

    def test_sin_archivo_es_none(self, tmp_path):
        assert catdb.ConfigCatalogo.desde_archivo(str(tmp_path / "no.ini")) is None

    @pytest.mark.parametrize("carpetas", [("../afuera",), ("C:/x",), ("Publicar", "Publicar/2026")])
    def test_rechaza_carpetas_fuera_o_encimadas(self, carpetas):
        with pytest.raises(catdb.ConfiguracionInvalida):
            catdb.ConfigCatalogo(servidor="x", carpetas=carpetas)

    def test_falta_servidor(self, tmp_path):
        ini = tmp_path / "catalogo.ini"
        ini.write_text("[catalogo]\nclave = x\n", encoding="utf-8")
        with pytest.raises(catdb.ConfiguracionInvalida):
            catdb.ConfigCatalogo.desde_archivo(str(ini))


# --------------------------------------------------------------- catalogado
@pytest.fixture
def nas(pg_conn, temp_dir):
    """Repo cuya raíz es una carpeta temporal que hace de \\\\truenas\\multimedia."""
    return Repo(pg_conn, temp_dir)


class TestPipeline:
    def test_run_stage0_end_to_end(self, nas, temp_dir):
        media_dir = os.path.join(temp_dir, "Media")
        os.makedirs(os.path.join(media_dir, "Pichanal"))
        _img(os.path.join(media_dir, "render_patio.jpg"), 1920, 1080)
        _img(os.path.join(media_dir, "Pichanal", "obra1.jpg"), 800, 600)
        _mp4(os.path.join(media_dir, "fondo.mp4"))

        calls = []
        stats = run_stage0(nas, media_dir, progress_cb=lambda d, t, p: calls.append((d, t)))

        assert stats.scanned == 3 and stats.added == 3
        assert nas.count_media() == 3
        assert calls[-1] == (3, 3)
        m = nas.get_media_by_path(os.path.join(media_dir, "render_patio.jpg"))
        assert m.width == 1920 and m.kind == "image" and m.rel_path == "Media/render_patio.jpg"
        assert m.sha256 is None              # ningún otro pesa lo mismo: no se lee entero
        assert m.folder_origin == "Media"    # relativa a la raíz: la carpeta del evento
        sub = nas.get_media_by_path(os.path.join(media_dir, "Pichanal", "obra1.jpg"))
        assert sub.folder_origin == "Media/Pichanal"
        vid = nas.get_media_by_path(os.path.join(media_dir, "fondo.mp4"))
        assert vid.kind == "video" and vid.duration and vid.duration > 0
        # miniaturas en la base, JPEG
        minis = nas.miniaturas([m.id, sub.id, vid.id])
        assert len(minis) == 3 and all(j[:2] == b"\xff\xd8" for _, j in minis.values())

    def test_run_stage0_is_idempotent(self, nas, temp_dir):
        _img(os.path.join(temp_dir, "a.jpg"), 1000, 800)
        run_stage0(nas, temp_dir)
        stats2 = run_stage0(nas, temp_dir)
        assert stats2.skipped == 1 and stats2.added == 0
        assert nas.count_media() == 1

    def test_detecta_duplicado_y_hashea_solo_los_del_mismo_tamano(self, nas, temp_dir):
        a = os.path.join(temp_dir, "a.jpg")
        _img(a, 1000, 800, "purple")
        shutil.copy(a, os.path.join(temp_dir, "a_copy.jpg"))
        _img(os.path.join(temp_dir, "otra.jpg"), 640, 480, "green")
        stats = run_stage0(nas, temp_dir)
        assert stats.added == 3 and stats.duplicates == 1
        assert len(nas.duplicate_groups()) == 1
        assert nas.get_media_by_path(os.path.join(temp_dir, "otra.jpg")).sha256 is None

    def test_fuera_de_la_raiz(self, nas):
        with pytest.raises(ValueError):
            run_stage0(nas, "/otro/lado")

    def test_archivo_movido_conserva_tags(self, nas, temp_dir):
        os.makedirs(os.path.join(temp_dir, "Entrada"))
        os.makedirs(os.path.join(temp_dir, "Publicar", "2026"))
        viejo = os.path.join(temp_dir, "Entrada", "bautismo.jpg")
        _img(viejo, 1200, 900, "blue")
        catalogar(nas, ["Publicar", "Entrada"])
        mid = nas.get_media_by_path(viejo).id
        nas.apply_tag(mid, "bautismo")

        nuevo = os.path.join(temp_dir, "Publicar", "2026", "bautismo.jpg")
        shutil.move(viejo, nuevo)          # conserva fecha y tamaño
        # Publicar se recorre ANTES que Entrada y aun así lo reconoce.
        stats = catalogar(nas, ["Publicar", "Entrada"])
        assert stats.moved == 1 and stats.added == 0
        m = nas.get_media_by_path(nuevo)
        assert m.id == mid and not m.missing and m.folder_origin == "Publicar/2026"
        assert nas.tags_for(mid) == ["bautismo"]
        assert nas.count_media() == 1

    def test_faltante_no_se_borra_y_vuelve(self, nas, temp_dir):
        p = os.path.join(temp_dir, "Entrada", "a.jpg")
        os.makedirs(os.path.dirname(p))
        _img(p, 800, 600)
        catalogar(nas, ["Entrada"])
        guardado = os.path.join(temp_dir, "a.jpg.bak")
        shutil.move(p, guardado)
        st = catalogar(nas, ["Entrada"])
        assert st.missing == 1
        (m,) = nas.faltantes()
        assert m.filename == "a.jpg" and nas.count_media() == 1
        assert nas.search("a")[-1].missing          # sigue apareciendo, al final
        shutil.move(guardado, p)
        catalogar(nas, ["Entrada"])
        assert nas.faltantes() == []

    def test_faltantes_solo_en_las_carpetas_recorridas(self, nas, temp_dir):
        for c in ("Entrada", "Historial"):
            os.makedirs(os.path.join(temp_dir, c))
            _img(os.path.join(temp_dir, c, "x.jpg"), 800, 600)
        catalogar(nas, ["Entrada", "Historial"])
        shutil.rmtree(os.path.join(temp_dir, "Historial"))
        os.makedirs(os.path.join(temp_dir, "Historial"))
        st = catalogar(nas, ["Entrada"])            # Historial no se recorre
        assert st.missing == 0 and nas.faltantes() == []

    def test_carpeta_inexistente_se_saltea(self, nas, temp_dir):
        os.makedirs(os.path.join(temp_dir, "Entrada"))
        _img(os.path.join(temp_dir, "Entrada", "x.jpg"), 800, 600)
        st = catalogar(nas, ["NoEsta", "Entrada"])
        assert st.inexistentes == ["NoEsta"] and st.added == 1

    def test_carpeta_ilegible_no_marca_faltantes(self, nas, temp_dir, monkeypatch):
        os.makedirs(os.path.join(temp_dir, "Entrada"))
        _img(os.path.join(temp_dir, "Entrada", "x.jpg"), 800, 600)
        catalogar(nas, ["Entrada"])

        def scan_cortado(root, errores=None):
            errores.append(OSError("el NAS no responde"))
            return []
        monkeypatch.setattr(inventory, "scan", scan_cortado)
        st = catalogar(nas, ["Entrada"])
        assert st.incompletas == ["Entrada"] and nas.faltantes() == []

    def test_dos_pcs_no_catalogan_la_misma_carpeta(self, pg_cfg, temp_dir):
        os.makedirs(os.path.join(temp_dir, "Entrada"))
        os.makedirs(os.path.join(temp_dir, "Publicar"))
        with catdb.conectar(pg_cfg) as otra_pc, catdb.conectar(pg_cfg) as esta:
            assert Repo(otra_pc, temp_dir).tomar_carpeta("Entrada")
            repo = Repo(esta, temp_dir)
            st = catalogar(repo, ["Entrada", "Publicar"])
            assert st.ocupadas == ["Entrada"]       # Publicar sí se catalogó
            with pytest.raises(CatalogoOcupado):
                run_stage0(repo, os.path.join(temp_dir, "Entrada"))
            # A mano, una subcarpeta toma el bloqueo de la carpeta de la lista.
            st = catalogar(repo, ["Entrada/2026"], bloqueo="Entrada")
            assert st.ocupadas == ["Entrada/2026"]


# ---------------------------------------------------------------- limpiar el catálogo
def _media(path, **kw):
    return Media(path=path, filename=os.path.basename(path), kind="image", **kw)


def test_delete_media_borra_tags_busqueda_y_miniatura(repo, temp_dir):
    a = repo.upsert_media(_media(os.path.join(temp_dir, "a.jpg")), miniatura=b"\xff\xd8a")
    b = repo.upsert_media(_media(os.path.join(temp_dir, "b.jpg")))
    repo.apply_tag(a, "bautismo")
    repo.delete_media([a])
    assert [m.id for m in repo.all_media()] == [b]
    assert repo.search("bautismo") == []
    assert repo.conn.execute("SELECT COUNT(*) AS n FROM media_tag").fetchone()["n"] == 0
    assert repo.miniaturas([a]) == {}


def test_miniatura_tiene_sello_por_contenido(repo):
    mid = repo.upsert_media(_media("/x/a.jpg"), miniatura=b"uno")
    s1 = repo.sellos_miniatura([mid])[mid]
    repo.upsert_media(_media("/x/a.jpg"), miniatura=b"dos")
    s2 = repo.sellos_miniatura([mid])[mid]
    assert s1 != s2 and repo.miniaturas([mid])[mid] == (s2, b"dos")


def test_cache_local_de_miniaturas(repo, tmp_path):
    from convertir3x4.catalog.cache_miniaturas import CacheMiniaturas
    cache = CacheMiniaturas(str(tmp_path / "cache"))
    a = repo.upsert_media(_media("/x/a.jpg"), miniatura=b"uno")
    b = repo.upsert_media(_media("/x/b.jpg"))                    # sin miniatura
    listas, faltan = cache.locales(repo, [a, b])
    assert listas == {} and faltan == [a]
    rutas_ = cache.asegurar(repo, [a, b])
    assert open(rutas_[a], "rb").read() == b"uno"
    assert cache.locales(repo, [a])[1] == []                     # ya no hay que bajarla
    repo.upsert_media(_media("/x/a.jpg"), miniatura=b"dos")      # cambió: se baja de nuevo
    assert cache.locales(repo, [a])[1] == [a]
    nueva = cache.asegurar(repo, [a])[a]
    assert open(nueva, "rb").read() == b"dos"
    assert len(list((tmp_path / "cache").glob(f"{a}-*.jpg"))) == 1   # la vieja se borró
    cache.olvidar([a])
    assert list((tmp_path / "cache").glob("*.jpg")) == []


# --------------------------------------------------------------- PostgreSQL
class TestConexionPostgres:
    def test_crea_el_esquema(self, pg_cfg):
        with catdb.conectar(pg_cfg) as c:
            assert catdb.version_esquema(c) == catdb.VERSION_ESQUEMA
            tablas = {r["tablename"] for r in c.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
            assert {"media", "thumbnail", "ai_result", "tag", "media_tag",
                    "tag_rejection", "person", "person_reference", "esquema"} <= tablas

    def test_conectar_dos_veces_no_reaplica(self, pg_cfg):
        catdb.conectar(pg_cfg).close()
        with catdb.conectar(pg_cfg) as c:
            n = c.execute("SELECT COUNT(*) AS n FROM esquema").fetchone()["n"]
        assert n == len(catdb.MIGRACIONES)

    def test_dos_pcs_arrancan_a_la_vez(self, pg_cfg):
        errores = []

        def abrir():
            try:
                catdb.conectar(pg_cfg).close()
            except Exception as e:  # noqa: BLE001
                errores.append(e)

        hilos = [threading.Thread(target=abrir) for _ in range(4)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        assert errores == []
        with catdb.conectar(pg_cfg) as c:
            assert catdb.version_esquema(c) == catdb.VERSION_ESQUEMA

    def test_app_vieja_no_toca_un_catalogo_nuevo(self, pg_cfg):
        with catdb.conectar(pg_cfg) as c:
            c.execute("INSERT INTO esquema (version) VALUES (%s)",
                      (catdb.VERSION_ESQUEMA + 1,))
            c.commit()
        with pytest.raises(catdb.CatalogoIncompatible):
            catdb.conectar(pg_cfg)

    def test_servidor_apagado_avisa_rapido(self):
        # Puerto cerrado: rechaza al instante.
        cfg = catdb.ConfigCatalogo(servidor="127.0.0.1", puerto=1, clave="x")
        t = time.monotonic()
        with pytest.raises(catdb.CatalogoNoDisponible) as e:
            catdb.conectar(cfg)
        assert time.monotonic() - t < 3
        assert "NAS" in str(e.value)

    def test_servidor_que_no_contesta_respeta_el_tiempo(self):
        # Dirección que no responde (ni rechaza): corta por timeout.
        cfg = catdb.ConfigCatalogo(servidor="10.255.255.1", clave="x")
        t = time.monotonic()
        with pytest.raises(catdb.CatalogoNoDisponible):
            catdb.conectar(cfg, timeout=2)
        assert time.monotonic() - t < 5

    def test_busqueda_por_palabras_sin_acentos(self, pg_cfg):
        with catdb.conectar(pg_cfg) as c:
            r = c.execute(
                "SELECT to_tsvector('catalogo', palabras(%s)) @@ "
                "to_tsquery('catalogo', 'cordoba:* & bautis:*') AS ok",
                ("FB_IMG_Córdoba-Bautismo.jpg",)).fetchone()
            assert r["ok"]

    def test_fragmento_usa_el_indice_trigram(self, pg_cfg):
        with catdb.conectar(pg_cfg) as c:
            c.execute(
                "INSERT INTO media (rel_path, filename, folder_origin, kind) "
                "SELECT 'x/' || i || '.jpg', 'FB_IMG_' || i || '.jpg', 'Culto', 'image' "
                "FROM generate_series(1, 2000) i")
            c.execute("ANALYZE media")
            c.execute("SET enable_seqscan = off")
            plan = " ".join(r["QUERY PLAN"] for r in c.execute(
                "EXPLAIN SELECT id FROM media "
                "WHERE texto_nombre(filename, folder_origin) LIKE %s", ("%1782%",)))
            assert "media_trgm" in plan
            n = c.execute(
                "SELECT COUNT(*) AS n FROM media "
                "WHERE texto_nombre(filename, folder_origin) LIKE %s", ("%1782%",)
            ).fetchone()["n"]
            assert n == 1


class TestVideosDeCamara:
    """`.mts`/`.m2t`: imageio no los abre por la extensión; ffmpeg sí (21/11/2026)."""

    @pytest.mark.parametrize("ext", [".mts", ".m2t", ".m2ts", ".mpg"])
    def test_se_catalogan(self, temp_dir, ext):
        from convertir3x4.catalog import thumbs
        src = os.path.join(temp_dir, "c.mp4")
        _mp4(src)
        dst = os.path.join(temp_dir, "grabacion" + ext)
        os.rename(src, dst)
        assert inventory.kind_for(dst) == "video"
        pr = probe.probe_video(dst)
        assert pr.quality_flag != "corrupt" and pr.width == 160 and pr.duration > 0
        assert thumbs.make_thumb(dst, "video")[:2] == b"\xff\xd8"


class TestAvisosDeCambio:
    """Migración 3: cada cambio del catálogo avisa por el canal `catalogo`."""

    def _avisos(self, pg_cfg):
        from convertir3x4.catalog.db import conectar
        oyente = conectar(pg_cfg)
        oyente.execute("LISTEN catalogo")
        return oyente

    def _recibidos(self, oyente):
        return [n.payload for n in oyente.notifies(timeout=0.5)]

    def test_avisa_altas_etiquetas_y_no_los_cambios_vacios(self, repo, pg_cfg):
        from convertir3x4.catalog.models import Media
        oyente = self._avisos(pg_cfg)
        try:
            mid = repo.upsert_media(Media(path="/a.jpg", filename="a.jpg", kind="image"))
            assert "media" in self._recibidos(oyente)
            repo.apply_tag(mid, "bautismo")
            assert {"tag", "media_tag"} <= set(self._recibidos(oyente))
            # un UPDATE que no cambia nada (p. ej. confirmar lo ya confirmado) no avisa
            repo.confirm_tag(mid, "bautismo")
            assert self._recibidos(oyente) == []
        finally:
            oyente.close()
