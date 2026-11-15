"""Pasada de IA sobre el catálogo: escena (CLIP) + personas de la lista (caras).

Corre después de la Etapa 0, sobre lo que ya está catalogado. Es idempotente:
analiza solo lo nuevo, lo que se analizó con otro modelo/vocabulario, o (solo
caras) lo que se analizó con otra lista de personas.

Todo lo que propone queda como tag `source='ia'` sin confirmar, y nunca pisa
ni repone un tag que una persona puso o sacó.
"""

import json
import os
from dataclasses import dataclass

from PIL import Image, ImageOps

from ..catalog.repo import Repo
from . import faces as faces_mod
from .vision import SceneClassifier, best_tag
from .vocab import VOCAB_VERSION

AI_MODEL = f"clip-vit-b32/vocab{VOCAB_VERSION}"
_DECODE_SIDE = 1280   # alcanza para CLIP (224) y para detectar caras


@dataclass
class AIStats:
    analyzed: int = 0
    scene_tags: int = 0
    people_tags: int = 0
    missing_files: int = 0
    errors: int = 0


def load_for_ai(path: str) -> Image.Image:
    img = Image.open(path)
    img.draft("RGB", (_DECODE_SIDE, _DECODE_SIDE))   # JPEG: decodifica ya achicado
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def reference_embedding(engine: faces_mod.FaceEngine, path: str) -> bytes | None:
    """Firma de una foto de referencia (la cara más grande), o None si no hay cara."""
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    v = engine.reference_feature(img)
    return None if v is None else faces_mod.to_blob(v)


def run_ai(repo: Repo, models_dir: str, progress_cb=None, threads: int = 4) -> AIStats:
    """`progress_cb(done, total, path)` opcional."""
    sig = repo.people_signature()
    refs = {name: [faces_mod.from_blob(b) for b in blobs]
            for name, blobs in repo.person_reference_blobs().items()}
    todo = repo.media_for_ai(AI_MODEL, sig)
    stats = AIStats()
    total = len(todo)
    if not total:
        return stats

    clf = SceneClassifier(models_dir, threads) if any(s for _, s in todo) else None
    face_engine = faces_mod.FaceEngine(models_dir) if refs else None

    for i, (m, need_scene) in enumerate(todo, 1):
        # Video: se analiza su miniatura.
        src = m.thumb_path if m.kind == "video" else m.path
        if not src or not os.path.exists(src):
            stats.missing_files += 1      # sin guardar: se reintenta en la próxima
            _progress(progress_cb, i, total, m.path)
            continue
        try:
            img = load_for_ai(src)
        except Exception as e:  # noqa: BLE001 — archivo ilegible: se anota y se sigue
            stats.errors += 1
            repo.save_ai_result(m.id, AI_MODEL, sig, None, [],
                                json.dumps({"error": str(e)[:200]}))
            _progress(progress_cb, i, total, m.path)
            continue

        raw = _previous_raw(repo, m.id)
        if need_scene:
            ranked = clf.classify(img)
            raw["scene"] = [[n, round(p, 3)] for n, p in ranked[:3]]
            tag = best_tag(ranked)
            raw["scene_tag"] = tag
            if tag and repo.suggest_tag(m.id, tag):
                stats.scene_tags += 1

        people: dict[str, float] = {}
        if face_engine is not None:
            n_faces, people = face_engine.recognize(img, refs)
            raw["faces"] = n_faces
            for name in people:
                if repo.suggest_tag(m.id, name, kind="persona"):
                    stats.people_tags += 1
        raw["people"] = {n: round(s, 3) for n, s in people.items()}

        repo.save_ai_result(m.id, AI_MODEL, sig, raw.get("scene_tag"),
                            sorted(people), json.dumps(raw, ensure_ascii=False))
        stats.analyzed += 1
        _progress(progress_cb, i, total, m.path)

    return stats


def _previous_raw(repo: Repo, media_id: int) -> dict:
    prev = repo.get_ai_raw(media_id)
    if not prev:
        return {}
    try:
        d = json.loads(prev)
        return d if isinstance(d, dict) and "error" not in d else {}
    except ValueError:
        return {}


def _progress(cb, done, total, path):
    if cb:
        cb(done, total, path)
