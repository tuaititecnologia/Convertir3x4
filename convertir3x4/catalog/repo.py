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
from dataclasses import dataclass
import os
import re

import psycopg
import psycopg.types.json

from . import consulta, rutas
from .alcance import Alcance
from .db import CANAL_CAMBIOS, CANAL_CARPETAS_LEIDAS, CANAL_PEDIR_CARPETAS, LLAVE_CATALOGAR
from .models import Media

_MEDIA_COLS = (
    "rel_path", "filename", "folder_origin", "kind", "width", "height", "aspect",
    "duration", "codec", "size_bytes", "file_mtime", "sha256", "phash",
    "status", "quality_flag", "tomada", "lat", "lon",
)

# Recalcula el texto de búsqueda de los medios dados: nombre, carpeta, tags y
# notas (lo escrito por personas) pesan más (A) que lo leído por la máquina (B).
_SQL_BUSQUEDA = """
UPDATE media m SET search_doc =
    setweight(to_tsvector('catalogo',
        palabras(m.filename || ' ' || COALESCE(m.folder_origin, ''))), 'A')
 || setweight(to_tsvector('catalogo', palabras(COALESCE(
        (SELECT string_agg(t.name, ' ') FROM media_tag mt
         JOIN tag t ON t.id = mt.tag_id WHERE mt.media_id = m.id), ''))), 'A')
 || setweight(to_tsvector('catalogo', palabras(m.notas)), 'A')
 || setweight(to_tsvector('catalogo', palabras(COALESCE(
        (SELECT concat_ws(' ', a.description, a.ocr_text, a.synonyms)
         FROM ai_result a WHERE a.media_id = m.id), ''))), 'B')
 || setweight(to_tsvector('catalogo', palabras(COALESCE(
        (SELECT f.texto FROM texto_foto f WHERE f.media_id = m.id), ''))), 'B')
WHERE m.id = ANY(%s)
"""


# Etiquetas de un tipo especial (pedido del 24/11/2026): se ponen desde sus
# propios campos en Buscar y se buscan con lugar: / evento:. Un medio tiene un
# solo lugar y cualquier cantidad de eventos. Un nombre es de un solo tipo: si
# «campamento» es un evento, no puede ser también una etiqueta común.
LUGAR, EVENTO = "lugar", "evento"
ESPECIALES = (LUGAR, EVENTO)
# Solo las comunes (escritas a mano) pasan a lugar o evento: las de personas
# las pone el reconocimiento de caras y las de la IA las vuelve a proponer el
# NAS por nombre.
_CONVERTIBLES = ("descriptivo",) + ESPECIALES
_NOMBRE_TIPO = {LUGAR: "un lugar", EVENTO: "un evento", "persona": "una persona",
                "ia-sugerido": "una etiqueta de la IA", "descriptivo": "una etiqueta común"}
_CAMPO_TIPO = {LUGAR: "el campo Lugar", EVENTO: "el campo Eventos",
               "descriptivo": "la barra de etiquetas"}


class NombreEnUso(ValueError):
    """El nombre ya es de otro tipo (p. ej. se quiere poner como evento un
    nombre que es una etiqueta común). El mensaje es para el operador."""

    def __init__(self, nombre: str, tipo: str, quiere: str):
        self.nombre, self.tipo, self.quiere = nombre, tipo, quiere
        donde = _CAMPO_TIPO.get(tipo)
        texto = f"«{nombre}» ya es {_NOMBRE_TIPO.get(tipo, 'otra cosa')}"
        texto += f": póngalo desde {donde}." if donde else "."
        super().__init__(texto)


class NoSePuedeConvertir(ValueError):
    """Cambiar el tipo de una etiqueta no se puede (mensaje para el operador)."""


def _en_carpetas(carpetas, col: str = "m.rel_path") -> tuple[str, list]:
    """Condición SQL «está dentro de alguna de estas carpetas» (relativas).
    "" es la raíz: contiene todo."""
    conds, params = [], []
    for c in carpetas:
        c = c.strip("/")
        if not c:
            return "TRUE", []
        conds.append(f"({col} = %s OR starts_with({col}, %s))")
        params += [c, c + "/"]
    return ("(" + " OR ".join(conds) + ")") if conds else "FALSE", params


# Carpeta (relativa) de un rel_path, en SQL: 'a/b/c.jpg' -> 'a/b', 'c.jpg' -> ''.
_CARPETA_SQL = ("CASE WHEN strpos(rel_path, '/') > 0 "
                "THEN regexp_replace(rel_path, '/[^/]*$', '') ELSE '' END")


