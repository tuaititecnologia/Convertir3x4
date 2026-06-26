"""Etapa 0 — dedup: hash exacto (sha256) y perceptual (dHash) para casi-duplicados.

El dHash (difference hash) se implementa con numpy + Pillow para no sumar
dependencias (imagehash arrastra scipy, pesado de bundlear). Es robusto a
recompresión/reescalado y suficiente para proponer casi-duplicados que SIEMPRE
confirma un humano antes de mandar a cuarentena.
"""

import hashlib

import numpy as np
from PIL import Image

_HASH_SIDE = 8  # dHash de 8x8 → 64 bits


def sha256_file(path: str, chunk_size: int = 1 << 20) -> str:
    """Hash exacto del contenido del archivo."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def dhash_image(path: str) -> str | None:
    """dHash perceptual de una imagen. Devuelve hex de 16 chars (64 bits) o None."""
    try:
        with Image.open(path) as img:
            small = img.convert("L").resize(
                (_HASH_SIDE + 1, _HASH_SIDE), Image.LANCZOS)
        arr = np.asarray(small, dtype=np.int16)
        diff = arr[:, 1:] > arr[:, :-1]      # comparar cada píxel con el de su derecha
        bits = diff.flatten()
        value = 0
        for bit in bits:
            value = (value << 1) | int(bit)
        return f"{value:016x}"
    except Exception:
        return None


def hamming(hash_a: str, hash_b: str) -> int:
    """Distancia de Hamming entre dos dHash hex. Menor = más parecidas."""
    return bin(int(hash_a, 16) ^ int(hash_b, 16)).count("1")
