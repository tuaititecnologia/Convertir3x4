"""Carpetas del NAS y conexión al catálogo (docs/carpetas-y-conexion.md).

Las marcas y el árbol son puros. Lo demás corre contra el PostgreSQL de
desarrollo (conftest.py), con una carpeta temporal como raíz del NAS.
"""

import dataclasses
import datetime as dt
import os
import time

import psycopg
import pytest
from PIL import Image

from convertir3x4 import catalogador as cat
from convertir3x4.catalog import db as catdb
from convertir3x4.catalog.alcance import Alcance, Arbol, es_de_sistema, minimas
from convertir3x4.catalog.models import Media
from convertir3x4.catalog.repo import Repo
from convertir3x4.engine import inventory


# El ejemplo del documento:
#   /
#     Fotos / Día 1, Día 2 / Temporal
#     Videos / Terminados
CARPETAS = ["Fotos", "Fotos/Día 1", "Fotos/Día 2", "Fotos/Día 2/Temporal",
            "Videos", "Videos/Terminados"]


class TestAlcance:
    def test_sin_marcas_no_se_cataloga_nada(self):
        a = Alcance()
        assert not a
        assert a.raices() == ()
        assert not a.estado("Fotos")
        assert a.describir() == []

    def test_todo_menos_una(self):
        a = Alcance({"": True, "Videos/Terminados": False})
        assert a.raices() == ("",)
        assert a.excluidas() == ("Videos/Terminados",)
        assert a.estado("Videos") and not a.estado("Videos/Terminados")
        assert not a.estado("Videos/Terminados/Viejos")        # hereda
        assert a.estado("Carpeta nueva")                       # una nueva entra sola
        assert a.incluye_archivo("Videos/a.mp4")               # sueltos de Videos
        assert not a.incluye_archivo("Videos/Terminados/b.mp4")
        assert a.incluye_archivo("suelta.jpg")                 # suelta en la raíz
        assert a.describir() == ["Toda la carpeta multimedia (excepto Videos/Terminados)"]

    def test_solo_una_honda(self):
        a = Alcance({"Fotos/Día 2/Temporal": True})
        assert a.raices() == ("Fotos/Día 2/Temporal",)
        assert not a.incluye_archivo("Fotos/a.jpg")            # sueltos de una de paso
        assert not a.incluye_archivo("Fotos/Día 2/a.jpg")
        assert a.incluye_archivo("Fotos/Día 2/Temporal/x/a.jpg")
        assert a.raiz_de("Fotos/Día 2/Temporal/x") == "Fotos/Día 2/Temporal"
        assert a.raiz_de("Fotos") is None

    def test_vuelve_a_entrar_dentro_de_una_excluida(self):
        a = Alcance({"": True, "A": False, "A/B": True, "A/B/C": False})
        assert a.raices() == ("", "A/B")
        assert a.excluidas() == ("A", "A/B/C")
        assert a.raiz_de("A/B/x") == "A/B"
        assert a.describir() == ["Toda la carpeta multimedia (excepto A)", "A/B (excepto A/B/C)"]

    def test_las_marcas_repetidas_se_borran(self):
        assert minimas({"": True, "Fotos": True, "Fotos/Día 1": False, "Fotos/Día 1/x": False,
                        "Videos": False}) == {"": True, "Fotos/Día 1": False, "Videos": False}
        assert Alcance({"Fotos": False}).marcas == {}           # la raíz ya es «no»
        assert Alcance({"Publicar\\2026\\": True}).marcas == {"Publicar/2026": True}

    def test_lista_vieja(self):
        """Lo que había antes del esquema 10: carpetas incluidas, sin anidar."""
        a = Alcance({"Entrada": True, "Publicar": True})
        assert a.raices() == ("Entrada", "Publicar")
        assert a.describir() == ["Entrada", "Publicar"]

    def test_carpetas_de_sistema(self):
        for n in (".zfs", ".recycle", "@eaDir", "#recycle", "$RECYCLE.BIN",
                  "System Volume Information", "ix-applications"):
            assert es_de_sistema(n), n
        for n in ("Fotos", "Historial", "eaDir", "recycle"):
            assert not es_de_sistema(n), n


