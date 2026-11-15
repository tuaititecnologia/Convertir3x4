"""Clasificación por escena con CLIP (ONNX Runtime, solo CPU).

Compara la imagen contra el vocabulario de la iglesia (`vocab.py`) y propone
un tag solo cuando está muy seguro (`SCENE_ACCEPT`). Sin Qt: corre en workers.
"""

import os

import numpy as np
from PIL import Image

from .vocab import VOCAB

LOGIT_SCALE = 100.0     # el de CLIP
SCENE_ACCEPT = 0.60     # prob mínima para proponer el tag (validado en spikes/spike_vision.py)

_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


def _session(path: str, threads: int):
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.intra_op_num_threads = threads
    return ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])


class SceneClassifier:
    def __init__(self, models_dir: str, threads: int = 4, vocab: dict | None = None):
        from tokenizers import Tokenizer
        self._vision = _session(os.path.join(models_dir, "clip_vision.onnx"), threads)
        text = _session(os.path.join(models_dir, "clip_text.onnx"), threads)
        tok = Tokenizer.from_file(os.path.join(models_dir, "clip_tokenizer.json"))

        vocab = vocab or VOCAB
        self.names = list(vocab)
        vecs = []
        for name in self.names:
            phrases = [_normalize(text.run(None, {"input_ids": np.array(
                [tok.encode(p).ids], dtype=np.int64)})[0][0]) for p in vocab[name]]
            vecs.append(_normalize(np.mean(phrases, axis=0)))
        self._tags = np.stack(vecs)
        # el modelo de texto solo hace falta para armar el vocabulario
        del text

    def classify(self, img: Image.Image) -> list[tuple[str, float]]:
        """Todas las etiquetas con su probabilidad, de mayor a menor."""
        emb = _normalize(self._vision.run(None, {"pixel_values": _preprocess(img)})[0][0])
        logits = LOGIT_SCALE * (self._tags @ emb)
        prob = np.exp(logits - logits.max())
        prob /= prob.sum()
        order = np.argsort(prob)[::-1]
        return [(self.names[i], float(prob[i])) for i in order]


def best_tag(ranked: list[tuple[str, float]]) -> str | None:
    """El tag a proponer, o None si el modelo no está seguro."""
    if ranked and ranked[0][1] >= SCENE_ACCEPT:
        return ranked[0][0]
    return None


def _normalize(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def _preprocess(img: Image.Image) -> np.ndarray:
    """Resize del lado corto a 224 + recorte central, normalizado como CLIP."""
    img = img.convert("RGB")
    w, h = img.size
    s = 224 / min(w, h)
    img = img.resize((max(224, round(w * s)), max(224, round(h * s))), Image.BICUBIC)
    w, h = img.size
    left, top = (w - 224) // 2, (h - 224) // 2
    img = img.crop((left, top, left + 224, top + 224))
    x = (np.asarray(img, dtype=np.float32) / 255.0 - _MEAN) / _STD
    return x.transpose(2, 0, 1)[None]