# Las fechas de toma se comparan por día en la hora de la iglesia.
ZONA_IGLESIA = "America/Argentina/Buenos_Aires"


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

    def upsert_media(self, m: Media, miniatura: bytes | None = None,
                     miniatura_version: int | None = None,
                     metadatos_version: int | None = None, tira: bytes | None = None) -> int:
        """Inserta o actualiza por ruta. Devuelve el id.

        Si el medio estaba como faltante y vuelve a su ruta, deja de faltar.
        `miniatura` (JPEG) se guarda en la misma transacción, con la versión de
        la regla con que se hizo (ver thumbs.MINIATURA_VERSION)."""
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
            if miniatura_version is not None:
                self._q("UPDATE media SET miniatura_version = %s WHERE id = %s",
                        (miniatura_version, media_id))
            if metadatos_version is not None:
                self._q("UPDATE media SET metadatos_version = %s WHERE id = %s",
                        (metadatos_version, media_id))
            if tira is not None:
                self._guardar_tira(media_id, tira)
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

    # ------------------------------------------------------------- tiras de cuadros
    def _guardar_tira(self, media_id: int, jpeg: bytes):
        self._q(
            "INSERT INTO tira (media_id, jpeg, sello) VALUES (%s, %s, %s) "
            "ON CONFLICT (media_id) DO UPDATE SET jpeg = EXCLUDED.jpeg, sello = EXCLUDED.sello",
            (media_id, jpeg, sello_de(jpeg)))

    def guardar_tira(self, media_id: int, jpeg: bytes):
        """Tira de cuadros de un video (la arma el NAS, catalog/tiras.py)."""
        self._guardar_tira(media_id, jpeg)

    def sellos_tira(self, ids: list[int]) -> dict[int, str]:
        if not ids:
            return {}
        return {r["media_id"]: r["sello"] for r in self._q(
            "SELECT media_id, sello FROM tira WHERE media_id = ANY(%s)", (list(ids),))}

    def tiras(self, ids: list[int]) -> dict[int, tuple[str, bytes]]:
        """{id: (sello, jpeg)} de los videos pedidos que tienen tira."""
        if not ids:
            return {}
        return {r["media_id"]: (r["sello"], bytes(r["jpeg"])) for r in self._q(
            "SELECT media_id, sello, jpeg FROM tira WHERE media_id = ANY(%s)", (list(ids),))}

    # ------------------------------------------------------------- fecha y lugar
    def guardar_metadatos(self, media_id: int, tomada, lat, lon, version: int):
        """Fecha de toma y ubicación de un medio ya catalogado (sin tocar lo demás)."""
        self._q("UPDATE media SET tomada = %s, lat = %s, lon = %s, metadatos_version = %s "
                "WHERE id = %s", (tomada, lat, lon, version, media_id))

    # ------------------------------------------------------------- faltantes y movidos
    def estado_carpeta(self, carpeta: str) -> dict[str, dict]:
        """Lo catalogado dentro de `carpeta` (relativa; "" = todo):
        {rel_path: {id, kind, duration, file_mtime, size_bytes, missing_since,
        quality_flag, miniatura_version, metadatos_version, tiene_tira}}."""
        cols = ("id, rel_path, kind, duration, file_mtime, size_bytes, missing_since, "
                "quality_flag, miniatura_version, metadatos_version, "
                "EXISTS (SELECT 1 FROM tira t WHERE t.media_id = media.id) AS tiene_tira")
        if carpeta:
            rows = self._q(
                f"SELECT {cols} FROM media "
                "WHERE rel_path = %s OR starts_with(rel_path, %s)",
                (carpeta, carpeta + "/"))
        else:
            rows = self._q(f"SELECT {cols} FROM media")
        return {r["rel_path"]: r for r in rows}

    def rehacer_miniatura(self, media_id: int, jpeg: bytes | None, version: int,
                          calidad: str | None = None):
        """Miniatura nueva (cambió cómo se hacen). Lo que la IA sugirió mirando la
        vieja se descarta y se vuelve a analizar; lo que confirmó una persona queda."""
        with self.conn.transaction():
            if jpeg:
                self._guardar_miniatura(media_id, jpeg)
            self._q("UPDATE media SET miniatura_version = %s, "
                    "quality_flag = COALESCE(%s, quality_flag) WHERE id = %s",
                    (version, calidad, media_id))
            self._q("DELETE FROM media_tag WHERE media_id = %s AND source = 'ia' "
                    "AND NOT confirmed", (media_id,))
            self._q("DELETE FROM ai_result WHERE media_id = %s", (media_id,))
            self._actualizar_busqueda([media_id])

    def marcar_miniatura_al_dia(self, media_id: int, version: int):
        self._q("UPDATE media SET miniatura_version = %s WHERE id = %s", (version, media_id))

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
    # ------------------------------------------------- catalogado en el NAS
    # Lo pide una PC (`pedir_catalogado`) y lo atiende el catalogador del NAS
    # (`tomar_pedido` … `cerrar_pedido`). Ver docs/catalogado-en-el-nas.md.

    def pedir_catalogado(self, carpeta: str | None = None, pedido_por: str = "",
                         pasos: list[str] | None = None, rehacer: bool = False) -> int:
        """Anota un pedido (carpeta None = todas; pasos None = todos, ver
        catalogador.PASOS). Si ya hay uno igual sin empezar, devuelve ese en
        lugar de duplicarlo."""
        pasos = sorted(set(pasos)) if pasos is not None else None
        with self.conn.transaction():
            fila = self._q(
                "SELECT id FROM catalogado_pedido WHERE terminado IS NULL AND empezado IS NULL"
                " AND carpeta IS NOT DISTINCT FROM %s AND pasos IS NOT DISTINCT FROM %s"
                " AND rehacer = %s ORDER BY id LIMIT 1", (carpeta, pasos, rehacer)).fetchone()
            if fila:
                return fila["id"]
            pid = self._q(
                "INSERT INTO catalogado_pedido (carpeta, pedido_por, pasos, rehacer)"
                " VALUES (%s, %s, %s, %s) RETURNING id",
                (carpeta, pedido_por or None, pasos, rehacer)).fetchone()["id"]
            self._q("NOTIFY catalogado")
        return pid

    # ------------------------------------------------- carpetas que se catalogan
    def marcas_carpetas(self) -> dict[str, bool]:
        """Las marcas del árbol de carpetas ("" = la raíz)."""
        return {r["ruta"]: r["incluir"] for r in self._q(
            "SELECT ruta, incluir FROM carpeta_catalogada")}

    def alcance(self) -> Alcance:
        """Qué se cataloga (ver catalog/alcance.py)."""
        return Alcance(self.marcas_carpetas())

    def carpetas_catalogadas(self) -> tuple[str, ...]:
        """Desde dónde recorre el NAS: las raíces del alcance (relativas)."""
        return self.alcance().raices()

    def medios_que_salen(self, marcas: dict[str, bool]) -> dict[str, tuple[int, int]]:
        """Lo catalogado que deja de estar en el alcance con estas marcas:
        {carpeta: (fotos, videos)}, agrupado por la carpeta que se destildó."""
        antes, despues = self.alcance(), Alcance(marcas)
        out: dict[str, list[int]] = {}
        for r in self._q("SELECT rel_path, kind FROM media"):
            rel = r["rel_path"]
            if not antes.incluye_archivo(rel) or despues.incluye_archivo(rel):
                continue
            c = rutas.carpeta_de(rel)
            clave = _marca_que_corta(despues, c)
            if clave is None:
                clave = antes.raiz_de(c) or ""
            n = out.setdefault(clave, [0, 0])
            n[0 if r["kind"] == "image" else 1] += 1
        return {k: (v[0], v[1]) for k, v in out.items()}

    def guardar_marcas(self, marcas: dict[str, bool], eliminar_medios: bool = False,
                       por: str = "") -> list[int]:
        """Reemplaza las marcas del árbol. Con `eliminar_medios`, saca del
        catálogo lo que deja de estar en el alcance (con sus etiquetas).
        Devuelve los ids quitados."""
        nuevas = Alcance(marcas).marcas
        with self.conn.transaction():
            self._q("LOCK TABLE carpeta_catalogada IN EXCLUSIVE MODE")
            ids: list[int] = []
            if eliminar_medios:
                antes, despues = self.alcance(), Alcance(nuevas)
                ids = [r["id"] for r in self._q("SELECT id, rel_path FROM media")
                       if antes.incluye_archivo(r["rel_path"])
                       and not despues.incluye_archivo(r["rel_path"])]
                self.delete_media(ids)
            actuales = self.marcas_carpetas()
            # Borrar y volver a insertar lo que cambia: el aviso a las PCs
            # (trigger de la migración 7) es de INSERT y DELETE.
            for ruta, valor in actuales.items():
                if nuevas.get(ruta) != valor:
                    self._q("DELETE FROM carpeta_catalogada WHERE ruta = %s", (ruta,))
            for ruta, valor in nuevas.items():
                if actuales.get(ruta) != valor:
                    self._q("INSERT INTO carpeta_catalogada (ruta, incluir, agregada_por)"
                            " VALUES (%s, %s, %s)", (ruta, valor, por or None))
        return ids

    # ------------------------------------------------- carpetas del NAS (árbol)
    def pedir_carpetas_nas(self):
        """Pide al catalogador que relea las carpetas del NAS. Si no está
        corriendo, el pedido se pierde (queda la lectura anterior)."""
        self._q(f"NOTIFY {CANAL_PEDIR_CARPETAS}")

    def reemplazar_carpetas_nas(self, carpetas) -> tuple[int, int]:
        """Lo que leyó el catalogador. Cambia solo la diferencia, anota la hora
        y avisa siempre (aunque no cambie nada: la PC que pidió espera la
        respuesta). Devuelve (agregadas, borradas)."""
        nuevas = {c.strip("/") for c in carpetas if c.strip("/")}
        with self.conn.transaction():
            actuales = {r["ruta"] for r in self._q("SELECT ruta FROM carpeta_nas")}
            borrar, agregar = sorted(actuales - nuevas), sorted(nuevas - actuales)
            if borrar:
                self._q("DELETE FROM carpeta_nas WHERE ruta = ANY(%s)", (borrar,))
            if agregar:
                with self.conn.cursor() as cur:
                    # ON CONFLICT: la relectura del hilo y la del final de una
                    # pasada pueden coincidir
                    cur.executemany("INSERT INTO carpeta_nas (ruta) VALUES (%s)"
                                    " ON CONFLICT DO NOTHING",
                                    [(c,) for c in agregar])
            self._q("UPDATE catalogado_estado SET carpetas_leidas = now()")
            self._q(f"NOTIFY {CANAL_CARPETAS_LEIDAS}")
        return len(agregar), len(borrar)

    def arbol_carpetas(self) -> dict:
        """Todo lo que necesita la ventana de carpetas, en una vuelta:
        carpetas del NAS, cuándo se leyeron, las marcas y {carpeta: (fotos,
        videos)} catalogados directamente en cada una (los que están)."""
        carpetas = [r["ruta"] for r in self._q("SELECT ruta FROM carpeta_nas")]
        leidas = self._q("SELECT carpetas_leidas FROM catalogado_estado").fetchone()
        conteos = {r["carpeta"]: (r["fotos"], r["videos"]) for r in self._q(
            f"SELECT {_CARPETA_SQL} AS carpeta, "
            "count(*) FILTER (WHERE kind = 'image') AS fotos, "
            "count(*) FILTER (WHERE kind = 'video') AS videos "
            "FROM media WHERE missing_since IS NULL GROUP BY 1")}
        return {"carpetas": carpetas, "leidas": leidas["carpetas_leidas"] if leidas else None,
                "marcas": self.marcas_carpetas(), "conteos": conteos}

    def agregar_carpeta(self, ruta: str, por: str = "") -> str:
        """Agrega una carpeta a la lista. Devuelve cómo quedó escrita. Lanza
        `ConfiguracionInvalida` si sale de la raíz, si ya está o si una está
        dentro de otra (se recorrería dos veces)."""
        from .db import ConfiguracionInvalida, normalizar_carpetas
        nueva = normalizar_carpetas([ruta])
        if not nueva:
            raise ConfiguracionInvalida("elija una carpeta dentro de la raíz")
        nueva = nueva[0]
        with self.conn.transaction():
            self._q("LOCK TABLE carpeta_catalogada IN EXCLUSIVE MODE")
            actuales = self.carpetas_catalogadas()
            if any(c.lower() == nueva.lower() for c in actuales):
                raise ConfiguracionInvalida(f"«{nueva}» ya está en la lista")
            normalizar_carpetas(list(actuales) + [nueva])      # ¿una dentro de otra?
            self._q("INSERT INTO carpeta_catalogada (ruta, agregada_por) VALUES (%s, %s)",
                    (nueva, por or None))
        return nueva

    def medios_en_carpeta(self, ruta: str) -> list[int]:
        cond, params = _en_carpetas([ruta], "rel_path")
        return [r["id"] for r in self._q(f"SELECT id FROM media WHERE {cond}", params)]

    def quitar_carpeta(self, ruta: str, con_sus_medios: bool = False) -> list[int]:
        """Saca una carpeta de la lista. Con `con_sus_medios`, también saca del
        catálogo sus fotos y videos (con sus etiquetas). Devuelve los ids quitados."""
        with self.conn.transaction():
            ids = self.medios_en_carpeta(ruta) if con_sus_medios else []
            self.delete_media(ids)
            self._q("DELETE FROM carpeta_catalogada WHERE ruta = %s", (ruta,))
        return ids

    # ------------------------------------------------- pantallas guardadas (Convertir)
    def pantallas(self) -> list[dict]:
        """[{id, nombre, ancho, alto}] por nombre. Medidas como float."""
        return [{"id": r["id"], "nombre": r["nombre"], "ancho": float(r["ancho"]),
                 "alto": float(r["alto"])}
                for r in self._q("SELECT id, nombre, ancho, alto FROM pantalla ORDER BY lower(nombre)")]

    def guardar_pantalla(self, nombre: str, ancho: float, alto: float,
                         pantalla_id: int | None = None) -> int:
        """Crea (o cambia, con `pantalla_id`) una pantalla. Lanza ValueError si
        falta el nombre, la medida no sirve o el nombre ya existe."""
        nombre = " ".join((nombre or "").split())
        if not nombre:
            raise ValueError("falta el nombre")
        if not (ancho and alto and ancho > 0 and alto > 0):
            raise ValueError("la medida tiene que ser mayor que cero")
        try:
            if pantalla_id is None:
                return self._q("INSERT INTO pantalla (nombre, ancho, alto) VALUES (%s, %s, %s) "
                               "RETURNING id", (nombre, ancho, alto)).fetchone()["id"]
            self._q("UPDATE pantalla SET nombre = %s, ancho = %s, alto = %s WHERE id = %s",
                    (nombre, ancho, alto, pantalla_id))
            return pantalla_id
        except psycopg.errors.UniqueViolation as e:
            raise ValueError(f"ya hay una pantalla «{nombre}»") from e

    def quitar_pantalla(self, pantalla_id: int):
        self._q("DELETE FROM pantalla WHERE id = %s", (pantalla_id,))

    def pedidos_abiertos(self) -> list[dict]:
        return self._q(
            "SELECT id, carpeta, pasos, rehacer, pedido_por, creado, empezado FROM catalogado_pedido"
            " WHERE terminado IS NULL ORDER BY id").fetchall()

    def pedido(self, pedido_id: int) -> dict | None:
        return self._q("SELECT * FROM catalogado_pedido WHERE id = %s", (pedido_id,)).fetchone()

    def tomar_pedido(self) -> dict | None:
        """El pedido más viejo sin empezar, ya marcado como empezado."""
        return self._q(
            "UPDATE catalogado_pedido SET empezado = now() WHERE id = ("
            " SELECT id FROM catalogado_pedido WHERE terminado IS NULL AND empezado IS NULL"
            " ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *").fetchone()

    def cerrar_pedido(self, pedido_id: int, resumen: dict | None = None,
                      error: str | None = None):
        self._q("UPDATE catalogado_pedido SET terminado = now(), resumen = %s, error = %s"
                " WHERE id = %s", (psycopg.types.json.Jsonb(resumen) if resumen is not None
                                   else None, error, pedido_id))

    def soltar_pedidos_colgados(self) -> int:
        """Al arrancar el catalogador: lo que quedó empezado y sin cerrar (se
        cortó a mitad) vuelve a la fila. La pasada es idempotente."""
        return self._q("UPDATE catalogado_pedido SET empezado = NULL"
                       " WHERE terminado IS NULL AND empezado IS NOT NULL").rowcount

    def estado_catalogado(self) -> dict:
        """La fila de estado, con `segundos_sin_latido` (None si nunca latió)."""
        return self._q(
            "SELECT *, EXTRACT(EPOCH FROM now() - latido)::integer AS segundos_sin_latido,"
            " now() AS ahora FROM catalogado_estado").fetchone()

    _CAMPOS_ESTADO = {"version_app", "carpetas", "carpetas_leidas", "fase", "detalle", "hechos", "total",
                      "pedido_id", "ultima_inicio", "ultima_fin", "ultima_resumen",
                      "ultima_error", "ultima_programada"}

    def actualizar_estado(self, latir: bool = True, **campos):
        """Cambia los campos dados de la fila de estado (y el latido)."""
        malos = set(campos) - self._CAMPOS_ESTADO
        if malos:
            raise ValueError(f"campos desconocidos: {sorted(malos)}")
        sets, vals = [], []
        for k, v in campos.items():
            if k == "ultima_resumen" and v is not None:
                v = psycopg.types.json.Jsonb(v)
            sets.append(f"{k} = %s")
            vals.append(v)
        if latir:
            sets.append("latido = now()")
        if sets:
            self._q("UPDATE catalogado_estado SET " + ", ".join(sets), vals)

    def latir(self):
        self._q("UPDATE catalogado_estado SET latido = now()")

    def normalize_tag(self, name: str) -> str:
        return " ".join(name.strip().lower().split())

    def _tag_por_nombre(self, norm: str):
        """La etiqueta con ese nombre, con o sin tildes (pedido del 24/11/2026:
        «predicacion» es la misma que «predicación»). Primero la exacta."""
        return self._q("SELECT id, kind, name FROM tag WHERE sin_acentos(name) = sin_acentos(%s) "
                       "ORDER BY name = %s DESC, id LIMIT 1", (norm, norm)).fetchone()

    def get_or_create_tag(self, name: str, kind: str = "descriptivo") -> int:
        norm = self.normalize_tag(name)
        row = self._tag_por_nombre(norm)
        if row is None:
            self._q("INSERT INTO tag (name, kind) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
                    (norm, kind))
            row = self._tag_por_nombre(norm)
        return row["id"]

    def all_tag_names(self) -> list[str]:
        return [r["name"] for r in self._q("SELECT name FROM tag ORDER BY name")]

    def nombres_por_tipo(self) -> dict[str, list[str]]:
        """{'comunes': [...], 'lugar': [...], 'evento': [...]}: para que cada
        campo ofrezca solo lo que acepta."""
        out: dict[str, list[str]] = {"comunes": [], LUGAR: [], EVENTO: []}
        for r in self._q("SELECT name, kind FROM tag ORDER BY name"):
            out[r["kind"] if r["kind"] in ESPECIALES else "comunes"].append(r["name"])
        return out

    def _tag_de_tipo(self, name: str, kind: str) -> int:
        """El id de la etiqueta `name`, creándola con ese tipo si no existe.
        NombreEnUso si existe con otro tipo (lugar/evento vs. el resto)."""
        norm = self.normalize_tag(name)
        row = self._tag_por_nombre(norm)
        if row is None:
            self._q("INSERT INTO tag (name, kind) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
                    (norm, kind))
            row = self._tag_por_nombre(norm)
        especial = kind in ESPECIALES
        if (row["kind"] != kind) if especial else (row["kind"] in ESPECIALES):
            raise NombreEnUso(row["name"], row["kind"], kind)
        return row["id"]

    def poner_lugar(self, media_ids: list[int], name: str):
        """El lugar de esos medios: reemplaza al que tuvieran."""
        tag_id = self._tag_de_tipo(name, LUGAR)
        ids = list(media_ids)
        with self.conn.transaction():
            self._q("DELETE FROM media_tag mt USING tag t WHERE t.id = mt.tag_id "
                    "AND t.kind = %s AND mt.media_id = ANY(%s) AND t.id <> %s",
                    (LUGAR, ids, tag_id))
            self._q("INSERT INTO media_tag (media_id, tag_id, source, confirmed) "
                    "SELECT unnest(%s::bigint[]), %s, 'humano', true "
                    "ON CONFLICT (media_id, tag_id) DO UPDATE SET source = 'humano', "
                    "confirmed = true", (ids, tag_id))
            self._actualizar_busqueda(ids)

    def poner_evento(self, media_ids: list[int], name: str):
        """Suma un evento a esos medios (pueden tener varios)."""
        tag_id = self._tag_de_tipo(name, EVENTO)
        ids = list(media_ids)
        with self.conn.transaction():
            self._q("INSERT INTO media_tag (media_id, tag_id, source, confirmed) "
                    "SELECT unnest(%s::bigint[]), %s, 'humano', true "
                    "ON CONFLICT (media_id, tag_id) DO UPDATE SET source = 'humano', "
                    "confirmed = true", (ids, tag_id))
            self._actualizar_busqueda(ids)

    def quitar_de(self, media_ids: list[int], name: str):
        """Saca un lugar o un evento de esos medios (sin anotarlo como rechazado:
        eso es para lo que propone la IA)."""
        ids = list(media_ids)
        with self.conn.transaction():
            row = self._tag_por_nombre(self.normalize_tag(name))
            if row is not None:
                self._q("DELETE FROM media_tag WHERE tag_id = %s AND media_id = ANY(%s)",
                        (row["id"], ids))
            self._actualizar_busqueda(ids)

    def especiales_de(self, media_ids: list[int]) -> dict[str, list[tuple[str, int]]]:
        """{'lugar': [(nombre, en cuántos)], 'evento': [...]} de una selección."""
        out: dict[str, list[tuple[str, int]]] = {LUGAR: [], EVENTO: []}
        if media_ids:
            for r in self._q(
                    "SELECT t.name, t.kind, COUNT(*) AS n FROM tag t "
                    "JOIN media_tag mt ON mt.tag_id = t.id "
                    "WHERE mt.media_id = ANY(%s) AND t.kind IN ('lugar', 'evento') "
                    "GROUP BY t.id ORDER BY n DESC, t.name", (list(media_ids),)):
                out[r["kind"]].append((r["name"], r["n"]))
        return out

    def cambiar_tipo(self, name: str, kind: str):
        """Una etiqueta común pasa a ser lugar o evento, o al revés, sin perder
        a qué fotos y videos está puesta. NoSePuedeConvertir si no corresponde."""
        norm = self.normalize_tag(name)
        destino = kind if kind in ESPECIALES else "descriptivo"
        with self.conn.transaction():
            row = self._tag_por_nombre(norm)
            if row is not None:
                norm = row["name"]
                self._q("SELECT 1 FROM tag WHERE id = %s FOR UPDATE", (row["id"],))
            if row is None:
                raise NoSePuedeConvertir(f"No existe la etiqueta «{norm}».")
            if row["kind"] not in _CONVERTIBLES:
                raise NoSePuedeConvertir(
                    f"«{norm}» es {_NOMBRE_TIPO.get(row['kind'], 'otra cosa')}: no se "
                    "puede convertir en lugar ni en evento.")
            if row["kind"] == destino:
                return
            if destino == LUGAR:
                dobles = self._q(
                    "SELECT count(DISTINCT mt.media_id) AS n FROM media_tag mt "
                    "JOIN media_tag otro ON otro.media_id = mt.media_id "
                    "JOIN tag t ON t.id = otro.tag_id AND t.kind = %s "
                    "WHERE mt.tag_id = %s", (LUGAR, row["id"])).fetchone()["n"]
                if dobles:
                    raise NoSePuedeConvertir(
                        f"No se puede: {dobles} de los elementos con «{norm}» ya tienen "
                        "un lugar, y cada uno puede tener uno solo. Quíteles ese lugar "
                        "primero.")
            self._q("UPDATE tag SET kind = %s WHERE id = %s", (destino, row["id"]))
            # Cambiar el tipo no toca `media`: se avisa a las PCs a mano
            self._q("SELECT pg_notify(%s, 'tag')", (CANAL_CAMBIOS,))

    def apply_tag(self, media_id: int, name: str, source: str = "humano",
                  confirmed: bool = True):
        """Pone (o confirma) un tag. Aplicarlo a mano sobre uno sugerido por la
        IA lo pasa a humano/confirmado y borra un rechazo previo. Un lugar o un
        evento no se pone por acá (NombreEnUso): tienen sus propios campos."""
        tag_id = self._tag_de_tipo(name, "descriptivo")
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
        tipo = self._q("SELECT kind FROM tag WHERE id = %s", (tag_id,)).fetchone()["kind"]
        if tipo in ESPECIALES:
            return False                 # un lugar o un evento lo pone una persona
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
        row = self._tag_por_nombre(self.normalize_tag(name))
        if not row:
            return
        with self.conn.transaction():
            self._q("DELETE FROM media_tag WHERE media_id = %s AND tag_id = %s",
                    (media_id, row["id"]))
            self._q("INSERT INTO tag_rejection (media_id, tag_id) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING", (media_id, row["id"]))
            self._actualizar_busqueda([media_id])

    def hermanas(self, ids: list[int], name: str, sin_lugar: bool = False) -> list[int]:
        """Fotos y videos «hermanos» de los elegidos, para ofrecerles la misma
        etiqueta (decisión del 22/11/2026): la misma carpeta y, si el elegido
        tiene fecha de toma, el mismo día (en la hora de la iglesia). No incluye
        los elegidos, los que faltan, los que ya la tienen ni aquellos a los que
        alguien se la quitó. Con `sin_lugar`, tampoco los que ya tienen un lugar
        (ponerles otro lo reemplazaría)."""
        if not ids:
            return []
        norm = self.normalize_tag(name)
        return [r["id"] for r in self._q(
            "SELECT DISTINCT h.id FROM media e JOIN media h "
            "  ON h.folder_origin IS NOT DISTINCT FROM e.folder_origin "
            " AND (e.tomada IS NULL OR (h.tomada AT TIME ZONE %s)::date "
            "                          = (e.tomada AT TIME ZONE %s)::date) "
            "WHERE e.id = ANY(%s) AND NOT h.id = ANY(%s) AND h.missing_since IS NULL "
            "  AND NOT EXISTS (SELECT 1 FROM media_tag mt JOIN tag t ON t.id = mt.tag_id "
            "                  WHERE mt.media_id = h.id AND t.name = %s) "
            "  AND NOT EXISTS (SELECT 1 FROM tag_rejection tr JOIN tag t ON t.id = tr.tag_id "
            "                  WHERE tr.media_id = h.id AND t.name = %s) "
            + ("AND NOT EXISTS (SELECT 1 FROM media_tag mt JOIN tag t ON t.id = mt.tag_id "
               "                WHERE mt.media_id = h.id AND t.kind = 'lugar') "
               if sin_lugar else "")
            + "ORDER BY h.id",
            (ZONA_IGLESIA, ZONA_IGLESIA, list(ids), list(ids), norm, norm))]

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
            "WHERE mt.media_id = ANY(%s) AND NOT mt.confirmed "
            "AND t.kind NOT IN ('lugar', 'evento') GROUP BY t.id",
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
            "WHERE mt.media_id = ANY(%s) AND t.kind NOT IN ('lugar', 'evento') "
            "GROUP BY t.id ORDER BY n DESC, t.name",
            (list(media_ids),))
        return [(r["name"], r["n"]) for r in rows]

    # ------------------------------------------------------------- personas
    def add_person(self, name: str) -> int:
        name = " ".join(name.split())
        self._q("INSERT INTO person (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (name,))
        return self._q("SELECT id FROM person WHERE name = %s", (name,)).fetchone()["id"]

    def list_persons(self) -> list[dict]:
        """Cada persona con sus fotos de referencia según cómo las dejó el NAS:
        {id, name, listas, pendientes, sin_cara: [nombres de archivo], sello}. `sello`
        cambia si cambia algo de sus fotos (p. ej. el NAS terminó de revisarlas)."""
        rows = self._q(
            "SELECT p.id, p.name,"
            " COUNT(r.id) FILTER (WHERE r.estado = 'lista') AS listas,"
            " COUNT(r.id) FILTER (WHERE r.estado = 'pendiente') AS pendientes,"
            " COALESCE(array_agg(r.source_path ORDER BY r.id)"
            "   FILTER (WHERE r.estado IN ('sin_cara', 'ilegible')), '{}') AS sin_cara,"
            " md5(COALESCE(string_agg(r.id || ':' || r.estado || ':' || COALESCE(r.caras::text, '-'),"
            "   ',' ORDER BY r.id), '')) AS sello "
            "FROM person p LEFT JOIN person_reference r ON r.person_id = p.id "
            "GROUP BY p.id ORDER BY p.name")
        return [{"id": r["id"], "name": r["name"], "listas": r["listas"],
                 "pendientes": r["pendientes"], "sello": r["sello"],
                 "sin_cara": [os.path.basename((p or "").replace("\\", "/")) for p in r["sin_cara"]]}
                for r in rows]

    def delete_person(self, person_id: int):
        """Borra la persona y sus firmas. Los tags ya puestos quedan."""
        self._q("DELETE FROM person WHERE id = %s", (person_id,))

    def add_person_reference(self, person_id: int, source_path: str, embedding: bytes):
        """Una firma ya calculada (la usan las pruebas y el NAS)."""
        self._q("INSERT INTO person_reference (person_id, source_path, embedding) "
                "VALUES (%s, %s, %s)", (person_id, source_path, embedding))

    def subir_fotos_de_referencia(self, nombre: str, fotos: list[tuple[str, bytes]],
                                  person_id: int | None = None) -> int:
        """La PC sube fotos (ruta de origen, JPEG achicado) de una persona; el NAS
        calcula las firmas. Crea la persona si es nueva y despierta al
        catalogador. Devuelve el id de la persona."""
        with self.conn.transaction():
            pid = person_id or self.add_person(nombre)
            for ruta, jpeg in fotos:
                self._q("INSERT INTO person_reference (person_id, source_path, foto, estado) "
                        "VALUES (%s, %s, %s, 'pendiente')", (pid, ruta, jpeg))
            self._q("NOTIFY catalogado")
        return pid

    def referencias_pendientes(self) -> list[tuple[int, bytes]]:
        """Fotos subidas por una PC que esperan su firma (las que vienen del
        catálogo sin foto todavía: `referencias_del_catalogo`)."""
        return [(r["id"], bytes(r["foto"])) for r in self._q(
            "SELECT id, foto FROM person_reference WHERE estado = 'pendiente' "
            "AND foto IS NOT NULL ORDER BY id")]

    def referencias_del_catalogo(self) -> list[tuple[int, str]]:
        """Fotos de referencia elegidas en Buscar que el NAS todavía no leyó:
        (id, ruta completa del archivo en este equipo). Si el medio ya no está
        en el catálogo, la ruta es la anotada al pedirla."""
        return [(r["id"], rutas.a_completa(r["rel_path"] or r["source_path"], self.raiz))
                for r in self._q(
                    "SELECT r.id, r.source_path, m.rel_path FROM person_reference r "
                    "LEFT JOIN media m ON m.id = r.media_id "
                    "WHERE r.estado = 'pendiente' AND r.foto IS NULL ORDER BY r.id")]

    def referencias_desde_el_catalogo(self, nombre: str, media_ids: list[int],
                                      person_id: int | None = None) -> int:
        """Fotos del catálogo como referencia de una persona: la PC manda solo
        cuáles son y el NAS lee los archivos (no viajan por la red). Los videos
        no se usan. Crea la persona si es nueva. Devuelve el id de la persona."""
        with self.conn.transaction():
            pid = person_id or self.add_person(nombre)
            self._q("INSERT INTO person_reference (person_id, source_path, media_id, estado) "
                    "SELECT %s, m.rel_path, m.id, 'pendiente' FROM media m "
                    "WHERE m.id = ANY(%s) AND m.kind = 'image' ORDER BY m.id",
                    (pid, list(media_ids)))
            self._q("NOTIFY catalogado")
        return pid

    def completar_foto_de_referencia(self, ref_id: int, jpeg: bytes | None):
        """El NAS guarda la foto achicada que leyó (o la marca ilegible)."""
        if jpeg is None:
            self._q("UPDATE person_reference SET estado = 'ilegible' WHERE id = %s", (ref_id,))
        else:
            self._q("UPDATE person_reference SET foto = %s WHERE id = %s", (jpeg, ref_id))

    def referencias_sin_revisar(self) -> list[tuple[int, bytes]]:
        """Fotos ya procesadas antes de que el NAS anotara qué vio (esquema v6)."""
        return [(r["id"], bytes(r["foto"])) for r in self._q(
            "SELECT id, foto FROM person_reference "
            "WHERE caras IS NULL AND foto IS NOT NULL AND estado <> 'pendiente' ORDER BY id")]

    def hay_referencias_pendientes(self) -> bool:
        return self._q("SELECT EXISTS (SELECT 1 FROM person_reference WHERE estado = 'pendiente'"
                       " OR (caras IS NULL AND foto IS NOT NULL)) AS hay").fetchone()["hay"]

    def guardar_referencia(self, ref_id: int, embedding: bytes | None, estado: str,
                           caras: int = 0, caja=None, confianza: float | None = None):
        self._q("UPDATE person_reference SET embedding = %s, estado = %s, caras = %s, "
                "cara_caja = %s, confianza = %s WHERE id = %s",
                (embedding, estado, caras, list(caja) if caja else None, confianza, ref_id))

    def guardar_revision(self, ref_id: int, caras: int, caja=None,
                         confianza: float | None = None):
        """Solo lo que vio el NAS, sin tocar la firma ni el estado: cambiarlos
        haría repasar las caras de todo el archivo."""
        self._q("UPDATE person_reference SET caras = %s, cara_caja = %s, confianza = %s "
                "WHERE id = %s", (caras, list(caja) if caja else None, confianza, ref_id))

    def detalle_persona(self, person_id: int, fotos: bool = True) -> dict | None:
        """Todo lo de una persona para el detalle de Catalogar: sus fotos de
        referencia (con la foto, la firma y lo que vio el NAS) y cuántos medios
        del catálogo tienen su nombre. None si ya no existe.

        `fotos=False` no trae las fotos (unos 270 KB cada una: por Netbird, 9
        fotos tardan más de 2 s); `tiene_foto` dice si hay, para pedirlas después
        con `fotos_de_referencia`, y `sello_foto` (md5) identifica la foto para la
        caché local, aunque el mismo id exista en otra base."""
        p = self._q("SELECT id, name FROM person WHERE id = %s", (person_id,)).fetchone()
        if p is None:
            return None
        col = "foto" if fotos else "NULL::bytea AS foto"
        refs = [{"id": r["id"], "archivo": os.path.basename((r["source_path"] or "").replace("\\", "/")),
                 "ruta": r["source_path"] or "", "estado": r["estado"],
                 "foto": bytes(r["foto"]) if r["foto"] is not None else None,
                 "tiene_foto": r["sello_foto"] is not None, "sello_foto": r["sello_foto"],
                 "firma": bytes(r["embedding"]) if r["embedding"] is not None else None,
                 "caras": r["caras"], "caja": tuple(r["cara_caja"]) if r["cara_caja"] else None,
                 "confianza": r["confianza"]}
                for r in self._q(
                    f"SELECT id, source_path, estado, {col}, md5(foto) AS sello_foto, "
                    "embedding, caras, cara_caja, confianza "
                    "FROM person_reference WHERE person_id = %s ORDER BY id", (person_id,))]
        n = self._q(
            "SELECT COUNT(*) FILTER (WHERE mt.confirmed) AS confirmadas,"
            " COUNT(*) FILTER (WHERE NOT mt.confirmed) AS sugeridas "
            "FROM media_tag mt JOIN tag t ON t.id = mt.tag_id WHERE t.name = %s",
            (self.normalize_tag(p["name"]),)).fetchone()
        return {"id": p["id"], "name": p["name"], "referencias": refs,
                "confirmadas": n["confirmadas"], "sugeridas": n["sugeridas"]}

    def fotos_de_referencia(self, ids: list[int]) -> dict[int, bytes]:
        """{id: JPEG} de las fotos de referencia pedidas que tienen foto."""
        if not ids:
            return {}
        return {r["id"]: bytes(r["foto"]) for r in self._q(
            "SELECT id, foto FROM person_reference WHERE id = ANY(%s) AND foto IS NOT NULL",
            (list(ids),))}

    def quitar_referencia(self, ref_id: int):
        """Saca una foto de referencia. Las etiquetas que ya se pusieron quedan."""
        self._q("DELETE FROM person_reference WHERE id = %s", (ref_id,))

    def person_reference_blobs(self) -> dict[str, list[bytes]]:
        out: dict[str, list[bytes]] = {}
        for r in self._q("SELECT p.name, r.embedding FROM person_reference r "
                         "JOIN person p ON p.id = r.person_id "
                         "WHERE r.estado = 'lista' ORDER BY r.id"):
            out.setdefault(r["name"], []).append(bytes(r["embedding"]))
        return out

    def people_signature(self) -> str:
        """Cambia cada vez que se agrega o quita una referencia o una persona."""
        rows = self._q("SELECT r.id, p.name FROM person_reference r "
                       "JOIN person p ON p.id = r.person_id "
                       "WHERE r.estado = 'lista' ORDER BY r.id").fetchall()
        if not rows:
            return ""
        raw = ";".join(f"{r['id']}:{r['name']}" for r in rows)
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    # ------------------------------------------------------------- IA
    def media_for_ai(self, model: str, people_sig: str) -> list[tuple[Media, bool]]:
        """Medios que hay que analizar: (medio, hace_falta_escena). Ver
        `pendientes_de_ia`, que es lo que usa la pasada."""
        return [(p.media, p.escena) for p in self.pendientes_de_ia(model, people_sig)]

    def pendientes_de_ia(self, model: str, people_sig: str, carpetas: list[str] | None = None,
                         escenas: bool = True, personas: bool = True,
                         rehacer: bool = False) -> list["PendienteIA"]:
        """Qué hay que analizar y qué parte de cada uno.

        Falta la escena si nunca se analizó o cambió el modelo/vocabulario; faltan
        las caras si cambió la lista de personas. `carpetas` (relativas) limita a
        esas carpetas: pedir una sola no repasa todo el archivo. `escenas` /
        `personas`: qué partes se pidieron; `rehacer`: también lo ya hecho. Los
        que faltan de su ruta esperan a reaparecer."""
        if not (escenas or personas):
            return []
        where = ["m.quality_flag NOT IN ('corrupt', 'en_negro')", "m.missing_since IS NULL"]
        params: list = []
        if carpetas is not None:
            if not carpetas:
                return []
            cond, p = _en_carpetas(carpetas)
            where.append(cond)
            params += p
        if not rehacer:
            falta = []
            if escenas:
                falta.append("a.media_id IS NULL OR a.model IS DISTINCT FROM %s")
                params.append(model)
            if personas:
                falta.append("a.media_id IS NULL OR COALESCE(a.people_sig, '') <> %s")
                params.append(people_sig)
            where.append("(" + " OR ".join(falta) + ")")
        rows = self._q(
            "SELECT m.*, a.model AS ai_model, a.people_sig AS ai_sig, a.media_id AS ai_id "
            "FROM media m LEFT JOIN ai_result a ON a.media_id = m.id "
            "WHERE " + " AND ".join(where) + " ORDER BY m.id", params).fetchall()
        out = []
        for r in rows:
            nuevo = r["ai_id"] is None
            esc = escenas and (rehacer or nuevo or r["ai_model"] != model)
            car = personas and (rehacer or nuevo or (r["ai_sig"] or "") != people_sig)
            if esc or car:
                out.append(PendienteIA(self._media(r), esc, car,
                                       r["ai_model"], r["ai_sig"] or ""))
        return out

    # ------------------------------------------------------- texto en las fotos
    def fotos_sin_texto(self, version: int, carpetas: list[str] | None = None,
                        rehacer: bool = False) -> list[Media]:
        """Fotos que falta leer (o que se leyeron con otra versión del OCR).
        Solo fotos que están: los videos no se leen (decisión del 23/11/2026)."""
        where = ["m.kind = 'image'", "m.missing_since IS NULL"]
        params: list = []
        if carpetas:
            cond, p = _en_carpetas(carpetas)
            where.append(cond)
            params += p
        # Lo que corrigió una persona no se vuelve a leer nunca
        where.append("NOT COALESCE(f.editado, false)")
        if not rehacer:
            where.append("(f.media_id IS NULL OR f.version <> %s)")
            params.append(version)
        rows = self._q(
            "SELECT m.* FROM media m LEFT JOIN texto_foto f ON f.media_id = m.id "
            "WHERE " + " AND ".join(where) + " ORDER BY m.id", params).fetchall()
        return [self._media(r) for r in rows]

    def guardar_texto(self, media_id: int, texto: str, version: int) -> None:
        """Guarda lo leído y rehace el índice de búsqueda de esa foto (solo si
        el texto cambió: así no se avisa a las PCs por nada). Si una persona ya
        corrigió el texto de esa foto, no se toca."""
        with self.conn.transaction():
            previo = self._q("SELECT texto, editado FROM texto_foto WHERE media_id = %s",
                             (media_id,)).fetchone()
            if previo is not None and previo["editado"]:
                return
            self._q("INSERT INTO texto_foto (media_id, texto, version) VALUES (%s, %s, %s) "
                    "ON CONFLICT (media_id) DO UPDATE SET texto = EXCLUDED.texto, "
                    "version = EXCLUDED.version, leida = now() "
                    "WHERE NOT texto_foto.editado",
                    (media_id, texto, version))
            if previo is None or previo["texto"] != texto:
                if texto or previo is not None:
                    self._q(_SQL_BUSQUEDA, ([media_id],))

    def corregir_texto(self, media_id: int, texto: str, quien: str | None = None) -> None:
        """El texto de la foto escrito o corregido a mano (desde los detalles de
        Buscar). Queda marcado: el NAS ya no lo vuelve a leer ni lo pisa."""
        texto = texto.strip()
        with self.conn.transaction():
            self._q("INSERT INTO texto_foto (media_id, texto, version, editado, editado_en, "
                    "editado_por) VALUES (%s, %s, 0, true, now(), %s) "
                    "ON CONFLICT (media_id) DO UPDATE SET texto = EXCLUDED.texto, "
                    "editado = true, editado_en = now(), editado_por = EXCLUDED.editado_por",
                    (media_id, texto, quien))
            self._q(_SQL_BUSQUEDA, ([media_id],))

    def guardar_notas(self, media_id: int, notas: str, quien: str | None = None) -> None:
        """Las notas de un medio (foto o video). Se buscan como las etiquetas."""
        notas = notas.strip()
        with self.conn.transaction():
            self._q("UPDATE media SET notas = %s, notas_en = now(), notas_por = %s "
                    "WHERE id = %s AND notas IS DISTINCT FROM %s",
                    (notas, quien, media_id, notas))
            self._q(_SQL_BUSQUEDA, ([media_id],))

    def notas_de(self, media_id: int) -> str:
        row = self._q("SELECT notas FROM media WHERE id = %s", (media_id,)).fetchone()
        return row["notas"] if row else ""

    def texto_de(self, media_id: int) -> str | None:
        row = self._q("SELECT texto FROM texto_foto WHERE media_id = %s",
                      (media_id,)).fetchone()
        return row["texto"] if row else None

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
    def buscar(self, query: str, *, kind: str | None = None, pending_only: bool = False,
               desde: int = 0, cuantos: int = 120,
               orden: str = "relevancia") -> tuple[list[Media], int | None]:
        """Una página de la búsqueda de Buscar, y cuántos hay en total.

        Mismas reglas que `_search` (palabras primero, por relevancia; después
        los fragmentos; los que faltan de su ruta al final), pero en UNA consulta
        con el orden resuelto en la base, así cada página sigue a la anterior sin
        repetir ni saltear. `kind` ('image'/'video') filtra en la base. No trae
        `search_doc` (el índice de texto: ~40 % de cada fila) y sí las etiquetas
        de cada uno, para la grilla. El total es None
        si se pidió una página después del final.

        `orden`: "relevancia" (lo de arriba), "nuevas" o "viejas" (por fecha de
        toma; los que no la tienen, al final, por nombre).

        `query` admite filtros como los de Gmail (`etiqueta:placa`, `tipo:video`,
        `desde:09/2025`, `-etiqueta:retiro`…): ver `consulta.py`. La relevancia
        se calcula solo con las palabras sueltas.
        """
        where, params = [], []
        c = consulta.analizar(query)
        kind = c.tipo or kind               # «tipo:» escrito manda sobre los botones
        terms = [consulta.terminos(t) for t in c.palabras]
        tsq = _to_tsquery(" ".join(terms)) if terms else None
        if terms:
            conds = []
            if tsq:
                conds.append("m.search_doc @@ to_tsquery('catalogo', %s)")
                params.append(tsq)
            conds.append("(" + " AND ".join(
                "texto_nombre(m.filename, m.folder_origin) LIKE sin_acentos(lower(%s))"
                for _ in terms) + ")")
            params += [f"%{_escape_like(t)}%" for t in terms]
            where.append("(" + " OR ".join(conds) + ")")
        for palabra in c.excluidas:
            t = consulta.terminos(palabra)
            q = _to_tsquery(t)
            cond = "texto_nombre(m.filename, m.folder_origin) LIKE sin_acentos(lower(%s))"
            p = [f"%{_escape_like(t)}%"]
            if q:
                cond = f"m.search_doc @@ to_tsquery('catalogo', %s) OR {cond}"
                p.insert(0, q)
            where.append(f"NOT COALESCE(({cond}), false)")
            params += p
        for f in c.filtros:
            sql, p = _filtro_sql(f)
            if sql:
                where.append(f"NOT COALESCE(({sql}), false)" if f.negado else f"({sql})")
                params += p
        if kind:
            where.append("m.kind = %s")
            params.append(kind)
        if pending_only:
            where.append("EXISTS (SELECT 1 FROM media_tag mt "
                         "WHERE mt.media_id = m.id AND NOT mt.confirmed)")
        criterio = orden
        orden = "m.missing_since IS NOT NULL"
        if criterio in ("nuevas", "viejas"):
            orden += ", m.tomada " + ("DESC" if criterio == "nuevas" else "ASC") + " NULLS LAST"
        elif tsq:
            orden += (", (m.search_doc @@ to_tsquery('catalogo', %s)) DESC, "
                      "ts_rank(m.search_doc, to_tsquery('catalogo', %s)) DESC")
            params += [tsq, tsq]
        orden += ", m.filename, m.id"
        rows = self._q(
            f"SELECT {_COLUMNAS_MEDIA}, {_ETIQUETAS_MEDIA}, {_TEXTO_MEDIA}, "
            "count(*) OVER () AS total FROM media m "
            + ("WHERE " + " AND ".join(where) + " " if where else "")
            + f"ORDER BY {orden} LIMIT %s OFFSET %s",
            params + [cuantos, desde]).fetchall()
        total = rows[0]["total"] if rows else (0 if desde == 0 else None)
        return [self._media(r) for r in rows], total

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


# Todas las columnas de `media` que lee _row_to_media (sin search_doc).
_COLUMNAS_MEDIA = ", ".join("m." + c for c in (
    "id", "rel_path", "filename", "folder_origin", "kind", "width", "height", "aspect",
    "duration", "codec", "size_bytes", "file_mtime", "sha256", "phash", "status",
    "quality_flag", "missing_since", "tomada", "lat", "lon", "notas"))
# Las etiquetas de cada medio, para la grilla: [[nombre, confirmada, tipo], ...]
# (_row_to_media separa el lugar y los eventos de las demás)
_ETIQUETAS_MEDIA = (
    "(SELECT COALESCE(json_agg(json_build_array(t.name, mt.confirmed, t.kind) "
    "ORDER BY mt.confirmed DESC, t.name), '[]') FROM media_tag mt "
    "JOIN tag t ON t.id = mt.tag_id WHERE mt.media_id = m.id) AS etiquetas")
# El texto leído en la foto, para mostrarlo en Buscar (NULL si no tiene)
_TEXTO_MEDIA = ("(SELECT NULLIF(f.texto, '') FROM texto_foto f "
                "WHERE f.media_id = m.id) AS texto")


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
        etiquetas=([(e[0], e[1]) for e in row["etiquetas"]
                    if (e[2] if len(e) > 2 else None) not in ESPECIALES]
                   if "etiquetas" in row else None),
        lugar=next((e[0] for e in row.get("etiquetas") or [] if len(e) > 2 and e[2] == LUGAR),
                   None),
        eventos=sorted(e[0] for e in row.get("etiquetas") or [] if len(e) > 2 and e[2] == EVENTO),
        tomada=row.get("tomada"),
        lat=row.get("lat"),
        lon=row.get("lon"),
        texto=row.get("texto"),
        notas=row.get("notas") or "",
    )


