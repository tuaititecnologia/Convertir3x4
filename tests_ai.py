"""Tests de la capa de IA: tags sugeridos, personas y la pasada run_ai.

La lógica se prueba con clasificador y caras falsos (no hacen falta los modelos).
El catálogo es el PostgreSQL de prueba (fixture `repo` de conftest.py).
Los tests que usan los modelos reales corren solo si CDLM_MODELS apunta a una
carpeta con ellos (p.ej. la de datos de la app).
"""

import io
import os
import tempfile

import numpy as np
import pytest
from PIL import Image

from convertir3x4.catalog.models import Media
from convertir3x4.catalog.repo import Repo
from convertir3x4.engine import ai_pipeline, ai_models, faces as faces_mod


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _add(repo, path, kind="image", quality="ok", thumb: bytes | None = None) -> int:
    return repo.upsert_media(Media(path=path, filename=os.path.basename(path),
                                   kind=kind, quality_flag=quality), miniatura=thumb)


def _jpeg(color="red") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (320, 240), color=color).save(out, "JPEG")
    return out.getvalue()


def _img(path, color="red"):
    Image.new("RGB", (400, 300), color=color).save(path, "JPEG")


# --------------------------------------------------------------- tags IA
class TestSuggestedTags:
    def test_suggest_is_unconfirmed_and_searchable(self, repo):
        mid = _add(repo, "/x/WhatsApp Image 1.jpeg")
        assert repo.suggest_tag(mid, "bautismo")
        assert repo.pending_tag_counts([mid]) == {"bautismo": 1}
        assert [m.id for m in repo.search("bautismo")] == [mid]

    def test_suggest_does_not_override_human_tag(self, repo):
        mid = _add(repo, "/x/a.jpg")
        repo.apply_tag(mid, "bautismo")
        assert not repo.suggest_tag(mid, "bautismo")
        assert repo.pending_tag_counts([mid]) == {}

    def test_removed_tag_is_not_suggested_again(self, repo):
        mid = _add(repo, "/x/a.jpg")
        repo.suggest_tag(mid, "oración")
        repo.remove_tag(mid, "oración")
        assert not repo.suggest_tag(mid, "oración")
        assert repo.tags_for(mid) == []

    def test_manual_apply_clears_rejection(self, repo):
        mid = _add(repo, "/x/a.jpg")
        repo.suggest_tag(mid, "oración")
        repo.remove_tag(mid, "oración")
        repo.apply_tag(mid, "oración")
        assert repo.tags_for(mid) == ["oración"]
        assert repo.pending_tag_counts([mid]) == {}

    def test_confirm_tag(self, repo):
        a, b = _add(repo, "/x/a.jpg"), _add(repo, "/x/b.jpg")
        repo.suggest_tag(a, "placa")
        repo.confirm_tag(a, "placa")
        repo.confirm_tag(b, "placa")          # b no lo tiene: no se agrega
        assert repo.pending_tag_counts([a, b]) == {}
        assert repo.tags_for(b) == []

    def test_search_pending_only(self, repo):
        a, b = _add(repo, "/x/foto_a.jpg"), _add(repo, "/x/foto_b.jpg")
        repo.suggest_tag(a, "placa")
        repo.apply_tag(b, "placa")
        assert [m.id for m in repo.search("", pending_only=True)] == [a]
        assert [m.id for m in repo.search("foto", pending_only=True)] == [a]
        assert repo.count_pending_tags() == 1


