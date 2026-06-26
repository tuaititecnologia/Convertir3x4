"""Conexión, esquema y migraciones de la base de datos SQLite del catálogo.

La búsqueda usa FTS5 (módulo estándar de SQLite). `media_fts` es una tabla FTS5
autónoma (no external-content) cuyo rowid = media.id; el repo la mantiene
sincronizada al insertar/actualizar medios. Así evitamos triggers frágiles y
queda lista para sumar columnas de IA (description, ocr_text, synonyms) en la
Fase 5 sin re-armar el índice.
"""

import sqlite3

SCHEMA_VERSION = 1

# Extensiones de medios reconocidas por el inventario.
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif", ".tiff", ".tif"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS media (
    id            INTEGER PRIMARY KEY,
    path          TEXT UNIQUE NOT NULL,
    filename      TEXT NOT NULL,
    folder_origin TEXT,
    kind          TEXT NOT NULL,            -- image | video
    width         INTEGER,
    height        INTEGER,
    aspect        REAL,
    duration      REAL,                     -- video
    codec         TEXT,                     -- video
    size_bytes    INTEGER,
    file_mtime    INTEGER,                  -- fecha de archivo (NO decide borrado)
    sha256        TEXT,                     -- dedup exacto
    phash         TEXT,                     -- dedup casi-duplicado
    status        TEXT NOT NULL DEFAULT 'library',  -- inbox|library|quarantine|canonical|variant
    canonical_id  INTEGER REFERENCES media(id) ON DELETE SET NULL,
    quality_flag  TEXT DEFAULT 'ok',        -- ok|low_res|corrupt
    thumb_path    TEXT,
    created_at    INTEGER,
    updated_at    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_media_sha256 ON media(sha256);
CREATE INDEX IF NOT EXISTS idx_media_phash  ON media(phash);
CREATE INDEX IF NOT EXISTS idx_media_status ON media(status);

CREATE TABLE IF NOT EXISTS ai_result (
    id                INTEGER PRIMARY KEY,
    media_id          INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    model             TEXT,
    description        TEXT,
    synonyms          TEXT,                 -- términos alternativos buscables
    media_type        TEXT,                 -- render|fondo|logo|slide|foto-evento|...
    ocr_text          TEXT,
    mood              TEXT,
    color             TEXT,
    energy            TEXT,
    people            TEXT,
    scene             TEXT,
    flags             TEXT,                 -- json
    is_ephemeral_score REAL,
    raw_json          TEXT,
    created_at        INTEGER,
    UNIQUE(media_id)
);

CREATE TABLE IF NOT EXISTS tag (
    id         INTEGER PRIMARY KEY,
    name       TEXT UNIQUE NOT NULL,        -- normalizado (minúsculas/trim)
    kind       TEXT DEFAULT 'descriptivo',  -- propio|descriptivo|ia-sugerido
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS media_tag (
    media_id  INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    tag_id    INTEGER NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
    source    TEXT DEFAULT 'humano',        -- humano|ia
    confirmed INTEGER DEFAULT 1,            -- bool
    PRIMARY KEY (media_id, tag_id)
);

CREATE TABLE IF NOT EXISTS reference_set (
    id         INTEGER PRIMARY KEY,
    name       TEXT UNIQUE NOT NULL,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS reference_set_member (
    set_id   INTEGER NOT NULL REFERENCES reference_set(id) ON DELETE CASCADE,
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    PRIMARY KEY (set_id, media_id)
);

CREATE TABLE IF NOT EXISTS series (
    id         INTEGER PRIMARY KEY,
    name       TEXT UNIQUE NOT NULL,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS series_member (
    series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
    media_id  INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    seq       INTEGER,                      -- orden cronológico
    PRIMARY KEY (series_id, media_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS media_fts USING fts5 (
    filename,
    folder_origin,
    description,
    ocr_text,
    synonyms,
    tags,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS embedding (
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    vector   BLOB,
    model    TEXT,
    PRIMARY KEY (media_id)
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    """Abre (o crea) la base, aplica el esquema y devuelve la conexión.

    `db_path` puede ser ':memory:' para tests.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection):
    conn.executescript(_SCHEMA)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()
    else:
        _migrate(conn, row["version"])


def _migrate(conn: sqlite3.Connection, from_version: int):
    """Aplica migraciones incrementales. Hoy solo existe la v1."""
    if from_version == SCHEMA_VERSION:
        return
    # Migraciones futuras: if from_version < 2: ...; bump schema_version.
    conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
    conn.commit()
