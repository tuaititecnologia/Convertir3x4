"""Spike: clasificación por escena (CLIP) + caras (YuNet/SFace) sobre fotos reales.

No toca la app ni la base. Toma una muestra al azar de imágenes, propone tags del
vocabulario de la iglesia, cuenta caras y (si se pasa --people) reconoce SOLO a
las personas de la lista. Las caras que no coinciden se descartan: no se guarda
ni se muestra nada de ellas más que el conteo.

Genera un reporte HTML con miniaturas y mide tiempos por etapa.

Uso:
    ./venv/bin/python spikes/spike_vision.py CARPETA --models DIR [--sample 150]
        [--quantized] [--threads 6] [--people DIR] [--out reporte.html]

--people DIR: una subcarpeta por persona (el nombre de la carpeta es el nombre
de la persona) con 3–5 fotos donde se la vea bien.

Modelos esperados en --models:
    vision_model.onnx / vision_model_quantized.onnx, text_model.onnx,
    tokenizer.json (Xenova/clip-vit-base-patch32), yunet.onnx, sface.onnx
"""

import argparse
import base64
import html
import io
import os
import random
import time
from collections import Counter, defaultdict

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageOps
from tokenizers import Tokenizer

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}

# Tag en español -> frases en inglés (CLIP entiende mejor el inglés).
VOCAB = {
    "alabanza": ["a worship band playing music on a church stage",
                 "people worshipping with raised hands at a concert",
                 "a singer with a microphone leading worship"],
    "predicación": ["a pastor preaching from a pulpit",
                    "a man speaking to an audience on a stage with a microphone",
                    "a preacher holding a bible on stage"],
    "bautismo": ["a baptism in a pool of water",
                 "a person being baptized in water by a pastor"],
    "oración": ["people praying with closed eyes",
                "a group of people laying hands and praying"],
    "congregación": ["a crowd of people seated in a church auditorium",
                     "the audience of a church service seen from the stage"],
    "niños": ["children playing at a church kids ministry",
              "a group of kids with a teacher"],
    "santa cena": ["communion bread and wine on a table",
                   "a cup of grape juice and bread for communion"],
    "boda": ["a wedding ceremony with a bride and groom"],
    "foto grupal": ["a group of people posing for a photo",
                    "a team photo of smiling people"],
    "retrato": ["a portrait photo of one person",
                "a selfie of a person"],
    "comida": ["people sharing a meal at tables",
               "a table with food"],
    "edificio": ["the exterior of a church building",
                 "a building facade on a street"],
    "paisaje": ["a landscape of nature", "a sunset sky"],
    "fondo": ["an abstract colorful background texture",
              "a blurred bokeh background with lights",
              "a worship background graphic without text"],
    "flyer": ["an event flyer poster with text and graphics",
              "a promotional banner with a date and title"],
    "texto": ["a slide with a bible verse written on it",
              "text on a plain background",
              "a quote written in large letters"],
    "logo": ["a logo on a plain background", "an emblem or brand mark"],
    "captura": ["a screenshot of a phone screen",
                "a screenshot of a chat conversation"],
    "documento": ["a photo of a paper document", "a scanned page with text"],
}

LOGIT_SCALE = 100.0           # el de CLIP
ACCEPT = 0.60                 # prob mínima para que el spike "ponga" el tag
FACE_SCORE = 0.85             # YuNet
FACE_MIN_PX = 40              # caras más chicas no se intentan reconocer
SFACE_COSINE = 0.42           # validado a ojo sobre el archivo (OpenCV sugiere 0.363: da falsos positivos)

MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


# ------------------------------------------------------------------ CLIP
class Clip:
    def __init__(self, models: str, quantized: bool, threads: int):
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        prov = ["CPUExecutionProvider"]
        vis = "vision_model_quantized.onnx" if quantized else "vision_model.onnx"
        self.vision = ort.InferenceSession(os.path.join(models, vis), so, providers=prov)
        self.text = ort.InferenceSession(os.path.join(models, "text_model.onnx"), so, providers=prov)
        self.tok = Tokenizer.from_file(os.path.join(models, "tokenizer.json"))

    def embed_text(self, phrase: str) -> np.ndarray:
        ids = np.array([self.tok.encode(phrase).ids], dtype=np.int64)
        v = self.text.run(None, {"input_ids": ids})[0][0]
        return v / np.linalg.norm(v)

    def embed_image(self, img: Image.Image) -> np.ndarray:
        img = img.convert("RGB")
        w, h = img.size
        s = 224 / min(w, h)
        img = img.resize((max(224, round(w * s)), max(224, round(h * s))), Image.BICUBIC)
        w, h = img.size
        l, t = (w - 224) // 2, (h - 224) // 2
        img = img.crop((l, t, l + 224, t + 224))
        x = (np.asarray(img, dtype=np.float32) / 255.0 - MEAN) / STD
        x = x.transpose(2, 0, 1)[None]
        v = self.vision.run(None, {"pixel_values": x})[0][0]
        return v / np.linalg.norm(v)


