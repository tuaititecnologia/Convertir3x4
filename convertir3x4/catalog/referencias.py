"""¿Sirven las fotos de referencia de una persona? (sin Qt)

El NAS anota qué vio en cada foto (catalogador.procesar_referencias): cuántas
caras de tamaño parecido, dónde está la elegida y qué tan clara. Acá se juzga
cada foto con eso y, además, se comparan sus firmas entre sí: una foto que no se
parece a las demás puede ser de otra persona (p. ej. alguien más cerca de la
cámara que la persona). Lo usa el detalle de la persona en Catalogar.

Los umbrales de tamaño y claridad son una primera estimación: ajustarlos con
fotos reales si avisan de más o de menos.
"""

import io
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw

from ..engine.faces import MATCH_COSINE

CARA_CHICA_PX = 80        # lado del rostro en la foto subida (1600 px): menos, poco detalle
CONFIANZA_BAJA = 0.90     # el detector acepta desde 0,85; por debajo de esto, dudoso
FOTOS_RECOMENDADAS = 3
MINI_LADO = 120           # lado de la miniatura de la foto en el detalle
CARA_LADO = 96            # lado del recorte del rostro


@dataclass
class Juicio:
    """Lo que se dice de una foto (o de la persona): `tono` como en la Grabadora
    ('ok', 'aviso', 'error', 'activo', 'neutro'), un título y el detalle."""
    tono: str
    titulo: str
    detalle: list[str] = field(default_factory=list)


@dataclass
class FotoRevisada:
    id: int
    archivo: str
    ruta: str
    juicio: Juicio
    coincide: tuple[int, int] | None = None   # (con cuántas otras fotos coincide, de cuántas)
    miniatura: bytes | None = None     # JPEG, con el rostro elegido marcado
    cara: bytes | None = None          # JPEG del rostro elegido


@dataclass
class Revision:
    nombre: str
    juicio: Juicio
    hacer: str
    fotos: list[FotoRevisada]
    listas: int
    confirmadas: int
    sugeridas: int


def _firma(b: bytes | None) -> np.ndarray | None:
    return None if b is None else np.frombuffer(b, dtype=np.float32)


def coincidencias(firmas: dict[int, np.ndarray]) -> dict[int, tuple[int, int]]:
    """Para cada foto: con cuántas de las otras fotos de la persona coincide (el
    mismo umbral que el reconocimiento) y de cuántas. No alcanza con «se parece a
    alguna»: dos fotos de la misma persona equivocada (p. ej. del mismo evento, con
    otro más cerca de la cámara) se parecen entre ellas."""
    out = {}
    for a, va in firmas.items():
        otras = [float(va @ vb) for b, vb in firmas.items() if b != a]
        if otras:
            out[a] = (sum(1 for c in otras if c >= MATCH_COSINE), len(otras))
    return out


def juzgar_foto(ref: dict, nombre: str, coincide: tuple[int, int] | None) -> Juicio:
    estado = ref["estado"]
    if estado == "pendiente":
        return Juicio("activo", "En proceso en el NAS")
    if estado == "ilegible":
        return Juicio("error", "No se pudo abrir",
                      ["El archivo no es una imagen válida. No se usa para reconocer."])
    if estado == "sin_cara":
        return Juicio("error", "No se encontró un rostro",
                      ["No se usa para reconocer. Reemplácela por una foto en la que el "
                       "rostro se vea de frente."])
    avisos, notas = [], []
    k, otras = coincide or (0, 0)
    # Con 3 fotos o más, una que coincide con menos de la mitad de las otras no
    # es de la misma persona que la mayoría. Con 2 no se puede saber cuál está mal.
    if otras == 1 and k == 0:
        return Juicio("error", "Puede ser de otra persona",
                      ["Las dos fotos no se parecen entre sí: una puede ser de otra persona. "
                       "Agregue más fotos para saber cuál."])
    if otras >= 2 and 2 * k < otras:
        return Juicio("error", "Puede ser de otra persona",
                      [f"No se parece a la mayoría de las otras fotos de {nombre}: puede ser "
                       "de otra persona."])
    confirmada = otras >= 2          # coincide con la mayoría: el rostro elegido es el suyo
    if ref.get("caras") is None:
        notas.append("El NAS todavía no revisó esta foto en detalle.")
    else:
        if ref["caras"] > 1:
            if confirmada:
                notas.append(f"Hay {ref['caras']} rostros de tamaño parecido; el marcado "
                             f"coincide con las otras fotos de {nombre}.")
            else:
                avisos.append(f"Hay {ref['caras']} rostros de tamaño parecido; se tomó el "
                              f"marcado. Verifique que sea el de {nombre}.")
        caja = ref.get("caja")
        if caja and min(caja[2], caja[3]) < CARA_CHICA_PX:
            avisos.append("El rostro se ve pequeño: el reconocimiento será menos preciso.")
        if ref.get("confianza") is not None and ref["confianza"] < CONFIANZA_BAJA:
            avisos.append("El rostro no se ve con claridad.")
    if avisos:
        return Juicio("aviso", "Revisar", avisos + notas)
    return Juicio("ok", "Sirve", notas)


