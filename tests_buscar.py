"""Búsqueda con operadores, como la de Gmail (pedido del 24/11/2026).

El intérprete (`catalog/consulta.py`) se prueba solo; la búsqueda en la base,
con el PostgreSQL de desarrollo (conftest.py); el formulario, con Qt offscreen.
"""

import datetime as dt
import os

import pytest

from convertir3x4.catalog import consulta
from convertir3x4.catalog.consulta import analizar, armar, periodo_de
from convertir3x4.catalog.models import Media
from convertir3x4.catalog.repo import Repo


# ---------------------------------------------------------------- el intérprete

class TestAnalizar:
    def test_palabras_y_filtros(self):
        c = analizar("culto etiqueta:placa tipo:videos carpeta:Reuniones")
        assert c.palabras == ["culto"]
        assert [(f.clave, f.valor) for f in c.filtros] == [
            ("etiqueta", "placa"), ("tipo", "videos"), ("carpeta", "Reuniones")]
        assert c.tipo == "video" and not c.avisos

    def test_alias_comillas_y_exclusiones(self):
        c = analizar('tag:"santa  cena" etiqueta:Ramírez -etiqueta:retiro -ensayo santa-cena')
        assert [(f.clave, f.valor, f.negado) for f in c.filtros] == [
            ("etiqueta", "santa cena", False), ("etiqueta", "Ramírez", False),
            ("etiqueta", "retiro", True)]
        assert c.palabras == ["santa-cena"] and c.excluidas == ["ensayo"]
        # persona: no es un operador (se quitó): queda como palabra
        assert analizar("persona:Ramírez").palabras == ["persona:Ramírez"]

    def test_lo_que_no_es_operador_queda_entero(self):
        # Nombres con hora y rutas pegadas se siguen buscando como siempre
        c = analizar(r"Culto 10:30 C:\Fotos\Retiro foo:bar")
        assert c.palabras == ["Culto", "10:30", r"C:\Fotos\Retiro", "foo:bar"]
        assert not c.filtros

    def test_operador_a_medio_escribir_no_hace_nada(self):
        c = analizar("culto etiqueta:")
        assert c.palabras == ["culto"] and not c.filtros and not c.avisos

    def test_fechas(self):
        assert periodo_de("21/09/2025") == (dt.date(2025, 9, 21),) * 2
        assert periodo_de("1/9/25") == (dt.date(2025, 9, 1),) * 2
        assert periodo_de("2025-09-21") == (dt.date(2025, 9, 21),) * 2
        assert periodo_de("02/2024") == (dt.date(2024, 2, 1), dt.date(2024, 2, 29))
        assert periodo_de("2025") == (dt.date(2025, 1, 1), dt.date(2025, 12, 31))
        for malo in ("32/13/2025", "ayer", "2025/09", "13/2025"):
            assert periodo_de(malo) is None, malo

    def test_lo_que_no_se_entiende_se_avisa(self):
        c = analizar("desde:32/13/2025 tipo:pdf tiene:color culto")
        assert c.palabras == ["culto"] and not c.filtros
        assert len(c.avisos) == 3 and "32/13/2025" in c.avisos[0]
        assert c.invalidos == ["desde:32/13/2025", "tipo:pdf", "tiene:color"]

    def test_ida_y_vuelta(self):
        for texto in ('culto "dia del padre" -ensayo etiqueta:"santa cena" -etiqueta:retiro '
                      'tipo:video carpeta:Historial desde:01/09/2025 hasta:2025 tiene:notas '
                      'sin:etiquetas',
                      "10:30 nombre:DSC texto:vigilia notas:pastor fecha:09/2025",
                      "antes:2025 después:01/09/2024",
                      'lugar:blessedland evento:"santa cena" tiene:ubicación -evento:retiro'):
            assert armar(analizar(texto)) == texto
            assert armar(analizar(armar(analizar(texto)))) == texto
        assert armar(analizar("tag:placa tipo:fotos")) == "etiqueta:placa tipo:foto"
        assert armar(analizar("despues:2024")) == "después:2024"
        assert "antes:13/2025" in analizar("antes:13/2025").invalidos

    def test_sin_tipo_deja_el_resto_como_estaba(self):
        assert consulta.sin_tipo("culto tipo:video desde:99 -etiqueta:x") == \
            "culto desde:99 -etiqueta:x"
        assert consulta.sin_tipo("culto") == "culto"