class TestArbol:
    def test_caso_a_destildar_una_hija(self):
        t = Arbol(CARPETAS)
        t.cambiar("", True)
        assert all(t.estados.values())
        t.cambiar("Videos/Terminados", False)
        assert t.estados["Videos"] and t.estados[""]
        assert t.marcas() == {"": True, "Videos/Terminados": False}
        c = t.contrarias_adentro()
        assert c == {"Videos": 1, "": 1}

    def test_caso_b_solo_temporal(self):
        t = Arbol(CARPETAS)
        t.cambiar("Fotos/Día 2/Temporal", True)
        assert not t.estados["Fotos"] and not t.estados["Fotos/Día 2"]
        assert t.contrarias_adentro() == {"Fotos/Día 2": 1, "Fotos": 1, "": 1}

    def test_tildar_la_madre_cambia_todo_lo_de_adentro(self):
        t = Arbol(CARPETAS, marcas={"": True, "Fotos/Día 1": False})
        t.cambiar("Fotos", False)
        assert not any(t.estados[c] for c in ("Fotos", "Fotos/Día 1", "Fotos/Día 2",
                                               "Fotos/Día 2/Temporal"))
        t.cambiar("Fotos", True)
        assert t.marcas() == {"": True}

    def test_con_filtro_solo_cambia_lo_que_se_ve(self):
        t = Arbol(CARPETAS)
        visibles = t.filtrar("temp")
        assert visibles == {"", "Fotos", "Fotos/Día 2", "Fotos/Día 2/Temporal"}
        t.cambiar("Fotos", True, visibles)
        assert t.estados["Fotos"] and t.estados["Fotos/Día 2"] and t.estados["Fotos/Día 2/Temporal"]
        assert not t.estados["Fotos/Día 1"]                    # no se veía
        assert t.marcas() == {"Fotos": True, "Fotos/Día 1": False}
        assert t.contrarias_adentro()["Fotos"] == 1

    def test_el_filtro_muestra_lo_de_adentro_de_lo_encontrado(self):
        t = Arbol(CARPETAS + ["Fotos/Día 2/Temporal/Crudo"])
        assert "Fotos/Día 2/Temporal/Crudo" in t.filtrar("temp")
        assert t.filtrar("  ") is None
        assert t.filtrar("DIA 1") == {"", "Fotos", "Fotos/Día 1"}     # sin tildes ni mayúsculas

    def test_carpeta_marcada_que_ya_no_esta(self):
        t = Arbol(["Fotos"], marcas={"Viejo/Evento": True})
        assert "Viejo/Evento" in t.hijos and "Viejo" in t.hijos
        assert "Viejo/Evento" not in t.en_nas
        assert t.estados["Viejo/Evento"]

    def test_cantidades_suman_hacia_arriba(self):
        t = Arbol(CARPETAS, conteos={"Fotos/Día 1": (3, 0), "Fotos": (1, 1), "": (2, 0)})
        assert t.totales("Fotos") == (4, 1)
        assert t.totales("") == (6, 1)
        assert t.totales("Videos") == (0, 0)

    def test_orden_del_arbol(self):
        t = Arbol(["b", "a", "a/z", "a/Á"])
        assert t.carpetas() == ["", "a", "a/Á", "a/z", "b"]


# ------------------------------------------------------------------ con la base
def _medio(repo, rel, kind="image"):
    return repo.upsert_media(Media(path="", rel_path=rel, filename=os.path.basename(rel),
                                   folder_origin=os.path.dirname(rel), kind=kind))