def revisar(detalle: dict, imagenes: bool = True) -> Revision:
    """Juzga cada foto y a la persona en conjunto. `imagenes`: armar también la
    miniatura y el recorte (PIL; correr fuera del hilo de la interfaz)."""
    nombre = detalle["name"]
    refs = detalle["referencias"]
    firmas = {r["id"]: _firma(r["firma"]) for r in refs
              if r["estado"] == "lista" and r["firma"] is not None}
    coinc = coincidencias(firmas)
    n_listas = len(firmas)
    fotos = []
    for r in refs:
        j = juzgar_foto(r, nombre, coinc.get(r["id"]))
        mini, cara = imagenes_de(r) if imagenes else (None, None)
        fotos.append(FotoRevisada(r["id"], r["archivo"], r["ruta"], j, coinc.get(r["id"]),
                                  mini, cara))
    juicio, hacer = _juicio_persona(nombre, fotos, n_listas)
    return Revision(nombre, juicio, hacer, fotos, n_listas,
                    detalle.get("confirmadas") or 0, detalle.get("sugeridas") or 0)


def _juicio_persona(nombre: str, fotos: list[FotoRevisada], n_listas: int) -> tuple[Juicio, str]:
    def cuantas(tono):
        return sum(1 for f in fotos if f.juicio.tono == tono)

    def n_fotos(k):
        return "1 foto" if k == 1 else f"{k} fotos"
    if not fotos:
        return (Juicio("error", "Sin fotos de referencia"),
                f"Agregue de 3 a 5 fotos en las que el rostro de {nombre} se vea con claridad.")
    pendientes = cuantas("activo")
    if n_listas == 0:
        if pendientes:
            return (Juicio("activo", "En proceso en el NAS",
                           ["El NAS está revisando las fotos. Esto tarda unos segundos."]), "")
        return (Juicio("error", f"Todavía no se puede reconocer a {nombre}",
                       ["Ninguna foto tiene un rostro que se pueda usar."]),
                "Agregue fotos en las que el rostro se vea de frente y con claridad.")
    detalle = [f"Se reconoce a {nombre} con {n_fotos(n_listas)}."]
    if pendientes:
        detalle.append(f"{n_fotos(pendientes)} todavía en proceso en el NAS.")
    errores = [f for f in fotos if f.juicio.tono == "error"]
    otra = [f for f in errores if f.juicio.titulo == "Puede ser de otra persona"]
    if otra:
        return (Juicio("error", "Hay fotos que pueden ser de otra persona", detalle),
                "Mire los rostros marcados y quite la foto que no sea de " + nombre + ". "
                "Mientras esté, se pueden sugerir etiquetas con su nombre en fotos de otra persona.")
    avisos = cuantas("aviso")
    if errores:
        detalle.append(f"{n_fotos(len(errores))} no se usan: no tienen un rostro que sirva.")
    if avisos:
        return (Juicio("aviso", f"Revise {n_fotos(avisos)}", detalle),
                "Mire en cada foto marcada que el rostro elegido sea el de " + nombre + ". "
                "Si alguna no lo es, quítela.")
    if n_listas < FOTOS_RECOMENDADAS:
        return (Juicio("aviso", "Conviene agregar más fotos", detalle),
                f"Con 3 a 5 fotos (de distintos días, con distinta luz) se reconoce a "
                f"{nombre} en más fotos del catálogo.")
    return Juicio("ok", "Las fotos sirven para reconocer", detalle), ""


def texto_catalogo(rev: Revision) -> str:
    """Cuántas fotos del catálogo ya tienen su nombre."""
    total = rev.confirmadas + rev.sugeridas
    if not total:
        return ("Todavía ninguna foto del catálogo tiene su nombre. Las sugerencias "
                "aparecen después de la próxima pasada del NAS (todas las noches, o con "
                "«Catalogar»).")
    t = ("1 foto o video del catálogo tiene su nombre" if total == 1 else
         f"{total} fotos y videos del catálogo tienen su nombre")
    partes = []
    if rev.confirmadas:
        partes.append("1 confirmada" if rev.confirmadas == 1 else f"{rev.confirmadas} confirmadas")
    if rev.sugeridas:
        partes.append("1 sugerida para revisar en Buscar" if rev.sugeridas == 1 else
                      f"{rev.sugeridas} sugeridas para revisar en Buscar")
    return t + ": " + ", ".join(partes) + "."


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def imagenes_de(ref: dict) -> tuple[bytes | None, bytes | None]:
    """Miniatura de la foto (con el rostro elegido en un recuadro) y el recorte
    del rostro con algo de margen."""
    if not ref.get("foto"):
        return None, None
    try:
        img = Image.open(io.BytesIO(ref["foto"])).convert("RGB")
    except Exception:  # noqa: BLE001 — foto ilegible: sin imagen
        return None, None
    caja = ref.get("caja")
    cara = None
    if caja:
        x, y, w, h = caja
        m = max(w, h) // 3
        lado = max(w, h) + 2 * m
        cx, cy = x + w // 2, y + h // 2
        rec = img.crop((cx - lado // 2, cy - lado // 2, cx + lado // 2, cy + lado // 2))
        cara = _jpeg(rec.resize((CARA_LADO * 2, CARA_LADO * 2), Image.LANCZOS))   # 2x
    mini = img.copy()
    mini.thumbnail((MINI_LADO * 2, MINI_LADO * 2), Image.LANCZOS)   # nítida en pantallas 2x
    if caja:
        k = mini.width / img.width
        x, y, w, h = (v * k for v in caja)
        ImageDraw.Draw(mini).rectangle((x, y, x + w, y + h), outline=(255, 200, 0),
                                       width=max(2, round(mini.width / 120)))
    return _jpeg(mini), cara
