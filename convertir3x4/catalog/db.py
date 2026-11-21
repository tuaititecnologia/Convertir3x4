"""Conexión y esquema del catálogo en PostgreSQL (el del NAS). Sin Qt.

Diseño: docs/catalogo-compartido.md. Lo esencial:

- Cada hilo abre su propia conexión con `conectar()` y la cierra al terminar.
- `conectar()` nunca espera más de unos segundos: si el NAS no responde, lanza
  `CatalogoNoDisponible` y la app sigue andando sin catálogo.
- El esquema se crea y se actualiza solo, con migraciones numeradas
  (`MIGRACIONES`). Dos PCs que arrancan a la vez no chocan: la migración toma un
  bloqueo de PostgreSQL. Una app más vieja que el catálogo no lo toca
  (`CatalogoIncompatible`).
- Las rutas se guardan relativas a la raíz del recurso compartido
  (`\\\\truenas\\multimedia`), con `/` como separador; cada PC arma la ruta
  completa con su `ConfigCatalogo.raiz`.

Las migraciones viven acá como texto, no como archivos .sql, para que el .exe
de PyInstaller no necesite datos extra.
"""

import configparser
import os
import re
from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row

# Extensiones de medios reconocidas por el inventario.
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif", ".tiff", ".tif"}
# .mpg/.mts/.m2t: grabaciones de cámara del archivo histórico (decidido el 21/11/2026).
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mpg", ".mpeg", ".mts", ".m2ts", ".m2t"}

TIEMPO_CONEXION = 3          # segundos; libpq no acepta menos de 2
_LLAVE_ESQUEMA = 0x43444C4D  # "CDLM": bloqueo para migrar de a una PC por vez
LLAVE_CATALOGAR = 0x43415441  # "CATA": junto con hashtext(carpeta), una pasada por carpeta


class CatalogoNoDisponible(Exception):
    """El servidor del catálogo no responde (NAS apagado, sin red, etc.)."""

    MENSAJE = "El catálogo no está disponible: el NAS no responde."

    def __init__(self, detalle: str = ""):
        super().__init__(self.MENSAJE)
        self.detalle = detalle      # para el registro, no para el operador


class CatalogoNoConfigurado(Exception):
    """Esta PC no tiene `catalogo.ini` (ni las variables de desarrollo)."""

    MENSAJE = ("Esta PC no tiene configurado el catálogo. Comuníquese con el "
               "responsable de la aplicación.")

    def __init__(self, detalle: str = ""):
        super().__init__(self.MENSAJE)
        self.detalle = detalle


class ConfiguracionInvalida(ValueError):
    """`catalogo.ini` tiene un dato que no sirve (el detalle va al registro)."""


class CatalogoIncompatible(Exception):
    """El catálogo fue actualizado por una versión más nueva de la app."""

    MENSAJE = ("Esta versión de la aplicación es anterior a la del catálogo. "
               "Instale la versión nueva para usarlo.")

    def __init__(self, version_base: int, version_app: int):
        super().__init__(self.MENSAJE)
        self.version_base = version_base
        self.version_app = version_app


