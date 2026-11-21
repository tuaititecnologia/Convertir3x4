"""Dataclasses del catálogo."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Media:
    """Una fila de la tabla `media` (un archivo físico).

    `path` es la ruta completa EN ESTA PC (raíz + `rel_path`): la que se abre,
    se arrastra a ProPresenter o se manda a Convertir."""
    path: str
    filename: str
    kind: str                       # image | video
    folder_origin: str | None = None
    width: int | None = None
    height: int | None = None
    aspect: float | None = None
    duration: float | None = None
    codec: str | None = None
    size_bytes: int | None = None
    file_mtime: int | None = None
    sha256: str | None = None
    phash: str | None = None
    status: str = "library"
    quality_flag: str = "ok"
    # Relativa a la raíz del recurso compartido, con "/". La arma el Repo.
    rel_path: str | None = None
    # Desde cuándo el archivo no está en su ruta (None: está). Ver repo.py.
    missing_since: datetime | None = None
    id: int | None = None
    # Etiquetas para mostrar en la grilla: [(nombre, confirmada)], confirmadas
    # primero. Solo las trae Repo.buscar (None: no se pidieron).
    etiquetas: list | None = None

    @property
    def is_video(self) -> bool:
        return self.kind == "video"

    @property
    def missing(self) -> bool:
        return self.missing_since is not None


@dataclass
class ProbeResult:
    """Salida de engine.probe para un archivo (campos técnicos)."""
    kind: str
    width: int | None = None
    height: int | None = None
    aspect: float | None = None
    duration: float | None = None
    codec: str | None = None
    quality_flag: str = "ok"
    extra: dict = field(default_factory=dict)
