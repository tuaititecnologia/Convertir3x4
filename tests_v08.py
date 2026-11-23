"""Pruebas de la v0.8.0 (docs/plan-v0.8.md): esquema 7, catalogar por partes,
fecha de toma y lugar, tiras de cuadros, fotos de referencia leídas por el NAS
y fotos hermanas. Las que usan la base necesitan el PostgreSQL de desarrollo
(conftest.py); sin él se saltean."""

import dataclasses
import datetime as dt
import os
import tempfile

import imageio
import numpy as np
import pytest
from PIL import Image

from convertir3x4 import catalogador as cat
from convertir3x4.catalog import db as catdb, tiras
from convertir3x4.catalog.cache_miniaturas import CacheTiras
from convertir3x4.catalog.models import Media
from convertir3x4.catalog.repo import Repo
from convertir3x4.engine import ai_pipeline, metadatos
from convertir3x4.engine.pipeline import Opciones, catalogar

UTC = dt.timezone.utc
IGLESIA = dt.timezone(dt.timedelta(hours=-3))


def _jpeg_con_exif(path, fecha: str | None = "2019:07:14 10:31:02", gps=None, color="red",
                   lado=900):
    img = Image.new("RGB", (lado, lado * 2 // 3), color)
    exif = Image.Exif()
    if fecha:
        exif.get_ifd(0x8769)[0x9003] = fecha
    if gps:
        (lat, lat_ref), (lon, lon_ref) = gps
        exif.get_ifd(0x8825).update({1: lat_ref, 2: lat, 3: lon_ref, 4: lon})
    img.save(path, "JPEG", exif=exif)


def _mp4(path, segundos=6, fps=4, w=160, h=120):
    wri = imageio.get_writer(path, fps=fps, codec="libx264", macro_block_size=1,
                             output_params=["-pix_fmt", "yuv420p", "-g", "4"])
    for i in range(segundos * fps):
        cuadro = np.zeros((h, w, 3), np.uint8)
        cuadro[:, :] = (40 + 8 * i) % 255, 120, 200
        wri.append_data(cuadro)
    wri.close()


@pytest.fixture
def raiz():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def nas(pg_conn, raiz):
    return Repo(pg_conn, raiz)


# --------------------------------------------------------------- metadatos
class TestMetadatos:
    def test_fecha_exif(self):
        f = metadatos.fecha_exif("2019:07:14 10:31:02")
        assert f == dt.datetime(2019, 7, 14, 10, 31, 2, tzinfo=IGLESIA)
        assert metadatos.fecha_exif("0000:00:00 00:00:00") is None      # cámara sin hora
        assert metadatos.fecha_exif("1970:01:01 00:00:00") is None
        assert metadatos.fecha_exif(None) is None

    def test_foto_con_fecha_y_gps(self, raiz):
        p = os.path.join(raiz, "a.jpg")
        _jpeg_con_exif(p, gps=(((34, 36, 13.2), "S"), ((58, 22, 54.0), "W")))
        md = metadatos.leer(p, "image")
        assert md.tomada == dt.datetime(2019, 7, 14, 10, 31, 2, tzinfo=IGLESIA)
        assert md.lat == pytest.approx(-34.6037, abs=1e-3)
        assert md.lon == pytest.approx(-58.3817, abs=1e-3)

    def test_foto_sin_exif_y_archivo_raro(self, raiz):
        p = os.path.join(raiz, "b.jpg")
        Image.new("RGB", (10, 10)).save(p)
        assert metadatos.leer(p, "image") == metadatos.Metadatos()
        q = os.path.join(raiz, "no.jpg")
        open(q, "wb").write(b"no es una foto")
        assert metadatos.leer(q, "image") == metadatos.Metadatos()      # nunca lanza

    def test_salida_de_ffmpeg(self):
        texto = ("  Metadata:\n    creation_time   : 2016-04-02T20:29:05.000000Z\n"
                 "    location        : -34.6037-058.3816/\n")
        md = metadatos.de_salida_ffmpeg(texto)
        assert md.tomada == dt.datetime(2016, 4, 2, 20, 29, 5, tzinfo=UTC)
        assert (md.lat, md.lon) == (-34.6037, -58.3816)
        assert metadatos.de_salida_ffmpeg("sin nada") == metadatos.Metadatos()


# --------------------------------------------------------------- tiras
def test_tira_de_un_video(raiz):
    p = os.path.join(raiz, "v.mp4")
    _mp4(p)
    jpeg = tiras.hacer_tira(p, 6.0)
    assert jpeg and jpeg[:2] == b"\xff\xd8"
    img = Image.open(__import__("io").BytesIO(jpeg))
    assert img.width == tiras.ANCHO * tiras.COLUMNAS       # 4 cuadros de 240 por fila
    assert tiras.hacer_tira(p, None) is None


# --------------------------------------------------------------- catalogado
class TestCatalogado:
    def test_lo_nuevo_trae_fecha_lugar_y_tira(self, nas, raiz):
        os.makedirs(os.path.join(raiz, "Entrada"))
        _jpeg_con_exif(os.path.join(raiz, "Entrada", "a.jpg"),
                       gps=(((34, 36, 0), "S"), ((58, 22, 0), "W")))
        _mp4(os.path.join(raiz, "Entrada", "v.mp4"))
        catalogar(nas, ["Entrada"])
        foto = nas.get_media_by_path(os.path.join(raiz, "Entrada", "a.jpg"))
        assert foto.tomada == dt.datetime(2019, 7, 14, 10, 31, 2, tzinfo=IGLESIA)
        assert foto.lat == pytest.approx(-34.6, abs=1e-3)
        video = nas.get_media_by_path(os.path.join(raiz, "Entrada", "v.mp4"))
        assert video.id in nas.sellos_tira([video.id])
        assert nas.sellos_tira([foto.id]) == {}

    def test_completa_una_vez_lo_ya_catalogado(self, nas, raiz):
        """Lo catalogado antes de la v0.8 (metadatos_version 0, sin tira) se
        completa sin rehacer miniaturas; la tira de lo viejo, solo si se pide."""
        os.makedirs(os.path.join(raiz, "Entrada"))
        _jpeg_con_exif(os.path.join(raiz, "Entrada", "a.jpg"))
        _mp4(os.path.join(raiz, "Entrada", "v.mp4"))
        catalogar(nas, ["Entrada"])
        nas._q("UPDATE media SET tomada = NULL, metadatos_version = 0")
        nas._q("DELETE FROM tira")
        st = catalogar(nas, ["Entrada"], opciones=Opciones(tiras_faltantes=False))
        assert st.metadatos == 2 and st.tiras == 0 and st.miniaturas_rehechas == 0
        assert nas.get_media_by_path(os.path.join(raiz, "Entrada", "a.jpg")).tomada
        st = catalogar(nas, ["Entrada"])
        assert st.metadatos == 0 and st.tiras == 1          # ya al día; la tira que faltaba

    def test_rehacer_miniaturas(self, nas, raiz):
        os.makedirs(os.path.join(raiz, "Entrada"))
        _jpeg_con_exif(os.path.join(raiz, "Entrada", "a.jpg"))
        catalogar(nas, ["Entrada"])
        st = catalogar(nas, ["Entrada"], opciones=Opciones(rehacer_miniaturas=True))
        assert st.miniaturas_rehechas == 1
        st = catalogar(nas, ["Entrada"])
        assert st.miniaturas_rehechas == 0


# --------------------------------------------------------------- IA por partes
def _medio(repo, rel, kind="image"):
    return repo.upsert_media(Media(path="", rel_path=rel, filename=os.path.basename(rel),
                                   folder_origin=os.path.dirname(rel), kind=kind))


class TestIAporPartes:
    def test_alcance_y_partes(self, nas):
        a = _medio(nas, "Entrada/Retiro/a.jpg")
        b = _medio(nas, "Publicar/b.jpg")
        todos = nas.pendientes_de_ia("m1", "s1")
        assert {p.media.id for p in todos} == {a, b} and all(p.escena and p.caras for p in todos)
        solo = nas.pendientes_de_ia("m1", "s1", carpetas=["Entrada/Retiro"])
        assert [p.media.id for p in solo] == [a]
        assert nas.pendientes_de_ia("m1", "s1", carpetas=[]) == []
        # Analizados con m1/s1: cambiar la firma pide solo caras, y solo si se piden personas
        for mid in (a, b):
            nas.save_ai_result(mid, "m1", "s1", None, [], "{}")
        assert nas.pendientes_de_ia("m1", "s2", escenas=True, personas=False) == []
        caras = nas.pendientes_de_ia("m1", "s2")
        assert {(p.escena, p.caras) for p in caras} == {(False, True)}
        # Rehacer: todo lo pedido, aunque esté al día
        re = nas.pendientes_de_ia("m1", "s1", carpetas=["Publicar"], personas=False, rehacer=True)
        assert [(p.media.id, p.escena, p.caras) for p in re] == [(b, True, False)]

    def test_la_parte_no_pedida_queda_como_estaba(self, nas, raiz, monkeypatch):
        """Pedir solo escenas no marca las caras al día (si no, nunca se buscaría
        a las personas nuevas en esas fotos)."""
        p = os.path.join(raiz, "a.jpg")
        Image.new("RGB", (64, 64), "white").save(p)
        mid = nas.upsert_media(Media(path=p, filename="a.jpg", kind="image"))
        nas.save_ai_result(mid, "viejo", "firma-vieja", None, [], "{}")

        class Clasificador:
            def __init__(self, *a):
                pass

            def classify(self, img):
                return [("alabanza", 0.9)]
        monkeypatch.setattr(ai_pipeline, "SceneClassifier", Clasificador)
        monkeypatch.setattr(nas, "people_signature", lambda: "firma-nueva")
        ai_pipeline.run_ai(nas, "/modelos", escenas=True, personas=False)
        fila = nas._q("SELECT model, people_sig FROM ai_result WHERE media_id = %s",
                      (mid,)).fetchone()
        assert fila["model"] == ai_pipeline.AI_MODEL and fila["people_sig"] == "firma-vieja"


# --------------------------------------------------------------- pedidos y catalogador
@pytest.fixture
def cfg(pg_cfg, raiz):
    os.makedirs(os.path.join(raiz, "Entrada", "Retiro"))
    os.makedirs(os.path.join(raiz, "Publicar"))
    _jpeg_con_exif(os.path.join(raiz, "Entrada", "Retiro", "a.jpg"))
    _jpeg_con_exif(os.path.join(raiz, "Publicar", "b.jpg"), color="blue")
    catalogo = dataclasses.replace(pg_cfg, raiz=raiz)
    return cat.ConfigCatalogador(catalogo=catalogo, ia=False, hora=dt.time(1, 30))


@pytest.fixture
def repo_nas(cfg):
    r = Repo(catdb.conectar(cfg.catalogo), cfg.catalogo.raiz)
    for c in ("Entrada", "Publicar"):
        r.agregar_carpeta(c)
    yield r
    r.close()


class TestPedidos:
    def test_pedido_con_pasos_no_se_confunde(self, repo_nas):
        a = repo_nas.pedir_catalogado("Entrada", "PC", ["personas"])
        assert repo_nas.pedir_catalogado("Entrada", "PC", ["personas"]) == a
        assert repo_nas.pedir_catalogado("Entrada", "PC") != a             # todo
        assert repo_nas.pedir_catalogado("Entrada", "PC", ["personas"], rehacer=True) != a
        p = repo_nas.pedido(a)
        assert p["pasos"] == ["personas"] and p["rehacer"] is False

    def test_la_ia_respeta_la_carpeta_y_los_pasos(self, repo_nas, cfg, monkeypatch):
        llamadas = []

        def run_ai(repo, modelos, **kw):
            llamadas.append(kw)
            return ai_pipeline.AIStats()
        monkeypatch.setattr(ai_pipeline, "run_ai", run_ai)
        monkeypatch.setattr(cat, "_referencias_al_paso", lambda *a: 0)
        from convertir3x4.engine import ai_models
        monkeypatch.setattr(ai_models, "ensure", lambda *a: None)
        cfg = dataclasses.replace(cfg, ia=True)
        repo_nas.pedir_catalogado("Entrada/Retiro", "PC", ["personas"], rehacer=True)
        cat.ciclo(repo_nas, cfg, ahora=dt.datetime(2026, 11, 22, 15, 0))
        assert llamadas[-1]["carpetas"] == ["Entrada/Retiro"]
        assert (llamadas[-1]["escenas"], llamadas[-1]["personas"], llamadas[-1]["rehacer"]) \
            == (False, True, True)
        # «archivos» corre siempre: lo nuevo de la carpeta ya está para la IA
        assert [m.rel_path for m in repo_nas.all_media()] == ["Entrada/Retiro/a.jpg"]
        # Solo archivos: sin IA
        repo_nas.pedir_catalogado(None, "PC", ["archivos"])
        n = len(llamadas)
        cat.ciclo(repo_nas, cfg, ahora=dt.datetime(2026, 11, 22, 15, 0))
        assert len(llamadas) == n and repo_nas.count_media() == 2


# --------------------------------------------------------------- referencias del catálogo
def test_referencias_elegidas_en_buscar_las_lee_el_nas(repo_nas, cfg, monkeypatch):
    from convertir3x4.engine import ai_models, faces
    repo_nas.pedir_catalogado()
    cat.ciclo(repo_nas, cfg, ahora=dt.datetime(2026, 11, 22, 15, 0))
    foto = repo_nas.get_media_by_path(os.path.join(cfg.catalogo.raiz, "Publicar", "b.jpg"))
    video = repo_nas.upsert_media(Media(path="", rel_path="Publicar/v.mp4", filename="v.mp4",
                                        kind="video"))
    pid = repo_nas.referencias_desde_el_catalogo("Juan Carlos Bazán", [foto.id, video])
    refs = repo_nas.detalle_persona(pid, fotos=False)["referencias"]
    assert len(refs) == 1 and not refs[0]["tiene_foto"]       # el video no se usa
    assert refs[0]["ruta"] == "Publicar/b.jpg" and refs[0]["estado"] == "pendiente"
    assert repo_nas.hay_referencias_pendientes()

    class Caras:
        def __init__(self, *a):
            pass

        def revisar_referencia(self, img):
            assert max(img.size) <= 1600            # la achicó el NAS
            return faces.RevisionReferencia(np.ones(128, np.float32), 1, (10, 10, 100, 100), 0.95)
    monkeypatch.setattr(ai_models, "ensure", lambda *a, **k: None)
    monkeypatch.setattr(faces, "FaceEngine", Caras)
    cfg = dataclasses.replace(cfg, ia=True)
    assert cat.procesar_referencias(repo_nas, cfg) == 1
    ref = repo_nas.detalle_persona(pid)["referencias"][0]
    assert ref["estado"] == "lista" and ref["foto"][:2] == b"\xff\xd8"


def test_referencia_del_catalogo_que_ya_no_esta(repo_nas, cfg, monkeypatch):
    mid = repo_nas.upsert_media(Media(path="", rel_path="Publicar/no.jpg", filename="no.jpg",
                                      kind="image"))
    pid = repo_nas.referencias_desde_el_catalogo("Invitada", [mid])
    from convertir3x4.engine import ai_models
    monkeypatch.setattr(ai_models, "ensure", lambda *a, **k: None)
    cat._leer_referencias_del_catalogo(repo_nas)
    assert repo_nas.detalle_persona(pid)["referencias"][0]["estado"] == "ilegible"


# --------------------------------------------------------------- hermanas y orden
class TestHermanas:
    def _cargar(self, repo):
        def m(rel, tomada):
            mid = _medio(repo, rel)
            repo._q("UPDATE media SET tomada = %s WHERE id = %s", (tomada, mid))
            return mid
        d1 = dt.datetime(2026, 3, 1, 10, 0, tzinfo=IGLESIA)
        return {
            "a": m("Historial/Retiro/a.jpg", d1),
            "b": m("Historial/Retiro/b.jpg", d1 + dt.timedelta(hours=8)),
            # 23:30 en la iglesia es el día siguiente en UTC: igual es el mismo día
            "c": m("Historial/Retiro/c.jpg", d1.replace(hour=23, minute=30)),
            "otro_dia": m("Historial/Retiro/d.jpg", d1 + dt.timedelta(days=1)),
            "sin_fecha": m("Historial/Retiro/e.jpg", None),
            "otra_carpeta": m("Historial/Culto/f.jpg", d1),
        }

    def test_misma_carpeta_y_mismo_dia(self, repo):
        ids = self._cargar(repo)
        repo.apply_tag(ids["a"], "Retiro 2026")
        assert set(repo.hermanas([ids["a"]], "Retiro 2026")) == {ids["b"], ids["c"]}
        # Sin fecha de toma: toda la carpeta
        assert set(repo.hermanas([ids["sin_fecha"]], "Retiro 2026")) == {
            ids["b"], ids["c"], ids["otro_dia"]}
        # Ni las que ya la tienen ni a las que alguien se la quitó
        repo.apply_tag(ids["b"], "Retiro 2026")
        repo.apply_tag(ids["c"], "Retiro 2026")
        repo.remove_tag(ids["c"], "Retiro 2026")
        assert repo.hermanas([ids["a"]], "retiro 2026") == []

    def test_orden_por_fecha_de_toma(self, repo):
        ids = self._cargar(repo)
        nuevas, _ = repo.buscar("", orden="nuevas")
        assert nuevas[0].id == ids["otro_dia"] and nuevas[-1].id == ids["sin_fecha"]
        viejas, _ = repo.buscar("", orden="viejas")
        assert viejas[0].id in (ids["a"], ids["otra_carpeta"])
        assert viejas[-1].id == ids["sin_fecha"]                 # sin fecha, al final
        assert nuevas[0].tomada is not None


def test_cache_de_tiras(repo, tmp_path):
    mid = _medio(repo, "Publicar/v.mp4", kind="video")
    cache = CacheTiras(str(tmp_path))
    assert cache.asegurar(repo, mid) is None                 # el NAS todavía no la armó
    repo.guardar_tira(mid, b"\xff\xd8uno")
    r = cache.asegurar(repo, mid)
    assert open(r, "rb").read() == b"\xff\xd8uno"
    monkey = repo.tiras
    repo.tiras = lambda ids: pytest.fail("la bajó otra vez")
    assert cache.asegurar(repo, mid) == r
    repo.tiras = monkey
    repo.guardar_tira(mid, b"\xff\xd8dos")                  # cambió: otra copia
    r2 = cache.asegurar(repo, mid)
    assert r2 != r and not os.path.exists(r) and open(r2, "rb").read() == b"\xff\xd8dos"


def test_migracion_7(pg_conn):
    assert catdb.version_esquema(pg_conn) == 7
    cols = {r["column_name"] for r in pg_conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'media'")}
    assert {"tomada", "lat", "lon", "metadatos_version"} <= cols


# --------------------------------------------------------------- «Catalogar ▾»
@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    from convertir3x4.ui.style import apply_theme
    a = QApplication.instance() or QApplication([])
    apply_theme(a, "dark")
    return a


class TestCatalogarPorPartes:
    def test_todo_apaga_las_demas(self, qapp):
        from convertir3x4.ui.widgets.catalogar_por_partes import CatalogarPorPartes
        d = CatalogarPorPartes(carpetas=("Entrada", "Publicar"), raiz="/nas")
        assert d.pasos is None and d.btn_ok.isEnabled()
        assert not any(c.isEnabled() for c in d.casillas.values())
        d.chk_todo.setChecked(False)
        assert all(c.isEnabled() for c in d.casillas.values())
        assert not d.btn_ok.isEnabled()                 # sin nada marcado no se puede
        d.casillas["personas"].setChecked(True)
        assert d.pasos == ["personas"] and d.btn_ok.isEnabled()
        d.chk_todo.setChecked(True)
        assert d.pasos is None and not d.casillas["personas"].isChecked()

    def test_una_carpeta_y_rehacer(self, qapp, monkeypatch):
        from convertir3x4.ui.widgets import catalogar_por_partes as cp
        d = cp.CatalogarPorPartes(carpetas=("Entrada", "Publicar"), raiz="/nas")
        d.rb_una.setChecked(True)
        assert not d.btn_ok.isEnabled()                 # falta elegir la carpeta
        monkeypatch.setattr(cp.QFileDialog, "getExistingDirectory",
                            staticmethod(lambda *a, **k: "/otro/lado"))
        d._elegir_carpeta()
        assert "no está entre" in d.lbl_carpeta.text() and d.carpeta_elegida() is None
        monkeypatch.setattr(cp.QFileDialog, "getExistingDirectory",
                            staticmethod(lambda *a, **k: "/nas/Entrada/Retiro"))
        d._elegir_carpeta()
        assert d.carpeta_elegida() == "Entrada/Retiro" and d.btn_ok.isEnabled()
        assert "Sin marcar" in d.lbl_tiempo.text()
        d.chk_rehacer.setChecked(True)
        assert d.rehacer and "con una carpeta" in d.lbl_tiempo.text()
        d.rb_todas.setChecked(True)
        assert "Puede tardar unas 6 horas" in d.lbl_tiempo.text()

    def test_el_pedido_lleva_lo_elegido(self, qapp, pg_cfg, tmp_path, monkeypatch):
        from convertir3x4.catalog.db import conectar
        from convertir3x4.ui.servicio_catalogo import ServicioCatalogo
        from convertir3x4.ui.tabs import tab_motor
        from convertir3x4.ui.widgets import catalogar_por_partes as cp

        def abrir():
            return Repo(conectar(pg_cfg), str(tmp_path))
        with abrir() as r:
            r.agregar_carpeta("Entrada")

        class Elegido:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                return True
            pasos, rehacer = ["personas"], True

            def carpeta_elegida(self):
                return "Entrada/Retiro"
        monkeypatch.setattr(cp, "CatalogarPorPartes", Elegido)
        srv = ServicioCatalogo(abrir=abrir)
        tab = tab_motor.MotorTab(srv=srv)
        try:
            import time
            t = time.time()
            while not tab.btn_run.isEnabled() and time.time() - t < 10:
                qapp.processEvents()
            assert tab.boton_catalogar.flecha.isEnabled()
            tab.boton_catalogar.flecha.click()
            t = time.time()
            pedidos = []
            while not pedidos and time.time() - t < 10:
                qapp.processEvents()
                with abrir() as r:
                    pedidos = r.pedidos_abiertos()
            p = pedidos[0]
            assert (p["carpeta"], p["pasos"], p["rehacer"]) == ("Entrada/Retiro", ["personas"], True)
        finally:
            tab.deleteLater()
            srv.cerrar()


# --------------------------------------------------------------- carpetas desde la app
class TestCarpetas:
    def test_agregar_y_quitar(self, repo):
        assert repo.carpetas_catalogadas() == ()                 # sin predeterminados
        assert repo.agregar_carpeta("Publicar\\2026\\") == "Publicar/2026"
        repo.agregar_carpeta("Entrada", "PC-EDICION")
        assert repo.carpetas_catalogadas() == ("Entrada", "Publicar/2026")
        for mala in ("Entrada", "entrada", "Publicar", "Publicar/2026/Culto", "../afuera", ""):
            with pytest.raises(catdb.ConfiguracionInvalida):
                repo.agregar_carpeta(mala)
        a = _medio(repo, "Publicar/2026/a.jpg")
        b = _medio(repo, "Publicar/2026x/b.jpg")             # otra carpeta con el mismo comienzo
        assert repo.medios_en_carpeta("Publicar/2026") == [a]
        assert repo.quitar_carpeta("Entrada") == []
        assert repo.quitar_carpeta("Publicar/2026", con_sus_medios=True) == [a]
        assert repo.carpetas_catalogadas() == ()
        assert repo.get_media(a) is None and repo.get_media(b) is not None

    def test_la_migracion_conserva_la_lista_del_nas(self, pg_cfg):
        """Al pasar al esquema 7, la lista que publicaba el catalogador (compose)
        queda en la base: el NAS no se queda sin carpetas al actualizar."""
        conn = catdb.conectar(pg_cfg, preparar=False)
        try:
            with conn.transaction():
                conn.execute("CREATE TABLE esquema (version integer PRIMARY KEY,"
                             " aplicada timestamptz NOT NULL DEFAULT now())")
                for v, sql in catdb.MIGRACIONES[:6]:
                    conn.execute(sql)
                    conn.execute("INSERT INTO esquema (version) VALUES (%s)", (v,))
                conn.execute("UPDATE catalogado_estado SET carpetas = "
                             "'{Entrada,Publicar,Historial}'")
            catdb.preparar_esquema(conn)
            assert Repo(conn, "/").carpetas_catalogadas() == ("Entrada", "Historial", "Publicar")
        finally:
            conn.close()

    def test_sin_carpetas_el_nas_no_recorre_nada(self, pg_cfg, raiz):
        os.makedirs(os.path.join(raiz, "Entrada"))
        Image.new("RGB", (900, 600)).save(os.path.join(raiz, "Entrada", "a.jpg"))
        cfg = cat.ConfigCatalogador(catalogo=dataclasses.replace(pg_cfg, raiz=raiz), ia=False)
        with Repo(catdb.conectar(cfg.catalogo), raiz) as r:
            r.pedir_catalogado()
            cat.ciclo(r, cfg, ahora=dt.datetime(2026, 11, 22, 15, 0))
            assert r.count_media() == 0
            r.agregar_carpeta("Entrada")                       # se agrega desde la app
            r.pedir_catalogado()
            cat.ciclo(r, cfg, ahora=dt.datetime(2026, 11, 22, 15, 0))
            assert r.count_media() == 1

    def test_ventana_de_opciones(self, qapp, pg_cfg, tmp_path, monkeypatch):
        import time
        from convertir3x4.catalog.db import conectar
        from convertir3x4.ui.servicio_catalogo import ServicioCatalogo
        from convertir3x4.ui.widgets import catalogo_avanzado as ca

        def abrir():
            return Repo(conectar(pg_cfg), str(tmp_path))

        def esperar(cond):
            t = time.time()
            while not cond() and time.time() - t < 10:
                qapp.processEvents()
                time.sleep(0.01)
            return cond()
        with abrir() as r:
            mid = _medio(r, "Historial/2014/a.jpg")
        monkeypatch.setattr(ca, "catalog_thumb_dir", lambda: str(tmp_path / "cache"))
        srv = ServicioCatalogo(abrir=abrir)
        d = ca.CatalogoAvanzado(raiz=str(tmp_path), srv=srv)
        try:
            assert "Todavía no hay ninguna" in d.lbl_carpetas.text()
            assert not d.btn_quitar_carpeta.isEnabled()
            d.agregar_carpeta("/otro/lado")
            assert "no está dentro" in d.lbl_carpetas.text()
            d.agregar_carpeta(str(tmp_path / "Historial"))
            assert esperar(lambda: d.lst_carpetas.count() == 1)
            assert "Se agregó «Historial»" in d.lbl_carpetas.text()
            d.agregar_carpeta(str(tmp_path / "Historial" / "2014"))
            assert esperar(lambda: "se superpone" in d.lbl_carpetas.text())
            # Quitar con sus fotos (lo que elige en la confirmación)
            d.lst_carpetas.item(0).setSelected(True)
            assert d.btn_quitar_carpeta.isEnabled()
            confirmado = []
            monkeypatch.setattr(d, "_confirmar_quitar_carpeta",
                                lambda ruta, n: confirmado.append((ruta, n))
                                or d.quitar_carpeta(ruta, con_sus_medios=True))
            d._quitar_carpeta()
            assert esperar(lambda: d.lst_carpetas.count() == 0)
            assert confirmado == [("Historial", 1)]
            assert "y 1 foto o video del catálogo" in d.lbl_carpetas.text()
            with abrir() as r:
                assert r.get_media(mid) is None
        finally:
            d.deleteLater()
            srv.cerrar()


# --------------------------------------------------------------- pantallas guardadas
class TestPantallas:
    def test_proporcion(self):
        from convertir3x4.ui.widgets.pantallas import describir, proporcion
        assert proporcion(4.5, 1.2) == (15, 4)
        assert proporcion(1920, 1080) == (16, 9)
        assert proporcion(8, 9) == (8, 9)
        w, h = proporcion(1000, 1)                  # muy alargada: igual números chicos
        assert w <= 100 and h >= 1
        assert describir({"nombre": "Primavera", "ancho": 4.5, "alto": 1.2}) == \
            "Primavera · 4,5 × 1,2 (15:4)"
        assert describir({"nombre": "LED", "ancho": 8, "alto": 9}) == "LED · 8 × 9"

    def test_guardar_cambiar_quitar(self, repo):
        a = repo.guardar_pantalla("  Evento de   primavera ", 4.5, 1.2)
        assert repo.pantallas() == [{"id": a, "nombre": "Evento de primavera",
                                     "ancho": 4.5, "alto": 1.2}]
        with pytest.raises(ValueError, match="ya hay"):
            repo.guardar_pantalla("evento de primavera", 1, 1)
        with pytest.raises(ValueError):
            repo.guardar_pantalla("", 1, 1)
        with pytest.raises(ValueError):
            repo.guardar_pantalla("x", 0, 1)
        repo.guardar_pantalla("Primavera", 5, 1.25, pantalla_id=a)
        assert repo.pantallas()[0]["nombre"] == "Primavera"
        repo.quitar_pantalla(a)
        assert repo.pantallas() == []

    def test_el_engranaje_ofrece_las_guardadas(self, qapp, pg_cfg, tmp_path):
        import time
        from convertir3x4.catalog.db import conectar
        from convertir3x4.ui.servicio_catalogo import ServicioCatalogo
        from convertir3x4.ui.widgets.advanced_dialog import AdvancedDialog, SessionOptions

        def abrir():
            return Repo(conectar(pg_cfg), str(tmp_path))
        with abrir() as r:
            r.guardar_pantalla("Evento de primavera", 4.5, 1.2)
        srv = ServicioCatalogo(abrir=abrir)
        d = AdvancedDialog(SessionOptions(), srv=srv)
        try:
            t = time.time()
            while d.combo_ratio.findText("Evento de primavera", flags=__import__(
                    "PyQt6.QtCore", fromlist=["Qt"]).Qt.MatchFlag.MatchStartsWith) < 0 \
                    and time.time() - t < 10:
                qapp.processEvents()
            i = d.combo_ratio.findText("Evento de primavera · 4,5 × 1,2 (15:4)")
            assert i >= 0
            assert d.combo_ratio.currentIndex() == 0        # sigue en el LED (8:9)
            d.combo_ratio.setCurrentIndex(i)
            o = d.options()
            assert o.aspect == (15, 4) and o.pantalla == "Evento de primavera"
            assert o.changes()[0] == "Pantalla «Evento de primavera»"
            # Al volver a abrir con esa elección, queda elegida
            d2 = AdvancedDialog(o, srv=srv)
            t = time.time()
            while d2._nombre_elegido() != "Evento de primavera" and time.time() - t < 10:
                qapp.processEvents()
            assert d2.options().pantalla == "Evento de primavera"
        finally:
            srv.cerrar()

    def test_sin_catalogo_solo_las_de_siempre(self, qapp):
        from convertir3x4.ui.widgets.advanced_dialog import AdvancedDialog, SessionOptions
        d = AdvancedDialog(SessionOptions())
        assert not d.btn_guardar_pantalla.isVisibleTo(d)
        assert d.options().pantalla is None and d.options().aspect == (8, 9)
