"""Tests del catalogador del NAS (convertir3x4/catalogador.py).

Las reglas (horario, copias) son puras. El resto corre contra el PostgreSQL de
desarrollo (conftest.py) con una carpeta temporal como raíz del NAS.
"""

import dataclasses
import datetime as dt
import json
import os
import tempfile
import threading
import time

import pytest
from PIL import Image

from convertir3x4 import catalogador as cat
from convertir3x4.catalog import db as catdb
from convertir3x4.catalog.repo import Repo

D = dt.datetime
HORA = dt.time(1, 30)


@pytest.fixture
def raiz():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "Publicar", "2026", "Culto"))
        os.makedirs(os.path.join(d, "Entrada"))
        Image.new("RGB", (900, 600), "red").save(os.path.join(d, "Publicar", "2026", "Culto", "a.jpg"))
        Image.new("RGB", (600, 900), "blue").save(os.path.join(d, "Entrada", "b.jpg"))
        yield d


@pytest.fixture
def cfg(pg_cfg, raiz):
    catalogo = dataclasses.replace(pg_cfg, raiz=raiz, carpetas=("Publicar", "Entrada"))
    return cat.ConfigCatalogador(catalogo=catalogo, ia=False, hora=HORA)


@pytest.fixture
def repo_nas(cfg):
    r = Repo(catdb.conectar(cfg.catalogo), cfg.catalogo.raiz)
    yield r
    r.close()


class TestHorario:
    @pytest.mark.parametrize("ahora, ultima, toca", [
        (D(2026, 11, 22, 1, 0), None, False),                       # antes de la hora
        (D(2026, 11, 22, 1, 30), None, True),
        (D(2026, 11, 22, 5, 0), None, True),                        # dentro de la ventana
        (D(2026, 11, 22, 8, 0), None, False),                       # pasó la ventana: no a la tarde
        (D(2026, 11, 22, 2, 0), dt.date(2026, 11, 22), False),      # ya corrió hoy
        (D(2026, 11, 22, 2, 0), dt.date(2026, 11, 21), True),
    ])
    def test_toca_programada(self, ahora, ultima, toca):
        assert cat.toca_programada(ahora, HORA, ultima) is toca

    def test_ventana_que_cruza_la_medianoche(self):
        hora = dt.time(23, 0)
        assert cat.toca_programada(D(2026, 11, 22, 1, 0), hora, None)
        # la de anoche ya corrió (se anota con la fecha en que empezó la ventana)
        assert not cat.toca_programada(D(2026, 11, 22, 1, 0), hora, dt.date(2026, 11, 21))


class TestCopias:
    def _estado(self, base, nombre, fase, hace=0):
        d = os.path.join(base, nombre)
        os.makedirs(d)
        ruta = os.path.join(d, "estado.json")
        with open(ruta, "w") as f:
            json.dump({"fase": fase}, f)
        t = time.time() - hace
        os.utime(ruta, (t, t))

    def test_sin_carpeta(self):
        assert cat.copia_en_curso(None) is None
        assert cat.copia_en_curso("/no/existe") is None

    def test_detecta_la_copia_activa(self, tmp_path):
        self._estado(tmp_path, "25B6-1F03", "TERMINADO")
        self._estado(tmp_path, "3670-12F3", "COPIANDO")
        assert cat.copia_en_curso(str(tmp_path)) == "3670-12F3"

    def test_copia_cortada_no_cuenta(self, tmp_path):
        self._estado(tmp_path, "3670-12F3", "COPIANDO", hace=3600)
        assert cat.copia_en_curso(str(tmp_path)) is None


class TestPedidos:
    def test_no_duplica_pedidos_iguales(self, repo):
        a = repo.pedir_catalogado(None, "PC-EDICION")
        assert repo.pedir_catalogado(None, "PC-PROPRESENTER") == a
        b = repo.pedir_catalogado("Publicar/2026")
        assert b != a
        assert [p["id"] for p in repo.pedidos_abiertos()] == [a, b]

    def test_tomar_cerrar_y_soltar(self, repo):
        a = repo.pedir_catalogado()
        p = repo.tomar_pedido()
        assert p["id"] == a and p["empezado"] is not None
        assert repo.tomar_pedido() is None
        # un pedido nuevo igual no se junta con el que ya empezó
        assert repo.pedir_catalogado() != a
        assert repo.soltar_pedidos_colgados() == 1
        repo.cerrar_pedido(a, {"ok": 1})
        assert repo.pedido(a)["resumen"] == {"ok": 1}

    def test_estado_inicial(self, repo):
        est = repo.estado_catalogado()
        assert est["fase"] == "esperando" and est["segundos_sin_latido"] is None
        repo.actualizar_estado(fase="catalogando", hechos=3, total=10)
        est = repo.estado_catalogado()
        assert est["hechos"] == 3 and est["segundos_sin_latido"] == 0
        with pytest.raises(ValueError):
            repo.actualizar_estado(otra_cosa=1)


