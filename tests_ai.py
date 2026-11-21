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
        assert repo.list_persons() == [{"id": pid, "name": "Juan Carlos Bazán", "listas": 0,
                                         "pendientes": 0, "sin_cara": []}]
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
