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
        c = analizar('tag:"santa  cena" persona:Ramírez -etiqueta:retiro -ensayo santa-cena')
        assert [(f.clave, f.valor, f.negado) for f in c.filtros] == [
            ("etiqueta", "santa cena", False), ("etiqueta", "Ramírez", False),
            ("etiqueta", "retiro", True)]
        assert c.palabras == ["santa-cena"] and c.excluidas == ["ensayo"]

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
                      "10:30 nombre:DSC texto:vigilia notas:pastor fecha:09/2025"):
            assert armar(analizar(texto)) == texto
            assert armar(analizar(armar(analizar(texto)))) == texto
        assert armar(analizar("tag:placa tipo:fotos")) == "etiqueta:placa tipo:foto"

    def test_sin_tipo_deja_el_resto_como_estaba(self):
        assert consulta.sin_tipo("culto tipo:video desde:99 -etiqueta:x") == \
            "culto desde:99 -etiqueta:x"
        assert consulta.sin_tipo("culto") == "culto"


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


class TestBuscarConFiltros:
    def test_etiqueta(self, nas, datos):
        d = datos
        assert _ids(nas, "etiqueta:placa") == [d["placa"]]
        assert _ids(nas, "tag:PLACA") == [d["placa"]]                 # como Gmail
        assert _ids(nas, 'etiqueta:"santa cena"') == sorted([d["placa"], d["video"]])
        assert _ids(nas, "persona:ramirez") == [d["retiro"]]          # sin tildes
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

    def test_tiene_y_sin(self, nas, datos):
        d = datos
        assert _ids(nas, "tiene:texto") == [d["placa"]]
        assert _ids(nas, "tiene:notas") == [d["video"]]
        assert _ids(nas, "sin:etiquetas") == [d["sin"]]
        assert _ids(nas, "sin:fecha") == [d["sin"]]
        assert _ids(nas, "tiene:lugar") == [d["placa"]]

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
        f.txt_desde.setText("09/2025")
        assert f.btn_buscar.isEnabled() and f.texto().endswith("desde:09/2025")

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

    def test_aviso_y_orden_con_filtros(self, app, sin_catalogo):
        from convertir3x4.ui.tabs.tab_buscar import BuscarTab
        tab = BuscarTab(srv=sin_catalogo)
        tab.txt_query.setText("etiqueta:placa")
        assert tab.cmb_orden.itemText(0) == "Por nombre"      # sin palabras sueltas
        tab.txt_query.setText("etiqueta:placa desde:32/13/2025")
        tab._revisar_consulta()
        assert tab.lbl_aviso_busqueda.isVisibleTo(tab)
        assert "32/13/2025" in tab.lbl_aviso_busqueda.text()
        tab.txt_query.setText("etiqueta:placa")
        tab._revisar_consulta()
        assert not tab.lbl_aviso_busqueda.isVisibleTo(tab)
        tab.deleteLater()