class TestSugerencias:
    E = ["alabanza", "placa", "placa grande", "santa cena"]

    def _s(self, texto, cursor=None):
        return consulta.sugerencias(texto, len(texto) if cursor is None else cursor, self.E)

    def test_claves(self):
        assert self._s("car") == (0, ["carpeta:"])
        assert self._s("culto eti") == (6, ["etiqueta:"])
        assert self._s("-eti") == (0, ["-etiqueta:"])
        assert self._s("sin") == (0, ["sin:"])
        assert self._s("desp") == (0, ["después:"])                   # sin tilde también
        assert self._s("des") == (0, ["desde:", "después:"])
        assert self._s("lug") == (0, ["lugar:"]) and self._s("eve") == (0, ["evento:"])
        assert self._s("ca") == (0, []) and self._s("et") == (0, [])   # menos de 3: nada
        assert self._s("culto") == (0, [])
        assert self._s("tag") == (0, [])                     # tag: se entiende, no se ofrece

    def test_valores(self):
        assert self._s("etiqueta:pl")[1] == ["etiqueta:placa", 'etiqueta:"placa grande"']
        assert self._s("tag:cena")[1] == ['tag:"santa cena"']          # también «contiene»
        assert self._s("tipo:")[1] == ["tipo:foto", "tipo:video"]
        assert self._s("sin:et")[1] == ["sin:etiquetas"]
        assert self._s("tiene:")[1] == ["tiene:texto", "tiene:notas", "tiene:etiquetas",
                                        "tiene:lugar", "tiene:evento", "tiene:fecha",
                                        "tiene:ubicación"]
        assert self._s("sin:ubic")[1] == ["sin:ubicación"]
        s = consulta.sugerencias("lugar:bl", 8, self.E, ["blessedland"], ["campamento"])
        assert s[1] == ["lugar:blessedland"]
        assert consulta.sugerencias("evento:", 7, self.E, ["x"], ["campamento"])[1] == \
            ["evento:campamento"]
        assert self._s("etiqueta:alabanza")[1] == []                  # ya está completa
        assert self._s("carpeta:x")[1] == [] and self._s("foo:bar")[1] == []

    def test_en_el_medio_de_una_palabra_no(self):
        assert self._s("etiqueta:pl culto", 11)[1][0] == "etiqueta:placa"
        assert self._s("cuxlto", 2)[1] == []


# ---------------------------------------------------------------- en la base

TZ = dt.timezone(dt.timedelta(hours=-3))


@pytest.fixture
def nas(pg_conn):
    return Repo(pg_conn, "/nas")


def _medio(repo, rel, kind="image", tomada=None, lat=None):
    return repo.upsert_media(Media(path="", rel_path=rel, filename=os.path.basename(rel),
                                   folder_origin=os.path.dirname(rel), kind=kind,
                                   tomada=tomada, lat=lat, lon=lat))