class TestCiclo:
    def test_sin_pedidos_fuera_de_horario_no_hace_nada(self, repo_nas, cfg):
        assert not cat.ciclo(repo_nas, cfg, ahora=D(2026, 11, 22, 15, 0))
        assert repo_nas.count_media() == 0

    def test_atiende_un_pedido(self, repo_nas, cfg):
        pid = repo_nas.pedir_catalogado()
        assert cat.ciclo(repo_nas, cfg, ahora=D(2026, 11, 22, 15, 0))
        assert repo_nas.count_media() == 2
        p = repo_nas.pedido(pid)
        assert p["terminado"] and p["error"] is None
        assert p["resumen"]["catalogado"]["added"] == 2
        est = repo_nas.estado_catalogado()
        assert est["fase"] == "esperando" and est["ultima_resumen"]["catalogado"]["added"] == 2
        assert est["ultima_programada"] is None      # un pedido no cuenta como la nocturna

    def test_pasada_nocturna_una_vez(self, repo_nas, cfg):
        assert cat.ciclo(repo_nas, cfg, ahora=D(2026, 11, 22, 1, 40))
        assert repo_nas.estado_catalogado()["ultima_programada"] == dt.date(2026, 11, 22)
        assert not cat.ciclo(repo_nas, cfg, ahora=D(2026, 11, 22, 2, 0))

    def test_subcarpeta(self, repo_nas, cfg):
        repo_nas.pedir_catalogado("Publicar/2026")
        cat.ciclo(repo_nas, cfg)
        assert [m.rel_path for m in repo_nas.all_media()] == ["Publicar/2026/Culto/a.jpg"]

    def test_subcarpeta_fuera_de_la_lista(self, repo_nas, cfg):
        pid = repo_nas.pedir_catalogado("Historial")
        cat.ciclo(repo_nas, cfg)
        p = repo_nas.pedido(pid)
        assert p["terminado"] and "no está dentro" in p["error"]
        assert repo_nas.estado_catalogado()["ultima_error"] == p["error"]

    def test_espera_si_se_copia_un_disco(self, repo_nas, cfg, tmp_path):
        d = tmp_path / "3670-12F3"
        d.mkdir()
        (d / "estado.json").write_text('{"fase": "COPIANDO"}')
        cfg = dataclasses.replace(cfg, copias=str(tmp_path))
        pid = repo_nas.pedir_catalogado()
        assert not cat.ciclo(repo_nas, cfg)
        est = repo_nas.estado_catalogado()
        assert est["fase"] == "esperando_copia" and est["detalle"] == "3670-12F3"
        assert repo_nas.pedido(pid)["empezado"] is None       # sigue en la fila

    def test_apagado_a_mitad(self, repo_nas, cfg):
        pid = repo_nas.pedir_catalogado()
        parar = threading.Event()
        parar.set()
        with pytest.raises(cat.Detenido):
            cat.ciclo(repo_nas, cfg, parar=parar)
        assert repo_nas.pedido(pid)["terminado"] is None
        assert repo_nas.soltar_pedidos_colgados() == 1


class TestServicio:
    def test_atiende_pedidos_por_aviso_y_late(self, cfg, repo_nas, monkeypatch):
        monkeypatch.setattr(cat, "LATIDO_CADA", 0.2)
        parar = threading.Event()
        hilo = threading.Thread(target=cat.servir, args=(cfg, parar), daemon=True)
        hilo.start()
        try:
            _esperar(lambda: repo_nas.estado_catalogado()["carpetas"] == ["Publicar", "Entrada"])
            pid = repo_nas.pedir_catalogado(None, "prueba")
            # ESPERA_PEDIDOS es 30 s: si llega antes, fue por el NOTIFY
            _esperar(lambda: repo_nas.pedido(pid)["terminado"] is not None, segundos=10)
            assert repo_nas.count_media() == 2
            assert repo_nas.estado_catalogado()["segundos_sin_latido"] <= 2
        finally:
            parar.set()
            hilo.join(timeout=40)
        assert not hilo.is_alive()


