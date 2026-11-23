"""Texto en las fotos (OCR), pedido del 23/11/2026.

Lee el texto que aparece en una foto (carteles, banners, flyers, pantallas)
para encontrarla buscando esas palabras y mostrarlo en el detalle de Buscar.
Solo fotos (decisión del 23/11: los videos no).

Modelos de PaddleOCR exportados a ONNX (Apache 2.0, `ai_models.OCR_FILES`):
- detección PP-OCRv3 mobile (2,4 MB): dónde hay renglones de texto;
- reconocimiento PP-OCRv5 latin mobile (7,9 MB, incluye español con tildes y ñ).

Corre con el onnxruntime y el OpenCV que el catalogador ya tiene: el pre y el
post proceso (DB + CTC) están acá, sin la librería de PaddleOCR ni pyclipper.
Sin Qt; solo lo usa el catalogador del NAS.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

import numpy as np

# Subir cuando cambie el modelo o la lectura: el NAS vuelve a leer las fotos una vez.
OCR_VERSION = 1

_LADO_MAX = 960          # la foto se achica a este lado mayor para detectar
_UMBRAL = 0.3            # píxel con texto
_UMBRAL_CAJA = 0.6       # renglón con texto (promedio dentro de la caja)
_AGRANDAR = 1.5          # la detección marca el centro del renglón: se agranda
_MIN_LADO = 3
_ALTO_REC = 48
_ANCHO_REC_MAX = 1600
_CONFIANZA = 0.6         # renglones con menos confianza se descartan
_MIN_LETRAS = 3          # «DE», «TO», «0»: casi siempre ruido de la foto
_LADO_FOTO = 1600        # la foto se abre achicada (JPEG: mucho más rápido)
_MEDIA = np.array([0.485, 0.456, 0.406], np.float32)
_DESVIO = np.array([0.229, 0.224, 0.225], np.float32)


@dataclass
class Renglon:
    texto: str
    confianza: float
    caja: np.ndarray            # 4 puntos (x, y) en la foto original


class LectorDeTexto:
    """Carga los modelos una vez; `leer(img)` devuelve el texto de la foto."""

    def __init__(self, models_dir: str):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, min(2, os.cpu_count() or 1))
        prov = ["CPUExecutionProvider"]
        self._det = ort.InferenceSession(os.path.join(models_dir, "ocr_det.onnx"), opts,
                                         providers=prov)
        self._rec = ort.InferenceSession(os.path.join(models_dir, "ocr_rec_latin.onnx"), opts,
                                         providers=prov)
        with open(os.path.join(models_dir, "ocr_dict_latin.txt"), encoding="utf-8") as f:
            letras = [ln.rstrip("\r\n") for ln in f]
        # CTC: 0 es «nada»; después el diccionario; al final el espacio.
        self._letras = [""] + letras + [" "]

    # ------------------------------------------------------------------ todo
    def leer(self, img) -> str:
        """`img`: PIL.Image (ya derecha según el EXIF). Renglones de arriba a
        abajo y de izquierda a derecha, uno por línea."""
        return "\n".join(r.texto for r in self.renglones(img))

    def renglones(self, img) -> list[Renglon]:
        rgb = np.asarray(img.convert("RGB"))
        cajas = self._detectar(rgb)
        out = []
        for caja in _ordenar(cajas):
            recorte = _recortar(rgb, caja)
            if recorte is None:
                continue
            texto, conf = self._reconocer(recorte)
            texto = " ".join(texto.split())
            if conf >= _CONFIANZA and sum(c.isalnum() for c in texto) >= _MIN_LETRAS:
                out.append(Renglon(texto, conf, caja))
        return out

    # ------------------------------------------------------------- detección
    def _detectar(self, rgb: np.ndarray) -> list[np.ndarray]:
        import cv2
        h, w = rgb.shape[:2]
        escala = min(1.0, _LADO_MAX / max(h, w))
        nh = max(32, int(round(h * escala / 32)) * 32)
        nw = max(32, int(round(w * escala / 32)) * 32)
        x = cv2.resize(rgb, (nw, nh)).astype(np.float32) / 255.0
        x = ((x - _MEDIA) / _DESVIO).transpose(2, 0, 1)[None]
        mapa = self._det.run(None, {self._det.get_inputs()[0].name: x})[0][0, 0]
        binario = (mapa > _UMBRAL).astype(np.uint8)
        contornos, _ = cv2.findContours(binario, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        fx, fy = w / nw, h / nh
        cajas = []
        for c in contornos[:1000]:
            rect = cv2.minAreaRect(c)
            if min(rect[1]) < _MIN_LADO:
                continue
            if _puntaje(mapa, c) < _UMBRAL_CAJA:
                continue
            (cx, cy), (rw, rh), ang = rect
            d = rw * rh * _AGRANDAR / max(2 * (rw + rh), 1e-6)
            rw, rh = rw + 2 * d, rh + 2 * d
            if min(rw, rh) < _MIN_LADO + 2:
                continue
            caja = cv2.boxPoints(((cx, cy), (rw, rh), ang))
            caja[:, 0] = np.clip(caja[:, 0] * fx, 0, w - 1)
            caja[:, 1] = np.clip(caja[:, 1] * fy, 0, h - 1)
            cajas.append(_en_orden(caja))
        return cajas

    # -------------------------------------------------------- reconocimiento
    def _reconocer(self, recorte: np.ndarray) -> tuple[str, float]:
        import cv2
        h, w = recorte.shape[:2]
        nw = min(_ANCHO_REC_MAX, max(8, int(math.ceil(_ALTO_REC * w / h))))
        x = cv2.resize(recorte, (nw, _ALTO_REC)).astype(np.float32) / 255.0
        x = ((x - 0.5) / 0.5).transpose(2, 0, 1)[None]
        prob = self._rec.run(None, {self._rec.get_inputs()[0].name: x})[0][0]
        idx = prob.argmax(axis=1)
        conf = prob.max(axis=1)
        letras, confs, previo = [], [], -1
        for i, p in zip(idx, conf):
            if i != previo and i != 0 and i < len(self._letras):
                letras.append(self._letras[i])
                confs.append(float(p))
            previo = i
        return "".join(letras), (sum(confs) / len(confs) if confs else 0.0)


def _puntaje(mapa: np.ndarray, contorno) -> float:
    """Promedio del mapa dentro del contorno (como `box_score_fast` de Paddle)."""
    import cv2
    x, y, w, h = cv2.boundingRect(contorno)
    mascara = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mascara, [contorno.reshape(-1, 2) - [x, y]], 1)
    return float(cv2.mean(mapa[y:y + h, x:x + w], mascara)[0])


def _en_orden(caja: np.ndarray) -> np.ndarray:
    """Arriba-izquierda, arriba-derecha, abajo-derecha, abajo-izquierda."""
    p = caja[np.argsort(caja[:, 0])]
    izq, der = p[:2], p[2:]
    ai, bi = izq[np.argsort(izq[:, 1])]
    ad, bd = der[np.argsort(der[:, 1])]
    return np.array([ai, ad, bd, bi], np.float32)


def _ordenar(cajas: list[np.ndarray]) -> list[np.ndarray]:
    """De arriba a abajo; en el mismo renglón (±10 px), de izquierda a derecha."""
    cajas = sorted(cajas, key=lambda c: (c[0, 1], c[0, 0]))
    for i in range(len(cajas) - 1):
        for j in range(i, -1, -1):
            a, b = cajas[j], cajas[j + 1]
            if abs(b[0, 1] - a[0, 1]) < 10 and b[0, 0] < a[0, 0]:
                cajas[j], cajas[j + 1] = b, a
            else:
                break
    return cajas


def _recortar(rgb: np.ndarray, caja: np.ndarray) -> np.ndarray | None:
    """El renglón enderezado; si es más alto que ancho, se gira (texto vertical)."""
    import cv2
    w = int(max(np.linalg.norm(caja[0] - caja[1]), np.linalg.norm(caja[2] - caja[3])))
    h = int(max(np.linalg.norm(caja[0] - caja[3]), np.linalg.norm(caja[1] - caja[2])))
    if w < 4 or h < 4:
        return None
    destino = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
    m = cv2.getPerspectiveTransform(caja.astype(np.float32), destino)
    recorte = cv2.warpPerspective(rgb, m, (w, h), borderMode=cv2.BORDER_REPLICATE,
                                  flags=cv2.INTER_CUBIC)
    if h / w >= 1.5:
        recorte = np.rot90(recorte)
    return np.ascontiguousarray(recorte)


# ------------------------------------------------------------------ la pasada

@dataclass
class EstadisticasTexto:
    leidas: int = 0
    con_texto: int = 0
    errores: int = 0


def abrir_foto(path: str):
    """La foto derecha (EXIF) y achicada: el texto se lee igual y abrir una
    foto de 7360 px entera tarda mucho en el NAS."""
    from PIL import Image, ImageOps
    img = Image.open(path)
    img.draft("RGB", (_LADO_FOTO, _LADO_FOTO))
    img = ImageOps.exif_transpose(img)
    img.thumbnail((_LADO_FOTO, _LADO_FOTO))
    return img


def leer_fotos(repo, models_dir: str, progress_cb=None, carpetas=None,
               rehacer: bool = False) -> EstadisticasTexto:
    """Lee el texto de las fotos que falten (todas con `rehacer`) y lo guarda.
    Una foto que no se puede abrir no se guarda: se reintenta la próxima vez."""
    st = EstadisticasTexto()
    fotos = repo.fotos_sin_texto(OCR_VERSION, carpetas, rehacer)
    if not fotos:
        return st
    lector = LectorDeTexto(models_dir)
    for i, m in enumerate(fotos):
        if progress_cb:
            progress_cb(i, len(fotos), m.path)
        try:
            texto = lector.leer(abrir_foto(m.path))
        except Exception:  # noqa: BLE001 — una foto dañada no corta la pasada
            st.errores += 1
            continue
        repo.guardar_texto(m.id, texto, OCR_VERSION)
        st.leidas += 1
        st.con_texto += bool(texto)
    if progress_cb:
        progress_cb(len(fotos), len(fotos), "")
    return st
