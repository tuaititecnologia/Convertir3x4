"""Orquestador de la Etapa 0 (local, sin IA).

Para cada archivo: probe técnico → sha256 → dHash (imágenes) → miniatura →
upsert en el catálogo. Idempotente: si el archivo ya está catalogado y no
cambió (mismo path, mtime y tamaño), lo saltea. Emite progreso por callback.

La detección de calidad (corrupt / low_res) la hace `probe` de paso, así que no
hay un módulo `quality` separado: vive dentro del probe para no reabrir archivos.
"""

import os
from dataclasses import dataclass

from ..catalog.models import Media
from ..catalog.repo import Repo
from ..catalog import thumbs
from . import inventory, probe as probe_mod, dedup


@dataclass
class Stage0Stats:
    scanned: int = 0
    added: int = 0
    updated: int = 0
    skipped: int = 0
    corrupt: int = 0
    duplicates: int = 0


def run_stage0(repo: Repo, root: str, thumb_dir: str,
               status: str = "library", progress_cb=None) -> Stage0Stats:
    """Corre la Etapa 0 sobre `root`. `progress_cb(done, total, path)` opcional."""
    paths = inventory.scan(root)
    stats = Stage0Stats()
    total = len(paths)

    for i, path in enumerate(paths):
        stats.scanned += 1
        try:
            st = os.stat(path)
        except OSError:
            if progress_cb:
                progress_cb(i + 1, total, path)
            continue

        kind = inventory.kind_for(path)
        existing = repo.get_media_by_path(path)
        unchanged = (
            existing is not None
            and existing.file_mtime == int(st.st_mtime)
            and existing.size_bytes == st.st_size
        )
        if unchanged:
            stats.skipped += 1
            if progress_cb:
                progress_cb(i + 1, total, path)
            continue

        pr = probe_mod.probe(path, kind)
        sha = dedup.sha256_file(path)
        if repo.get_media_by_sha256(sha) is not None and existing is None:
            stats.duplicates += 1
        ph = dedup.dhash_image(path) if kind == "image" else None
        thumb = thumbs.make_thumb(path, kind, sha, thumb_dir)
        if pr.quality_flag == "corrupt":
            stats.corrupt += 1

        media = Media(
            path=path,
            filename=os.path.basename(path),
            folder_origin=inventory.folder_origin(path, root),
            kind=kind,
            width=pr.width,
            height=pr.height,
            aspect=pr.aspect,
            duration=pr.duration,
            codec=pr.codec,
            size_bytes=st.st_size,
            file_mtime=int(st.st_mtime),
            sha256=sha,
            phash=ph,
            status=status,
            quality_flag=pr.quality_flag,
            thumb_path=thumb,
        )
        repo.upsert_media(media)
        if existing:
            stats.updated += 1
        else:
            stats.added += 1

        if progress_cb:
            progress_cb(i + 1, total, path)

    return stats
