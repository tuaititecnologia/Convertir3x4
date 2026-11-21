"""CRUD y consultas sobre el catálogo (PostgreSQL). Sin Qt.

Toda la UI y el motor pasan por acá; nadie toca SQL directo afuera de este
módulo (el esquema vive en `db.py`).

- La conexión viene de `db.conectar()` (autocommit, filas como dict). Lo que
  tiene que guardarse junto va en `with self.conn.transaction()`.
- Las rutas se guardan relativas a la raíz (`rel_path`, con "/"); `Media.path`
  es la ruta completa en esta PC (ver `rutas.py`).
- La búsqueda por palabras usa `media.search_doc` (tsvector), que se recalcula
  en la misma transacción que el alta, los tags o el análisis de la IA.
- Un medio cuyo archivo no aparece en su ruta no se borra: queda con
  `missing_since`. Si reaparece en otra ruta, se le cambia la ruta y conserva
  sus tags (`mover`).
"""

import hashlib
import re

import psycopg

from . import rutas
from .db import LLAVE_CATALOGAR
from .models import Media

_MEDIA_COLS = (
    "rel_path", "filename", "folder_origin", "kind", "width", "height", "aspect",
    "duration", "codec", "size_bytes", "file_mtime", "sha256", "phash",
    "status", "quality_flag",
)

# Recalcula el texto de búsqueda de los medios dados: nombre, carpeta y tags
# pesan más (A) que lo que escribió la IA (B).
_SQL_BUSQUEDA = """
UPDATE media m SET search_doc =
    setweight(to_tsvector('catalogo',
        palabras(m.filename || ' ' || COALESCE(m.folder_origin, ''))), 'A')
 || setweight(to_tsvector('catalogo', palabras(COALESCE(
        (SELECT string_agg(t.name, ' ') FROM media_tag mt
         JOIN tag t ON t.id = mt.tag_id WHERE mt.media_id = m.id), ''))), 'A')
 || setweight(to_tsvector('catalogo', palabras(COALESCE(
        (SELECT concat_ws(' ', a.description, a.ocr_text, a.synonyms)
         FROM ai_result a WHERE a.media_id = m.id), ''))), 'B')
WHERE m.id = ANY(%s)
"""


