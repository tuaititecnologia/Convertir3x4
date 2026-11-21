"""Catalogado (Etapa 0, sin IA): recorre las carpetas del NAS y anota cada
foto y video en el catálogo. Sin Qt.

Una pasada (`catalogar`), sobre la lista de carpetas de la configuración:

1. Toma el bloqueo de cada carpeta en PostgreSQL. Si otra PC ya la está
   catalogando, la saltea (queda en `stats.ocupadas`).
2. Lista los archivos de todas las carpetas y marca como faltante lo
   catalogado que ya no está en su ruta. Esto va ANTES de procesar para que un
   archivo movido de una carpeta a otra (Entrada → Publicar) se reconozca
   aunque la carpeta nueva se recorra primero. Si una carpeta no se pudo leer
   entera (el NAS se cortó), no se marca nada en ella.
3. Por cada archivo: si no cambió (misma fecha y tamaño), lo saltea. Si es
   nuevo, busca entre los faltantes uno con el mismo nombre, tamaño y fecha
   (desempate por sha256) y, si lo encuentra, le cambia la ruta y conserva sus
   tags. Si no, probe técnico → dHash → miniatura → alta.
4. Calcula el sha256 solo de los archivos que pesan lo mismo que otro: el
   resto no puede tener duplicados exactos, y leer todo el NAS por la red
   llevaría horas.

Idempotente: si se corta, la pasada siguiente retoma.
"""

import os
from dataclasses import dataclass, field

import psycopg

from ..catalog import rutas, thumbs
from ..catalog.db import CatalogoNoDisponible
from ..catalog.models import Media
from ..catalog.repo import Repo
from . import inventory, probe as probe_mod, dedup


class CatalogoOcupado(Exception):
    """Otra PC está catalogando esa carpeta."""

    MENSAJE = ("Otra PC está catalogando esta carpeta en este momento. "
               "Espere a que termine e intente de nuevo.")

    def __init__(self, carpeta: str):
        super().__init__(self.MENSAJE)
        self.carpeta = carpeta


@dataclass
class Stage0Stats:
    scanned: int = 0
    added: int = 0
    updated: int = 0
    skipped: int = 0
    corrupt: int = 0
    duplicates: int = 0
    moved: int = 0          # reaparecieron en otra ruta (conservan sus tags)
    en_negro: int = 0       # fotos negras o videos en negro (no se etiquetan)
    miniaturas_rehechas: int = 0   # hechas con una regla vieja (thumbs.MINIATURA_VERSION)
    missing: int = 0        # dejaron de estar en esta pasada y no reaparecieron
    ocupadas: list[str] = field(default_factory=list)      # otra PC las catalogaba
    inexistentes: list[str] = field(default_factory=list)  # no están en el NAS
    incompletas: list[str] = field(default_factory=list)   # no se pudieron leer enteras


def catalogar(repo: Repo, carpetas: list[str], status: str = "library",
              progress_cb=None, bloqueo: str | None = None) -> Stage0Stats:
    """Cataloga `carpetas` (relativas a `repo.raiz`). `progress_cb(hechos, total, ruta)`.

    `bloqueo`: carpeta cuyo bloqueo se toma en lugar de cada una (para catalogar
    a mano una subcarpeta de una carpeta de la lista sin chocar con la pasada
    nocturna de esa carpeta)."""
    stats = Stage0Stats()
    tomadas: list[str] = []
    marcados: list[int] = []
    try:
        for c in carpetas:
            llave = bloqueo if bloqueo is not None else c
            if llave in tomadas or repo.tomar_carpeta(llave):
                if llave not in tomadas:
                    tomadas.append(llave)
            else:
                stats.ocupadas.append(c)
        libres = [c for c in carpetas if c not in stats.ocupadas]

        trabajos = []
        for c in libres:
            raiz_c = rutas.a_completa(c, repo.raiz)
            if not os.path.isdir(raiz_c):
                stats.inexistentes.append(c)
                continue
            errores: list = []
            paths = inventory.scan(raiz_c, errores)
            existentes = repo.estado_carpeta(c)
            if errores:
                stats.incompletas.append(c)
            else:
                vistos = {repo.rel(p) for p in paths}
                marcados += repo.marcar_faltantes(
                    [r["id"] for rel, r in existentes.items()
                     if rel not in vistos and r["missing_since"] is None])
            trabajos.append((c, paths, existentes))

        total = sum(len(p) for _, p, _ in trabajos)
        hechos = 0
        agregados: set[int] = set()
        for _c, paths, existentes in trabajos:
            for path in paths:
                hechos += 1
                stats.scanned += 1
                mid = _procesar(repo, path, existentes, status, stats)
                if mid is not None:
                    agregados.add(mid)
                if progress_cb:
                    progress_cb(hechos, total, path)

        for c, _, _ in trabajos:
            for m in repo.pendientes_de_sha(c):
                try:
                    repo.guardar_sha(m.id, dedup.sha256_file(m.path))
                except OSError:
                    pass
        stats.duplicates = _contar_duplicados(repo, agregados)
        stats.missing = repo.siguen_faltando(marcados)
    except psycopg.OperationalError as e:
        raise CatalogoNoDisponible(str(e).strip()) from e
    finally:
        for llave in tomadas:
            try:
                repo.soltar_carpeta(llave)
            except psycopg.Error:
                pass    # conexión caída: el bloqueo se suelta solo al cerrarse
    return stats