def _esperar(cond, segundos=5.0):
    fin = time.monotonic() + segundos
    while time.monotonic() < fin:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("no se cumplió a tiempo")


def test_horas_con_zona(repo_nas, cfg):
    """`ultima_fin` es la hora real, en cualquier zona de la base (la de
    desarrollo está en UTC; la del NAS, en Buenos Aires)."""
    repo_nas.pedir_catalogado()
    antes = dt.datetime.now().astimezone()
    cat.ciclo(repo_nas, cfg)
    est = repo_nas.estado_catalogado()
    assert abs((est["ultima_fin"] - antes).total_seconds()) < 30
    assert abs((est["ultima_inicio"] - antes).total_seconds()) < 30


class _CarasFalsas:
    """FaceEngine de mentira: hay cara si la foto es azul."""
    def __init__(self, *a, **k):
        pass

    def reference_feature(self, img):
        import numpy as np
        r, g, b = img.getpixel((0, 0))
        return np.ones(128, dtype=np.float32) if b > 200 else None

    def revisar_referencia(self, img):
        from convertir3x4.engine.faces import RevisionReferencia
        v = self.reference_feature(img)
        if v is None:
            return RevisionReferencia(None)
        return RevisionReferencia(v, 2, (100, 120, 300, 400), 0.93)