class Repo:
    def __init__(self, conn: psycopg.Connection, raiz: str):
        self.conn = conn
        self.raiz = raiz

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _q(self, sql: str, params=None):
        return self.conn.execute(sql, params)

    def _media(self, row) -> Media:
        return _row_to_media(row, self.raiz)

    def rel(self, path: str) -> str | None:
        """Ruta relativa a la raíz, o None si `path` no está en el NAS."""
        return rutas.a_relativa(path, self.raiz)

    # ------------------------------------------------------------- media
    def get_media_by_path(self, path: str) -> Media | None:
        rel = self.rel(path)
        if rel is None:
            return None
        row = self._q("SELECT * FROM media WHERE rel_path = %s", (rel,)).fetchone()
        return self._media(row) if row else None

    def get_media(self, media_id: int) -> Media | None:
        row = self._q("SELECT * FROM media WHERE id = %s", (media_id,)).fetchone()
        return self._media(row) if row else None

    def upsert_media(self, m: Media, miniatura: bytes | None = None) -> int:
        """Inserta o actualiza por ruta. Devuelve el id.

        Si el medio estaba como faltante y vuelve a su ruta, deja de faltar.
        `miniatura` (JPEG) se guarda en la misma transacción."""
        rel = m.rel_path if m.rel_path is not None else self.rel(m.path)
        if rel is None:
            raise ValueError(f"fuera de la raíz del catálogo: {m.path}")
        m.rel_path = rel
        values = [getattr(m, c) for c in _MEDIA_COLS]
        cols = ", ".join(_MEDIA_COLS)
        marcas = ", ".join("%s" for _ in _MEDIA_COLS)
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in _MEDIA_COLS if c != "rel_path")
        with self.conn.transaction():
            media_id = self._q(
                f"INSERT INTO media ({cols}) VALUES ({marcas}) "
                f"ON CONFLICT (rel_path) DO UPDATE SET {sets}, "
                f"missing_since = NULL, updated_at = now() RETURNING id",
                values).fetchone()["id"]
            if miniatura is not None:
                self._guardar_miniatura(media_id, miniatura)
            self._actualizar_busqueda([media_id])
        m.id = media_id
        m.missing_since = None
        return media_id

    def count_media(self, status: str | None = None) -> int:
        if status:
            return self._q("SELECT COUNT(*) AS n FROM media WHERE status = %s",
                           (status,)).fetchone()["n"]
        return self._q("SELECT COUNT(*) AS n FROM media").fetchone()["n"]

    def all_media(self, limit: int | None = None) -> list[Media]:
        rows = self._q("SELECT * FROM media ORDER BY missing_since IS NOT NULL, filename, id "
                       "LIMIT %s", (limit,)).fetchall()
        return [self._media(r) for r in rows]

    def delete_media(self, ids: list[int]):
        """Quita fotos/videos del catálogo, con sus tags, análisis y miniatura
        (por cascada). Los archivos no se tocan."""
        if ids:
            self._q("DELETE FROM media WHERE id = ANY(%s)", (list(ids),))

    # ------------------------------------------------------------- miniaturas
    def _guardar_miniatura(self, media_id: int, jpeg: bytes):
        self._q(
            "INSERT INTO thumbnail (media_id, jpeg, sello) VALUES (%s, %s, %s) "
            "ON CONFLICT (media_id) DO UPDATE SET jpeg = EXCLUDED.jpeg, sello = EXCLUDED.sello",
            (media_id, jpeg, sello_de(jpeg)))

    def guardar_miniatura(self, media_id: int, jpeg: bytes):
        self._guardar_miniatura(media_id, jpeg)

    def sellos_miniatura(self, ids: list[int]) -> dict[int, str]:
        """Sello de la miniatura de cada medio (sin bajar los bytes)."""
        if not ids:
            return {}
        return {r["media_id"]: r["sello"] for r in self._q(
            "SELECT media_id, sello FROM thumbnail WHERE media_id = ANY(%s)", (list(ids),))}

    def miniaturas(self, ids: list[int]) -> dict[int, tuple[str, bytes]]:
        """{id: (sello, jpeg)} de los medios pedidos que tienen miniatura."""
        if not ids:
            return {}
        return {r["media_id"]: (r["sello"], bytes(r["jpeg"])) for r in self._q(
            "SELECT media_id, sello, jpeg FROM thumbnail WHERE media_id = ANY(%s)",
            (list(ids),))}

    def miniatura(self, media_id: int) -> bytes | None:
        par = self.miniaturas([media_id]).get(media_id)
        return par[1] if par else None

    # ------------------------------------------------------------- faltantes y movidos
    def estado_carpeta(self, carpeta: str) -> dict[str, dict]:
        """Lo catalogado dentro de `carpeta` (relativa; "" = todo):
        {rel_path: {id, file_mtime, size_bytes, missing_since}}."""
        if carpeta:
            rows = self._q(
                "SELECT id, rel_path, file_mtime, size_bytes, missing_since FROM media "
                "WHERE rel_path = %s OR starts_with(rel_path, %s)",
                (carpeta, carpeta + "/"))
        else:
            rows = self._q(
                "SELECT id, rel_path, file_mtime, size_bytes, missing_since FROM media")
        return {r["rel_path"]: r for r in rows}

    def marcar_faltantes(self, ids: list[int]) -> list[int]:
        """Anota que estos medios ya no están en su ruta. No se borran.
        Devuelve los que se marcaron ahora."""
        if not ids:
            return []
        return [r["id"] for r in self._q(
            "UPDATE media SET missing_since = now() "
            "WHERE id = ANY(%s) AND missing_since IS NULL RETURNING id", (list(ids),))]

    def siguen_faltando(self, ids) -> int:
        """Cuántos de estos medios siguen sin aparecer."""
        if not ids:
            return 0
        return self._q("SELECT COUNT(*) AS n FROM media WHERE id = ANY(%s) "
                       "AND missing_since IS NOT NULL", (list(ids),)).fetchone()["n"]

    def marcar_presente(self, media_id: int):
        self._q("UPDATE media SET missing_since = NULL WHERE id = %s", (media_id,))

    def candidatos_reaparecido(self, filename: str, size: int, mtime: int) -> list[Media]:
        """Faltantes que pueden ser este archivo nuevo (mismo nombre, tamaño y fecha)."""
        rows = self._q(
            "SELECT * FROM media WHERE missing_since IS NOT NULL AND filename = %s "
            "AND size_bytes = %s AND file_mtime = %s ORDER BY id",
            (filename, size, mtime)).fetchall()
        return [self._media(r) for r in rows]

    def mover(self, media_id: int, rel_path: str, folder_origin: str | None) -> bool:
        """Cambia la ruta de un medio que se movió; conserva tags y análisis.
        False si otra PC ya catalogó esa ruta."""
        try:
            with self.conn.transaction():
                self._q("UPDATE media SET rel_path = %s, folder_origin = %s, "
                        "missing_since = NULL, updated_at = now() WHERE id = %s",
                        (rel_path, folder_origin, media_id))
                self._actualizar_busqueda([media_id])
        except psycopg.errors.UniqueViolation:
            return False
        return True

    def faltantes(self) -> list[Media]:
        rows = self._q("SELECT * FROM media WHERE missing_since IS NOT NULL "
                       "ORDER BY rel_path").fetchall()
        return [self._media(r) for r in rows]

    # ------------------------------------------------------------- hash y duplicados
    def pendientes_de_sha(self, carpeta: str = "") -> list[Media]:
        """Medios sin sha256 que pesan lo mismo que otro: solo esos pueden ser
        duplicados exactos, así que solo a esos vale la pena leerlos enteros."""
        rows = self._q(
            "SELECT * FROM media WHERE sha256 IS NULL AND missing_since IS NULL "
            "AND (%s = '' OR rel_path = %s OR starts_with(rel_path, %s || '/')) "
            "AND size_bytes IN (SELECT size_bytes FROM media WHERE size_bytes IS NOT NULL "
            "                   GROUP BY size_bytes HAVING COUNT(*) > 1) "
            "ORDER BY id", (carpeta, carpeta, carpeta)).fetchall()
        return [self._media(r) for r in rows]

    def guardar_sha(self, media_id: int, sha: str):
        self._q("UPDATE media SET sha256 = %s WHERE id = %s", (sha, media_id))

    def get_media_by_sha256(self, sha: str) -> Media | None:
        row = self._q("SELECT * FROM media WHERE sha256 = %s ORDER BY id LIMIT 1",
                      (sha,)).fetchone()
        return self._media(row) if row else None

    def duplicate_groups(self) -> list[list[Media]]:
        """Grupos de medios que comparten sha256 (duplicados exactos)."""
        rows = self._q(
            "SELECT * FROM media WHERE sha256 IN (SELECT sha256 FROM media "
            "WHERE sha256 IS NOT NULL GROUP BY sha256 HAVING COUNT(*) > 1) "
            "ORDER BY sha256, id").fetchall()
        grupos: dict[str, list[Media]] = {}
        for r in rows:
            grupos.setdefault(r["sha256"], []).append(self._media(r))
        return list(grupos.values())

    # ------------------------------------------------------------- bloqueo por carpeta
    def tomar_carpeta(self, carpeta: str) -> bool:
        """Que una sola PC catalogue `carpeta` a la vez. False si otra la tiene.
        El bloqueo es de esta conexión: se suelta con `soltar_carpeta` o al cerrarla."""
        return self._q("SELECT pg_try_advisory_lock(%s, hashtext(%s)) AS ok",
                       (LLAVE_CATALOGAR, carpeta)).fetchone()["ok"]

    def soltar_carpeta(self, carpeta: str):
        self._q("SELECT pg_advisory_unlock(%s, hashtext(%s))", (LLAVE_CATALOGAR, carpeta))

    # ------------------------------------------------------------- tags
    def normalize_tag(self, name: str) -> str:
        return " ".join(name.strip().lower().split())

    def get_or_create_tag(self, name: str, kind: str = "descriptivo") -> int:
        norm = self.normalize_tag(name)
        self._q("INSERT INTO tag (name, kind) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
                (norm, kind))
        return self._q("SELECT id FROM tag WHERE name = %s", (norm,)).fetchone()["id"]

    def all_tag_names(self) -> list[str]:
        return [r["name"] for r in self._q("SELECT name FROM tag ORDER BY name")]

    def apply_tag(self, media_id: int, name: str, source: str = "humano",
                  confirmed: bool = True):
        """Pone (o confirma) un tag. Aplicarlo a mano sobre uno sugerido por la
        IA lo pasa a humano/confirmado y borra un rechazo previo."""
        tag_id = self.get_or_create_tag(name)
        with self.conn.transaction():
            self._q(
                "INSERT INTO media_tag (media_id, tag_id, source, confirmed) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (media_id, tag_id) DO UPDATE "
                "SET source = EXCLUDED.source, confirmed = EXCLUDED.confirmed",
                (media_id, tag_id, source, confirmed))
            self._q("DELETE FROM tag_rejection WHERE media_id = %s AND tag_id = %s",
                    (media_id, tag_id))
            self._actualizar_busqueda([media_id])

    def suggest_tag(self, media_id: int, name: str, kind: str = "ia-sugerido") -> bool:
        """Tag propuesto por la IA (source='ia', sin confirmar).

        No pisa un tag que ya exista (humano o no) ni vuelve a proponer uno que
        alguien sacó. Devuelve True si lo agregó.
        """
        tag_id = self.get_or_create_tag(name, kind=kind)
        with self.conn.transaction():
            rechazado = self._q(
                "SELECT 1 FROM tag_rejection WHERE media_id = %s AND tag_id = %s",
                (media_id, tag_id)).fetchone()
            if rechazado:
                return False
            n = self._q(
                "INSERT INTO media_tag (media_id, tag_id, source, confirmed) "
                "VALUES (%s, %s, 'ia', false) ON CONFLICT DO NOTHING",
                (media_id, tag_id)).rowcount
            if n:
                self._actualizar_busqueda([media_id])
        return bool(n)

    def remove_tag(self, media_id: int, name: str):
        """Saca un tag y lo anota como rechazado, para que la IA no lo reponga."""
        row = self._q("SELECT id FROM tag WHERE name = %s",
                      (self.normalize_tag(name),)).fetchone()
        if not row:
            return
        with self.conn.transaction():
            self._q("DELETE FROM media_tag WHERE media_id = %s AND tag_id = %s",
                    (media_id, row["id"]))
            self._q("INSERT INTO tag_rejection (media_id, tag_id) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING", (media_id, row["id"]))
            self._actualizar_busqueda([media_id])

    def confirm_tag(self, media_id: int, name: str):
        """Confirma un tag sugerido (si el medio lo tiene). Queda source='ia'."""
        self._q("UPDATE media_tag SET confirmed = true WHERE media_id = %s "
                "AND tag_id = (SELECT id FROM tag WHERE name = %s)",
                (media_id, self.normalize_tag(name)))

    def pending_tag_counts(self, media_ids: list[int]) -> dict[str, int]:
        """De los tags de la selección, cuántos medios los tienen sin confirmar."""
        if not media_ids:
            return {}
        rows = self._q(
            "SELECT t.name, COUNT(*) AS n FROM tag t JOIN media_tag mt ON mt.tag_id = t.id "
            "WHERE mt.media_id = ANY(%s) AND NOT mt.confirmed GROUP BY t.id",
            (list(media_ids),))
        return {r["name"]: r["n"] for r in rows}

    def count_pending_tags(self) -> int:
        """Medios con al menos un tag de la IA sin confirmar."""
        return self._q("SELECT COUNT(DISTINCT media_id) AS n FROM media_tag "
                       "WHERE NOT confirmed").fetchone()["n"]

    def tags_for(self, media_id: int) -> list[str]:
        return [r["name"] for r in self._q(
            "SELECT t.name FROM tag t JOIN media_tag mt ON mt.tag_id = t.id "
            "WHERE mt.media_id = %s ORDER BY t.name", (media_id,))]

    def tag_counts(self, media_ids: list[int]) -> list[tuple[str, int]]:
        """Tags presentes en una selección, con cuántos de esos medios los tienen.

        Útil para los chips del taggeo por lote: un tag con count == len(sel) está
        en TODA la selección; con count < len(sel) está solo en algunos.
        """
        if not media_ids:
            return []
        rows = self._q(
            "SELECT t.name, COUNT(*) AS n FROM tag t JOIN media_tag mt ON mt.tag_id = t.id "
            "WHERE mt.media_id = ANY(%s) GROUP BY t.id ORDER BY n DESC, t.name",
            (list(media_ids),))
        return [(r["name"], r["n"]) for r in rows]

    # ------------------------------------------------------------- personas
    def add_person(self, name: str) -> int:
        name = " ".join(name.split())
        self._q("INSERT INTO person (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (name,))
        return self._q("SELECT id FROM person WHERE name = %s", (name,)).fetchone()["id"]

    def list_persons(self) -> list[tuple[int, str, int]]:
        """(id, nombre, cantidad de fotos de referencia)."""
        rows = self._q(
            "SELECT p.id, p.name, COUNT(r.id) AS n FROM person p "
            "LEFT JOIN person_reference r ON r.person_id = p.id "
            "GROUP BY p.id ORDER BY p.name")
        return [(r["id"], r["name"], r["n"]) for r in rows]

    def delete_person(self, person_id: int):
        """Borra la persona y sus firmas. Los tags ya puestos quedan."""
        self._q("DELETE FROM person WHERE id = %s", (person_id,))

    def add_person_reference(self, person_id: int, source_path: str, embedding: bytes):
        self._q("INSERT INTO person_reference (person_id, source_path, embedding) "
                "VALUES (%s, %s, %s)", (person_id, source_path, embedding))

    def person_reference_blobs(self) -> dict[str, list[bytes]]:
        out: dict[str, list[bytes]] = {}
        for r in self._q("SELECT p.name, r.embedding FROM person_reference r "
                         "JOIN person p ON p.id = r.person_id ORDER BY r.id"):
            out.setdefault(r["name"], []).append(bytes(r["embedding"]))
        return out

    def people_signature(self) -> str:
        """Cambia cada vez que se agrega o quita una referencia o una persona."""
        rows = self._q("SELECT r.id, p.name FROM person_reference r "
                       "JOIN person p ON p.id = r.person_id ORDER BY r.id").fetchall()
        if not rows:
            return ""
        raw = ";".join(f"{r['id']}:{r['name']}" for r in rows)
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    # ------------------------------------------------------------- IA
    def media_for_ai(self, model: str, people_sig: str) -> list[tuple[Media, bool]]:
        """Medios que hay que analizar: (medio, hace_falta_escena).

        Falta escena si nunca se analizó o cambió el modelo/vocabulario. Si solo
        cambió la lista de personas, se repasan las caras y no la escena. Los
        que faltan de su ruta esperan a reaparecer.
        """
        rows = self._q(
            "SELECT m.*, a.model AS ai_model, a.media_id AS ai_id FROM media m "
            "LEFT JOIN ai_result a ON a.media_id = m.id "
            "WHERE m.quality_flag <> 'corrupt' AND m.missing_since IS NULL "
            "AND (a.media_id IS NULL OR a.model IS DISTINCT FROM %s "
            "     OR COALESCE(a.people_sig, '') <> %s) ORDER BY m.id",
            (model, people_sig)).fetchall()
        return [(self._media(r), r["ai_id"] is None or r["ai_model"] != model)
                for r in rows]

    def get_ai_raw(self, media_id: int) -> str | None:
        row = self._q("SELECT raw_json FROM ai_result WHERE media_id = %s",
                      (media_id,)).fetchone()
        return row["raw_json"] if row else None

    def save_ai_result(self, media_id: int, model: str, people_sig: str,
                       media_type: str | None, people: list[str], raw_json: str):
        self._q(
            "INSERT INTO ai_result (media_id, model, people_sig, media_type, people, raw_json) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (media_id) DO UPDATE SET "
            "model = EXCLUDED.model, people_sig = EXCLUDED.people_sig, "
            "media_type = EXCLUDED.media_type, people = EXCLUDED.people, "
            "raw_json = EXCLUDED.raw_json, created_at = now()",
            (media_id, model, people_sig, media_type, ", ".join(people), raw_json))

    # ------------------------------------------------------------- búsqueda
    def search(self, query: str, limit: int = 500,
               pending_only: bool = False) -> list[Media]:
        """Ver `_search`. Con `pending_only`, solo medios con tags de la IA sin confirmar."""
        if not pending_only:
            return self._search(query, limit)
        if not query or not query.strip():
            rows = self._q(
                "SELECT * FROM media WHERE id IN "
                "(SELECT media_id FROM media_tag WHERE NOT confirmed) "
                "ORDER BY missing_since IS NOT NULL, filename, id LIMIT %s", (limit,)).fetchall()
            return [self._media(r) for r in rows]
        pending = {r["media_id"] for r in self._q(
            "SELECT DISTINCT media_id FROM media_tag WHERE NOT confirmed")}
        return [m for m in self._search(query, 100000) if m.id in pending][:limit]

    def _search(self, query: str, limit: int = 500) -> list[Media]:
        """Búsqueda combinada. Vacío → todos los medios.

        Capa 1: palabras (prefijo, sin acentos) sobre nombre, carpeta, tags y lo
        que escribió la IA, ordenado por relevancia.
        Capa 2: fragmento (LIKE con índice pg_trgm) sobre nombre + carpeta, para
        cazar pedazos en el MEDIO de nombres opacos (p.ej. '4782' dentro de
        FB_IMG_...4782...), que la capa 1 no encuentra.
        Los de la capa 1 van primero; los de la 2 se agregan sin repetir. Los
        que faltan de su ruta van al final.
        """
        if not query or not query.strip():
            return self.all_media(limit)

        results: list[Media] = []
        seen: set[int] = set()

        tsq = _to_tsquery(query)
        if tsq:
            for r in self._q(
                    "SELECT m.* FROM media m, to_tsquery('catalogo', %s) q "
                    "WHERE m.search_doc @@ q "
                    "ORDER BY ts_rank(m.search_doc, q) DESC, m.filename, m.id LIMIT %s",
                    (tsq, limit)):
                seen.add(r["id"])
                results.append(self._media(r))

        for r in self._substring_rows(query, limit):
            if r["id"] not in seen:
                seen.add(r["id"])
                results.append(self._media(r))

        results = ([m for m in results if not m.missing]
                   + [m for m in results if m.missing])
        return results[:limit]

    def _substring_rows(self, query: str, limit: int):
        """Filas cuyo nombre+carpeta contienen TODOS los términos (fragmento)."""
        terms = [t for t in query.split() if t]
        if not terms:
            return []
        clauses = " AND ".join(
            "texto_nombre(filename, folder_origin) LIKE sin_acentos(lower(%s))"
            for _ in terms)
        params = [f"%{_escape_like(t)}%" for t in terms] + [limit]
        return self._q(f"SELECT * FROM media WHERE {clauses} "
                       f"ORDER BY filename, id LIMIT %s", params).fetchall()

    # ------------------------------------------------------------- interno
    def _actualizar_busqueda(self, ids: list[int]):
        self._q(_SQL_BUSQUEDA, (list(ids),))


def sello_de(jpeg: bytes) -> str:
    """Identifica una miniatura por su contenido (para la caché de cada PC)."""
    return hashlib.md5(jpeg).hexdigest()


def _row_to_media(row, raiz: str) -> Media:
    return Media(
        id=row["id"],
        path=rutas.a_completa(row["rel_path"], raiz),
        rel_path=row["rel_path"],
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
        quality_flag=row["quality_flag"],
        missing_since=row["missing_since"],
    )


def _to_tsquery(query: str) -> str | None:
    """Texto libre -> tsquery con prefijo por palabra ('foto:* & culto:*').

    Se separa igual que al indexar (`palabras()` en la base): todo lo que no es
    letra o número corta, así que no puede colarse un operador de tsquery.
    """
    terms = re.findall(r"[^\W_]+", query)
    return " & ".join(f"{t}:*" for t in terms) or None


def _escape_like(term: str) -> str:
    """Escapa los comodines de LIKE (\\, %, _) para tratarlos como literales."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
