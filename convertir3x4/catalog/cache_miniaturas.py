"""Caché local de las miniaturas del catálogo, en cada PC. Sin Qt.

La miniatura original está en la base (tabla `thumbnail`), compartida por las
PCs. Buscar no la baja cada vez: la guarda en disco como `<id>-<sello>.jpg` y
la lee de ahí. El sello cambia si la miniatura cambia, así que una copia vieja
nunca se muestra. Se puede borrar la carpeta entera sin riesgo: se vuelve a
llenar sola.
"""

import glob
import os

from .repo import Repo

_LOTE = 200   # miniaturas por consulta (~4 MB)


class CacheMiniaturas:
    def __init__(self, carpeta: str):
        self.carpeta = carpeta
        os.makedirs(carpeta, exist_ok=True)

    def ruta(self, media_id: int, sello: str) -> str:
        return os.path.join(self.carpeta, f"{media_id}-{sello[:16]}.jpg")

    def locales(self, repo: Repo, ids: list[int]) -> tuple[dict[int, str], list[int]]:
        """({id: ruta local} de las que ya están al día, [ids que hay que bajar]).
        Consulta solo los sellos, no los bytes."""
        listas, faltan = {}, []
        for mid, sello in repo.sellos_miniatura(ids).items():
            r = self.ruta(mid, sello)
            if os.path.exists(r):
                listas[mid] = r
            else:
                faltan.append(mid)
        return listas, faltan

    def bajar(self, repo: Repo, ids: list[int]) -> dict[int, str]:
        """Baja de la base las miniaturas pedidas y las guarda. {id: ruta local}."""
        out = {}
        for i in range(0, len(ids), _LOTE):
            for mid, (sello, jpeg) in repo.miniaturas(ids[i:i + _LOTE]).items():
                out[mid] = self._guardar(mid, sello, jpeg)
        return out

    def asegurar(self, repo: Repo, ids: list[int]) -> dict[int, str]:
        listas, faltan = self.locales(repo, ids)
        listas.update(self.bajar(repo, faltan))
        return listas

    def olvidar(self, ids):
        """Borra las copias locales de estos medios (p. ej. quitados del catálogo)."""
        for mid in ids:
            for viejo in glob.glob(os.path.join(self.carpeta, f"{mid}-*.jpg")):
                _borrar(viejo)

    def _guardar(self, mid: int, sello: str, jpeg: bytes) -> str:
        destino = self.ruta(mid, sello)
        tmp = destino + ".tmp"
        with open(tmp, "wb") as f:
            f.write(jpeg)
        os.replace(tmp, destino)           # nunca queda una a medio escribir
        for viejo in glob.glob(os.path.join(self.carpeta, f"{mid}-*.jpg")):
            if viejo != destino:
                _borrar(viejo)
        return destino


def _borrar(ruta: str):
    try:
        os.remove(ruta)
    except OSError:
        pass