def _jpeg(color, lado=2400):
    import io
    buf = io.BytesIO()
    Image.new("RGB", (lado, lado // 2), color).save(buf, "JPEG")
    return buf.getvalue()


class TestFotosDePersonas:
    """La PC sube las fotos; el NAS calcula las firmas (la PC no usa modelos)."""

    @pytest.fixture(autouse=True)
    def _sin_modelos(self, monkeypatch):
        from convertir3x4.engine import ai_models, ai_pipeline, faces
        monkeypatch.setattr(ai_models, "ensure", lambda *a, **k: None)
        monkeypatch.setattr(faces, "FaceEngine", _CarasFalsas)
        monkeypatch.setattr(ai_pipeline, "run_ai", lambda *a, **k: ai_pipeline.AIStats())

    @pytest.fixture
    def cfg_ia(self, cfg):
        return dataclasses.replace(cfg, ia=True)

    def _subir(self, repo, tmp_path):
        from convertir3x4.catalog.thumbs import foto_de_referencia
        rutas = []
        for nombre, color in (("con_cara.jpg", "blue"), ("de_espaldas.jpg", "red")):
            ruta = tmp_path / nombre
            ruta.write_bytes(_jpeg(color))
            rutas.append(str(ruta))
        fotos = [(r, foto_de_referencia(r)) for r in rutas]
        fotos.append((str(tmp_path / "rota.jpg"), b"no es una imagen"))
        return repo.subir_fotos_de_referencia("Juan Carlos Bazán", fotos)

    def test_la_pc_sube_achicado_y_despierta_al_nas(self, repo_nas, cfg, tmp_path, pg_cfg):
        import io
        oyente = catdb.conectar(cfg.catalogo)
        oyente.execute("LISTEN catalogado")
        try:
            self._subir(repo_nas, tmp_path)
            assert any(True for _ in oyente.notifies(timeout=1, stop_after=1))
        finally:
            oyente.close()
        (_, foto), *_ = repo_nas.referencias_pendientes()
        assert max(Image.open(io.BytesIO(foto)).size) == 1600
        # mientras el NAS no la procesa, la persona no cuenta para el reconocimiento
        assert repo_nas.people_signature() == ""
        assert repo_nas.list_persons()[0]["pendientes"] == 3

    def test_el_nas_calcula_las_firmas_en_la_vuelta(self, repo_nas, cfg_ia, tmp_path):
        self._subir(repo_nas, tmp_path)
        assert cat.ciclo(repo_nas, cfg_ia, ahora=D(2026, 11, 22, 15, 0))   # hizo algo
        assert repo_nas.referencias_pendientes() == []
        (p,) = repo_nas.list_persons()
        assert p["listas"] == 1 and p["pendientes"] == 0
        assert p["sin_cara"] == ["de_espaldas.jpg", "rota.jpg"]
        assert len(repo_nas.person_reference_blobs()["Juan Carlos Bazán"]) == 1
        assert repo_nas.people_signature() != ""
        assert repo_nas.count_media() == 0            # no catalogó nada por esto

    def test_en_medio_de_una_pasada_larga(self, repo_nas, cfg_ia, tmp_path, monkeypatch):
        monkeypatch.setattr(cat, "REVISAR_REFERENCIAS", 0)
        llamadas = []
        real = cat.procesar_referencias
        monkeypatch.setattr(cat, "procesar_referencias",
                            lambda *a: (llamadas.append(repo_nas.count_media()), real(*a))[1])
        repo_nas.pedir_catalogado()
        # suben las fotos cuando la pasada ya empezó (antes del primer archivo)
        orig = cat._Avance.__call__

        def avance(self, *a, **k):
            if not llamadas and not repo_nas.referencias_pendientes():
                TestFotosDePersonas._subir(None, repo_nas, tmp_path)
            return orig(self, *a, **k)
        monkeypatch.setattr(cat._Avance, "__call__", avance)
        cat.ciclo(repo_nas, cfg_ia)
        assert repo_nas.referencias_pendientes() == []
        assert llamadas and llamadas[0] < 2           # se atendió con la pasada en curso
        assert repo_nas.pedido(1)["terminado"]

    def test_con_la_ia_apagada_no_se_tocan(self, repo_nas, cfg, tmp_path):
        self._subir(repo_nas, tmp_path)
        cat.ciclo(repo_nas, dataclasses.replace(cfg, ia=False), ahora=D(2026, 11, 22, 15, 0))
        assert len(repo_nas.referencias_pendientes()) == 3

    def test_anota_lo_que_vio_en_cada_foto(self, repo_nas, cfg_ia, tmp_path):
        pid = self._subir(repo_nas, tmp_path)
        cat.procesar_referencias(repo_nas, cfg_ia)
        refs = {r["archivo"]: r for r in repo_nas.detalle_persona(pid)["referencias"]}
        assert refs["con_cara.jpg"]["estado"] == "lista"
        assert refs["con_cara.jpg"]["caras"] == 2
        assert refs["con_cara.jpg"]["caja"] == (100, 120, 300, 400)
        assert refs["con_cara.jpg"]["confianza"] == pytest.approx(0.93)
        assert refs["de_espaldas.jpg"]["caras"] == 0 and refs["de_espaldas.jpg"]["caja"] is None
        assert refs["rota.jpg"]["estado"] == "ilegible"

    def test_completa_las_de_antes_sin_repasar_el_archivo(self, repo_nas, cfg_ia, tmp_path):
        """Fotos procesadas con la versión anterior (sin `caras`): el NAS las revisa
        sin tocar la firma ni el estado, así la firma de la lista no cambia y no se
        repasan las caras de todo el catálogo."""
        pid = self._subir(repo_nas, tmp_path)
        cat.procesar_referencias(repo_nas, cfg_ia)
        repo_nas.conn.execute("UPDATE person_reference SET caras = NULL, cara_caja = NULL, "
                              "confianza = NULL")
        antes = repo_nas.people_signature()
        blobs = repo_nas.person_reference_blobs()
        assert repo_nas.hay_referencias_pendientes()
        assert len(repo_nas.referencias_sin_revisar()) == 3       # las tres tienen su foto
        assert cat.procesar_referencias(repo_nas, cfg_ia) == 3
        assert repo_nas.people_signature() == antes
        assert repo_nas.person_reference_blobs() == blobs
        refs = {r["archivo"]: r for r in repo_nas.detalle_persona(pid)["referencias"]}
        assert refs["con_cara.jpg"]["caras"] == 2 and refs["con_cara.jpg"]["estado"] == "lista"
        assert refs["de_espaldas.jpg"]["estado"] == "sin_cara"
        assert refs["rota.jpg"]["estado"] == "ilegible" and refs["rota.jpg"]["caras"] == 0
        assert not repo_nas.hay_referencias_pendientes()
        assert repo_nas.referencias_sin_revisar() == []