def build_tag_matrix(clip: Clip):
    names, vecs = [], []
    for name, phrases in VOCAB.items():
        m = np.mean([clip.embed_text(p) for p in phrases], axis=0)
        names.append(name)
        vecs.append(m / np.linalg.norm(m))
    return names, np.stack(vecs)


# ------------------------------------------------------------------ caras
class Faces:
    def __init__(self, models: str):
        self.det = cv2.FaceDetectorYN.create(
            os.path.join(models, "yunet.onnx"), "", (320, 320), FACE_SCORE, 0.3, 5000)
        self.rec = cv2.FaceRecognizerSF.create(os.path.join(models, "sface.onnx"), "")

    def detect(self, bgr: np.ndarray):
        h, w = bgr.shape[:2]
        self.det.setInputSize((w, h))
        _, faces = self.det.detect(bgr)
        return [] if faces is None else list(faces)

    def feature(self, bgr, face) -> np.ndarray:
        crop = self.rec.alignCrop(bgr, face)
        f = self.rec.feature(crop)[0]
        return f / np.linalg.norm(f)


def load_people(faces: Faces, people_dir: str) -> dict[str, list[np.ndarray]]:
    """Firma de referencia de cada persona listada: la cara más grande de cada foto."""
    refs = {}
    for person in sorted(os.listdir(people_dir)):
        pdir = os.path.join(people_dir, person)
        if not os.path.isdir(pdir):
            continue
        feats = []
        for fn in sorted(os.listdir(pdir)):
            if os.path.splitext(fn)[1].lower() not in IMAGE_EXTS:
                continue
            img = load_image(os.path.join(pdir, fn))
            if max(img.size) < 800:  # referencias chicas (bajadas de la web)
                k = 800 / max(img.size)
                img = img.resize((round(img.width * k), round(img.height * k)), Image.LANCZOS)
            bgr = to_bgr(img, 1600)
            found = faces.detect(bgr)
            if not found:
                print(f"  [aviso] sin cara en {person}/{fn}")
                continue
            big = max(found, key=lambda f: f[2] * f[3])
            feats.append(faces.feature(bgr, big))
            print(f"    {fn}: cara de {int(big[2])}x{int(big[3])} px (sobre {bgr.shape[1]}x{bgr.shape[0]})")
        if feats:
            refs[person] = feats
            print(f"  {person}: {len(feats)} fotos de referencia")
    return refs


def match_person(feat, refs) -> tuple[str | None, float]:
    best, best_s = None, -1.0
    for person, feats in refs.items():
        s = max(float(feat @ r) for r in feats)
        if s > best_s:
            best, best_s = person, s
    return (best, best_s) if best_s >= SFACE_COSINE else (None, best_s)


# ------------------------------------------------------------------ util
def load_image(path: str) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def to_bgr(img: Image.Image, max_side: int) -> np.ndarray:
    if max(img.size) > max_side:
        img = img.copy()
        img.thumbnail((max_side, max_side))
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


