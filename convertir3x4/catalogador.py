"""Catalogador del NAS: corre en un contenedor al lado del PostgreSQL del
catálogo y hace todo el catalogado (recorrido, faltantes, movidos, miniaturas,
duplicados) y el análisis con IA local. Sin Qt.

Diseño: docs/catalogado-en-el-nas.md. Lo esencial:

- Corre todas las noches (`CDLM_HORA`) y cuando una PC lo pide
  (`Repo.pedir_catalogado`, tabla `catalogado_pedido`). `NOTIFY catalogado` lo
  despierta al instante, pero lo que cuenta es la fila: un pedido hecho con el
  catalogador apagado se atiende al arrancar.
- Informa qué hace en `catalogado_estado`, con un latido cada pocos segundos
  desde un hilo con su propia conexión.
- No arranca una pasada mientras se copia un disco de la grabadora.
- Si el NAS (la base) se cae, se reconecta solo. Si el catálogo es de una
  versión más nueva, no lo toca y lo informa.

Uso: python -m convertir3x4.catalogador
"""

import datetime
import json
import logging
import os
import signal
import socket
import threading
import time
import dataclasses
from dataclasses import asdict, dataclass

import psycopg

from .catalog import rutas
from .catalog.db import (
    CatalogoIncompatible, CatalogoNoConfigurado, CatalogoNoDisponible,
    ConfigCatalogo, conectar, normalizar_carpetas,
)
from .catalog.repo import Repo
from .config import APP_VERSION

log = logging.getLogger("catalogador")

LATIDO_CADA = 15          # segundos entre latidos
ESPERA_PEDIDOS = 30       # segundos máximos esperando un NOTIFY
ESPERA_RECONEXION = 30    # segundos entre intentos si la base no responde
VENTANA_PROGRAMADA = 6    # horas después de CDLM_HORA en que todavía se corre la nocturna
VIGENCIA_COPIA = 600      # segundos: una copia sin novedades por más tiempo se da por muerta
FASES_COPIA = ("COPIANDO", "MONTANDO")


@dataclass(frozen=True)
class ConfigCatalogador:
    catalogo: ConfigCatalogo
    modelos: str = "/modelos"
    hora: datetime.time = datetime.time(1, 30)
    copias: str | None = None   # /mnt/tank/ingreso/_copias, montado en solo lectura
    hilos: int = 2
    ia: bool = True

    @classmethod
    def desde_entorno(cls) -> "ConfigCatalogador":
        """CDLM_CATALOGO_* (como en desarrollo) más CDLM_MODELOS, CDLM_HORA
        ("HH:MM"), CDLM_COPIAS, CDLM_HILOS y CDLM_IA ("0" la apaga).

        La clave puede venir de un archivo (CDLM_CATALOGO_CLAVE_ARCHIVO), para
        que no quede escrita en el compose de la App."""
        cat = ConfigCatalogo.desde_entorno()
        if cat is None:
            raise CatalogoNoConfigurado("falta CDLM_CATALOGO_SERVIDOR")
        e = os.environ.get
        archivo = e("CDLM_CATALOGO_CLAVE_ARCHIVO")
        if archivo:
            with open(archivo, encoding="utf-8") as f:
                cat = dataclasses.replace(cat, clave=f.read().strip())
        hh, _, mm = e("CDLM_HORA", "01:30").partition(":")
        return cls(
            catalogo=cat,
            modelos=e("CDLM_MODELOS", "/modelos"),
            hora=datetime.time(int(hh), int(mm or 0)),
            copias=e("CDLM_COPIAS") or None,
            hilos=int(e("CDLM_HILOS", "2")),
            ia=e("CDLM_IA", "1") != "0",
        )


# ---------------------------------------------------------------- reglas
def toca_programada(ahora: datetime.datetime, hora: datetime.time,
                    ultima: datetime.date | None) -> bool:
    """¿Corresponde la pasada nocturna? Desde `hora` y durante unas horas, una
    vez por día. Si el NAS estuvo apagado toda la ventana, queda para la
    noche siguiente: no se corre a media tarde."""
    inicio = datetime.datetime.combine(ahora.date(), hora)
    if ahora < inicio:
        inicio -= datetime.timedelta(days=1)       # ventana que empezó ayer
    if ahora >= inicio + datetime.timedelta(hours=VENTANA_PROGRAMADA):
        return False
    return ultima != inicio.date()