# --------------------------------------------------------------- personas
class TestPersons:
    def test_people_signature_changes_with_references(self, repo):
        assert repo.people_signature() == ""
        pid = repo.add_person("Juan Carlos  Bazán")
        (p,) = repo.list_persons()
        assert p.pop("sello")
        assert p == {"id": pid, "name": "Juan Carlos Bazán", "listas": 0,
                     "pendientes": 0, "sin_cara": []}
        repo.add_person_reference(pid, "/r/1.jpg", faces_mod.to_blob(np.ones(128)))
        s1 = repo.people_signature()
        repo.add_person_reference(pid, "/r/2.jpg", faces_mod.to_blob(np.ones(128)))
        s2 = repo.people_signature()
        assert s1 and s2 and s1 != s2
        assert len(repo.person_reference_blobs()["Juan Carlos Bazán"]) == 2
        repo.delete_person(pid)
        assert repo.people_signature() == "" and repo.list_persons() == []

    def test_add_person_is_idempotent(self, repo):
        assert repo.add_person("Ana") == repo.add_person("Ana")

    def test_match_threshold(self):
        ref = np.zeros(128, dtype=np.float32)
        ref[0] = 1
        near = np.zeros(128, dtype=np.float32)
        near[0], near[1] = 0.5, np.sqrt(1 - 0.25)      # coseno 0.5
        far = np.zeros(128, dtype=np.float32)
        far[0], far[1] = 0.3, np.sqrt(1 - 0.09)        # coseno 0.3
        assert faces_mod.match(near, {"P": [ref]})[0] == "P"
        assert faces_mod.match(far, {"P": [ref]})[0] is None


# --------------------------------------------------------------- run_ai
class _FakeClassifier:
    calls = 0

    def __init__(self, *a, **k):
        pass

    def classify(self, img):
        _FakeClassifier.calls += 1
        r, g, b = img.getpixel((0, 0))
        if r > 200:
            return [("bautismo", 0.9), ("alabanza", 0.1)]
        return [("placa", 0.4), ("flyer", 0.35)]          # dudoso: no propone


class _FakeFaces:
    calls = 0

    def __init__(self, *a, **k):
        pass

    def recognize(self, img, refs):
        _FakeFaces.calls += 1
        r, g, b = img.getpixel((0, 0))
        return (2, {"Juan Carlos Bazán": 0.6}) if b > 200 else (1, {})


@pytest.fixture
def fake_models(monkeypatch):
    _FakeClassifier.calls = _FakeFaces.calls = 0
    monkeypatch.setattr(ai_pipeline, "SceneClassifier", _FakeClassifier)
    monkeypatch.setattr(ai_pipeline.faces_mod, "FaceEngine", _FakeFaces)


class TestRunAI:
    def test_scene_tags_and_threshold(self, repo, temp_dir, fake_models):
        red, gray = os.path.join(temp_dir, "r.jpg"), os.path.join(temp_dir, "g.jpg")
        _img(red, "red"), _img(gray, "gray")
        a, b = _add(repo, red), _add(repo, gray)

        st = ai_pipeline.run_ai(repo, "unused")
        assert st.analyzed == 2 and st.scene_tags == 1
        assert repo.tags_for(a) == ["bautismo"]
        assert repo.tags_for(b) == []          # 0.4 < umbral: nada
        assert _FakeFaces.calls == 0           # sin personas en la lista, no mira caras

    def test_is_idempotent(self, repo, temp_dir, fake_models):
        p = os.path.join(temp_dir, "r.jpg")
        _img(p, "red")
        _add(repo, p)
        ai_pipeline.run_ai(repo, "unused")
        st = ai_pipeline.run_ai(repo, "unused")
        assert st.analyzed == 0 and _FakeClassifier.calls == 1

    def test_new_person_rescans_faces_only(self, repo, temp_dir, fake_models):
        blue = os.path.join(temp_dir, "b.jpg")
        _img(blue, "blue")
        mid = _add(repo, blue)
        ai_pipeline.run_ai(repo, "unused")
        assert _FakeClassifier.calls == 1

        pid = repo.add_person("Juan Carlos Bazán")
        repo.add_person_reference(pid, "/r/1.jpg", faces_mod.to_blob(np.ones(128)))
        st = ai_pipeline.run_ai(repo, "unused")
        assert st.analyzed == 1 and st.people_tags == 1
        assert _FakeClassifier.calls == 1      # la escena no se recalcula
        assert repo.tags_for(mid) == ["juan carlos bazán"]
        assert repo.pending_tag_counts([mid]) == {"juan carlos bazán": 1}

    def test_missing_file_is_retried_later(self, repo, temp_dir, fake_models):
        _add(repo, os.path.join(temp_dir, "no_existe.jpg"))
        st = ai_pipeline.run_ai(repo, "unused")
        assert st.missing_files == 1 and st.analyzed == 0
        assert len(repo.media_for_ai(ai_pipeline.AI_MODEL, "")) == 1

    def test_corrupt_and_video_without_thumb(self, repo, temp_dir, fake_models):
        _add(repo, os.path.join(temp_dir, "x.jpg"), quality="corrupt")
        vid = _add(repo, os.path.join(temp_dir, "v.mp4"), kind="video", thumb=_jpeg("red"))
        _add(repo, os.path.join(temp_dir, "sin_mini.mp4"), kind="video")
        st = ai_pipeline.run_ai(repo, "unused")
        assert st.analyzed == 1                # el corrupto ni se intenta
        assert st.missing_files == 1           # video sin miniatura: se reintenta después
        assert repo.tags_for(vid) == ["bautismo"]   # el video se analiza por su miniatura