def thumb_b64(img: Image.Image) -> str:
    t = img.copy()
    t.thumbnail((220, 220))
    buf = io.BytesIO()
    t.save(buf, "JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode()


def scan(root: str) -> list[str]:
    out = []
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if os.path.splitext(fn)[1].lower() in IMAGE_EXTS and not fn.startswith("."):
                out.append(os.path.join(dp, fn))
    return sorted(out)


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--models", required=True)
    ap.add_argument("--sample", type=int, default=150)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--quantized", action="store_true")
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--people")
    ap.add_argument("--out", default="spike_vision.html")
    ap.add_argument("--json", help="vuelca las predicciones (sin miniaturas) a JSON")
    a = ap.parse_args()

    paths = scan(a.folder)
    random.Random(a.seed).shuffle(paths)
    paths = paths[: a.sample]
    print(f"{len(paths)} imágenes de muestra")

    clip = Clip(a.models, a.quantized, a.threads)
    faces = Faces(a.models)
    names, tag_mat = build_tag_matrix(clip)
    refs = load_people(faces, a.people) if a.people else {}

    t = defaultdict(float)
    rows, accepted, face_total, recognized = [], Counter(), 0, Counter()
    for i, p in enumerate(paths, 1):
        t0 = time.perf_counter()
        try:
            img = load_image(p)
        except Exception as e:  # noqa: BLE001 — spike: reportar y seguir
            print(f"  [error] {p}: {e}")
            continue
        t1 = time.perf_counter()
        emb = clip.embed_image(img)
        logits = LOGIT_SCALE * (tag_mat @ emb)
        prob = np.exp(logits - logits.max())
        prob /= prob.sum()
        top = np.argsort(prob)[::-1][:3]
        t2 = time.perf_counter()
        bgr = to_bgr(img, 1280)
        found = faces.detect(bgr)
        people = []
        for f in found:
            if refs and min(f[2], f[3]) >= FACE_MIN_PX:
                who, score = match_person(faces.feature(bgr, f), refs)
                if who:  # los que no coinciden se descartan sin guardar nada
                    people.append((who, score))
        t3 = time.perf_counter()
        t["decode"] += t1 - t0
        t["clip"] += t2 - t1
        t["caras"] += t3 - t2

        best = names[top[0]] if prob[top[0]] >= ACCEPT else None
        accepted[best or "(sin tag)"] += 1
        face_total += len(found)
        for who, _ in people:
            recognized[who] += 1
        rows.append(dict(path=p, b64=thumb_b64(img), best=best,
                         top=[(names[j], float(prob[j])) for j in top],
                         n_faces=len(found), people=people))
        if i % 25 == 0:
            print(f"  {i}/{len(paths)}")

    n = len(rows)
    print("\nTiempo medio por imagen (ms):")
    for k, v in t.items():
        print(f"  {k:7s} {1000 * v / n:7.1f}")
    print(f"  total   {1000 * sum(t.values()) / n:7.1f}")
    print(f"\nModelo visión: {'cuantizado' if a.quantized else 'fp32'}, hilos: {a.threads}")
    print(f"Tags puestos (prob ≥ {ACCEPT}):")
    for k, v in accepted.most_common():
        print(f"  {k:14s} {v}")
    print(f"Caras detectadas: {face_total}")
    if refs:
        print(f"Reconocidos: {dict(recognized)}")

    write_report(a.out, rows, t, n, a)
    if a.json:
        import json
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump([{k: v for k, v in r.items() if k != "b64"} for r in rows],
                      fh, ensure_ascii=False, indent=1)
    print(f"\nReporte: {os.path.abspath(a.out)}")


def write_report(out, rows, t, n, a):
    groups = defaultdict(list)
    for r in rows:
        groups[r["best"] or "(sin tag)"].append(r)
    order = sorted(groups, key=lambda k: (k == "(sin tag)", -len(groups[k])))
    ms = 1000 * sum(t.values()) / n
    parts = [f"""<!doctype html><meta charset=utf-8><title>Spike visión</title>
<style>
body{{font:14px system-ui;background:#1e1e1e;color:#ddd;margin:16px}}
h2{{border-bottom:1px solid #444;padding-bottom:4px;margin-top:28px}}
.g{{display:flex;flex-wrap:wrap;gap:10px}}
.c{{width:220px;background:#2a2a2a;border-radius:6px;padding:6px;font-size:12px}}
.c img{{width:220px;height:165px;object-fit:contain;background:#111;display:block}}
.n{{color:#888;word-break:break-all}} .p{{color:#7cc4ff}} .f{{color:#e0c060}}
</style>
<h1>Spike visión — {n} imágenes</h1>
<p>Modelo: {'cuantizado' if a.quantized else 'fp32'} · hilos {a.threads} ·
{ms:.0f} ms/imagen · umbral {ACCEPT}</p>"""]
    for g in order:
        parts.append(f"<h2>{html.escape(g)} ({len(groups[g])})</h2><div class=g>")
        for r in groups[g]:
            top = " · ".join(f"{html.escape(k)} {v:.2f}" for k, v in r["top"])
            ppl = "".join(f"<div class=p>👤 {html.escape(w)} ({s:.2f})</div>"
                          for w, s in r["people"])
            parts.append(
                f"<div class=c><img src='data:image/jpeg;base64,{r['b64']}'>"
                f"<div>{top}</div><div class=f>caras: {r['n_faces']}</div>{ppl}"
                f"<div class=n>{html.escape(os.path.basename(r['path']))}</div></div>")
        parts.append("</div>")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))


if __name__ == "__main__":
    main()
