"""Cliente de panel-copias, la API del NAS que maneja la copia de discos externos.

Del lado del NAS corre `panel-copias.py` (repo CDLM-Freenas, scripts/), que envuelve
a `copiar-disco.py`. Este módulo no usa Qt: la pestaña Discos lo llama desde un
worker. Solo biblioteca estándar (urllib), sin dependencias nuevas.

Se prueban las direcciones en orden (LAN primero, Netbird después) y se recuerda
la última que respondió.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

# LAN de la iglesia y, desde afuera, Netbird (cdlm-truenas).
URLS_POR_DEFECTO = ("http://192.168.1.224:8790", "http://100.118.161.236:8790")


class PanelError(Exception):
    """Error para mostrar tal cual en la UI (en castellano)."""


class PanelSinConexion(PanelError):
    pass


class PanelClient:
    def __init__(self, urls, token: str, timeout: float = 5.0):
        self.urls = [u.rstrip("/") for u in urls if u and u.strip()]
        self.token = (token or "").strip()
        self.timeout = timeout
        self.url_activa: str | None = None

    # ------------------------------------------------------------ transporte
    def _pedir(self, metodo, ruta, datos=None, timeout=None):
        if not self.urls:
            raise PanelSinConexion("Falta configurar la conexión con el NAS. Comuníquese con el administrador del NAS.")
        orden = self.urls if self.url_activa not in self.urls else \
            [self.url_activa] + [u for u in self.urls if u != self.url_activa]
        cuerpo = json.dumps(datos).encode() if datos is not None else None
        ultimo = ""
        for base in orden:
            req = urllib.request.Request(base + ruta, data=cuerpo, method=metodo)
            req.add_header("Authorization", f"Bearer {self.token}")
            if cuerpo is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                    self.url_activa = base
                    return json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                # El NAS respondió: no tiene sentido probar la otra dirección.
                self.url_activa = base
                raise PanelError(_mensaje_http(e)) from None
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
                ultimo = str(getattr(e, "reason", e))
                continue
        self.url_activa = None
        # El detalle técnico (`ultimo`) no se muestra: al operador le sirve qué hacer.
        raise PanelSinConexion("No se pudo conectar con el NAS. Verifique que esté encendido "
                               "y que esta PC esté conectada a la red de la iglesia.")

    # ------------------------------------------------------------ consultas
    def estado(self) -> dict:
        return self._pedir("GET", "/v1/estado")

    def contenido(self, uuid: str) -> dict:
        """Qué hay en la raíz del disco. La primera vez el NAS lo monta y lo lee
        (puede tardar); después responde de memoria mientras siga enchufado."""
        return self._pedir("GET", f"/v1/contenido?uuid={uuid}", timeout=200)

    def log(self, uuid: str, lineas: int = 300) -> str:
        return self._pedir("GET", f"/v1/log?uuid={uuid}&lineas={lineas}").get("texto", "")

    # ------------------------------------------------------------ acciones
    def copiar(self, uuid: str, destino: str, reintentar: bool = False) -> str:
        r = self._pedir("POST", "/v1/copiar",
                        {"uuid": uuid, "destino": destino, "reintentar": reintentar}, timeout=60)
        return r.get("mensaje", "")

    def detener(self, uuid: str) -> str:
        return self._pedir("POST", "/v1/detener", {"uuid": uuid}, timeout=120).get("mensaje", "")

    def verificar(self, uuid: str) -> str:
        return self._pedir("POST", "/v1/verificar", {"uuid": uuid}).get("mensaje", "")

    def formatear(self, uuid: str, serie: str) -> str:
        return self._pedir("POST", "/v1/formatear",
                           {"uuid": uuid, "serie": serie, "confirmacion": "FORMATEAR"}).get("mensaje", "")

    def set_auto(self, activo: bool) -> bool:
        return bool(self._pedir("POST", "/v1/auto", {"activo": activo}).get("auto"))


def _mensaje_http(e: urllib.error.HTTPError) -> str:
    try:
        detalle = json.loads(e.read() or b"{}").get("error", "")
    except ValueError:
        detalle = ""
    if e.code == 401:
        return "El NAS no reconoce a esta PC. Comuníquese con el administrador del NAS."
    if e.code == 403:
        return "El NAS no acepta conexiones desde esta red."
    return detalle[:1].upper() + detalle[1:] if detalle else "Se produjo un error en el NAS. Intente nuevamente en unos minutos."


# ------------------------------------------------------------------ textos para la UI
FASES = {
    "MONTANDO": "Preparando",
    "COPIANDO": "Copiando",
    "TERMINADO": "Copiado completo",
    "CON_ERRORES": "Copiado con errores",
    "DETENIDA": "Detenida",
    "ERROR": "Error",
    "VERIFICACION_FALLIDA": "Verificación fallida",
    "FORMATEADO": "Vacío",
}


@dataclass
class Resumen:
    titulo: str             # "Copiando", "Copiado completo"…
    detalle: str            # "120,3 GB de 936,9 GB · 175 MB/s · faltan ~78 min"
    porcentaje: int | None  # 0-100 mientras copia; None si no aplica
    tono: str               # "activo" | "ok" | "aviso" | "error" | "neutro"


_FS = {"exfat": "exFAT", "vfat": "FAT", "ntfs": "NTFS", "hfsplus": "HFS+", "ext4": "ext4",
       "xfs": "XFS", "apfs": "APFS"}


def nombre_fs(fs) -> str:
    return _FS.get(fs or "", fs or "")


def fecha(texto) -> str:
    """'2026-11-15' -> '15/11/2026' y '2026-11-15 19:42:10' -> '15/11/2026 19:42'."""
    t = str(texto or "")
    if len(t) >= 10 and t[4] == "-" and t[7] == "-":
        d = f"{t[8:10]}/{t[5:7]}/{t[0:4]}"
        return f"{d} {t[11:16]}" if len(t) >= 16 else d
    return t


def _gb(x) -> str:
    # 250 GB de 1000 GB; con decimales solo si es chico (8,4 GB)
    v = float(x or 0)
    return (f"{v:.0f}" if v >= 10 else f"{v:.1f}".replace(".", ",")) + " GB"


def _minutos(m) -> str:
    m = int(m or 0)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def resumir(copia: dict | None, tarea: dict | None = None, disco: dict | None = None) -> Resumen:
    """Una línea de estado legible para una copia (estado.json + si corre).

    `tarea` es la verificación o el formateo pedidos desde la app; `disco`, la
    partición enchufada (para reconocer un disco recién formateado, que tiene UUID
    nuevo y por eso no tiene copia propia)."""
    if tarea and tarea.get("estado") == "corriendo":
        accion = "Verificando y formateando" if tarea.get("accion") == "formatear" else "Verificando"
        return Resumen(accion, "Compara lo que hay en el disco con lo copiado en el NAS.",
                       None, "activo")
    if tarea and tarea.get("estado") == "error" and not (copia or {}).get("corriendo"):
        que = "No se formateó" if tarea.get("accion") == "formatear" else "La verificación falló"
        return Resumen(que, tarea.get("mensaje") or "", None, "error")
    if disco and disco.get("admitido") is False:
        return Resumen("El NAS no puede leer este disco", "Tiene un formato que el NAS no "
                       "entiende. Hay que copiarlo desde una PC.", None, "aviso")
    if not copia:
        f = (disco or {}).get("formateado")
        if f:
            return Resumen("Vacío", f"Se borró el {fecha(f.get('fecha'))}, después de copiar "
                           f"{f.get('archivos', '?')} archivos. Listo para volver a grabar.", None, "ok")
        return Resumen("Sin copiar", "Este disco nunca se copió al NAS.", None, "neutro")
    fase = copia.get("fase") or ""
    titulo = FASES.get(fase, fase.title() or "Desconocido")
    errores = int(copia.get("errores") or 0)
    if copia.get("corriendo"):
        if fase == "TERMINADO":   # modo auto: verifica y formatea dentro de la misma unidad
            return Resumen("Verificando y formateando", "Copia completa. Dejando el disco vacío para la grabadora.", None, "activo")
        hecho, falta = float(copia.get("copiado_gb") or 0), float(copia.get("falta_gb") or 0)
        total = hecho + falta
        pct = int(round(100 * hecho / total)) if total > 0 else 0
        partes = [f"{_gb(hecho)} de {_gb(total)}"]
        if copia.get("eta_min") not in (None, "") and hecho > 0:
            partes.append(f"falta {_minutos(copia['eta_min'])} aprox.")
        if errores:
            partes.append(f"{errores} con error")
        return Resumen("Copiando" if fase == "COPIANDO" else titulo, " · ".join(partes), pct, "activo")
    if fase == "TERMINADO":
        v = copia.get("verificado")
        det = f"{copia.get('archivos', '?')} archivos en el NAS."
        if v and not v.get("fallos"):
            det += f" Verificado el {fecha(v.get('fecha'))}."
        if copia.get("error"):
            det += f" {copia['error']}"
        return Resumen(titulo, det, None, "ok")
    if fase == "FORMATEADO":
        return Resumen(titulo, "Vacío y listo para volver a grabar.", None, "ok")
    if fase == "CON_ERRORES":
        return Resumen(titulo, f"{errores} archivos no se pudieron leer. El disco no se borrará: "
                       "comuníquese con el administrador del NAS para intentar recuperarlos.",
                       None, "aviso")
    if fase == "DETENIDA":
        return Resumen(titulo, "Haga clic en Copiar para continuar desde donde se detuvo.", None, "aviso")
    return Resumen(titulo, copia.get("error") or "", None, "error" if fase in
                   ("ERROR", "VERIFICACION_FALLIDA") else "neutro")


# ------------------------------------------------------------------ contenido del disco
def _dia(ts) -> str:
    import time as _t
    lt = _t.localtime(ts)
    return f"{lt.tm_mday}/{lt.tm_mon}"


def resumir_contenido(c: dict | None) -> str:
    """Lo que tiene el disco, en una frase. '' si no hay nada que decir todavía."""
    if not c or c.get("ocupado") or c.get("admitido") is False:
        return ""
    total = int(c.get("total_archivos") or 0)
    if not total:
        return "Está vacío."
    tam = _gb(float(c.get("total_bytes") or 0) / 1e9)
    if c.get("solo_grabadora"):
        n = int(c.get("grabaciones") or 0)
        que = "1 grabación" if n == 1 else f"{n} grabaciones"
        desde, hasta = c.get("desde"), c.get("hasta")
        cuando = ""
        if desde and hasta:
            d1, d2 = _dia(desde), _dia(hasta)
            cuando = f", del {d1}" if d1 == d2 else f", del {d1} al {d2}"
        return f"Tiene {que} ({tam}){cuando}."
    sueltos = [a["nombre"] for a in c.get("archivos") or []]
    carpetas = [x["nombre"] for x in c.get("carpetas") or []]
    que = "1 archivo" if total == 1 else f"{total} archivos"
    carp = (f"la carpeta {carpetas[0]}" if len(carpetas) == 1 else f"{len(carpetas)} carpetas")
    if sueltos:
        ejemplo = " y ".join(sueltos[:2])
        texto = f"Tiene {que} ({tam}), por ejemplo {ejemplo}"
        return texto + (f", y {carp}." if carpetas else ".")
    return f"Tiene {que} ({tam}) en {carp}."


def lineas_contenido(c: dict | None) -> list[str]:
    """Cada archivo y carpeta de la raíz en una línea, para la lista desplegable."""
    if not c:
        return []
    out = []
    for car in c.get("carpetas") or []:
        n = int(car.get("archivos") or 0)
        out.append(f"{car['nombre']}  ·  carpeta con {'1 archivo' if n == 1 else f'{n} archivos'}"
                   f" ({_gb(float(car.get('bytes') or 0) / 1e9)})")
    import time as _t
    for a in c.get("archivos") or []:
        cuando = _t.strftime("%d/%m/%Y %H:%M", _t.localtime(a["fecha"])) if a.get("fecha") else ""
        out.append(f"{a['nombre']}  ·  {_gb(float(a.get('bytes') or 0) / 1e9)}"
                   + (f"  ·  {cuando}" if cuando else ""))
    return out


# ------------------------------------------------------------------ ¿es de la grabadora?
# El NAS (copiar-disco.py, modo automático) reconoce un disco de la grabadora
# (HyperDeck) así: USB, exFAT y en la raíz SOLO archivos HyperDeck_NNNN (.mov,
# .mp4 o .mcc). Cualquier otro disco lo deja quieto. La app no repite esa
# revisión: la lee de la nota que deja el NAS y de sus registros de borrado.

def tipo_disco(disco: dict | None) -> str | None:
    """'grabadora', 'otro' o None si el NAS todavía no lo revisó (p. ej. con la
    copia automática apagada)."""
    if not disco:
        return None
    if disco.get("formateado"):
        return "grabadora"       # el NAS ya lo dejó vacío para la grabadora
    nota = (disco.get("nota_auto") or "").lower()
    if "disco de hyperdeck," in nota or "vacio, listo para grabar" in nota:
        return "grabadora"
    if "no es un disco de hyperdeck" in nota or "no es de la hyperdeck" in nota:
        return "otro"
    return None


def nota_operador(disco: dict | None) -> str:
    """La nota del modo automático en palabras del operador ('' si no hace falta)."""
    nota = ((disco or {}).get("nota_auto") or "").lower()
    if not nota:
        return ""
    if "modo automatico esta apagado" in nota:
        return ("La copia automática está desactivada. Si es un disco de la grabadora, haga clic en Copiar.")
    if "no es un disco de hyperdeck" in nota:
        return ("No es un disco de la grabadora (contiene otros archivos), por eso no se "
                "copia automáticamente. Si desea copiarlo de todos modos, haga clic en Copiar.")
    if "no es de la hyperdeck" in nota:
        return "No es un disco de la grabadora, por eso no se copia automáticamente."
    if "vacio, listo para grabar" in nota:
        return "Está vacío, listo para la grabadora."
    if "montado en otro lado" in nota:
        return "El disco está en uso en otra parte del NAS, por eso no se copió automáticamente."
    if "disco de hyperdeck," in nota or "ya hay una copia corriendo" in nota:
        return ""                # el estado de la copia ya lo dice
    return "El NAS no pudo revisar este disco automáticamente. Si es de la grabadora, haga clic en Copiar."


def acciones_para(disco: dict, copia: dict | None, tarea: dict | None) -> set[str]:
    """Qué botones tiene sentido mostrar para un disco enchufado."""
    if tarea and tarea.get("estado") == "corriendo":
        return set()
    if not disco.get("admitido"):
        return set()
    if copia and copia.get("corriendo"):
        return {"detener"}
    fase = (copia or {}).get("fase")
    if fase == "TERMINADO":
        # "copiar" también: si se volvió a grabar, baja solo lo nuevo.
        return {"copiar", "verificar", "formatear"}
    if fase == "CON_ERRORES":
        return {"copiar", "reintentar"}
    return {"copiar"}
