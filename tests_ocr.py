"""Texto en las fotos (OCR, v0.8.1, pedido del 23/11/2026).

Las pruebas del catálogo usan el PostgreSQL de desarrollo (conftest.py). La
prueba con los modelos reales corre si CDLM_MODELS tiene los archivos del OCR.
"""

import dataclasses
import datetime as dt
import glob
import os
import tempfile

import pytest
from PIL import Image, ImageDraw, ImageFont

from convertir3x4 import catalogador as cat
from convertir3x4.catalog import db as catdb
from convertir3x4.catalog.models import Media
from convertir3x4.catalog.repo import Repo
from convertir3x4.engine import ocr

_MODELS = os.environ.get("CDLM_MODELS", "")
_OCR = ("ocr_det.onnx", "ocr_rec_latin.onnx", "ocr_dict_latin.txt")
real = pytest.mark.skipif(
    not _MODELS or not all(os.path.exists(os.path.join(_MODELS, f)) for f in _OCR),
    reason="CDLM_MODELS no tiene los modelos del OCR")


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def raiz():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def nas(pg_conn, raiz):
    return Repo(pg_conn, raiz)


def _medio(repo, rel, kind="image"):
    return repo.upsert_media(Media(path="", rel_path=rel, filename=os.path.basename(rel),
                                   folder_origin=os.path.dirname(rel), kind=kind))


def _cartel(path=None, lineas=("CATEDRAL DE LOS MILAGROS",
                               "Domingo mañana — Reunión de oración")):
    from convertir3x4.ui.style import ASSETS_DIR
    fuente = sorted(glob.glob(os.path.join(ASSETS_DIR, "**", "Inter*.ttf"), recursive=True))[0]
    img = Image.new("RGB", (1600, 700), (30, 40, 70))
    d = ImageDraw.Draw(img)
    for i, t in enumerate(lineas):
        d.text((80, 150 + 250 * i), t, font=ImageFont.truetype(fuente, 80 - 15 * i),
               fill="white")
    if path:
        img.save(path, quality=92)
    return img


# ---------------------------------------------------------------- la base

