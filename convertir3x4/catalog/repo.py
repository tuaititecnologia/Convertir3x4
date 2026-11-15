"""CRUD y consultas sobre el catálogo.

Toda la UI y el motor pasan por acá; nadie toca SQL directo afuera de este módulo.
El índice FTS (`media_fts`) se mantiene sincronizado en cada upsert de medio/tags.
"""

import hashlib
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
        """Pone (o confirma) un tag. Aplicarlo a mano sobre uno sugerido por la
        IA lo pasa a humano/confirmado y borra un rechazo previo."""
        tag_id = self.get_or_create_tag(name)
        self.conn.execute(
            "INSERT OR REPLACE INTO media_tag (media_id, tag_id, source, confirmed) "
            "VALUES (?, ?, ?, ?)",
            (media_id, tag_id, source, 1 if confirmed else 0),
        )
        self.conn.execute(
            "DELETE FROM tag_rejection WHERE media_id = ? AND tag_id = ?",
            (media_id, tag_id),
        )
        self._sync_fts(media_id)
        self.conn.commit()

    def suggest_tag(self, media_id: int, name: str, kind: str = "ia-sugerido") -> bool:
        """Tag propuesto por la IA (source='ia', sin confirmar).

        No pisa un tag que ya exista (humano o no) ni vuelve a proponer uno que
        alguien sacó. Devuelve True si lo agregó.
        """
        tag_id = self.get_or_create_tag(name, kind=kind)
        rejected = self.conn.execute(
            "SELECT 1 FROM tag_rejection WHERE media_id = ? AND tag_id = ?",
            (media_id, tag_id),
        ).fetchone()
        if rejected:
            return False
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO media_tag (media_id, tag_id, source, confirmed) "
            "VALUES (?, ?, 'ia', 0)",
            (media_id, tag_id),
        )
        if cur.rowcount:
            self._sync_fts(media_id)
        self.conn.commit()
        return bool(cur.rowcount)

    def remove_tag(self, media_id: int, name: str):
        """Saca un tag y lo anota como rechazado, para que la IA no lo reponga."""
        norm = self.normalize_tag(name)
        row = self.conn.execute("SELECT id FROM tag WHERE name = ?", (norm,)).fetchone()
        if not row:
            return
        self.conn.execute(
            "DELETE FROM media_tag WHERE media_id = ? AND tag_id = ?",
            (media_id, row["id"]),
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO tag_rejection (media_id, tag_id) VALUES (?, ?)",
            (media_id, row["id"]),
        )
        self._sync_fts(media_id)
        self.conn.commit()

    def confirm_tag(self, media_id: int, name: str):
        """Confirma un tag sugerido (si el medio lo tiene). Queda source='ia'."""
        norm = self.normalize_tag(name)
        self.conn.execute(
            "UPDATE media_tag SET confirmed = 1 WHERE media_id = ? "
            "AND tag_id = (SELECT id FROM tag WHERE name = ?)",
            (media_id, norm),
        )
        self.conn.commit()

    def pending_tag_counts(self, media_ids: list[int]) -> dict[str, int]:
        """De los tags de la selección, cuántos medios los tienen sin confirmar."""
        if not media_ids:
            return {}
        placeholders = ",".join("?" for _ in media_ids)
        rows = self.conn.execute(
            f"SELECT t.name, COUNT(*) AS n FROM tag t "
            f"JOIN media_tag mt ON mt.tag_id = t.id "
            f"WHERE mt.media_id IN ({placeholders}) AND mt.confirmed = 0 "
            f"GROUP BY t.id",
            media_ids,
        ).fetchall()
        return {r["name"]: r["n"] for r in rows}

    def count_pending_tags(self) -> int:
        """Medios con al menos un tag de la IA sin confirmar."""
        return self.conn.execute(
            "SELECT COUNT(DISTINCT media_id) AS n FROM media_tag WHERE confirmed = 0"
        ).fetchone()["n"]

    # ------------------------------------------------------------- personas
    def add_person(self, name: str) -> int:
        name = " ".join(name.split())
        row = self.conn.execute("SELECT id FROM person WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO person (name, created_at) VALUES (?, ?)", (name, _now()))
        self.conn.commit()
        return cur.lastrowid

    def list_persons(self) -> list[tuple[int, str, int]]:
        """(id, nombre, cantidad de fotos de referencia)."""
        rows = self.conn.execute(
            "SELECT p.id, p.name, COUNT(r.id) AS n FROM person p "
            "LEFT JOIN person_reference r ON r.person_id = p.id "
            "GROUP BY p.id ORDER BY p.name"
        ).fetchall()
        return [(r["id"], r["name"], r["n"]) for r in rows]

    def delete_person(self, person_id: int):
        """Borra la persona y sus firmas. Los tags ya puestos quedan."""
        self.conn.execute("DELETE FROM person WHERE id = ?", (person_id,))
        self.conn.commit()

    def add_person_reference(self, person_id: int, source_path: str, embedding: bytes):
        self.conn.execute(
            "INSERT INTO person_reference (person_id, source_path, embedding, created_at) "
            "VALUES (?, ?, ?, ?)",
            (person_id, source_path, embedding, _now()),
        )
        self.conn.commit()

    def person_reference_blobs(self) -> dict[str, list[bytes]]:
        out: dict[str, list[bytes]] = {}
        for r in self.conn.execute(
            "SELECT p.name, r.embedding FROM person_reference r "
            "JOIN person p ON p.id = r.person_id ORDER BY r.id"
        ):
            out.setdefault(r["name"], []).append(r["embedding"])
        return out

    def people_signature(self) -> str:
        """Cambia cada vez que se agrega o quita una referencia o una persona."""
        rows = self.conn.execute(
            "SELECT r.id, p.name FROM person_reference r "
            "JOIN person p ON p.id = r.person_id ORDER BY r.id"
        ).fetchall()
        if not rows:
            return ""
        raw = ";".join(f"{r['id']}:{r['name']}" for r in rows)
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    # ------------------------------------------------------------- IA
    def media_for_ai(self, model: str, people_sig: str) -> list[tuple[Media, bool]]:
        """Medios que hay que analizar: (medio, hace_falta_escena).

        Falta escena si nunca se analizó o cambió el modelo/vocabulario. Si solo
        cambió la lista de personas, se repasan las caras y no la escena.
        """
        rows = self.conn.execute(
            "SELECT m.*, a.model AS ai_model, a.people_sig AS ai_sig, "
            "a.id AS ai_id FROM media m LEFT JOIN ai_result a ON a.media_id = m.id "
            "WHERE IFNULL(m.quality_flag, 'ok') != 'corrupt' "
            "AND (a.id IS NULL OR a.model IS NOT ? OR IFNULL(a.people_sig, '') != ?) "
            "ORDER BY m.id",
            (model, people_sig),
        ).fetchall()
        return [(_row_to_media(r), r["ai_id"] is None or r["ai_model"] != model)
                for r in rows]

    def get_ai_raw(self, media_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT raw_json FROM ai_result WHERE media_id = ?", (media_id,)).fetchone()
        return row["raw_json"] if row else None

    def save_ai_result(self, media_id: int, model: str, people_sig: str,
                       media_type: str | None, people: list[str], raw_json: str):
        self.conn.execute(
            "INSERT INTO ai_result (media_id, model, people_sig, media_type, people, "
            "raw_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(media_id) DO UPDATE SET model = excluded.model, "
            "people_sig = excluded.people_sig, media_type = excluded.media_type, "
            "people = excluded.people, raw_json = excluded.raw_json, "
            "created_at = excluded.created_at",
            (media_id, model, people_sig, media_type, ", ".join(people), raw_json, _now()),
        )
        self.conn.commit()

    def tags_for(self, media_id: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT t.name FROM tag t JOIN media_tag mt ON mt.tag_id = t.id "
            "WHERE mt.media_id = ? ORDER BY t.name",
            (media_id,),
        ).fetchall()
        return [r["name"] for r in rows]

    def tag_counts(self, media_ids: list[int]) -> list[tuple[str, int]]:
        """Tags presentes en una selección, con cuántos de esos medios los tienen.

        Útil para los chips del taggeo por lote: un tag con count == len(sel) está
        en TODA la selección; con count < len(sel) está solo en algunos.
        """
        if not media_ids:
            return []
        placeholders = ",".join("?" for _ in media_ids)
        rows = self.conn.execute(
            f"SELECT t.name, COUNT(*) AS n FROM tag t "
            f"JOIN media_tag mt ON mt.tag_id = t.id "
            f"WHERE mt.media_id IN ({placeholders}) "
            f"GROUP BY t.id ORDER BY n DESC, t.name",
            media_ids,
        ).fetchall()
        return [(r["name"], r["n"]) for r in rows]

    # ------------------------------------------------------------- búsqueda
    def search(self, query: str, limit: int = 500,
               pending_only: bool = False) -> list[Media]:
        """Ver `_search`. Con `pending_only`, solo medios con tags de la IA sin confirmar."""
        if not pending_only:
            return self._search(query, limit)
        pending = {r["media_id"] for r in self.conn.execute(
            "SELECT DISTINCT media_id FROM media_tag WHERE confirmed = 0")}
        if not query or not query.strip():
            rows = self.conn.execute(
                "SELECT * FROM media WHERE id IN "
                "(SELECT media_id FROM media_tag WHERE confirmed = 0) "
                "ORDER BY filename LIMIT ?", (limit,)).fetchall()
            return [_row_to_media(r) for r in rows]
        return [m for m in self._search(query, 100000) if m.id in pending][:limit]

    def _search(self, query: str, limit: int = 500) -> list[Media]:
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

    def delete_media(self, ids: list[int]) -> list[str]:
        """Quita fotos/videos del catálogo (con sus tags y análisis, por cascada).

        Devuelve las miniaturas que quedaron sin uso, para borrar sus archivos:
        dos archivos idénticos pueden compartir miniatura, y esa se conserva."""
        if not ids:
            return []
        marcas = ",".join("?" * len(ids))
        thumbs = {r[0] for r in self.conn.execute(
            f"SELECT DISTINCT thumb_path FROM media WHERE id IN ({marcas}) AND thumb_path IS NOT NULL",
            ids)}
        self.conn.execute(f"DELETE FROM media_fts WHERE rowid IN ({marcas})", ids)
        self.conn.execute(f"DELETE FROM media WHERE id IN ({marcas})", ids)
        self.conn.commit()
        en_uso = {r[0] for r in self.conn.execute(
            "SELECT DISTINCT thumb_path FROM media WHERE thumb_path IS NOT NULL")}
        return sorted(thumbs - en_uso)

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