# --------------------------------------------------------------- ¿sirven las fotos?
def _ref(i, estado="lista", firma=None, caras=1, caja=(500, 300, 200, 260), confianza=0.94,
         foto=None):
    return {"id": i, "archivo": f"{i}.jpg", "ruta": f"/r/{i}.jpg", "estado": estado,
            "foto": foto, "firma": None if firma is None else faces_mod.to_blob(firma),
            "caras": caras, "caja": caja, "confianza": confianza}


def _dir(*componentes):
    """Firma unitaria de 128 dims armada con pesos en las primeras dimensiones."""
    v = np.zeros(128, np.float32)
    v[:len(componentes)] = componentes
    return v / np.linalg.norm(v)


class TestRevisionDeReferencias:
    def _rev(self, refs, **kw):
        from convertir3x4.catalog import referencias
        return referencias.revisar({"name": "Juan Carlos Bazán", "referencias": refs,
                                    "confirmadas": 0, "sugeridas": 0, **kw})

    def test_todas_parecidas_sirven(self):
        refs = [_ref(1, firma=_dir(1, .2)), _ref(2, firma=_dir(1, .3)), _ref(3, firma=_dir(1, .1))]
        rev = self._rev(refs)
        assert [f.juicio.tono for f in rev.fotos] == ["ok"] * 3
        assert rev.juicio.tono == "ok" and rev.hacer == ""
        assert rev.listas == 3

    def test_una_de_otra_persona(self):
        refs = [_ref(1, firma=_dir(1, .2)), _ref(2, firma=_dir(1, .3)), _ref(3, firma=_dir(0, 1))]
        rev = self._rev(refs)
        assert [f.juicio.tono for f in rev.fotos] == ["ok", "ok", "error"]
        assert "otra persona" in rev.fotos[2].juicio.detalle[0]
        assert rev.fotos[2].coincide == (0, 2)
        assert rev.juicio.tono == "error" and "quite la foto" in rev.hacer

    def test_con_dos_que_no_se_parecen_no_se_sabe_cual(self):
        rev = self._rev([_ref(1, firma=_dir(1, 0)), _ref(2, firma=_dir(0, 1))])
        assert all(f.juicio.tono == "error" for f in rev.fotos)
        assert "Agregue más fotos" in rev.fotos[0].juicio.detalle[0]

    def test_dos_de_la_misma_persona_equivocada(self):
        """Del mismo evento, con otro más cerca de la cámara: las dos se parecen
        entre ellas, pero no a la mayoría."""
        refs = [_ref(i, firma=_dir(1, i / 10)) for i in (1, 2, 3, 4)]
        refs += [_ref(5, firma=_dir(0, 1, .1)), _ref(6, firma=_dir(0, 1, .2))]
        rev = self._rev(refs)
        assert [f.juicio.tono for f in rev.fotos] == ["ok"] * 4 + ["error"] * 2
        assert rev.fotos[4].coincide == (1, 5)
        assert rev.juicio.tono == "error"

    def test_varias_caras_confirmadas_por_las_otras_fotos(self):
        refs = [_ref(1, firma=_dir(1, .2), caras=2), _ref(2, firma=_dir(1, .3)),
                _ref(3, firma=_dir(1, .1))]
        rev = self._rev(refs)
        assert rev.fotos[0].juicio.tono == "ok"
        assert "coincide con las otras fotos" in rev.fotos[0].juicio.detalle[0]
        assert rev.juicio.tono == "ok"
        # con una sola foto no hay con qué comparar: hay que mirarla
        rev = self._rev([_ref(1, firma=_dir(1), caras=2)])
        assert rev.fotos[0].juicio.tono == "aviso"
        assert "Verifique" in rev.fotos[0].juicio.detalle[0]

    def test_chica_o_borrosa_avisan(self):
        refs = [_ref(1, firma=_dir(1, .2)), _ref(2, firma=_dir(1, .3), caja=(0, 0, 50, 70)),
                _ref(3, firma=_dir(1, .1), confianza=0.86)]
        rev = self._rev(refs)
        assert [f.juicio.tono for f in rev.fotos] == ["ok", "aviso", "aviso"]
        assert "pequeño" in rev.fotos[1].juicio.detalle[0]
        assert "claridad" in rev.fotos[2].juicio.detalle[0]
        assert rev.juicio.tono == "aviso" and rev.juicio.titulo == "Revise 2 fotos"

    def test_sin_revisar_por_el_nas_no_es_un_problema(self):
        refs = [_ref(i, firma=_dir(1, i / 10), caras=None, caja=None, confianza=None)
                for i in (1, 2, 3)]
        rev = self._rev(refs)
        assert all(f.juicio.tono == "ok" for f in rev.fotos)
        assert "todavía no revisó" in rev.fotos[0].juicio.detalle[0]

    def test_sin_cara_pendiente_y_pocas(self):
        refs = [_ref(1, firma=_dir(1)), _ref(2, estado="sin_cara", caras=0, caja=None),
                _ref(3, estado="pendiente", caras=None, caja=None)]
        rev = self._rev(refs)
        assert [f.juicio.tono for f in rev.fotos] == ["ok", "error", "activo"]
        assert rev.juicio.tono == "aviso" and rev.juicio.titulo == "Conviene agregar más fotos"
        assert "1 foto todavía en proceso" in " ".join(rev.juicio.detalle)

    def test_nada_usable(self):
        rev = self._rev([_ref(1, estado="sin_cara", caras=0, caja=None)])
        assert rev.juicio.tono == "error" and "Todavía no se puede" in rev.juicio.titulo
        assert self._rev([]).juicio.titulo == "Sin fotos de referencia"
        rev = self._rev([_ref(1, estado="pendiente", caras=None, caja=None)])
        assert rev.juicio.tono == "activo"

    def test_imagenes_con_el_rostro_marcado(self):
        import io
        buf = io.BytesIO()
        Image.new("RGB", (1600, 1067), "blue").save(buf, "JPEG")
        rev = self._rev([_ref(1, firma=_dir(1), foto=buf.getvalue())])
        mini = Image.open(io.BytesIO(rev.fotos[0].miniatura))
        cara = Image.open(io.BytesIO(rev.fotos[0].cara))
        assert max(mini.size) == 240 and cara.size == (192, 192)
        # el recuadro amarillo está donde está el rostro (500, 300) * 240/1600
        assert mini.getpixel((round(500 * 0.15), round(400 * 0.15)))[0] > 150   # el recuadro
        assert mini.getpixel((20, 20))[0] < 50                                   # el fondo azul
        sin = self._rev([_ref(1, firma=_dir(1), foto=b"roto")]).fotos[0]
        assert sin.miniatura is None and sin.cara is None

    def test_texto_del_catalogo(self):
        from convertir3x4.catalog import referencias
        assert "Todavía ninguna" in referencias.texto_catalogo(self._rev([]))
        t = referencias.texto_catalogo(self._rev([], confirmadas=3, sugeridas=1))
        assert t == ("4 fotos y videos del catálogo tienen su nombre: 3 confirmadas, "
                     "1 sugerida para revisar en Buscar.")