def test_migracion_8_y_9(pg_conn):
    assert catdb.version_esquema(pg_conn) == 9
    cols = {r["column_name"] for r in pg_conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'texto_foto'")}
    assert {"media_id", "texto", "version", "leida", "editado", "editado_por"} <= cols
    cols = {r["column_name"] for r in pg_conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'media'")}
    assert {"notas", "notas_en", "notas_por"} <= cols


class TestTextoEnLaBase:
    def test_que_falta_leer(self, nas):
        a = _medio(nas, "Entrada/Retiro/a.jpg")
        b = _medio(nas, "Publicar/b.jpg")
        _medio(nas, "Entrada/v.mp4", kind="video")          # los videos no se leen
        c = _medio(nas, "Entrada/c.jpg")
        nas.marcar_faltantes([c])                            # ni lo que no está
        ids = lambda fotos: sorted(m.id for m in fotos)      # noqa: E731
        assert ids(nas.fotos_sin_texto(1)) == sorted([a, b])
        assert ids(nas.fotos_sin_texto(1, ["Entrada/Retiro"])) == [a]
        nas.guardar_texto(a, "", 1)                          # leída, sin texto
        assert ids(nas.fotos_sin_texto(1)) == [b]
        assert ids(nas.fotos_sin_texto(2)) == sorted([a, b])  # otra versión: se relee
        assert ids(nas.fotos_sin_texto(1, rehacer=True)) == sorted([a, b])

    def test_se_busca_y_se_muestra(self, nas):
        a = _medio(nas, "Entrada/DSC_0001.jpg")
        _medio(nas, "Entrada/DSC_0002.jpg")
        nas.guardar_texto(a, "VIGILIA DE ORACIÓN\nViernes 22 hs", 1)
        for q in ("vigilia", "oracion", "viernes"):         # sin tildes, como el resto
            res, total = nas.buscar(q)
            assert [m.id for m in res] == [a], q
        res, _ = nas.buscar("vigilia")
        assert res[0].texto == "VIGILIA DE ORACIÓN\nViernes 22 hs"
        todos, _ = nas.buscar("")
        assert {m.id: m.texto for m in todos}[a].startswith("VIGILIA")
        assert [m.texto for m in todos if m.id != a] == [None]
        assert nas.texto_de(a).startswith("VIGILIA")
        # Si se vuelve a leer y ya no tiene ese texto, deja de encontrarse
        nas.guardar_texto(a, "", 2)
        assert nas.buscar("vigilia")[0] == []
        assert nas.buscar("dsc_0001")[0][0].texto is None

    def test_el_nombre_pesa_mas_que_el_texto(self, nas):
        por_texto = _medio(nas, "Entrada/DSC_0003.jpg")
        por_nombre = _medio(nas, "Entrada/bautismo.jpg")
        nas.guardar_texto(por_texto, "Bautismo en el río", 1)
        res, _ = nas.buscar("bautismo")
        assert [m.id for m in res] == [por_nombre, por_texto]


class TestEscritoAMano:
    """Texto corregido y notas desde los detalles de Buscar (pedido del 24/11/2026)."""

    def test_el_texto_corregido_no_lo_pisa_el_nas(self, nas):
        a = _medio(nas, "Entrada/DSC_0010.jpg")
        b = _medio(nas, "Entrada/DSC_0011.jpg")          # sin leer todavía
        nas.guardar_texto(a, "VIGILA DE ORACON", 1)       # el OCR leyó mal
        nas.corregir_texto(a, "  Vigilia de oración \n", "PC-EDICION")
        nas.corregir_texto(b, "Santa Cena", "PC-EDICION")  # escrito antes de leerla
        assert nas.texto_de(a) == "Vigilia de oración" and nas.texto_de(b) == "Santa Cena"
        assert nas.buscar("vigila")[0] == []
        assert [m.id for m in nas.buscar("vigilia")[0]] == [a]
        assert [m.id for m in nas.buscar("cena")[0]] == [b]
        # Ni otra versión del OCR ni «rehacer» vuelven a leerlas, y si el NAS
        # igual guarda (estaba leyendo mientras se corregía), no las pisa
        assert nas.fotos_sin_texto(1) == [] and nas.fotos_sin_texto(2, rehacer=True) == []
        nas.guardar_texto(a, "VIGILA DE ORACON", 2)
        assert nas.texto_de(a) == "Vigilia de oración"
        # Borrarlo a mano también vale: queda sin texto y no se relee
        nas.corregir_texto(a, "")
        assert nas.texto_de(a) == "" and nas.buscar("vigilia")[0] == []
        assert nas.fotos_sin_texto(3) == []

    def test_las_notas_se_buscan_y_se_muestran(self, nas):
        a = _medio(nas, "Entrada/DSC_0020.jpg")
        v = _medio(nas, "Entrada/MVI_0021.mp4", kind="video")
        nas.guardar_notas(a, "Pastor Ramírez con los jóvenes", "PC-EDICION")
        nas.guardar_notas(v, "Usado en el anuncio del retiro")
        assert [m.id for m in nas.buscar("ramirez")[0]] == [a]     # sin tildes
        assert [m.id for m in nas.buscar("retiro anuncio")[0]] == [v]
        assert {m.id: m.notas for m in nas.buscar("")[0]} == {
            a: "Pastor Ramírez con los jóvenes", v: "Usado en el anuncio del retiro"}
        assert nas.get_media(a).notas == "Pastor Ramírez con los jóvenes"
        # Pesan como el nombre: van antes que lo leído en otra foto
        b = _medio(nas, "Entrada/DSC_0022.jpg")
        nas.guardar_texto(b, "Jóvenes en acción", 1)
        nas.guardar_notas(a, "jóvenes jóvenes")
        assert [m.id for m in nas.buscar("jovenes")[0]] == [a, b]
        # Se pueden borrar; catalogar de nuevo no las toca
        nas.upsert_media(nas.get_media(v))
        assert nas.notas_de(v) == "Usado en el anuncio del retiro"
        nas.guardar_notas(v, "   ")
        assert nas.notas_de(v) == "" and nas.buscar("anuncio")[0] == []

    def test_avisa_a_las_pcs(self, nas, pg_cfg):
        a = _medio(nas, "Entrada/DSC_0030.jpg")
        oye = catdb.conectar(pg_cfg)
        try:
            oye.execute("LISTEN " + catdb.CANAL_CAMBIOS)
            nas.guardar_notas(a, "una nota")
            assert list(oye.notifies(timeout=2, stop_after=1))
            nas.corregir_texto(a, "Cartel")
            assert list(oye.notifies(timeout=2, stop_after=1))
        finally:
            oye.close()


# ---------------------------------------------------------------- la pasada

class _LectorFalso:
    def __init__(self, _models_dir):
        pass

    def leer(self, img):
        return "CARTEL" if img.getpixel((0, 0))[0] > 128 else ""


def test_leer_fotos(nas, raiz, monkeypatch):
    monkeypatch.setattr(ocr, "LectorDeTexto", _LectorFalso)
    os.makedirs(os.path.join(raiz, "Entrada"))
    Image.new("RGB", (50, 40), "white").save(os.path.join(raiz, "Entrada", "con.jpg"))
    Image.new("RGB", (50, 40), "black").save(os.path.join(raiz, "Entrada", "sin.jpg"))
    with open(os.path.join(raiz, "Entrada", "rota.jpg"), "wb") as f:
        f.write(b"no es una foto")
    con, sin, rota = (_medio(nas, f"Entrada/{n}.jpg") for n in ("con", "sin", "rota"))
    avance = []
    st = ocr.leer_fotos(nas, "/modelos", progress_cb=lambda h, t, p: avance.append((h, t)))
    assert (st.leidas, st.con_texto, st.errores) == (2, 1, 1)
    assert avance[-1] == (3, 3)
    assert (nas.texto_de(con), nas.texto_de(sin), nas.texto_de(rota)) == ("CARTEL", "", None)
    # La foto dañada se reintenta; las otras ya están
    assert [m.id for m in nas.fotos_sin_texto(ocr.OCR_VERSION)] == [rota]
    assert ocr.leer_fotos(nas, "/modelos").leidas == 0


def test_la_foto_se_abre_derecha_y_achicada(tmp_path):
    p = str(tmp_path / "grande.jpg")
    img = Image.new("RGB", (4000, 3000), "white")
    exif = img.getexif()
    exif[0x0112] = 6                                         # girada 90°
    img.save(p, exif=exif)
    abierta = ocr.abrir_foto(p)
    assert max(abierta.size) <= 1600 and abierta.size[1] > abierta.size[0]


def test_el_catalogador_pide_el_texto(monkeypatch, pg_cfg):
    with tempfile.TemporaryDirectory() as raiz:
        os.makedirs(os.path.join(raiz, "Entrada", "Retiro"))
        catalogo = dataclasses.replace(pg_cfg, raiz=raiz)
        cfg = cat.ConfigCatalogador(catalogo=catalogo, ia=True, hora=dt.time(1, 30),
                                    modelos=raiz)
        llamadas = []
        monkeypatch.setattr("convertir3x4.engine.ai_models.ensure", lambda d: None)
        monkeypatch.setattr(ocr, "leer_fotos", lambda repo, d, progress_cb=None, carpetas=None,
                            rehacer=False: llamadas.append((carpetas, rehacer))
                            or ocr.EstadisticasTexto(leidas=4, con_texto=1))
        repo = Repo(catdb.conectar(catalogo), raiz)
        try:
            repo.agregar_carpeta("Entrada")
            r = cat.pasada(repo, cfg, "Entrada/Retiro", pasos=["texto"], rehacer=True)
            assert r["texto"] == {"leidas": 4, "con_texto": 1, "errores": 0}
            assert llamadas == [(["Entrada/Retiro"], True)]
            r = cat.pasada(repo, cfg, None, pasos=["personas"])
            assert "texto" not in r and len(llamadas) == 1
        finally:
            repo.close()
    assert "texto" in cat.PASOS


# ---------------------------------------------------------------- la interfaz

def test_texto_en_el_detalle_de_buscar(app):
    from convertir3x4.ui.widgets.detalle_medio import PanelDetalles
    d = PanelDetalles()
    d.mostrar([Media(path="/x/a.jpg", filename="a.jpg", kind="image", id=1,
                     texto="VIGILIA\nViernes 22 hs")])
    assert d.txt_texto.toPlainText() == "VIGILIA\nViernes 22 hs"   # entero, se puede copiar
    assert d.sec_texto.isVisibleTo(d)
    # Sin texto leído el cuadro igual está, para escribirlo a mano
    d.mostrar([Media(path="/x/b.jpg", filename="b.jpg", kind="image", id=2)])
    assert d.sec_texto.isVisibleTo(d) and d.txt_texto.toPlainText() == ""
    assert not d.txt_texto.isReadOnly() and d.sec_notas.isVisibleTo(d)
    # Los videos no tienen texto leído, pero sí notas
    d.mostrar([Media(path="/x/v.mp4", filename="v.mp4", kind="video", id=3, notas="hola")])
    assert not d.sec_texto.isVisibleTo(d) and d.txt_notas.toPlainText() == "hola"
    d.deleteLater()


def test_editar_en_los_detalles_no_pierde_nada(app):
    from convertir3x4.ui.widgets.detalle_medio import PanelDetalles
    d = PanelDetalles()
    enviados = []
    d.guardar.connect(lambda *a: enviados.append(a))
    a = Media(path="/x/a.jpg", filename="a.jpg", kind="image", id=1, texto="VIGILA")
    b = Media(path="/x/b.jpg", filename="b.jpg", kind="image", id=2)
    d.mostrar([a])
    assert enviados == []                                    # mostrar no guarda nada
    d.txt_texto.setPlainText("VIGILIA")
    d.txt_notas.setPlainText("Para el anuncio")
    # Llega un aviso del NAS (la grilla se vuelve a pedir) mientras escribe: no lo pisa
    d.mostrar([Media(path="/x/a.jpg", filename="a.jpg", kind="image", id=1, texto="VIGILA")])
    assert d.txt_texto.toPlainText() == "VIGILIA" and enviados == []
    # Elige otro: se guarda lo del anterior, con el id del anterior
    d.mostrar([b])
    assert sorted(enviados) == [(1, "notas", "Para el anuncio"), (1, "texto", "VIGILIA")]
    assert d.txt_texto.toPlainText() == "" and d.txt_notas.toPlainText() == ""
    # Si no se pudo guardar, queda escrito y se avisa
    d.txt_notas.setPlainText("otra")
    d.guardar_ya()
    d.guardado(2, "notas", "No hay conexión con el NAS.")
    assert d.lbl_notas.isVisibleTo(d) and "No se pudo guardar" in d.lbl_notas.text()
    d.mostrar([Media(path="/x/b.jpg", filename="b.jpg", kind="image", id=2)])
    assert d.txt_notas.toPlainText() == "otra"
    # Sin conexión no se puede escribir
    d.set_editable(False)
    assert d.txt_notas.isReadOnly() and d.txt_texto.isReadOnly()
    d.deleteLater()


def test_buscar_guarda_lo_escrito(app, tmp_path):
    from convertir3x4.ui.tabs.tab_buscar import BuscarTab

    class _Srv:
        estado, texto = "ok", ""

        class _Senal:
            def connect(self, *_a):
                pass
        estado_cambiado = _Senal()

        def __init__(self):
            self.hechos = []

        def pedir(self, fn, ok=None, error=None):
            hecho = []

            class _Repo:
                def __getattr__(self, nombre):
                    return lambda *a, **_k: hecho.append((nombre,) + a) or ([], 0)
            try:
                fn(_Repo())
            except Exception:  # noqa: BLE001 — solo interesa lo que se guarda
                return 0
            self.hechos += hecho
            if hecho and hecho[0][0] in ("corregir_texto", "guardar_notas") and ok:
                ok(None)
            return 0

    srv = _Srv()
    tab = BuscarTab(srv=srv)
    m = Media(path=str(tmp_path / "a.jpg"), filename="a.jpg", kind="image", id=7,
              texto="VIGILA", etiquetas=[])
    tab._results = [m]
    tab.detalles.guardar.emit(7, "texto", "Vigilia ")
    tab.detalles.guardar.emit(7, "notas", "nota")
    nombres = [h[:3] for h in srv.hechos]
    assert ("corregir_texto", 7, "Vigilia ") in nombres and ("guardar_notas", 7, "nota") in nombres
    assert m.texto == "Vigilia" and m.notas == "nota"
    # Si falla cuando ya eligió otro, se avisa en la barra de Buscar
    srv.pedir = lambda fn, ok=None, error=None: error(RuntimeError("sin conexión"))
    tab.detalles.guardar.emit(7, "notas", "otra nota")
    assert "No se pudo guardar las notas de a.jpg" in tab.lbl_seleccion.text()
    assert m.notas == "nota"
    tab.deleteLater()


def test_paso_en_catalogar_por_partes():
    from convertir3x4.ui.widgets import catalogar_por_partes as cp
    assert [p[0] for p in cp.PASOS] == list(cat.PASOS)
    from convertir3x4.ui.tabs.tab_motor import texto_estado
    t, trabaja = texto_estado({"fase": "leyendo_texto", "hechos": 3, "total": 10,
                               "segundos_sin_latido": 5}, [])
    assert t == "El NAS está leyendo el texto de las fotos: 3 de 10." and trabaja


# ---------------------------------------------------------------- modelos reales

@real
def test_lee_un_cartel_con_tildes_y_enie():
    texto = ocr.LectorDeTexto(_MODELS).leer(_cartel())
    renglones = texto.splitlines()
    assert len(renglones) == 2, texto
    assert "MILAGROS" in renglones[0]
    assert renglones[1] == "Domingo mañana — Reunión de oración"


@real
def test_una_foto_sin_texto():
    img = Image.new("RGB", (800, 600), (90, 120, 60))
    assert ocr.LectorDeTexto(_MODELS).leer(img) == ""