@pytest.fixture
def datos(nas):
    d = {}
    d["placa"] = _medio(nas, "Historial/Reuniones/placa_domingo.jpg",
                        tomada=dt.datetime(2025, 9, 21, 22, 30, tzinfo=TZ), lat=-34.6)
    d["video"] = _medio(nas, "Historial/Reuniones/culto 10:30.mp4", kind="video",
                        tomada=dt.datetime(2025, 9, 28, 10, 30, tzinfo=TZ))
    d["retiro"] = _medio(nas, "Entrada/Retiro/DSC_0001.jpg",
                         tomada=dt.datetime(2024, 2, 10, 9, 0, tzinfo=TZ))
    d["sin"] = _medio(nas, "Entrada/Varios/IMG_9999.jpg")
    nas.apply_tag(d["placa"], "placa")
    nas.apply_tag(d["placa"], "Santa Cena")
    nas.apply_tag(d["video"], "santa cena")
    nas.apply_tag(d["retiro"], "retiro")
    nas.apply_tag(d["retiro"], "Ramírez")
    nas.guardar_texto(d["placa"], "VIGILIA DE ORACIÓN", 1)
    nas.guardar_notas(d["video"], "Lo pidió el pastor para el anuncio")
    return d


def _ids(nas, q, **k):
    res, total = nas.buscar(q, **k)
    assert total == len(res)
    return sorted(m.id for m in res)


class TestLugarYEvento:
    """Lugar y eventos: etiquetas de tipo especial (pedido del 24/11/2026)."""

    def test_un_lugar_por_elemento_y_varios_eventos(self, nas, datos):
        d = datos
        ids = [d["placa"], d["video"]]
        nas.poner_lugar(ids, "Blessedland")
        nas.poner_lugar([d["placa"]], "templo central")          # reemplaza
        nas.poner_evento(ids, "campamento")
        nas.poner_evento([d["placa"]], "bautismo en el río")
        res = {m.id: m for m in nas.buscar("")[0]}
        assert res[d["placa"]].lugar == "templo central"
        assert res[d["video"]].lugar == "blessedland"
        assert res[d["placa"]].eventos == ["bautismo en el río", "campamento"]
        # No se mezclan con las etiquetas comunes
        assert ("campamento", True) not in res[d["placa"]].etiquetas
        assert [n for n, _ in res[d["placa"]].etiquetas] == ["placa", "santa cena"]
        assert ("campamento", 2) not in nas.tag_counts(ids)
        assert nas.nombres_por_tipo()["evento"] == ["bautismo en el río", "campamento"]
        assert "blessedland" not in nas.nombres_por_tipo()["comunes"]
        # Se buscan con su operador, con etiqueta: y como palabra
        assert _ids(nas, "lugar:blessedland") == [d["video"]]
        assert _ids(nas, 'evento:"bautismo en el rio"') == [d["placa"]]
        assert _ids(nas, "etiqueta:campamento") == sorted(ids)
        assert _ids(nas, "blessedland") == [d["video"]]
        assert _ids(nas, "lugar:campamento") == []                  # es un evento
        assert _ids(nas, "tiene:lugar") == sorted(ids)
        assert _ids(nas, "sin:evento") == sorted([d["retiro"], d["sin"]])
        # Quitar: sin rechazo (no es una sugerencia de la IA)
        nas.quitar_de([d["video"]], "blessedland")
        assert {m.id: m.lugar for m in nas.buscar("")[0]}[d["video"]] is None

    def test_un_nombre_es_de_un_solo_tipo(self, nas, datos):
        from convertir3x4.catalog.repo import NombreEnUso
        d = datos
        nas.poner_evento([d["video"]], "campamento")
        with pytest.raises(NombreEnUso, match="ya es un evento: póngalo desde el campo Eventos"):
            nas.apply_tag(d["placa"], "Campamento")
        with pytest.raises(NombreEnUso, match="ya es un evento"):
            nas.poner_lugar([d["placa"]], "campamento")
        with pytest.raises(NombreEnUso, match="ya es una etiqueta común"):
            nas.poner_lugar([d["placa"]], "placa")
        with pytest.raises(NombreEnUso, match="ya es una etiqueta común"):
            nas.poner_evento([d["placa"]], "retiro")
        # La IA no pone lugares ni eventos aunque el nombre coincida
        assert nas.suggest_tag(d["sin"], "campamento") is False

    def test_convertir(self, nas, datos, pg_cfg):
        from convertir3x4.catalog import db as catdb
        from convertir3x4.catalog.repo import NoSePuedeConvertir
        d = datos
        nas.apply_tag(d["video"], "blessedland")
        nas.apply_tag(d["sin"], "blessedland")
        oye = catdb.conectar(pg_cfg)
        try:
            oye.execute("LISTEN " + catdb.CANAL_CAMBIOS)
            nas.cambiar_tipo("blessedland", "lugar")
            assert list(oye.notifies(timeout=2, stop_after=1))      # avisa a las PCs
        finally:
            oye.close()
        res = {m.id: m for m in nas.buscar("")[0]}
        assert res[d["video"]].lugar == res[d["sin"]].lugar == "blessedland"
        assert _ids(nas, "lugar:blessedland") == sorted([d["video"], d["sin"]])
        # Dos lugares en el mismo elemento: no
        nas.apply_tag(d["video"], "parque")
        with pytest.raises(NoSePuedeConvertir, match="1 de los elementos"):
            nas.cambiar_tipo("parque", "lugar")
        # Ni las de personas ni las de la IA
        nas.suggest_tag(d["sin"], "juan", kind="persona")
        nas.suggest_tag(d["sin"], "alabanza")
        for nombre in ("juan", "alabanza"):
            with pytest.raises(NoSePuedeConvertir):
                nas.cambiar_tipo(nombre, "evento")
        # Y de vuelta a común
        nas.cambiar_tipo("blessedland", "descriptivo")
        assert ("blessedland", True) in {m.id: m for m in nas.buscar("")[0]}[d["sin"]].etiquetas