def _foto(raiz, rel):
    p = os.path.join(raiz, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    Image.new("RGB", (900, 600), "red").save(p)


@pytest.fixture
def raiz(tmp_path):
    for rel in ("Fotos/suelta.jpg", "Fotos/Día 1/a.jpg", "Fotos/Día 2/b.jpg",
                "Fotos/Día 2/Temporal/c.jpg", "Videos/d.jpg", "Videos/Terminados/e.jpg",
                "Fotos/@eaDir/miniatura.jpg", "#recycle/borrada.jpg", "en_la_raiz.jpg"):
        _foto(str(tmp_path), rel)
    return str(tmp_path)


@pytest.fixture
def cfg(pg_cfg, raiz):
    return cat.ConfigCatalogador(catalogo=dataclasses.replace(pg_cfg, raiz=raiz), ia=False)


@pytest.fixture
def nas(cfg):
    r = Repo(catdb.conectar(cfg.catalogo), cfg.catalogo.raiz)
    yield r
    r.close()


def _catalogados(repo):
    return sorted(r["rel_path"] for r in repo.conn.execute(
        "SELECT rel_path FROM media WHERE missing_since IS NULL"))


def _pasada(repo, cfg):
    repo.pedir_catalogado()
    cat.ciclo(repo, cfg, ahora=dt.datetime(2026, 11, 24, 15, 0))


class TestMarcasEnLaBase:
    def test_migracion_conserva_la_lista(self, pg_cfg):
        """Las carpetas de antes del esquema 10 quedan tildadas."""
        conn = catdb.conectar(pg_cfg, preparar=False)
        try:
            with conn.transaction():
                conn.execute("CREATE TABLE esquema (version integer PRIMARY KEY,"
                             " aplicada timestamptz NOT NULL DEFAULT now())")
                for v, sql in catdb.MIGRACIONES[:9]:
                    conn.execute(sql)
                    conn.execute("INSERT INTO esquema (version) VALUES (%s)", (v,))
                conn.execute("INSERT INTO carpeta_catalogada (ruta) VALUES ('Entrada'), ('Publicar')")
            catdb.preparar_esquema(conn)
            r = Repo(conn, "/")
            assert r.marcas_carpetas() == {"Entrada": True, "Publicar": True}
            assert r.carpetas_catalogadas() == ("Entrada", "Publicar")
        finally:
            conn.close()

    def test_guardar_marcas_y_lo_que_sale(self, repo):
        a = _medio(repo, "Videos/Terminados/a.mp4", "video")
        b = _medio(repo, "Videos/Terminados/b.jpg")
        c = _medio(repo, "Videos/c.mp4", "video")
        d = _medio(repo, "Fotos/d.jpg")
        repo.guardar_marcas({"": True, "Fotos": True}, por="PC")   # la de Fotos sobra
        assert repo.marcas_carpetas() == {"": True}
        nuevas = {"": True, "Videos/Terminados": False}
        assert repo.medios_que_salen(nuevas) == {"Videos/Terminados": (1, 1)}
        assert repo.medios_que_salen({"Fotos": True}) == {"": (1, 2)}     # se destildó la raíz
        assert repo.guardar_marcas(nuevas) == []                       # se dejan
        assert repo.get_media(a) is not None
        assert repo.alcance() == Alcance(nuevas)
        repo.guardar_marcas({"": True})
        quitados = repo.guardar_marcas({"": True, "Videos": False}, eliminar_medios=True)
        assert sorted(quitados) == sorted([a, b, c])
        assert repo.get_media(a) is None and repo.get_media(c) is None
        assert repo.get_media(d) is not None

    def test_la_raiz_como_carpeta(self, repo):
        a = _medio(repo, "x/a.jpg")
        b = _medio(repo, "b.jpg")
        assert sorted(repo.medios_en_carpeta("")) == sorted([a, b])
        assert {p.media.id for p in repo.pendientes_de_ia("m", "s", carpetas=[""])} == {a, b}
        assert {m.id for m in repo.fotos_sin_texto(1, carpetas=[""])} == {a, b}

    def test_carpetas_del_nas(self, repo):
        oyente = psycopg.connect(repo.conn.info.dsn, password=repo.conn.info.password,
                                 autocommit=True)
        try:
            oyente.execute(f"LISTEN {catdb.CANAL_CARPETAS_LEIDAS}")
            assert repo.reemplazar_carpetas_nas(["Fotos", "Fotos/Día 1", "Videos"]) == (3, 0)
            assert repo.reemplazar_carpetas_nas(["Fotos", "Videos", "Nueva"]) == (1, 1)
            assert repo.reemplazar_carpetas_nas(["Fotos", "Videos", "Nueva"]) == (0, 0)
            avisos = list(oyente.notifies(timeout=1))
            assert len(avisos) == 3            # avisa siempre, aunque no cambie nada
        finally:
            oyente.close()
        _medio(repo, "Fotos/a.jpg")
        _medio(repo, "Fotos/b.mp4", "video")
        _medio(repo, "raiz.jpg")
        faltante = _medio(repo, "Fotos/c.jpg")
        repo.marcar_faltantes([faltante])
        repo.guardar_marcas({"Fotos": True})
        t = repo.arbol_carpetas()
        assert sorted(t["carpetas"]) == ["Fotos", "Nueva", "Videos"]
        assert t["leidas"] is not None
        assert t["marcas"] == {"Fotos": True}
        assert t["conteos"] == {"Fotos": (1, 1), "": (1, 0)}


class TestRecorrido:
    def test_leer_carpetas(self, raiz):
        os.symlink(os.path.join(raiz, "Fotos"), os.path.join(raiz, "enlace"))
        assert inventory.leer_carpetas(raiz) == [
            "Fotos", "Fotos/Día 1", "Fotos/Día 2", "Fotos/Día 2/Temporal", "Videos",
            "Videos/Terminados"]

    def test_todo_menos_terminados(self, nas, cfg):
        nas.guardar_marcas({"": True, "Videos/Terminados": False})
        _pasada(nas, cfg)
        assert _catalogados(nas) == [
            "Fotos/Día 1/a.jpg", "Fotos/Día 2/Temporal/c.jpg", "Fotos/Día 2/b.jpg",
            "Fotos/suelta.jpg", "Videos/d.jpg", "en_la_raiz.jpg"]     # sin @eaDir ni #recycle

    def test_solo_temporal_y_una_carpeta_nueva_entra_sola(self, nas, cfg, raiz):
        nas.guardar_marcas({"Fotos/Día 2/Temporal": True})
        _pasada(nas, cfg)
        assert _catalogados(nas) == ["Fotos/Día 2/Temporal/c.jpg"]
        _foto(raiz, "Fotos/Día 2/Temporal/Nueva/n.jpg")
        _pasada(nas, cfg)
        assert "Fotos/Día 2/Temporal/Nueva/n.jpg" in _catalogados(nas)

    def test_lo_excluido_que_queda_no_se_marca_faltante(self, nas, cfg, raiz):
        nas.guardar_marcas({"Videos": True})
        _pasada(nas, cfg)
        assert "Videos/Terminados/e.jpg" in _catalogados(nas)
        nas.guardar_marcas({"Videos": True, "Videos/Terminados": False})   # se dejan
        _pasada(nas, cfg)
        assert "Videos/Terminados/e.jpg" in _catalogados(nas)
        os.remove(os.path.join(raiz, "Videos", "d.jpg"))
        _pasada(nas, cfg)
        assert _catalogados(nas) == ["Videos/Terminados/e.jpg"]       # d sí falta

    def test_una_tildada_dentro_de_una_excluida_no_pierde_sus_etiquetas(self, nas, cfg, raiz):
        """Temporal es otra raíz: al recorrer la de arriba (que no entra en Fotos)
        no se marca como faltante, y una copia en otra carpeta no se toma por
        un archivo movido."""
        import shutil
        nas.guardar_marcas({"": True, "Fotos": False, "Fotos/Día 2/Temporal": True})
        _pasada(nas, cfg)
        original = nas.get_media_by_path(os.path.join(raiz, "Fotos", "Día 2", "Temporal", "c.jpg"))
        nas.apply_tag(original.id, "culto")
        shutil.copy2(os.path.join(raiz, "Fotos", "Día 2", "Temporal", "c.jpg"),
                     os.path.join(raiz, "Videos", "c.jpg"))
        _pasada(nas, cfg)
        assert nas.get_media(original.id).rel_path == "Fotos/Día 2/Temporal/c.jpg"
        copia = nas.get_media_by_path(os.path.join(raiz, "Videos", "c.jpg"))
        assert copia is not None and copia.id != original.id
        assert "Fotos/Día 2/Temporal/c.jpg" in _catalogados(nas)

    def test_subcarpeta_pedida(self, nas, cfg):
        nas.guardar_marcas({"": True, "Fotos/Día 2/Temporal": False})
        cat.pasada(nas, cfg, carpeta="Fotos/Día 2")
        assert _catalogados(nas) == ["Fotos/Día 2/b.jpg"]
        with pytest.raises(ValueError):
            cat.pasada(nas, cfg, carpeta="Fotos/Día 2/Temporal")

    def test_la_pasada_relee_las_carpetas(self, nas, cfg):
        nas.guardar_marcas({"Fotos": True})
        _pasada(nas, cfg)
        assert "Videos/Terminados" in nas.arbol_carpetas()["carpetas"]


class TestRapidez:
    def test_tildar_en_un_arbol_grande(self):
        """Cada clic recalcula todo el árbol: tiene que seguir siendo instantáneo
        con miles de carpetas (Historial)."""
        carpetas = [f"H/{a}/{b}/{c}" for a in range(20) for b in range(15) for c in range(16)]
        t = Arbol(carpetas, marcas={"": True})
        assert len(t.hijos) > 5000
        inicio = time.perf_counter()
        for i in range(5):
            t.cambiar(f"H/{i}", False)
            t.marcas()
            t.contrarias_adentro()
        assert (time.perf_counter() - inicio) / 5 < 0.25


class TestLectorDeCarpetas:
    def test_relee_cuando_una_pc_lo_pide(self, nas, cfg, raiz, monkeypatch):
        monkeypatch.setattr(cat, "ESPERA_RECONEXION", 0.2)
        lector = cat.LectorDeCarpetas(cfg.catalogo)
        lector.start()
        try:
            def leidas():
                return nas.arbol_carpetas()["carpetas"]
            t = time.time()
            while "Fotos" not in leidas() and time.time() - t < 10:   # lee al arrancar
                time.sleep(0.05)
            assert "Fotos" in leidas()
            os.makedirs(os.path.join(raiz, "Culto de hoy"))
            time.sleep(0.3)
            nas.pedir_carpetas_nas()
            t = time.time()
            while "Culto de hoy" not in leidas() and time.time() - t < 10:
                time.sleep(0.05)
            assert "Culto de hoy" in leidas()
        finally:
            lector.parar()
            lector.join(5)
        assert not lector.is_alive()


# ------------------------------------------------------------------ interfaz
@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication
    from convertir3x4.ui.style import apply_theme
    a = QApplication.instance() or QApplication([])
    apply_theme(a, "dark")
    return a


def _esperar(app, cond, segundos=10):
    t = time.time()
    while not cond() and time.time() - t < segundos:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return cond()


@pytest.fixture
def srv(pg_cfg, tmp_path):
    from convertir3x4.ui.servicio_catalogo import ServicioCatalogo
    s = ServicioCatalogo(abrir=lambda: Repo(catdb.conectar(pg_cfg), str(tmp_path)))
    yield s
    s.cerrar()


def _abrir(pg_cfg, tmp_path):
    return Repo(catdb.conectar(pg_cfg), str(tmp_path))


class TestVentanaDeCarpetas:
    def test_tildar_filtrar_y_guardar(self, app, srv, pg_cfg, tmp_path, monkeypatch):
        from PyQt6.QtCore import Qt
        from convertir3x4.ui.avisos_catalogo import hub
        from convertir3x4.ui.widgets import arbol_carpetas as ac
        monkeypatch.setattr(ac, "SIN_RESPUESTA_MS", 300)
        with _abrir(pg_cfg, tmp_path) as r:
            r.reemplazar_carpetas_nas(CARPETAS)
            r.guardar_marcas({"Videos": True})
            e = _medio(r, "Videos/Terminados/e.mp4", "video")
            _medio(r, "Videos/d.jpg")
        d = ac.ElegirCarpetas(srv=srv)
        d.show()
        try:
            assert _esperar(app, lambda: "Videos/Terminados" in d._items)
            assert "1 foto y 1 video" in d._items["Videos"].text(1)
            assert d._items["Videos"].checkState(0) == Qt.CheckState.Checked
            assert d._items["Videos/Terminados"].checkState(0) == Qt.CheckState.Checked
            assert not d.btn_guardar.isEnabled()
            # el NAS no contesta (no hay catalogador en la prueba)
            assert _esperar(app, lambda: "no respondió" in d.lbl_lectura.text(), 3)
            # destildar una hija con la casilla misma
            d._items["Videos/Terminados"].setCheckState(0, Qt.CheckState.Unchecked)
            assert d._arbol.estados["Videos"]
            assert "1 excluida adentro" in d._items["Videos"].text(1)
            assert "Videos (excepto Videos/Terminados)" in d.lbl_resumen.text()
            assert d.btn_guardar.isEnabled()
            # con el filtro, tildar Fotos no toca Día 1 (no se ve)
            d.txt_filtro.setText("temp")
            assert d._items["Fotos/Día 1"].isHidden()
            assert not d._items["Fotos/Día 2/Temporal"].isHidden()
            d.tildar("Fotos", True)
            d.txt_filtro.setText("")
            assert not d._items["Fotos/Día 1"].isHidden()
            assert d._items["Fotos/Día 1"].checkState(0) == Qt.CheckState.Unchecked
            # el NAS leyó una carpeta nueva: aparece y lo cambiado sigue
            with _abrir(pg_cfg, tmp_path) as r:
                r.reemplazar_carpetas_nas(CARPETAS + ["Fotos/Culto de hoy"])
            hub().carpetas.emit()
            assert _esperar(app, lambda: "Fotos/Culto de hoy" in d._items)
            assert d._arbol.estados["Fotos/Culto de hoy"]         # hereda de Fotos
            assert not d._arbol.estados["Videos/Terminados"]
            assert "leídas hoy" in d.lbl_lectura.text()
            # guardar: Terminados queda afuera con lo que tiene; se elige dejarlo
            pedidos = []
            monkeypatch.setattr(d, "confirmar", lambda a, salen: pedidos.append(salen) or "dejar")
            guardado = []
            d.guardado.connect(lambda: guardado.append(1))
            d.guardar()
            assert _esperar(app, lambda: guardado)
            assert pedidos == [{"Videos/Terminados": (0, 1)}]
            with _abrir(pg_cfg, tmp_path) as r:
                assert r.marcas_carpetas() == {"Videos": True, "Videos/Terminados": False,
                                               "Fotos": True, "Fotos/Día 1": False}
                assert r.get_media(e) is not None
        finally:
            d.deleteLater()

    def test_eliminar_lo_que_sale(self, app, srv, pg_cfg, tmp_path, monkeypatch):
        from convertir3x4.ui.widgets import arbol_carpetas as ac
        monkeypatch.setattr("convertir3x4.appdata.catalog_thumb_dir", lambda: str(tmp_path / "c"))
        with _abrir(pg_cfg, tmp_path) as r:
            r.reemplazar_carpetas_nas(CARPETAS)
            r.guardar_marcas({"Videos": True})
            e = _medio(r, "Videos/Terminados/e.mp4", "video")
        d = ac.ElegirCarpetas(srv=srv)
        d.show()
        try:
            assert _esperar(app, lambda: "Videos/Terminados" in d._items)
            d.tildar("Videos/Terminados", False)
            monkeypatch.setattr(d, "confirmar", lambda a, salen: "eliminar")
            guardado = []
            d.guardado.connect(lambda: guardado.append(1))
            d.guardar()
            assert _esperar(app, lambda: guardado)
            with _abrir(pg_cfg, tmp_path) as r:
                assert r.get_media(e) is None
        finally:
            d.deleteLater()

    def test_cancelar_en_el_resumen_no_guarda(self, app, srv, pg_cfg, tmp_path, monkeypatch):
        from convertir3x4.ui.widgets import arbol_carpetas as ac
        with _abrir(pg_cfg, tmp_path) as r:
            r.reemplazar_carpetas_nas(CARPETAS)
        d = ac.ElegirCarpetas(srv=srv)
        d.show()
        try:
            assert _esperar(app, lambda: "Fotos" in d._items)
            d.tildar("", True)
            preguntado = []
            monkeypatch.setattr(d, "confirmar", lambda a, salen: preguntado.append(1))
            d.guardar()
            assert _esperar(app, lambda: preguntado)
            assert _esperar(app, lambda: d.btn_guardar.isEnabled())
            with _abrir(pg_cfg, tmp_path) as r:
                assert r.marcas_carpetas() == {}
        finally:
            d.deleteLater()

    def test_una_carpeta_solo_de_las_que_se_catalogan(self, app, srv, pg_cfg, tmp_path):
        from convertir3x4.ui.widgets import arbol_carpetas as ac
        with _abrir(pg_cfg, tmp_path) as r:
            r.reemplazar_carpetas_nas(CARPETAS)
            r.guardar_marcas({"Fotos": True, "Fotos/Día 1": False})
        d = ac.ElegirUnaCarpeta(srv=srv)
        d.show()
        try:
            assert _esperar(app, lambda: "Fotos/Día 2" in d._items)
            assert not d.elegir("Videos") and not d.btn_ok.isEnabled()
            assert not d.elegir("Fotos/Día 1")
            assert d.elegir("Fotos/Día 2") and d.btn_ok.isEnabled()
            d._aceptar()
            assert d.carpeta == "Fotos/Día 2"
        finally:
            d.deleteLater()


class TestAvisosDeCarpetas:
    def test_la_relectura_no_hace_consultar_a_buscar(self, app, pg_cfg):
        from convertir3x4.ui.avisos_catalogo import AvisosCatalogo, hub
        avisos = AvisosCatalogo(abrir=lambda: Repo(catdb.conectar(pg_cfg), "/"))
        cambios, carpetas = [], []
        avisos.cambio.connect(lambda: cambios.append(1))
        hub().carpetas.connect(lambda: carpetas.append(1))     # lo que escucha la ventana
        avisos.start()
        try:
            time.sleep(0.5)
            with Repo(catdb.conectar(pg_cfg), "/") as r:
                r.reemplazar_carpetas_nas(["Fotos"])
            assert _esperar(app, lambda: carpetas)
            time.sleep(0.3)
            app.processEvents()
            assert cambios == []
            with Repo(catdb.conectar(pg_cfg), "/") as r:
                _medio(r, "Fotos/a.jpg")
            assert _esperar(app, lambda: cambios)
        finally:
            avisos.detener()


class TestConexion:
    @pytest.fixture
    def sin_entorno(self, monkeypatch, tmp_path):
        """El catalogo.ini va a una carpeta temporal: nunca al del repositorio."""
        from convertir3x4 import appdata
        for k in list(os.environ):
            if k.startswith("CDLM_CATALOGO_"):
                monkeypatch.delenv(k)
        junto, datos = tmp_path / "junto" / "catalogo.ini", tmp_path / "datos" / "catalogo.ini"
        junto.parent.mkdir()
        monkeypatch.setattr(appdata, "rutas_config_catalogo", lambda: [str(junto), str(datos)])
        return junto, datos

    def test_guardar_y_leer(self, sin_entorno):
        from convertir3x4 import appdata
        junto, datos = sin_entorno
        assert appdata.origen_config_catalogo() == ("ninguno", None)
        cfg = catdb.ConfigCatalogo(servidor="10.0.0.5", clave="a%b;c", raiz=r"Z:\multimedia",
                                   puerto=5433)
        assert appdata.guardar_config_catalogo(cfg) == str(junto)
        assert appdata.origen_config_catalogo() == ("archivo", str(junto))
        assert appdata.config_catalogo() == cfg
        assert not os.path.exists(str(junto) + ".nuevo")

    def test_sin_permiso_junto_al_exe_va_a_los_datos(self, sin_entorno, monkeypatch):
        from convertir3x4 import appdata
        junto, datos = sin_entorno
        os.chmod(junto.parent, 0o500)
        try:
            cfg = catdb.ConfigCatalogo(servidor="10.0.0.5")
            assert appdata.guardar_config_catalogo(cfg) == str(datos)
            assert appdata.config_catalogo() == cfg
        finally:
            os.chmod(junto.parent, 0o700)

    def test_con_variables_no_se_guarda(self, sin_entorno, monkeypatch):
        from convertir3x4 import appdata
        monkeypatch.setenv("CDLM_CATALOGO_SERVIDOR", "127.0.0.1")
        assert appdata.ruta_para_guardar_config() is None
        with pytest.raises(PermissionError):
            appdata.guardar_config_catalogo(catdb.ConfigCatalogo(servidor="x"))

    def test_probar(self, pg_cfg, tmp_path):
        from convertir3x4.catalog import conexion
        (tmp_path / "Publicar").mkdir()
        cfg = dataclasses.replace(pg_cfg, raiz=str(tmp_path))
        with Repo(catdb.conectar(cfg), cfg.raiz) as r:
            r.guardar_marcas({"Publicar": True})
        bien, texto = conexion.probar(cfg)
        assert bien and "Todo está bien" in texto
        bien, texto = conexion.probar(dataclasses.replace(cfg, clave="otra"))
        assert not bien and "usuario o la clave" in texto
        bien, texto = conexion.probar(dataclasses.replace(cfg, raiz=str(tmp_path / "no")))
        assert not bien and "no encuentra la carpeta" in texto
        (tmp_path / "otra").mkdir()
        bien, texto = conexion.probar(dataclasses.replace(cfg, raiz=str(tmp_path / "otra")))
        assert not bien and "(Publicar)" in texto
        bien, texto = conexion.probar(dataclasses.replace(cfg, servidor="127.0.0.1", puerto=1))
        assert not bien and "No se pudo llegar" in texto

    def test_probar_no_migra(self, pg_cfg, tmp_path):
        from convertir3x4.catalog import conexion
        conn = catdb.conectar(pg_cfg, preparar=False)
        try:
            with conn.transaction():
                conn.execute("CREATE TABLE esquema (version integer PRIMARY KEY,"
                             " aplicada timestamptz NOT NULL DEFAULT now())")
                for v, sql in catdb.MIGRACIONES[:9]:
                    conn.execute(sql)
                    conn.execute("INSERT INTO esquema (version) VALUES (%s)", (v,))
            bien, texto = conexion.probar(dataclasses.replace(pg_cfg, raiz=str(tmp_path)))
            assert bien and "se actualiza" in texto
            assert catdb.version_esquema(conn) == 9
        finally:
            conn.close()

    def test_pantalla(self, app, sin_entorno):
        from convertir3x4.ui.widgets.conexion_catalogo import ConexionCatalogo
        guardados = []
        d = ConexionCatalogo(cfg=None, guardar=lambda c: guardados.append(c) or "/x/catalogo.ini",
                             probar=lambda c: (True, f"probado {c.servidor}"), destino="/x")
        try:
            assert d.txt_raiz.text() == r"\\truenas\multimedia"
            d.guardar()
            assert guardados == [] and "Escriba el servidor" in d.lbl_resultado.text()
            d.txt_servidor.setText(" 192.168.1.224 ")
            d.txt_clave.setText("secreta")
            assert d.txt_clave.echoMode() == d.txt_clave.EchoMode.Password
            d.btn_ver.setChecked(True)
            assert d.txt_clave.echoMode() == d.txt_clave.EchoMode.Normal
            d.probar()
            assert _esperar(app, lambda: "probado 192.168.1.224" in d.lbl_resultado.text())
            d.guardar()
            assert guardados[0].servidor == "192.168.1.224" and guardados[0].clave == "secreta"
            assert guardados[0].puerto == 5432 and guardados[0].usuario == "catalogo"
        finally:
            d.deleteLater()
        d = ConexionCatalogo(cfg=catdb.ConfigCatalogo(servidor="127.0.0.1"),
                             origen=("entorno", None), destino="")
        try:
            assert not d.btn_guardar.isEnabled()
        finally:
            d.deleteLater()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