@dataclass(frozen=True)
class ConfigCatalogo:
    servidor: str
    clave: str = ""
    usuario: str = "catalogo"
    base: str = "catalogo"
    puerto: int = 5432
    raiz: str = r"\\truenas\multimedia"
    # Carpetas que se catalogan, relativas a `raiz` y con "/" (p. ej. "Publicar").
    carpetas: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "carpetas", normalizar_carpetas(self.carpetas))

    @classmethod
    def desde_entorno(cls) -> "ConfigCatalogo | None":
        """Para desarrollo: CDLM_CATALOGO_SERVIDOR, _PUERTO, _BASE, _USUARIO,
        _CLAVE, _RAIZ y _CARPETAS (separadas por ";"). None si no hay servidor."""
        e = os.environ.get
        servidor = e("CDLM_CATALOGO_SERVIDOR")
        if not servidor:
            return None
        return cls(
            servidor=servidor,
            clave=e("CDLM_CATALOGO_CLAVE", ""),
            usuario=e("CDLM_CATALOGO_USUARIO", "catalogo"),
            base=e("CDLM_CATALOGO_BASE", "catalogo"),
            puerto=int(e("CDLM_CATALOGO_PUERTO", "5432")),
            raiz=e("CDLM_CATALOGO_RAIZ", r"\\truenas\multimedia"),
            carpetas=tuple(e("CDLM_CATALOGO_CARPETAS", "").split(";")),
        )

    @classmethod
    def desde_archivo(cls, ruta: str) -> "ConfigCatalogo | None":
        """Lee `catalogo.ini` (sección [catalogo]). None si el archivo no existe.

        Lanza `ConfiguracionInvalida` si falta el servidor o hay un dato malo.
        """
        if not os.path.isfile(ruta):
            return None
        ini = configparser.ConfigParser(interpolation=None)
        try:
            ini.read(ruta, encoding="utf-8")
        except configparser.Error as e:
            raise ConfiguracionInvalida(f"{ruta}: {e}") from e
        if not ini.has_section("catalogo"):
            raise ConfiguracionInvalida(f"{ruta}: falta la sección [catalogo]")
        sec = ini["catalogo"]
        if not sec.get("servidor", "").strip():
            raise ConfiguracionInvalida(f"{ruta}: falta 'servidor'")
        try:
            puerto = int(sec.get("puerto", "5432"))
        except ValueError as e:
            raise ConfiguracionInvalida(f"{ruta}: 'puerto' no es un número") from e
        return cls(
            servidor=sec["servidor"].strip(),
            clave=sec.get("clave", ""),
            usuario=sec.get("usuario", "catalogo").strip(),
            base=sec.get("base", "catalogo").strip(),
            puerto=puerto,
            raiz=sec.get("raiz", r"\\truenas\multimedia").strip(),
            carpetas=tuple(sec.get("carpetas", "").splitlines()),
        )


def normalizar_carpetas(carpetas) -> tuple[str, ...]:
    """Limpia la lista de carpetas a catalogar: relativas a la raíz, con "/",
    sin repetidas. Lanza `ConfiguracionInvalida` si una sale de la raíz o si
    una está dentro de otra (se recorrería dos veces)."""
    out: list[str] = []
    for c in carpetas:
        c = c.strip().replace("\\", "/").strip("/")
        if not c:
            continue
        partes = c.split("/")
        if re.match(r"^[A-Za-z]:", c) or any(p in ("", ".", "..") for p in partes):
            raise ConfiguracionInvalida(f"carpeta fuera de la raíz: {c!r}")
        if c not in out:
            out.append(c)
    for a in out:
        for b in out:
            if a != b and b.lower().startswith(a.lower() + "/"):
                raise ConfiguracionInvalida(f"{b!r} está dentro de {a!r}")
    return tuple(out)


def conectar(cfg: ConfigCatalogo, timeout: int = TIEMPO_CONEXION,
             preparar: bool = True) -> psycopg.Connection:
    """Abre una conexión (filas como dict) y deja el esquema al día.

    Lanza `CatalogoNoDisponible` si el servidor no responde en `timeout`
    segundos, y `CatalogoIncompatible` si el catálogo es de una app más nueva.
    """
    try:
        conn = psycopg.connect(
            host=cfg.servidor, port=cfg.puerto, dbname=cfg.base,
            user=cfg.usuario, password=cfg.clave,
            connect_timeout=max(2, int(timeout)),
            application_name="Convertir3x4",
            row_factory=dict_row,
            # Cada sentencia se guarda sola; lo que tiene que ir junto usa
            # `with conn.transaction()`. Sin esto, una lectura deja abierta una
            # transacción y las escrituras siguientes nunca se confirmarían.
            autocommit=True,
            # Si el NAS se cae con la conexión abierta, que se note en
            # segundos y no quede una consulta colgada para siempre.
            keepalives=1, keepalives_idle=10, keepalives_interval=5,
            keepalives_count=3,
            options="-c statement_timeout=60000 -c lock_timeout=10000",
        )
    except psycopg.OperationalError as e:
        raise CatalogoNoDisponible(str(e).strip()) from e
    if preparar:
        try:
            preparar_esquema(conn)
        except BaseException:
            conn.close()
            raise
    return conn