def copia_en_curso(directorio: str | None, ahora: float | None = None) -> str | None:
    """Disco de la grabadora que se está copiando (su carpeta), o None.

    Lee el `estado.json` que escribe `copiar-disco.py` por cada disco. Una
    copia que dejó de actualizarse hace rato se cortó (NAS reiniciado) y no
    cuenta."""
    if not directorio or not os.path.isdir(directorio):
        return None
    ahora = time.time() if ahora is None else ahora
    for nombre in sorted(os.listdir(directorio)):
        ruta = os.path.join(directorio, nombre, "estado.json")
        try:
            with open(ruta, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("fase") not in FASES_COPIA:
                continue
            if ahora - os.path.getmtime(ruta) <= VIGENCIA_COPIA:
                return nombre
        except (OSError, ValueError):
            continue
    return None


def carpeta_madre(rel: str, carpetas) -> str | None:
    """La carpeta de la lista que contiene `rel` (o None)."""
    return next((c for c in carpetas if rutas.dentro_de(rel, c)), None)


# ---------------------------------------------------------------- latido
class Latido(threading.Thread):
    """Actualiza `catalogado_estado.latido` con su propia conexión: sigue
    latiendo aunque un video tarde en procesarse."""

    def __init__(self, cfg: ConfigCatalogo):
        super().__init__(name="latido", daemon=True)
        self._cfg = cfg
        self._parar = threading.Event()

    def run(self):
        repo = None
        while not self._parar.is_set():
            try:
                if repo is None:
                    repo = Repo(conectar(self._cfg, preparar=False), self._cfg.raiz)
                repo.latir()
            except (psycopg.Error, CatalogoNoDisponible) as e:
                log.warning("latido: %s", e)
                if repo is not None:
                    try:
                        repo.close()
                    except psycopg.Error:
                        pass
                    repo = None
            self._parar.wait(LATIDO_CADA)
        if repo is not None:
            repo.close()

    def parar(self):
        self._parar.set()


# ---------------------------------------------------------------- una pasada
class Detenido(Exception):
    """Se pidió apagar el catalogador en medio de una pasada."""


class _Avance:
    """progress_cb que escribe en la fila de estado, como mucho cada 2 s, y
    corta la pasada si se está apagando el contenedor."""

    def __init__(self, repo: Repo, fase: str, parar: threading.Event | None = None):
        self.repo, self.fase, self._parar, self._ultimo = repo, fase, parar, 0.0

    def __call__(self, hechos, total, ruta=""):
        if self._parar is not None and self._parar.is_set():
            raise Detenido()
        ahora = time.monotonic()
        if ahora - self._ultimo < 2 and hechos != total:
            return
        self._ultimo = ahora
        detalle = self.repo.rel(ruta) if ruta and not str(ruta).startswith("⬇") else None
        self.repo.actualizar_estado(fase=self.fase, hechos=hechos, total=total,
                                    detalle=detalle)


def pasada(repo: Repo, cfg: ConfigCatalogador, carpeta: str | None = None,
           parar: threading.Event | None = None) -> dict:
    """Cataloga (todas las carpetas o una subcarpeta) y después analiza con IA.
    Devuelve el resumen que se guarda en la base."""
    from .engine.pipeline import catalogar
    carpetas = cfg.catalogo.carpetas
    if carpeta is None:
        lista, bloqueo = list(carpetas), None
    else:
        rel = carpeta.replace("\\", "/").strip("/")
        madre = carpeta_madre(rel, carpetas)
        if madre is None:
            raise ValueError(f"«{rel}» no está dentro de las carpetas que se catalogan")
        lista, bloqueo = [rel], madre
    stats = catalogar(repo, lista, progress_cb=_Avance(repo, "catalogando", parar),
                      bloqueo=bloqueo)
    resumen = {"catalogado": asdict(stats)}
    if cfg.ia:
        from .engine import ai_models
        from .engine.ai_pipeline import run_ai
        repo.actualizar_estado(fase="preparando_ia", hechos=None, total=None, detalle=None)
        ai_models.ensure(cfg.modelos)
        st = run_ai(repo, cfg.modelos, progress_cb=_Avance(repo, "analizando", parar),
                    threads=cfg.hilos)
        resumen["ia"] = asdict(st)
    return resumen


def atender(repo: Repo, cfg: ConfigCatalogador, pedido: dict | None,
            ahora: datetime.datetime | None = None,
            parar: threading.Event | None = None) -> dict | None:
    """Corre una pasada (la de `pedido`, o la nocturna si es None), la anota en
    el estado y cierra el pedido. Devuelve el resumen (None si falló)."""
    ahora = ahora or datetime.datetime.now()
    carpeta = pedido["carpeta"] if pedido else None
    quien = f"pedido {pedido['id']}" if pedido else "pasada nocturna"
    log.info("empieza %s (%s)", quien, carpeta or "todas las carpetas")
    repo.actualizar_estado(fase="catalogando", hechos=0, total=None, detalle=None,
                           pedido_id=pedido["id"] if pedido else None,
                           ultima_inicio=ahora.astimezone())   # con zona: la base es timestamptz
    resumen, error = None, None
    try:
        resumen = pasada(repo, cfg, carpeta, parar)
    except (CatalogoNoDisponible, psycopg.OperationalError, Detenido):
        # La base se cayó o se está apagando el contenedor: el pedido vuelve a
        # la fila al arrancar y la nocturna se repite (la pasada es idempotente).
        raise
    except Exception as e:  # noqa: BLE001 — cualquier otra falla se informa y se sigue
        log.exception("falló %s", quien)
        error = f"{e.__class__.__name__}: {e}"[:500]
    fin = dict(fase="esperando", hechos=None, total=None, detalle=None, pedido_id=None,
               ultima_fin=datetime.datetime.now().astimezone(), ultima_resumen=resumen, ultima_error=error)
    if pedido is None:
        # Al terminar, no al empezar: si se corta a mitad, se repite esa noche.
        fin["ultima_programada"] = ahora.date() if ahora.time() >= cfg.hora else \
            ahora.date() - datetime.timedelta(days=1)
    repo.actualizar_estado(**fin)
    if pedido:
        repo.cerrar_pedido(pedido["id"], resumen, error)
    log.info("terminó %s: %s", quien, error or json.dumps(resumen, ensure_ascii=False))
    return resumen


def ciclo(repo: Repo, cfg: ConfigCatalogador,
          ahora: datetime.datetime | None = None,
          parar: threading.Event | None = None) -> bool:
    """Una vuelta: atiende un pedido o la pasada nocturna, si corresponde.
    Devuelve True si hizo algo."""
    ahora = ahora or datetime.datetime.now()
    copia = copia_en_curso(cfg.copias)
    if copia:
        est = repo.estado_catalogado()
        hay_trabajo = bool(repo.pedidos_abiertos()) or toca_programada(
            ahora, cfg.hora, est["ultima_programada"])
        repo.actualizar_estado(fase="esperando_copia" if hay_trabajo else "esperando",
                               detalle=copia if hay_trabajo else None)
        return False
    pedido = repo.tomar_pedido()
    if pedido is None:
        est = repo.estado_catalogado()
        if not toca_programada(ahora, cfg.hora, est["ultima_programada"]):
            if est["fase"] != "esperando":
                repo.actualizar_estado(fase="esperando", detalle=None)
            return False
    atender(repo, cfg, pedido, ahora, parar)
    return True


# ---------------------------------------------------------------- servicio
def _esperar_aviso(conn: psycopg.Connection, segundos: float, parar: threading.Event):
    """Espera un NOTIFY catalogado (o `segundos`), de a 1 s para notar enseguida
    que se está apagando el contenedor."""
    fin = time.monotonic() + segundos
    while not parar.is_set() and time.monotonic() < fin:
        for _ in conn.notifies(timeout=1, stop_after=1):
            return


def _marcar_incompatible(cfg: ConfigCatalogo, e: CatalogoIncompatible):
    """El catálogo es de una versión más nueva: no se toca nada, salvo avisar."""
    try:
        with conectar(cfg, preparar=False) as conn:
            conn.execute(
                "UPDATE catalogado_estado SET fase = 'detenido', latido = now(),"
                " detalle = %s", (f"versión {APP_VERSION} (esquema {e.version_app}) es "
                                  f"anterior al catálogo (esquema {e.version_base})",))
    except (psycopg.Error, CatalogoNoDisponible):
        pass


def servir(cfg: ConfigCatalogador, parar: threading.Event | None = None):
    parar = parar or threading.Event()
    while not parar.is_set():
        try:
            conn = conectar(cfg.catalogo, timeout=10)
        except CatalogoNoDisponible as e:
            log.warning("la base no responde: %s", e.detalle)
            parar.wait(ESPERA_RECONEXION)
            continue
        except CatalogoIncompatible as e:
            log.error("catálogo más nuevo que esta versión (%s > %s): no se toca",
                      e.version_base, e.version_app)
            _marcar_incompatible(cfg.catalogo, e)
            parar.wait(300)
            continue
        latido = Latido(cfg.catalogo)
        try:
            repo = Repo(conn, cfg.catalogo.raiz)
            n = repo.soltar_pedidos_colgados()
            if n:
                log.info("%s pedido(s) cortados a mitad vuelven a la fila", n)
            repo.actualizar_estado(version_app=APP_VERSION, carpetas=list(cfg.catalogo.carpetas),
                                   fase="esperando", detalle=None, hechos=None, total=None,
                                   pedido_id=None)
            conn.execute("LISTEN catalogado")
            latido.start()
            log.info("listo: %s, carpetas %s, nocturna %s", cfg.catalogo.raiz,
                     ", ".join(cfg.catalogo.carpetas), cfg.hora.strftime("%H:%M"))
            while not parar.is_set():
                if not ciclo(repo, cfg, parar=parar):
                    _esperar_aviso(conn, ESPERA_PEDIDOS, parar)
        except Detenido:
            log.info("pasada interrumpida: se retoma al volver a arrancar")
        except (psycopg.OperationalError, CatalogoNoDisponible) as e:
            log.warning("se cortó la conexión con la base: %s", e)
            parar.wait(ESPERA_RECONEXION)
        finally:
            latido.parar()
            try:
                conn.close()
            except psycopg.Error:
                pass


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = ConfigCatalogador.desde_entorno()
    if not cfg.catalogo.carpetas:
        raise SystemExit("CDLM_CATALOGO_CARPETAS está vacío: no hay nada para catalogar")
    normalizar_carpetas(cfg.catalogo.carpetas)
    parar = threading.Event()

    def _senal(_n, _f):
        log.info("deteniendo…")
        parar.set()
    signal.signal(signal.SIGTERM, _senal)
    signal.signal(signal.SIGINT, _senal)
    log.info("catalogador %s en %s", APP_VERSION, socket.gethostname())
    servir(cfg, parar)


if __name__ == "__main__":
    main()