class TestBuscarConFiltros:
    def test_etiqueta(self, nas, datos):
        d = datos
        assert _ids(nas, "etiqueta:placa") == [d["placa"]]
        assert _ids(nas, "tag:PLACA") == [d["placa"]]                 # como Gmail
        assert _ids(nas, 'etiqueta:"santa cena"') == sorted([d["placa"], d["video"]])
        assert _ids(nas, "etiqueta:ramirez") == [d["retiro"]]         # sin tildes
        assert _ids(nas, "etiqueta:plac") == []                       # la etiqueta entera
        assert _ids(nas, '-etiqueta:"santa cena"') == sorted([d["retiro"], d["sin"]])

    def test_tipo_manda_sobre_los_botones(self, nas, datos):
        assert _ids(nas, "tipo:video") == [datos["video"]]
        assert _ids(nas, "tipo:video", kind="image") == [datos["video"]]
        assert _ids(nas, "-tipo:video") == sorted(
            [datos["placa"], datos["retiro"], datos["sin"]])

    def test_carpeta_nombre_texto_y_notas(self, nas, datos):
        d = datos
        assert _ids(nas, "carpeta:reuniones") == sorted([d["placa"], d["video"]])
        assert _ids(nas, "carpeta:reuniones -nombre:placa") == [d["video"]]
        assert _ids(nas, "nombre:dsc_") == [d["retiro"]]              # _ no es comodín
        assert _ids(nas, "texto:oracion") == [d["placa"]]
        assert _ids(nas, 'texto:"vigilia de"') == [d["placa"]]
        assert _ids(nas, "notas:pastor") == [d["video"]]
        assert _ids(nas, "-notas:pastor") == sorted([d["placa"], d["retiro"], d["sin"]])

    def test_fechas_en_la_hora_de_la_iglesia(self, nas, datos):
        d = datos
        # 21/09 22:30 en Buenos Aires es 22/09 en UTC: cuenta el día de la iglesia
        assert _ids(nas, "fecha:21/09/2025") == [d["placa"]]
        assert _ids(nas, "fecha:09/2025") == sorted([d["placa"], d["video"]])
        assert _ids(nas, "desde:22/09/2025") == [d["video"]]
        assert _ids(nas, "hasta:21/09/2025") == sorted([d["placa"], d["retiro"]])  # incluye
        assert _ids(nas, "fecha:2024") == [d["retiro"]]
        assert _ids(nas, "-fecha:2025") == sorted([d["retiro"], d["sin"]])  # sin fecha también
        # antes: y después: no incluyen la fecha dada (como Gmail)
        assert _ids(nas, "antes:21/09/2025") == [d["retiro"]]
        assert _ids(nas, "antes:22/09/2025") == sorted([d["placa"], d["retiro"]])
        assert _ids(nas, "antes:2025") == [d["retiro"]]
        assert _ids(nas, "después:21/09/2025") == [d["video"]]
        assert _ids(nas, "despues:09/2025") == []                    # empieza en octubre
        assert _ids(nas, "despues:2024 antes:28/09/2025") == [d["placa"]]

    def test_tiene_y_sin(self, nas, datos):
        d = datos
        assert _ids(nas, "tiene:texto") == [d["placa"]]
        assert _ids(nas, "tiene:notas") == [d["video"]]
        assert _ids(nas, "sin:etiquetas") == [d["sin"]]
        assert _ids(nas, "sin:fecha") == [d["sin"]]
        assert _ids(nas, "tiene:ubicación") == [d["placa"]]           # coordenadas del archivo
        assert _ids(nas, "tiene:ubicacion") == _ids(nas, "tiene:gps") == [d["placa"]]
        assert _ids(nas, "tiene:lugar") == []                         # lugar escrito: ninguno

    def test_palabras_con_filtros(self, nas, datos):
        d = datos
        assert _ids(nas, "domingo etiqueta:placa") == [d["placa"]]
        assert _ids(nas, "domingo etiqueta:retiro") == []
        assert _ids(nas, "anuncio") == [d["video"]]                   # las notas se buscan
        assert _ids(nas, "-anuncio carpeta:reuniones") == [d["placa"]]
        assert _ids(nas, "10:30") == [d["video"]]                     # sigue igual que antes
        assert _ids(nas, "etiqueta:") == _ids(nas, "")                # a medio escribir

    def test_orden_con_filtros_y_sin_palabras_es_por_nombre(self, nas, datos):
        res, _ = nas.buscar("carpeta:reuniones")
        assert [m.filename for m in res] == ["culto 10:30.mp4", "placa_domingo.jpg"]

    def test_por_paginas(self, nas, datos):
        for i in range(5):
            m = _medio(nas, f"Historial/Reuniones/extra_{i}.jpg")
            nas.apply_tag(m, "placa")
        pag1, total = nas.buscar("etiqueta:placa", cuantos=4)
        pag2, _ = nas.buscar("etiqueta:placa", desde=4, cuantos=4)
        assert total == 6 and len(pag1) == 4 and len(pag2) == 2
        assert not {m.id for m in pag1} & {m.id for m in pag2}