def version_esquema(conn: psycopg.Connection) -> int:
    """Versión del esquema en la base (0 si está vacía)."""
    existe = conn.execute("SELECT to_regclass('esquema') IS NOT NULL AS hay").fetchone()["hay"]
    if not existe:
        return 0
    return conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM esquema").fetchone()["v"]


def preparar_esquema(conn: psycopg.Connection):
    """Aplica las migraciones que falten, de a una PC por vez."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LLAVE_ESQUEMA,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS esquema ("
            " version integer PRIMARY KEY,"
            " aplicada timestamptz NOT NULL DEFAULT now())")
        actual = version_esquema(conn)
        if actual > VERSION_ESQUEMA:
            raise CatalogoIncompatible(actual, VERSION_ESQUEMA)
        for version, sql in MIGRACIONES:
            if version > actual:
                conn.execute(sql)
                conn.execute("INSERT INTO esquema (version) VALUES (%s)", (version,))


# ----------------------------------------------------------------- migraciones
_V1 = r"""
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- unaccent() no es IMMUTABLE y los índices lo exigen. Esta versión fija el
-- diccionario, así que sí lo es.
CREATE FUNCTION sin_acentos(text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
    RETURN public.unaccent('public.unaccent'::regdictionary, $1);

-- Texto donde se buscan fragmentos (ILIKE '%4782%'): nombre y carpeta, en
-- minúsculas y sin acentos. Índice pg_trgm sobre esta misma expresión.
CREATE FUNCTION texto_nombre(filename text, carpeta text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    RETURN sin_acentos(lower(COALESCE(filename, '') || ' ' || COALESCE(carpeta, '')));

-- Búsqueda por palabras: configuración `simple` (no recorta palabras a su
-- raíz, que en nombres de archivo confunde) más unaccent.
CREATE TEXT SEARCH CONFIGURATION catalogo (COPY = simple);
ALTER TEXT SEARCH CONFIGURATION catalogo
    ALTER MAPPING FOR hword, hword_part, word WITH unaccent, simple;

-- Separa "FB_IMG_2024-bautismo.jpg" en palabras antes de indexar: el parser
-- de PostgreSQL dejaría "bautismo.jpg" como un solo término.
CREATE FUNCTION palabras(text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    RETURN regexp_replace(COALESCE($1, ''), '[^[:alnum:]]+', ' ', 'g');

-- Un archivo del NAS.
CREATE TABLE media (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rel_path      text NOT NULL UNIQUE,     -- relativa a la raíz, separador '/'
    filename      text NOT NULL,
    folder_origin text,                     -- carpeta del evento, relativa
    kind          text NOT NULL CHECK (kind IN ('image', 'video')),
    width         integer,
    height        integer,
    aspect        real,
    duration      real,                     -- video, en segundos
    codec         text,                     -- video
    size_bytes    bigint,
    file_mtime    bigint,                   -- fecha del archivo (epoch)
    sha256        text,                     -- solo si otro archivo pesa lo mismo
    phash         text,                     -- dHash, imágenes
    status        text NOT NULL DEFAULT 'library',
    quality_flag  text NOT NULL DEFAULT 'ok' CHECK (quality_flag IN ('ok', 'low_res', 'corrupt')),
    -- NULL: el archivo está. Si no, desde cuándo no aparece en su ruta. Un
    -- medio que falta no se borra: si reaparece en otra ruta, se le cambia la
    -- ruta y conserva sus tags.
    missing_since timestamptz,
    search_doc    tsvector NOT NULL DEFAULT ''::tsvector,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX media_sha256 ON media (sha256) WHERE sha256 IS NOT NULL;
CREATE INDEX media_size   ON media (size_bytes);
CREATE INDEX media_folder ON media (folder_origin);
CREATE INDEX media_missing ON media (filename, size_bytes, file_mtime)
    WHERE missing_since IS NOT NULL;
CREATE INDEX media_search ON media USING gin (search_doc);
CREATE INDEX media_trgm   ON media USING gin (texto_nombre(filename, folder_origin) gin_trgm_ops);

-- Miniatura JPEG (unos 20 KB), en la base para que todas las PCs la vean.
-- Cada PC la guarda además en su caché local; `sello` (md5 del JPEG) dice si
-- la copia local sigue siendo la misma sin traer los bytes.
CREATE TABLE thumbnail (
    media_id bigint PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
    jpeg     bytea NOT NULL,
    sello    text NOT NULL
);

CREATE TABLE ai_result (
    media_id    bigint PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
    model       text,
    people_sig  text,                       -- lista de personas con que se analizó
    media_type  text,                       -- tag de escena elegido
    people      text,
    description text,
    ocr_text    text,
    synonyms    text,
    raw_json    text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tag (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       text NOT NULL UNIQUE,        -- normalizado (minúsculas, espacios simples)
    kind       text NOT NULL DEFAULT 'descriptivo',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE media_tag (
    media_id  bigint NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    tag_id    bigint NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
    source    text NOT NULL DEFAULT 'humano' CHECK (source IN ('humano', 'ia')),
    confirmed boolean NOT NULL DEFAULT true,
    PRIMARY KEY (media_id, tag_id)
);
CREATE INDEX media_tag_tag ON media_tag (tag_id);
CREATE INDEX media_tag_pendiente ON media_tag (media_id) WHERE NOT confirmed;

-- Tags que una persona sacó: la IA no los vuelve a proponer para ese medio.
CREATE TABLE tag_rejection (
    media_id bigint NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    tag_id   bigint NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
    PRIMARY KEY (media_id, tag_id)
);

-- Personas reconocibles: SOLO las cargadas a mano (pastores, invitados).
-- Se guardan las firmas de sus fotos de referencia; de nadie más.
CREATE TABLE person (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE person_reference (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id   bigint NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    source_path text,
    embedding   bytea NOT NULL,             -- float32 normalizado (SFace)
    created_at  timestamptz NOT NULL DEFAULT now()
);
"""

# El catalogado corre en el NAS (docs/catalogado-en-el-nas.md). Las PCs lo
# piden y lo miran a través de estas dos tablas.
_V2 = r"""
-- Un pedido de catalogar: «Catalogar ahora» (carpeta NULL = todas las de la
-- lista) o una subcarpeta desde el ⚙. El catalogador del NAS lo toma, lo marca
-- empezado y lo cierra con el resultado (o el error).
CREATE TABLE catalogado_pedido (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    carpeta    text,
    pedido_por text,
    creado     timestamptz NOT NULL DEFAULT now(),
    empezado   timestamptz,
    terminado  timestamptz,
    resumen    jsonb,
    error      text
);
CREATE INDEX catalogado_pedido_abierto ON catalogado_pedido (id) WHERE terminado IS NULL;

-- Una sola fila: qué está haciendo el catalogador del NAS. `latido` se
-- actualiza cada pocos segundos mientras el catalogador está vivo.
CREATE TABLE catalogado_estado (
    id             boolean PRIMARY KEY DEFAULT true CHECK (id),
    version_app    text,
    carpetas       text[] NOT NULL DEFAULT '{}',
    fase           text NOT NULL DEFAULT 'esperando',
    detalle        text,
    hechos         integer,
    total          integer,
    pedido_id      bigint,
    ultima_inicio  timestamptz,
    ultima_fin     timestamptz,
    ultima_resumen jsonb,
    ultima_error   text,
    ultima_programada date,
    latido         timestamptz
);
INSERT INTO catalogado_estado DEFAULT VALUES;
"""

MIGRACIONES: list[tuple[int, str]] = [
    (1, _V1),
    (2, _V2),
]
VERSION_ESQUEMA = MIGRACIONES[-1][0]
