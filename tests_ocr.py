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

def test_migracion_8(pg_conn):
    assert catdb.version_esquema(pg_conn) == 8
    cols = {r["column_name"] for r in pg_conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'texto_foto'")}
    assert {"media_id", "texto", "version", "leida"} <= cols


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
    from convertir3x4.ui.widgets.detalle_medio import DetalleMedio
    d = DetalleMedio()
    d.mostrar(Media(path="/x/a.jpg", filename="a.jpg", kind="image", id=1,
                    texto="VIGILIA\nViernes 22 hs"))
    assert d.txt_texto.toPlainText() == "VIGILIA\nViernes 22 hs"   # entero, se puede copiar
    assert d.sec_texto.isVisibleTo(d)
    d.mostrar(Media(path="/x/b.jpg", filename="b.jpg", kind="image", id=2))
    assert not d.sec_texto.isVisibleTo(d)
    d.deleteLater()


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
