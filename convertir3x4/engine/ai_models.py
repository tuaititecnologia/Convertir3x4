"""Modelos de IA: qué archivos hacen falta, de dónde se bajan y cómo se verifican.

Los modelos NO van dentro del ejecutable (pesan ~650 MB). Se bajan una sola vez
a la carpeta de datos de la app. Cada archivo está fijado a una revisión exacta
del repositorio en HuggingFace y se verifica por sha256: si el hash no coincide,
se descarta.

Licencias: CLIP (MIT, export ONNX de Xenova), YuNet y SFace (Apache 2.0, OpenCV Zoo),
OCR de PaddleOCR (Apache 2.0, export ONNX de monkt/paddleocr-onnx; engine/ocr.py).
"""

import hashlib
import os
import ssl
import urllib.request

_CLIP = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/6b7c523703ae26e39be4b03774966f8571c7f523"
_YUNET = "https://huggingface.co/opencv/face_detection_yunet/resolve/3cc26e7f1014a5ee5d74a42acee58bafc9d0a310"
_SFACE = "https://huggingface.co/opencv/face_recognition_sface/resolve/3d7082438a6e4551e840c9b2bb60b71e8da4b524"
_OCR = "https://huggingface.co/monkt/paddleocr-onnx/resolve/7b02d0a30a07ba2b92ad1ff5a8941ae2c633de65"

# nombre local -> (url, sha256, tamaño en bytes)
MODEL_FILES = {
    "clip_vision.onnx": (f"{_CLIP}/onnx/vision_model.onnx",
                         "fd6e1402a588279d1723c7534d4bcba5bc0b14b47dfab0e46f8c47b8270d7d40", 351685709),
    "clip_text.onnx": (f"{_CLIP}/onnx/text_model.onnx",
                       "3f6571f5bad13a97c469c1622e1cfc4d9aef78b79fdbfcff804ca357bfada8cc", 254058553),
    "clip_tokenizer.json": (f"{_CLIP}/tokenizer.json",
                            "f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd", 2224119),
    "yunet.onnx": (f"{_YUNET}/face_detection_yunet_2023mar.onnx",
                   "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4", 232589),
    "sface.onnx": (f"{_SFACE}/face_recognition_sface_2021dec.onnx",
                   "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79", 38696353),
    # Texto en las fotos: detección PP-OCRv3 mobile + reconocimiento PP-OCRv5 latin
    "ocr_det.onnx": (f"{_OCR}/detection/v3/det.onnx",
                     "ee40e80071ba3a320d4efda75f3e22047a7d049e9bf7bcaaf9daea23fc21b935", 2429873),
    "ocr_rec_latin.onnx": (f"{_OCR}/languages/latin/rec.onnx",
                           "614ffc2d6d3902d360fad7f1b0dd455ee45e877069d14c4e51a99dc4ef144409",
                           7862832),
    "ocr_dict_latin.txt": (f"{_OCR}/languages/latin/dict.txt",
                           "3c0a8a79b612653c25f765271714f71281e4e955962c153e272b7b8c1d2b13ff", 1634),
}

_CHUNK = 1 << 20


def missing(models_dir: str) -> list[str]:
    """Archivos que faltan (o quedaron a medio bajar). No recalcula hashes."""
    out = []
    for name, (_, _, size) in MODEL_FILES.items():
        p = os.path.join(models_dir, name)
        if not os.path.exists(p) or os.path.getsize(p) != size:
            out.append(name)
    return out


def total_bytes(names: list[str]) -> int:
    return sum(MODEL_FILES[n][2] for n in names)


def ensure(models_dir: str, progress_cb=None):
    """Baja lo que falte. `progress_cb(done_bytes, total_bytes, nombre)`."""
    os.makedirs(models_dir, exist_ok=True)
    todo = missing(models_dir)
    total = total_bytes(todo)
    done = 0
    for name in todo:
        url, sha, size = MODEL_FILES[name]
        dst = os.path.join(models_dir, name)
        part = dst + ".part"
        h = hashlib.sha256()
        with urllib.request.urlopen(url, context=_ssl_context(), timeout=60) as r, \
                open(part, "wb") as fh:
            while True:
                chunk = r.read(_CHUNK)
                if not chunk:
                    break
                fh.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, total, name)
        if h.hexdigest() != sha:
            os.remove(part)
            raise RuntimeError(f"El modelo {name} se descargó dañado. Intente nuevamente.")
        os.replace(part, dst)


def _ssl_context():
    # En el ejecutable empaquetado puede no haber certificados del sistema:
    # si certifi está disponible, usarlo.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()