# ---------------------------------------------------------------- el formulario

@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class TestFormulario:
    def test_se_llena_con_lo_escrito_y_lo_vuelve_a_escribir(self, app):
        from convertir3x4.ui.widgets.busqueda_avanzada import FormularioBusqueda
        f = FormularioBusqueda()
        texto = ('culto -ensayo foo:bar etiqueta:"santa cena" etiqueta:placa '
                 '-etiqueta:retiro tipo:video carpeta:Reuniones nombre:DSC texto:vigilia '
                 'notas:pastor desde:01/09/2025 hasta:30/09/2025 tiene:notas sin:etiquetas '
                 '-carpeta:Entrada')
        f.cargar(texto)
        assert f.txt_palabras.text() == "culto foo:bar -ensayo -carpeta:Entrada"
        assert f.txt_etiquetas.text() == "santa cena, placa"
        assert f.txt_sin_etiquetas.text() == "retiro"
        assert f.cmb_tipo.currentData() == "video"
        assert (f.txt_carpeta.text(), f.txt_nombre.text()) == ("Reuniones", "DSC")
        assert (f.txt_texto.text(), f.txt_notas.text()) == ("vigilia", "pastor")
        assert (f.txt_desde.text(), f.txt_hasta.text()) == ("01/09/2025", "30/09/2025")
        assert f.chk_notas.isChecked() and f.chk_sin_etiquetas.isChecked()
        assert not f.chk_texto.isChecked()
        # Lo que arma busca exactamente lo mismo
        armado = f.texto()
        a, b = analizar(texto), analizar(armado)
        clave = lambda c: (sorted(c.palabras), sorted(c.excluidas), sorted(  # noqa: E731
            (x.clave, x.valor, x.negado) for x in c.filtros))
        assert clave(a) == clave(b)

    def test_campos_vacios_y_fecha_que_no_se_entiende(self, app):
        from convertir3x4.ui.widgets.busqueda_avanzada import FormularioBusqueda
        f = FormularioBusqueda()
        f.cargar("")
        assert f.texto() == ""
        f.txt_etiquetas.setText(" placa ,, santa cena ")
        f.txt_desde.setText("32/13/2025")
        assert f.texto() == 'etiqueta:placa etiqueta:"santa cena"'
        assert not f.btn_buscar.isEnabled() and "32/13/2025" in f.lbl_aviso.text()
        assert f.txt_desde.property("error") is True                  # en rojo
        f.txt_desde.setText("09/2025")
        assert f.btn_buscar.isEnabled() and f.texto().endswith("desde:09/2025")
        assert not f.txt_desde.property("error")
        # Una fecha mal escrita en el campo va a su cuadro, en rojo
        f.cargar("culto hasta:13/13 tipo:pdf")
        assert f.txt_hasta.text() == "13/13" and f.txt_hasta.property("error") is True
        assert f.txt_palabras.text() == "culto tipo:pdf" and not f.btn_buscar.isEnabled()

    def test_buscar_escribe_en_el_campo(self, app, sin_catalogo):
        from convertir3x4.ui.tabs.tab_buscar import BuscarTab
        tab = BuscarTab(srv=sin_catalogo)
        tab.txt_query.setText("culto")
        tab._abrir_formulario()
        f = tab.formulario
        assert f.isVisible() and f.txt_palabras.text() == "culto"
        f.txt_etiquetas.setText("placa")
        f.cmb_tipo.setCurrentIndex(f.cmb_tipo.findData("video"))
        f.btn_buscar.click()
        assert tab.txt_query.text() == "culto etiqueta:placa tipo:video"
        assert not f.isVisible()
        # El botón «Videos» se marca solo; elegir «Fotos» saca el tipo: del texto
        assert tab._kind() == "video"
        fotos = [b for b in tab._kind_group.buttons() if b.property("kind") == "image"][0]
        fotos.click()
        assert tab.txt_query.text() == "culto etiqueta:placa" and tab._kind() == "image"
        tab.deleteLater()

    def test_sugerencias_mientras_escribe(self, app, sin_catalogo):
        from PyQt6.QtTest import QTest
        from convertir3x4.ui.tabs.tab_buscar import BuscarTab
        tab = BuscarTab(srv=sin_catalogo)
        tab._etiquetas_conocidas = ["placa", "santa cena"]
        tab.show()
        tab.txt_query.setFocus()
        QTest.keyClicks(tab.txt_query, "culto et")
        assert not tab.completar.popup().isVisible()          # con 2 letras, todavía no
        QTest.keyClicks(tab.txt_query, "i")
        pop = tab.completar.popup()
        assert pop.isVisible() and tab._sugerencias.stringList() == ["etiqueta:"]
        tab.completar.activated[str].emit("etiqueta:")
        assert tab.txt_query.text() == "culto etiqueta:"
        app.processEvents()
        assert tab._sugerencias.stringList() == ["etiqueta:placa", 'etiqueta:"santa cena"']
        tab.completar.activated[str].emit('etiqueta:"santa cena"')
        assert tab.txt_query.text() == 'culto etiqueta:"santa cena" '
        QTest.keyClicks(tab.txt_query, "zz")
        assert not pop.isVisible()
        tab.close()
        tab.deleteLater()

    def test_aviso_y_orden_con_filtros(self, app, sin_catalogo):
        from convertir3x4.ui.tabs.tab_buscar import BuscarTab
        pedidos = []
        sin_catalogo.pedir = lambda *a, **k: pedidos.append(a)
        tab = BuscarTab(srv=sin_catalogo)
        tab.txt_query.setText("etiqueta:placa")
        assert tab.cmb_orden.itemText(0) == "Por nombre"      # sin palabras sueltas
        # Mientras se escribe la fecha no se pone rojo; al buscar, sí, y no se busca
        tab.txt_query.setText("etiqueta:placa desde:32/13/2025")
        assert not tab.lbl_aviso_busqueda.isVisibleTo(tab)
        antes = len(pedidos)
        tab._do_search()
        assert len(pedidos) == antes                          # no se pidió nada al NAS
        assert tab.txt_query.property("error") is True
        assert tab.lbl_aviso_busqueda.isVisibleTo(tab)
        assert "32/13/2025" in tab.lbl_aviso_busqueda.text()
        # Corregida, se saca el rojo en el momento y se vuelve a buscar
        tab.txt_query.setText("etiqueta:placa desde:09/2025")
        assert not tab.txt_query.property("error")
        assert not tab.lbl_aviso_busqueda.isVisibleTo(tab)
        tab._do_search()
        assert len(pedidos) == antes + 1
        tab.deleteLater()


