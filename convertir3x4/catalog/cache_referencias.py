"""Caché local de las imágenes del detalle de una persona (Catalogar). Sin Qt.

La foto de referencia está en la base (`person_reference.foto`, unos 270 KB a
1.600 px). El detalle muestra una miniatura con el rostro marcado y el recorte
del rostro: se arman una vez con la foto y se guardan en disco, así la próxima
vez no se trae la foto del NAS. La foto de una referencia no cambia; lo que
puede cambiar es el recuadro del rostro (el NAS la vuelve a revisar), por eso
la clave lleva el recuadro y el sello de la foto (md5, lo calcula la base: el
mismo id puede ser otra foto en otra base, p. ej. la de pruebas). Se puede borrar la carpeta entera sin riesgo.
"""

import hashlib
import os

from . import referencias

_VERSION = 1      # subir si cambia cómo se dibujan (tamaños, recuadro)


class CacheReferencias:
    def __init__(self, carpeta: str):
        self.carpeta = carpeta
        os.makedirs(carpeta, exist_ok=True)

    def _base(self, ref: dict) -> str:
        sello = hashlib.md5(repr((_VERSION, referencias.MINI_LADO, referencias.CARA_LADO,
                                  ref.get("caja"), ref.get("sello_foto"))).encode()).hexdigest()[:16]
        return os.path.join(self.carpeta, f"{ref['id']}-{sello}")

    def leer(self, ref: dict) -> tuple[bytes, bytes | None] | None:
        """(miniatura, rostro) guardados para esta referencia, o None si faltan."""
        base = self._base(ref)
        try:
            with open(base + "-foto.jpg", "rb") as f:
                mini = f.read()
        except OSError:
            return None
        try:
            with open(base + "-cara.jpg", "rb") as f:
                cara = f.read()
        except OSError:
            cara = None
        return mini, cara

    def guardar(self, ref: dict, mini: bytes | None, cara: bytes | None):
        if not mini:
            return
        base = self._base(ref)
        for sufijo, datos in (("-cara.jpg", cara), ("-foto.jpg", mini)):   # la foto al final: marca «completo»
            if datos:
                tmp = base + sufijo + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(datos)
                os.replace(tmp, base + sufijo)
