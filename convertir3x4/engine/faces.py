"""Reconocimiento de caras SOLO para personas cargadas en una lista.

Decisión de la iglesia (31/10/2026): se reconoce a pastores y gente importante
que alguien cargó a mano. La congregación no se identifica. Una cara que no
coincide con nadie de la lista se descarta: no se devuelve su firma, no se
guarda y no se agrupa con otras.

Detección: YuNet. Firma de la cara: SFace. Ambos de OpenCV Zoo (Apache 2.0).
"""

import os
from dataclasses import dataclass

import numpy as np
from PIL import Image

FACE_SCORE = 0.85       # confianza mínima de YuNet
FACE_MIN_PX = 40        # caras más chicas no se intentan reconocer
MATCH_COSINE = 0.42     # validado a ojo sobre el archivo (OpenCV sugiere 0.363: falsos positivos)
DETECT_MAX_SIDE = 1280  # las fotos grandes se achican antes de detectar
REF_MIN_SIDE = 800      # las referencias chicas se agrandan para que YuNet las vea
OTRA_CARA = 0.5         # en una referencia, otra cara de al menos la mitad del lado de la
                        # elegida se cuenta: puede ser de otra persona (la de atrás, no)


@dataclass
class RevisionReferencia:
    """Lo que vio el NAS en una foto de referencia.

    `firma`: la de la cara más grande (None si no hay cara). `caras`: cuántas
    caras de tamaño parecido hay, contando la elegida. `caja`: (x, y, ancho,
    alto) de la elegida, en píxeles de la foto recibida. `confianza`: la del
    detector para esa cara (0 a 1).
    """
    firma: np.ndarray | None
    caras: int = 0
    caja: tuple[int, int, int, int] | None = None
    confianza: float | None = None


class FaceEngine:
    def __init__(self, models_dir: str):
        import cv2
        self._cv2 = cv2
        self._det = cv2.FaceDetectorYN.create(
            os.path.join(models_dir, "yunet.onnx"), "", (320, 320), FACE_SCORE, 0.3, 5000)
        self._rec = cv2.FaceRecognizerSF.create(os.path.join(models_dir, "sface.onnx"), "")

    def _detect(self, bgr: np.ndarray) -> list:
        h, w = bgr.shape[:2]
        self._det.setInputSize((w, h))
        _, faces = self._det.detect(bgr)
        return [] if faces is None else list(faces)

    def _feature(self, bgr: np.ndarray, face) -> np.ndarray:
        f = self._rec.feature(self._rec.alignCrop(bgr, face))[0]
        return (f / np.linalg.norm(f)).astype(np.float32)

    def _bgr(self, img: Image.Image, max_side: int) -> np.ndarray:
        img = img.convert("RGB")
        if max(img.size) > max_side:
            img = img.copy()
            img.thumbnail((max_side, max_side))
        return self._cv2.cvtColor(np.asarray(img), self._cv2.COLOR_RGB2BGR)

    def reference_feature(self, img: Image.Image) -> np.ndarray | None:
        """Firma de la cara más grande de una foto de referencia, o None."""
        return self.revisar_referencia(img).firma

    def revisar_referencia(self, img: Image.Image) -> RevisionReferencia:
        """La firma de la cara más grande y lo necesario para juzgar si la foto
        sirve: cuántas caras parecidas hay, dónde está la elegida y qué tan clara."""
        ancho = img.width
        if max(img.size) < REF_MIN_SIDE:
            k = REF_MIN_SIDE / max(img.size)
            img = img.convert("RGB").resize(
                (round(img.width * k), round(img.height * k)), Image.LANCZOS)
        bgr = self._bgr(img, 1600)
        found = self._detect(bgr)
        if not found:
            return RevisionReferencia(None)
        cara = max(found, key=lambda f: f[2] * f[3])
        lado = min(cara[2], cara[3])
        caras = sum(1 for f in found if min(f[2], f[3]) >= OTRA_CARA * lado)
        k = ancho / bgr.shape[1]           # de vuelta a píxeles de la foto recibida
        caja = tuple(int(round(float(v) * k)) for v in cara[:4])
        return RevisionReferencia(self._feature(bgr, cara), caras, caja,
                                  round(float(cara[-1]), 3))

    def recognize(self, img: Image.Image,
                  refs: dict[str, list[np.ndarray]]) -> tuple[int, dict[str, float]]:
        """Devuelve (caras detectadas, {persona: mejor puntaje}) solo de la lista.

        Las firmas de las caras se calculan en memoria y se tiran acá mismo.
        """
        bgr = self._bgr(img, DETECT_MAX_SIDE)
        found = self._detect(bgr)
        people: dict[str, float] = {}
        if not refs:
            return len(found), people
        for f in found:
            if min(f[2], f[3]) < FACE_MIN_PX:
                continue
            who, score = match(self._feature(bgr, f), refs)
            if who and score > people.get(who, 0.0):
                people[who] = score
        return len(found), people


def match(feat: np.ndarray, refs: dict[str, list[np.ndarray]]) -> tuple[str | None, float]:
    best, best_s = None, -1.0
    for person, feats in refs.items():
        s = max(float(feat @ r) for r in feats)
        if s > best_s:
            best, best_s = person, s
    return (best, best_s) if best_s >= MATCH_COSINE else (None, best_s)


def to_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)