class TestLugarYEventoEnBuscar:
    class _Srv:
        """Corre lo pedido contra un repo de mentira que anota las llamadas."""
        estado, texto = "ok", ""

        class _Senal:
            def connect(self, *_a):
                pass
        estado_cambiado = _Senal()

        def __init__(self, falla=None):
            self.hechos, self.falla = [], falla

        def pedir(self, fn, ok=None, error=None):
            hechos = self.hechos

            class _Repo:
                def __getattr__(self, nombre):
                    def llamar(*a, **_k):
                        hechos.append((nombre,) + a)
                        if nombre == "hermanas":
                            return []
                        if nombre == "nombres_por_tipo":
                            return {"comunes": ["placa"], "lugar": ["blessedland"],
                                    "evento": ["campamento"]}
                        return ([], 0) if nombre == "buscar" else None
                    return llamar
            try:
                r = fn(_Repo())
            except Exception:  # noqa: BLE001 — búsquedas que el falso no sabe armar
                return 0
            if self.falla and hechos and hechos[-1][0] == self.falla[0]:
                error and error(self.falla[1])
            elif ok:
                ok(r)
            return 0

    def _tab(self, srv, tmp_path):
        from convertir3x4.ui.tabs.tab_buscar import BuscarTab
        tab = BuscarTab(srv=srv)
        medios = [Media(path=str(tmp_path / f"{i}.jpg"), filename=f"{i}.jpg", kind="image",
                        id=i, etiquetas=[], lugar="blessedland" if i == 1 else None,
                        eventos=["campamento"] if i < 3 else [])
                  for i in (1, 2, 3)]
        tab._results = medios
        tab._populate(medios)
        tab._reload_known_tags()
        return tab, medios

    def test_poner_lugar_y_evento_a_la_seleccion(self, app, tmp_path):
        srv = self._Srv()
        tab, medios = self._tab(srv, tmp_path)
        for i in range(3):
            tab.grid.item(i).setSelected(True)
        textos = lambda b: [w.text() for w in b._chips_inner.findChildren(  # noqa: E731
            type(tab.btn_convertir))]
        assert textos(tab.lugar_bar) == ["blessedland (1/3) ✕"]
        assert textos(tab.evento_bar) == ["campamento (2/3) ✕"]
        tab.lugar_bar.tagApplied.emit("templo central")
        assert ("poner_lugar", [1, 2, 3], "templo central") in srv.hechos
        assert [m.lugar for m in medios] == ["templo central"] * 3
        assert textos(tab.lugar_bar) == ["templo central ✕"]
        assert ("hermanas", [1, 2, 3], "templo central", True) in srv.hechos  # sin pisar lugares
        tab.evento_bar.tagApplied.emit("Bautismo")
        assert medios[2].eventos == ["bautismo"] and medios[0].eventos == ["bautismo",
                                                                           "campamento"]
        tab.evento_bar.tagRemoved.emit("campamento")
        assert all("campamento" not in m.eventos for m in medios)
        tab.deleteLater()

    def test_un_lugar_no_entra_como_etiqueta_comun(self, app, tmp_path):
        srv = self._Srv()
        tab, _ = self._tab(srv, tmp_path)
        tab.grid.item(0).setSelected(True)
        tab.tag_bar.tagApplied.emit("Blessedland")
        assert not any(h[0] == "apply_tag" for h in srv.hechos)
        assert tab.lbl_seleccion.text() == ("«blessedland» ya es un lugar: póngalo desde el "
                                            "campo Lugar.")
        tab.deleteLater()

    def test_el_error_del_repo_se_muestra_tal_cual(self, app, tmp_path):
        from convertir3x4.catalog.repo import NombreEnUso
        srv = self._Srv(falla=("poner_evento", NombreEnUso("placa", "descriptivo", "evento")))
        tab, _ = self._tab(srv, tmp_path)
        tab.grid.item(0).setSelected(True)
        tab.evento_bar.tagApplied.emit("placa")
        assert tab.lbl_seleccion.text() == ("«placa» ya es una etiqueta común: póngalo desde "
                                            "la barra de etiquetas.")
        tab.deleteLater()

    def test_convertir_pregunta_antes(self, app, tmp_path, monkeypatch):
        from convertir3x4.ui.tabs import tab_buscar
        srv = self._Srv()
        tab, _ = self._tab(srv, tmp_path)
        respuestas = [False, True]
        monkeypatch.setattr(tab_buscar, "confirmar", lambda *a, **k: respuestas.pop(0))
        tab.tag_bar.accionChip.emit("evento", "placa")
        assert not any(h[0] == "cambiar_tipo" for h in srv.hechos)
        tab.tag_bar.accionChip.emit("evento", "placa")
        assert ("cambiar_tipo", "placa", "evento") in srv.hechos
        tab.deleteLater()

    def test_formulario_y_detalles(self, app):
        from convertir3x4.ui.widgets.busqueda_avanzada import FormularioBusqueda
        from convertir3x4.ui.widgets import detalle_medio as dm
        f = FormularioBusqueda()
        f.cargar('culto lugar:blessedland evento:campamento evento:"santa cena"')
        assert f.txt_lugar.text() == "blessedland"
        assert f.txt_eventos.text() == "campamento, santa cena"
        assert f.txt_palabras.text() == "culto"
        assert f.texto() == 'culto lugar:blessedland evento:campamento evento:"santa cena"'
        m = Media(path="/x", filename="a.jpg", kind="image", lugar="blessedland",
                  eventos=["campamento", "bautismo"])
        assert ("Lugar", "blessedland") in dm.datos_de(m)
        assert ("Eventos", "campamento, bautismo") in dm.datos_de(m)
        otros = [m, Media(path="/y", filename="b.jpg", kind="image", eventos=["campamento"])]
        filas = dict(dm.resumen_de(otros, {}))
        assert filas["Lugar"] == "blessedland (1 sin lugar)"
        assert filas["Eventos"] == "campamento, bautismo (1/2)"