def run_stage0(repo: Repo, root: str, status: str = "library",
               progress_cb=None) -> Stage0Stats:
    """Cataloga una sola carpeta dada por su ruta completa (dentro de la raíz)."""
    rel = repo.rel(root)
    if rel is None:
        raise ValueError(f"La carpeta no está dentro de {repo.raiz}: {root}")
    stats = catalogar(repo, [rel], status=status, progress_cb=progress_cb)
    if stats.ocupadas:
        raise CatalogoOcupado(rel)
    return stats


def _procesar(repo: Repo, path: str, existentes: dict, status: str,
              stats: Stage0Stats) -> int | None:
    """Un archivo. Devuelve el id si se agregó uno nuevo."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    rel = repo.rel(path)
    size, mtime = st.st_size, int(st.st_mtime)
    previo = existentes.get(rel)

    if previo is not None and previo["file_mtime"] == mtime and previo["size_bytes"] == size:
        if previo["missing_since"] is not None:
            repo.marcar_presente(previo["id"])
        if previo["miniatura_version"] < thumbs.MINIATURA_VERSION \
                and previo["quality_flag"] != "corrupt":
            _rehacer_miniatura(repo, path, previo, stats)
        stats.skipped += 1
        return None

    sha = None
    if previo is None:
        movido, sha = _reaparecido(repo, path, size, mtime)
        if movido is not None and repo.mover(movido.id, rel, rutas.carpeta_de(rel)):
            stats.moved += 1
            return None

    kind = inventory.kind_for(path)
    pr = probe_mod.probe(path, kind)
    if pr.quality_flag == "corrupt":
        stats.corrupt += 1
    miniatura = thumbs.make_thumb(path, kind, pr.duration) if pr.quality_flag != "corrupt" \
        else None
    calidad = pr.quality_flag
    # En negro pesa más que la baja resolución: no muestra nada, se use donde se use.
    if calidad in ("ok", "low_res") and thumbs.en_negro(miniatura):
        calidad = "en_negro"
        stats.en_negro += 1
    media = Media(
        path=path,
        rel_path=rel,
        filename=os.path.basename(path),
        folder_origin=rutas.carpeta_de(rel),
        kind=kind,
        width=pr.width,
        height=pr.height,
        aspect=pr.aspect,
        duration=pr.duration,
        codec=pr.codec,
        size_bytes=size,
        file_mtime=mtime,
        sha256=sha,
        phash=dedup.dhash_image(path) if kind == "image" else None,
        status=status,
        quality_flag=calidad,
    )
    mid = repo.upsert_media(media, miniatura=miniatura,
                            miniatura_version=thumbs.MINIATURA_VERSION)
    if previo is None:
        stats.added += 1
        return mid
    stats.updated += 1
    return None


def _rehacer_miniatura(repo: Repo, path: str, previo: dict, stats: Stage0Stats):
    """Un archivo que no cambió pero cuya miniatura se hizo con una regla vieja.
    Las fotos se rehacen desde la miniatura guardada (no hace falta leer el
    archivo: solo se mira si es negra); los videos, desde el archivo."""
    if previo["kind"] == "video":
        jpeg = thumbs.make_thumb(path, "video", previo["duration"])
    else:
        jpeg = None
    negro = thumbs.en_negro(jpeg if jpeg is not None else repo.miniatura(previo["id"]))
    calidad = None
    if negro and previo["quality_flag"] in ("ok", "low_res"):
        calidad = "en_negro"
    elif not negro and previo["quality_flag"] == "en_negro":
        calidad = "ok"
    if negro:
        stats.en_negro += 1
    if jpeg is not None or calidad != previo["quality_flag"]:
        repo.rehacer_miniatura(previo["id"], jpeg, thumbs.MINIATURA_VERSION, calidad)
        stats.miniaturas_rehechas += 1
    else:
        repo.marcar_miniatura_al_dia(previo["id"], thumbs.MINIATURA_VERSION)


def _reaparecido(repo: Repo, path: str, size: int, mtime: int):
    """(faltante que es este archivo o None, sha256 si hubo que calcularlo)."""
    candidatos = repo.candidatos_reaparecido(os.path.basename(path), size, mtime)
    if len(candidatos) == 1:
        return candidatos[0], None
    if not candidatos:
        return None, None
    try:
        sha = dedup.sha256_file(path)
    except OSError:
        return None, None
    iguales = [m for m in candidatos if m.sha256 == sha]
    return (iguales[0] if len(iguales) == 1 else None), sha


def _contar_duplicados(repo: Repo, agregados: set[int]) -> int:
    """Archivos agregados en esta pasada que repiten otro ya catalogado (el
    primero de un grupo de copias nuevas no cuenta como duplicado)."""
    if not agregados:
        return 0
    n = 0
    for grupo in repo.duplicate_groups():
        nuevos = sum(1 for m in grupo if m.id in agregados)
        if nuevos:
            n += nuevos if len(grupo) > nuevos else nuevos - 1
    return n