class TestDetallePersona:
    def test_detalle_quitar_y_cuantas_tiene(self, repo, temp_dir):
        pid = repo.add_person("Juan Carlos Bazán")
        repo.subir_fotos_de_referencia("Juan Carlos Bazán", [("C:\\fotos\\a.jpg", b"x"),
                                                             ("/fotos/b.jpg", b"y")], pid)
        sello = repo.list_persons()[0]["sello"]
        d = repo.detalle_persona(pid)
        assert [r["archivo"] for r in d["referencias"]] == ["a.jpg", "b.jpg"]
        assert d["referencias"][0]["foto"] == b"x" and d["referencias"][0]["caras"] is None
        ref = d["referencias"][0]["id"]
        repo.guardar_referencia(ref, faces_mod.to_blob(np.ones(128)), "lista", 1,
                                (1, 2, 3, 4), 0.9)
        assert repo.list_persons()[0]["sello"] != sello
        assert repo.detalle_persona(pid)["referencias"][0]["caja"] == (1, 2, 3, 4)
        m = _add(repo, os.path.join(temp_dir, "a.jpg"))
        n = _add(repo, os.path.join(temp_dir, "b.jpg"))
        repo.suggest_tag(m, "Juan Carlos Bazán", kind="persona")
        repo.suggest_tag(n, "Juan Carlos Bazán", kind="persona")
        repo.confirm_tag(n, "Juan Carlos Bazán")
        d = repo.detalle_persona(pid)
        assert (d["confirmadas"], d["sugeridas"]) == (1, 1)
        repo.quitar_referencia(ref)
        assert [r["archivo"] for r in repo.detalle_persona(pid)["referencias"]] == ["b.jpg"]
        assert repo.tags_for(m) == ["juan carlos bazán"]       # las etiquetas quedan
        repo.delete_person(pid)
        assert repo.detalle_persona(pid) is None

    def test_detalle_sin_fotos_y_fotos_aparte(self, repo):
        """Por Netbird, traer las fotos (≈270 KB cada una) demoraba el detalle: se
        piden aparte, y el sello identifica la foto para la caché de la PC."""
        pid = repo.add_person("Juan Carlos Bazán")
        repo.subir_fotos_de_referencia("Juan Carlos Bazán", [("a.jpg", b"x"), ("b.jpg", b"y")], pid)
        refs = repo.detalle_persona(pid, fotos=False)["referencias"]
        assert [r["foto"] for r in refs] == [None, None]
        assert all(r["tiene_foto"] for r in refs)
        assert refs[0]["sello_foto"] != refs[1]["sello_foto"]
        assert repo.fotos_de_referencia([r["id"] for r in refs]) == {refs[0]["id"]: b"x",
                                                                       refs[1]["id"]: b"y"}
        assert repo.fotos_de_referencia([]) == {}

    def test_cache_de_imagenes_de_referencia(self, temp_dir):
        from convertir3x4.catalog.cache_referencias import CacheReferencias
        cache = CacheReferencias(os.path.join(temp_dir, "refs"))
        ref = {"id": 7, "caja": (1, 2, 3, 4), "sello_foto": "abc"}
        assert cache.leer(ref) is None
        cache.guardar(ref, b"mini", b"cara")
        assert cache.leer(ref) == (b"mini", b"cara")
        assert cache.leer({**ref, "caja": (5, 6, 7, 8)}) is None       # el NAS movió el recuadro
        assert cache.leer({**ref, "sello_foto": "otra"}) is None        # mismo id, otra base
        cache.guardar({**ref, "id": 8}, b"mini", None)
        assert cache.leer({**ref, "id": 8}) == (b"mini", None)


# --------------------------------------------------------------- modelos reales
_MODELS = os.environ.get("CDLM_MODELS")
real = pytest.mark.skipif(not _MODELS or ai_models.missing(_MODELS),
                          reason="CDLM_MODELS no apunta a los modelos descargados")


@real
class TestRealModels:
    def test_classifier_returns_vocab_probabilities(self):
        from convertir3x4.engine.vision import SceneClassifier
        from convertir3x4.engine.vocab import VOCAB
        clf = SceneClassifier(_MODELS, threads=2)
        ranked = clf.classify(Image.new("RGB", (640, 480), "white"))
        assert {n for n, _ in ranked} == set(VOCAB)
        assert abs(sum(p for _, p in ranked) - 1) < 1e-4

    def test_no_face_in_plain_image(self):
        eng = faces_mod.FaceEngine(_MODELS)
        assert eng.reference_feature(Image.new("RGB", (640, 480), "white")) is None
        assert eng.recognize(Image.new("RGB", (640, 480), "white"), {}) == (0, {})
