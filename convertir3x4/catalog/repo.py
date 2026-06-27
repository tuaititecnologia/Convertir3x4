"""CRUD y consultas sobre el catálogo.

Toda la UI y el motor pasan por acá; nadie toca SQL directo afuera de este módulo.
El índice FTS (`media_fts`) se mantiene sincronizado en cada upsert de medio/tags.
"""

import sqlite3
import time

from .models import Media

_MEDIA_COLS = (
    "path", "filename", "folder_origin", "kind", "width", "height", "aspect",
    "duration", "codec", "size_bytes", "file_mtime", "sha256", "phash",
    "status", "canonical_id", "quality_flag", "thumb_path",
)


def _now() -> int:
    return int(time.time())


class Repo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ------------------------------------------------------------- media
    def get_media_by_path(self, path: str) -> Media | None:
        row = self.conn.execute("SELECT * FROM media WHERE path = ?", (path,)).fetchone()
        return _row_to_media(row) if row else None

    def get_media_by_sha256(self, sha: str) -> Media | None:
        row = self.conn.execute(
            "SELECT * FROM media WHERE sha256 = ? LIMIT 1", (sha,)
        ).fetchone()
        return _row_to_media(row) if row else None

    def upsert_media(self, m: Media) -> int:
        """Inserta o actualiza por `path`. Devuelve el id. Sincroniza FTS."""
        now = _now()
        existing = self.conn.execute(
            "SELECT id FROM media WHERE path = ?", (m.path,)
        ).fetchone()
        values = [getattr(m, c) for c in _MEDIA_COLS]

        if existing:
            media_id = existing["id"]
            assignments = ", ".join(f"{c} = ?" for c in _MEDIA_COLS)
            self.conn.execute(
                f"UPDATE media SET {assignments}, updated_at = ? WHERE id = ?",
                (*values, now, media_id),
            )
        else:
            cols = ", ".join(_MEDIA_COLS)
            placeholders = ", ".join("?" for _ in _MEDIA_COLS)
            cur = self.conn.execute(
                f"INSERT INTO media ({cols}, created_at, updated_at) "
                f"VALUES ({placeholders}, ?, ?)",
                (*values, now, now),
            )
            media_id = cur.lastrowid

        m.id = media_id
        self._sync_fts(media_id)
        self.conn.commit()
        return media_id

    def count_media(self, status: str | None = None) -> int:
        if status:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM media WHERE status = ?", (status,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM media").fetchone()
        return row["n"]

    def all_media(self) -> list[Media]:
        rows = self.conn.execute("SELECT * FROM media ORDER BY filename").fetchall()
        return [_row_to_media(r) for r in rows]

    # ------------------------------------------------------------- dedup
    def duplicate_groups(self) -> list[list[Media]]:
        """Grupos de medios que comparten sha256 (duplicados exactos)."""
        rows = self.conn.execute(
            "SELECT sha256 FROM media WHERE sha256 IS NOT NULL "
            "GROUP BY sha256 HAVING COUNT(*) > 1"
        ).fetchall()
        groups = []
        for r in rows:
            members = self.conn.execute(
                "SELECT * FROM media WHERE sha256 = ? ORDER BY id", (r["sha256"],)
            ).fetchall()
            groups.append([_row_to_media(m) for m in members])
        return groups

    # ------------------------------------------------------------- tags
    def normalize_tag(self, name: str) -> str:
        return " ".join(name.strip().lower().split())

    def get_or_create_tag(self, name: str, kind: str = "descriptivo") -> int:
        norm = self.normalize_tag(name)
        row = self.conn.execute("SELECT id FROM tag WHERE name = ?", (norm,)).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO tag (name, kind, created_at) VALUES (?, ?, ?)",
            (norm, kind, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def all_tag_names(self) -> list[str]:
        rows = self.conn.execute("SELECT name FROM tag ORDER BY name").fetchall()
        return [r["name"] for r in rows]

    def apply_tag(self, media_id: int, name: str, source: str = "humano",
                  confirmed: bool = True):
        tag_id = self.get_or_create_tag(name)
        self.conn.execute(
            "INSERT OR REPLACE INTO media_tag (media_id, tag_id, source, confirmed) "
            "VALUES (?, ?, ?, ?)",
            (media_id, tag_id, source, 1 if confirmed else 0),
        )
        self._sync_fts(media_id)
        self.conn.commit()

    def tags_for(self, media_id: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT t.name FROM tag t JOIN media_tag mt ON mt.tag_id = t.id "
            "WHERE mt.media_id = ? ORDER BY t.name",
            (media_id,),
        ).fetchall()
        return [r["name"] for r in rows]

    # ------------------------------------------------------------- búsqueda
    def search(self, query: str, limit: int = 500) -> list[Media]:
        """Búsqueda combinada. Vacío → todos los medios.

        Capa 1: FTS5 (palabras/prefijo/tags/sin acentos) sobre nombre, carpeta,
        descripción IA, OCR, sinónimos y tags — ranqueado por relevancia.
        Capa 2: substring (LIKE) sobre nombre + carpeta, para cazar pedazos en
        el MEDIO de nombres opacos (p.ej. '4782' dentro de FB_IMG_...4782...),
        que FTS no encuentra porque solo matchea prefijos de token.
        Los resultados de FTS van primero; los de substring se agregan sin duplicar.
        """
        if not query or not query.strip():
            return self.all_media()[:limit]

        results: list[Media] = []
        seen: set[int] = set()

        match = _to_fts_query(query)
        for r in self.conn.execute(
            "SELECT m.* FROM media_fts f JOIN media m ON m.id = f.rowid "
            "WHERE media_fts MATCH ? ORDER BY rank LIMIT ?",
            (match, limit),
        ):
            if r["id"] not in seen:
                seen.add(r["id"])
                results.append(_row_to_media(r))

        for r in self._substring_rows(query, limit):
            if r["id"] not in seen:
                seen.add(r["id"])
                results.append(_row_to_media(r))

        return results[:limit]

    def _substring_rows(self, query: str, limit: int):
        """Filas cuyo nombre+carpeta contienen TODOS los términos (substring)."""
        terms = [t for t in query.split() if t]
        if not terms:
            return []
        # Cada término debe aparecer como substring en filename||folder_origin.
        clauses = " AND ".join(
            "(filename LIKE ? ESCAPE '\\' OR IFNULL(folder_origin,'') LIKE ? ESCAPE '\\')"
            for _ in terms
        )
        params = []
        for t in terms:
            like = f"%{_escape_like(t)}%"
            params.extend([like, like])
        params.append(limit)
        return self.conn.execute(
            f"SELECT * FROM media WHERE {clauses} ORDER BY filename LIMIT ?", params
        ).fetchall()

    # ------------------------------------------------------------- FTS interno
    def _sync_fts(self, media_id: int):
        m = self.conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
        if not m:
            return
        ai = self.conn.execute(
            "SELECT description, ocr_text, synonyms FROM ai_result WHERE media_id = ?",
            (media_id,),
        ).fetchone()
        tags = " ".join(self.tags_for(media_id))
        self.conn.execute("DELETE FROM media_fts WHERE rowid = ?", (media_id,))
        self.conn.execute(
            "INSERT INTO media_fts (rowid, filename, folder_origin, description, "
            "ocr_text, synonyms, tags) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                media_id,
                m["filename"] or "",
                m["folder_origin"] or "",
                (ai["description"] if ai else "") or "",
                (ai["ocr_text"] if ai else "") or "",
                (ai["synonyms"] if ai else "") or "",
                tags,
            ),
        )


def _row_to_media(row: sqlite3.Row) -> Media:
    return Media(
        id=row["id"],
        path=row["path"],
        filename=row["filename"],
        folder_origin=row["folder_origin"],
        kind=row["kind"],
        width=row["width"],
        height=row["height"],
        aspect=row["aspect"],
        duration=row["duration"],
        codec=row["codec"],
        size_bytes=row["size_bytes"],
        file_mtime=row["file_mtime"],
        sha256=row["sha256"],
        phash=row["phash"],
        status=row["status"],
        canonical_id=row["canonical_id"],
        quality_flag=row["quality_flag"],
        thumb_path=row["thumb_path"],
    )


def _to_fts_query(query: str) -> str:
    """Convierte texto libre en una consulta FTS5 con prefijo por término.

    Cada palabra se vuelve un término con `*` (prefijo) y se unen con AND
    implícito. Se citan los términos para neutralizar caracteres especiales.
    """
    terms = []
    for word in query.split():
        clean = word.replace('"', "")
        if clean:
            terms.append(f'"{clean}"*')
    return " ".join(terms) if terms else '""'


def _escape_like(term: str) -> str:
    """Escapa los comodines de LIKE (\\, %, _) para tratarlos como literales."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