@dataclass
class PendienteIA:
    """Un medio que la IA tiene que mirar y qué parte (ver Repo.pendientes_de_ia).
    `modelo` / `firma`: con qué se analizó antes, para no marcar como hecha la
    parte que no se corrió."""
    media: Media
    escena: bool
    caras: bool
    modelo: str | None
    firma: str


def _filtro_sql(f: "consulta.Filtro") -> tuple[str | None, list]:
    """Un filtro de la búsqueda (`etiqueta:placa`, `desde:09/2025`…) como
    condición sobre `media m`. (None, []) si no filtra nada (p. ej. `tipo:`,
    que va por `kind`)."""
    contiene = lambda: [f"%{_escape_like(f.valor.lower())}%"]   # noqa: E731
    if f.clave == "tipo":                   # el pedido va por `kind`; aquí, el excluido
        return ("m.kind = %s", [consulta.TIPOS[consulta._simple(f.valor)]]) if f.negado \
            else (None, [])
    if f.clave == "etiqueta":
        return ("EXISTS (SELECT 1 FROM media_tag mt JOIN tag t ON t.id = mt.tag_id "
                "WHERE mt.media_id = m.id AND sin_acentos(t.name) = sin_acentos(%s))",
                [" ".join(f.valor.lower().split())])
    if f.clave in ESPECIALES:
        return ("EXISTS (SELECT 1 FROM media_tag mt JOIN tag t ON t.id = mt.tag_id "
                "WHERE mt.media_id = m.id AND t.kind = %s "
                "AND sin_acentos(t.name) = sin_acentos(%s))",
                [f.clave, " ".join(f.valor.lower().split())])
    if f.clave == "carpeta":
        return ("sin_acentos(lower(COALESCE(m.folder_origin, ''))) LIKE sin_acentos(%s)",
                contiene())
    if f.clave == "nombre":
        return "sin_acentos(lower(m.filename)) LIKE sin_acentos(%s)", contiene()
    if f.clave == "texto":
        return ("EXISTS (SELECT 1 FROM texto_foto tf WHERE tf.media_id = m.id "
                "AND sin_acentos(lower(tf.texto)) LIKE sin_acentos(%s))", contiene())
    if f.clave == "notas":
        return "sin_acentos(lower(m.notas)) LIKE sin_acentos(%s)", contiene()
    if f.clave in consulta.FECHAS:
        dia = f"(m.tomada AT TIME ZONE '{ZONA_IGLESIA}')::date"
        ini, fin = f.periodo
        if f.clave == "desde":
            return f"{dia} >= %s", [ini]
        if f.clave == "hasta":
            return f"{dia} <= %s", [fin]
        if f.clave == "antes":                  # sin incluir esa fecha (como Gmail)
            return f"{dia} < %s", [ini]
        if f.clave == "después":
            return f"{dia} > %s", [fin]
        return f"{dia} BETWEEN %s AND %s", [ini, fin]
    if f.clave in ("tiene", "sin"):
        que = consulta.TIENE[consulta._simple(f.valor)]
        sql = {"texto": "EXISTS (SELECT 1 FROM texto_foto tf WHERE tf.media_id = m.id "
                        "AND tf.texto <> '')",
               "notas": "m.notas <> ''",
               "etiquetas": "EXISTS (SELECT 1 FROM media_tag mt JOIN tag t ON t.id = mt.tag_id "
                            "WHERE mt.media_id = m.id AND t.kind NOT IN ('lugar', 'evento'))",
               "fecha": "m.tomada IS NOT NULL",
               "lugar": "EXISTS (SELECT 1 FROM media_tag mt JOIN tag t ON t.id = mt.tag_id "
                        "WHERE mt.media_id = m.id AND t.kind = 'lugar')",
               "evento": "EXISTS (SELECT 1 FROM media_tag mt JOIN tag t ON t.id = mt.tag_id "
                         "WHERE mt.media_id = m.id AND t.kind = 'evento')",
               "ubicacion": "m.lat IS NOT NULL"}[que]
        return (sql if f.clave == "tiene" else f"NOT ({sql})"), []
    return None, []


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


def _marca_que_corta(alcance: Alcance, carpeta: str) -> str | None:
    """La carpeta destildada más cercana hacia arriba (o la misma), o None."""
    c = carpeta
    while True:
        if alcance.marcas.get(c) is False:
            return c
        if not c:
            return None
        c = rutas.carpeta_de(c)
