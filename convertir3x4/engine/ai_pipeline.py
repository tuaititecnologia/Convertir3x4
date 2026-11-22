"""Pasada de IA sobre el catálogo: escena (CLIP) + personas de la lista (caras).

Corre después de la Etapa 0, sobre lo que ya está catalogado. Es idempotente:
analiza solo lo nuevo, lo que se analizó con otro modelo/vocabulario, o (solo
caras) lo que se analizó con otra lista de personas.

Todo lo que propone queda como tag `source='ia'` sin confirmar, y nunca pisa
ni repone un tag que una persona puso o sacó.
"""

import io
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


def load_for_ai(path) -> Image.Image:
    """`path`: ruta o archivo en memoria (BytesIO)."""
    img = Image.open(path)
    img.draft("RGB", (_DECODE_SIDE, _DECODE_SIDE))   # JPEG: decodifica ya achicado
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def reference_embedding(engine: faces_mod.FaceEngine, path: str) -> bytes | None:
    """Firma de una foto de referencia (la cara más grande), o None si no hay cara."""
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    v = engine.reference_feature(img)
    return None if v is None else faces_mod.to_blob(v)


def run_ai(repo: Repo, models_dir: str, progress_cb=None, threads: int = 4,
           carpetas: list[str] | None = None, escenas: bool = True,
           personas: bool = True, rehacer: bool = False) -> AIStats:
    """`progress_cb(done, total, path)` opcional. `carpetas` (relativas) limita a
    esas carpetas; `escenas` / `personas`: qué partes correr; `rehacer`: también
    lo ya analizado. Una parte que no se corrió queda como estaba (su modelo o
    su firma de personas no se marcan al día)."""
    sig = repo.people_signature()
    refs = {name: [faces_mod.from_blob(b) for b in blobs]
            for name, blobs in repo.person_reference_blobs().items()}
    todo = repo.pendientes_de_ia(AI_MODEL, sig, carpetas=carpetas, escenas=escenas,
                                 personas=personas, rehacer=rehacer)
    stats = AIStats()
    total = len(todo)
    if not total:
        return stats

    clf = SceneClassifier(models_dir, threads) if any(p.escena for p in todo) else None
    face_engine = faces_mod.FaceEngine(models_dir) if refs and any(p.caras for p in todo) \
        else None

    for i, p in enumerate(todo, 1):
        m = p.media
        modelo = AI_MODEL if p.escena else p.modelo
        firma = sig if p.caras else p.firma
        # Video: se analiza su miniatura (la de la base).
        if m.kind == "video":
            jpeg = repo.miniatura(m.id)
            src = io.BytesIO(jpeg) if jpeg else None
        else:
            src = m.path if os.path.exists(m.path) else None
        if src is None:
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
        if p.escena:
            ranked = clf.classify(img)
            raw["scene"] = [[n, round(pr, 3)] for n, pr in ranked[:3]]
            tag = best_tag(ranked)
            raw["scene_tag"] = tag
            if tag and repo.suggest_tag(m.id, tag):
                stats.scene_tags += 1

        if p.caras:
            people: dict[str, float] = {}
            if face_engine is not None:
                n_faces, people = face_engine.recognize(img, refs)
                raw["faces"] = n_faces
                for name in people:
                    if repo.suggest_tag(m.id, name, kind="persona"):
                        stats.people_tags += 1
            raw["people"] = {n: round(s, 3) for n, s in people.items()}

        repo.save_ai_result(m.id, modelo, firma, raw.get("scene_tag"),
                            sorted(raw.get("people") or {}),
                            json.dumps(raw, ensure_ascii=False))
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
