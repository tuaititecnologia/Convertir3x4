"""Fixtures compartidos: el catálogo de prueba en PostgreSQL.

Corren contra un PostgreSQL de desarrollo (ver CLAUDE.md):
  docker run -d --name cdlm-catalogo-dev -p 127.0.0.1:55432:5432 \
    -e POSTGRES_USER=catalogo -e POSTGRES_PASSWORD=catalogo-dev \
    -e POSTGRES_DB=catalogo postgres:18.7-trixie
Otro servidor: CDLM_TEST_PG="host=... port=... user=... password=...".
Cada prueba usa una base nueva que se borra al terminar. Sin servidor, las
pruebas que la usan se saltean.
"""

import os
import uuid

import psycopg
import pytest

from convertir3x4.catalog import db as catdb
from convertir3x4.catalog.repo import Repo

_TEST_PG = os.environ.get(
    "CDLM_TEST_PG", "host=127.0.0.1 port=55432 user=catalogo password=catalogo-dev")


def _pg_admin():
    try:
        return psycopg.connect(_TEST_PG + " dbname=postgres connect_timeout=2",
                               autocommit=True)
    except psycopg.OperationalError as e:
        pytest.skip(f"Sin PostgreSQL de prueba ({e.__class__.__name__})")


@pytest.fixture
def pg_cfg():
    """Configuración de un catálogo vacío y descartable (raíz "/")."""
    params = psycopg.conninfo.conninfo_to_dict(_TEST_PG)
    nombre = f"prueba_{uuid.uuid4().hex[:12]}"
    admin = _pg_admin()
    admin.execute(f'CREATE DATABASE "{nombre}"')
    try:
        yield catdb.ConfigCatalogo(
            servidor=params["host"], puerto=int(params.get("port", 5432)),
            usuario=params["user"], clave=params.get("password", ""), base=nombre,
            raiz="/")
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)')
        admin.close()


@pytest.fixture
def pg_conn(pg_cfg):
    c = catdb.conectar(pg_cfg)
    yield c
    c.close()


@pytest.fixture
def repo(pg_conn):
    """Repo con raíz "/": cualquier ruta absoluta de la Mac o Linux entra."""
    return Repo(pg_conn, "/")


class _SinCatalogo:
    """Servicio del catálogo que nunca contesta: para probar Buscar llenando la
    grilla a mano, sin que un aviso de conexión la vacíe por detrás."""
    estado, texto = "ok", ""

    class _Senal:
        def connect(self, *_a):
            pass

    estado_cambiado = _Senal()

    def pedir(self, *_a, **_k):
        return 0


@pytest.fixture
def sin_catalogo():
    return _SinCatalogo()
