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
