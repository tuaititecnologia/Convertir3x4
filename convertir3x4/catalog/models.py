"""Dataclasses del catálogo."""

from dataclasses import dataclass, field


@dataclass
class Media:
    """Una fila de la tabla `media` (un archivo físico)."""
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
    canonical_id: int | None = None
    quality_flag: str = "ok"
    thumb_path: str | None = None
    id: int | None = None

    @property
    def is_video(self) -> bool:
        return self.kind == "video"


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
