"""Búsqueda con operadores, como la de Gmail (pedido del 24/11/2026). Sin Qt.

El operador escribe en el campo de Buscar palabras sueltas y, si quiere,
filtros con la forma `clave:valor`:

    etiqueta:placa  lugar:blessedland  evento:campamento  tipo:video
    carpeta:reuniones  desde:01/09/2025
    -etiqueta:retiro  texto:"santa cena"  tiene:notas  sin:etiquetas

- Un guion adelante excluye (`-etiqueta:retiro`, `-ensayo`). Un guion en el
  medio de una palabra no (`santa-cena`).
- Las comillas dejan valores con espacios (`etiqueta:"santa cena"`).
- Una clave que no es de las conocidas queda como palabra, entera: «10:30» o
  «C:\\Fotos» se siguen buscando como siempre (por fragmento del nombre).
- Un operador sin valor (`etiqueta:` mientras se escribe) no hace nada.
- Las fechas son las de la toma, en la hora de la iglesia: `21/09/2025`,
  `09/2025` (todo el mes) o `2025` (todo el año). `desde:` y `hasta:` incluyen
  esa fecha; `antes:` y `después:` no, como en Gmail (`antes:2025` llega al
  31/12/2024; `después:09/2025` empieza el 01/10/2025). `despues:` sin tilde
  también vale.

`analizar(texto)` lo convierte en una `Consulta`; `armar(consulta)` la vuelve
a escribir (el formulario de búsqueda de Buscar usa las dos). La traducción a
SQL está en `Repo.buscar`.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field

# Clave escrita -> clave que se usa. `tag` porque es como lo escribe cualquiera
# que conoce Gmail.
CLAVES = {
    "etiqueta": "etiqueta", "etiquetas": "etiqueta", "tag": "etiqueta",
    "lugar": "lugar", "evento": "evento", "eventos": "evento",
    "tipo": "tipo",
    "carpeta": "carpeta",
    "nombre": "nombre", "archivo": "nombre",
    "texto": "texto",
    "notas": "notas", "nota": "notas",
    "desde": "desde", "hasta": "hasta", "fecha": "fecha",
    "antes": "antes", "despues": "después",
    "tiene": "tiene", "sin": "sin",
}
TIPOS = {"foto": "image", "fotos": "image", "imagen": "image", "imagenes": "image",
         "video": "video", "videos": "video"}
NOMBRE_TIPO = {"image": "foto", "video": "video"}
# tiene:… / sin:…
# `lugar` es el lugar escrito (etiqueta de tipo lugar); `gps`, las coordenadas
# que trae el archivo (pocas fotos de teléfono).
TIENE = {"texto": "texto", "notas": "notas", "nota": "notas",
         "etiquetas": "etiquetas", "etiqueta": "etiquetas",
         "fecha": "fecha", "lugar": "lugar", "evento": "evento", "eventos": "evento",
         "gps": "gps"}
_VALORES_TIENE = ("texto", "notas", "etiquetas", "lugar", "evento", "fecha", "gps")
FECHAS = ("desde", "hasta", "fecha", "antes", "después")

_TOKEN = re.compile(r'''
    (?P<neg>-)?
    (?:
        (?P<clave>[^\W\d_][\w]*):(?P<valor>"[^"]*"?|\S*)    # clave:valor
      | (?P<frase>"[^"]*"?)                                # "frase entre comillas"
      | (?P<palabra>\S+)
    )
''', re.X)


@dataclass
class Filtro:
    clave: str              # una de CLAVES.values()
    valor: str              # tal como se escribió, sin comillas
    negado: bool = False
    # Para fechas: el período que abarca (desde, hasta), ambos incluidos
    periodo: tuple[dt.date, dt.date] | None = None


@dataclass
class Consulta:
    palabras: list[str] = field(default_factory=list)       # para buscar
    excluidas: list[str] = field(default_factory=list)      # -palabra
    filtros: list[Filtro] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)         # lo que no se entendió
    invalidos: list[str] = field(default_factory=list)      # ese texto, tal cual

    @property
    def tipo(self) -> str | None:
        """'image' / 'video' si se pidió un tipo (el último, si hay varios)."""
        for f in reversed(self.filtros):
            if f.clave == "tipo" and not f.negado:
                return TIPOS[_simple(f.valor)]
        return None

    @property
    def vacia(self) -> bool:
        return not (self.palabras or self.excluidas or self.filtros)


def _simple(s: str) -> str:
    """minúsculas y sin acentos (para comparar claves y valores fijos)."""
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _sin_comillas(v: str) -> str:
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] == '"' else v.strip('"')


def analizar(texto: str) -> Consulta:
    c = Consulta()
    for m in _TOKEN.finditer(texto or ""):
        neg = bool(m.group("neg"))
        clave = m.group("clave")
        if clave is not None and _simple(clave) in CLAVES:
            valor = " ".join(_sin_comillas(m.group("valor")).split())
            if valor and not _agregar_filtro(c, CLAVES[_simple(clave)], valor, neg):
                c.invalidos.append(m.group(0))
            continue
        if clave is not None:                      # «10:30», «C:\…»: palabra entera
            bruto = m.group(0)[1:] if neg else m.group(0)
            (c.excluidas if neg else c.palabras).append(bruto)
            continue
        if m.group("frase") is not None:
            frase = " ".join(_sin_comillas(m.group("frase")).split())
            if frase:
                (c.excluidas if neg else c.palabras).append(
                    f'"{frase}"' if " " in frase else frase)
            continue
        palabra = m.group("palabra")
        if neg and palabra:
            c.excluidas.append(palabra)
        elif not neg:
            c.palabras.append(palabra)
    c.palabras = [p for p in c.palabras if p]
    return c


def _agregar_filtro(c: Consulta, clave: str, valor: str, neg: bool) -> bool:
    """False si no se entendió (queda el aviso)."""
    if clave == "tipo":
        if _simple(valor) not in TIPOS:
            c.avisos.append(f"«tipo:{valor}» no se entiende: use tipo:foto o tipo:video.")
            return False
    elif clave in ("tiene", "sin"):
        if _simple(valor) not in TIENE:
            c.avisos.append(f"«{clave}:{valor}» no se entiende: use {clave}:texto, notas, "
                            "etiquetas, lugar, evento, fecha o gps.")
            return False
    elif clave in FECHAS:
        periodo = periodo_de(valor)
        if periodo is None:
            c.avisos.append(f"La fecha «{valor}» no se entiende: escríbala como 21/09/2025, "
                            "09/2025 o 2025.")
            return False
        c.filtros.append(Filtro(clave, valor, neg, periodo))
        return True
    c.filtros.append(Filtro(clave, valor, neg))
    return True


def periodo_de(valor: str) -> tuple[dt.date, dt.date] | None:
    """«21/09/2025» → ese día; «09/2025» → ese mes; «2025» → ese año.
    También 2025-09-21. None si no es una fecha."""
    v = valor.strip()
    try:
        if re.fullmatch(r"\d{4}", v):
            a = int(v)
            return dt.date(a, 1, 1), dt.date(a, 12, 31)
        m = re.fullmatch(r"(\d{1,2})[/.-](\d{4})", v)
        if m:
            mes, a = int(m.group(1)), int(m.group(2))
            return dt.date(a, mes, 1), dt.date(a, mes, calendar.monthrange(a, mes)[1])
        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", v)
        if m:
            d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return d, d
        m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})", v)
        if m:
            a = int(m.group(3))
            d = dt.date(a + 2000 if a < 100 else a, int(m.group(2)), int(m.group(1)))
            return d, d
    except ValueError:
        return None
    return None


def _valor(v: str) -> str:
    v = v.replace('"', "")
    return f'"{v}"' if (" " in v or not v) else v


def armar(c: Consulta) -> str:
    """La consulta escrita como se teclea (palabras primero, después los filtros)."""
    partes = list(c.palabras)
    partes += ["-" + p for p in c.excluidas]
    for f in c.filtros:
        valor = NOMBRE_TIPO[TIPOS[_simple(f.valor)]] if f.clave == "tipo" else f.valor
        partes.append(("-" if f.negado else "") + f"{f.clave}:{_valor(valor)}")
    return " ".join(partes)


def sin_tipo(texto: str) -> str:
    """El texto sin los `tipo:` (los botones Todo / Fotos / Videos mandan). El
    resto queda tal como se escribió."""
    quedan = [m.group(0) for m in _TOKEN.finditer(texto or "")
              if not (m.group("clave") and CLAVES.get(_simple(m.group("clave"))) == "tipo")]
    return " ".join(quedan)


def terminos(palabra: str) -> str:
    """Una palabra o «frase» de la consulta, sin las comillas."""
    return _sin_comillas(palabra)


# ---------------------------------------------------------------- sugerencias
# Las claves que se sugieren al escribir (las otras formas, como tag:, se
# entienden igual pero no se ofrecen).
SUGERIR = ("etiqueta", "lugar", "evento", "tipo", "carpeta", "nombre", "texto", "notas",
           "desde", "hasta", "fecha", "antes", "después", "tiene", "sin")
_MAX_SUGERENCIAS = 12


def sugerencias(texto: str, cursor: int, etiquetas: list[str], lugares=(),
                eventos=()) -> tuple[int, list[str]]:
    """Qué ofrecer mientras se escribe la palabra que termina en `cursor`.

    Devuelve (desde dónde se reemplaza, opciones); cada opción reemplaza
    `texto[desde:cursor]`. Sin opciones: ([], no se muestra nada).
      «car»           → carpeta:
      «etiqueta:pl»   → etiqueta:placa, etiqueta:"placa grande"…
      «tipo:»         → tipo:foto, tipo:video
    """
    inicio = cursor
    while inicio > 0 and not texto[inicio - 1].isspace():
        inicio -= 1
    if cursor < len(texto) and not texto[cursor].isspace():
        return inicio, []                     # en el medio de una palabra: nada
    escrito = texto[inicio:cursor]
    if '"' in escrito.split(":", 1)[0]:
        return inicio, []
    neg = "-" if escrito.startswith("-") else ""
    escrito = escrito[len(neg):]
    if ":" not in escrito:
        p = _simple(escrito)
        if len(p) < 3:                        # con menos, salta con cualquier palabra
            return inicio, []
        return inicio, [f"{neg}{k}:" for k in SUGERIR
                        if _simple(k).startswith(p) and _simple(k) != p] + \
            [f"{neg}{k}:" for k in SUGERIR if _simple(k) == p]
    clave, valor = escrito.split(":", 1)
    canon = CLAVES.get(_simple(clave))
    v = _simple(valor.lstrip('"'))
    if canon in ("etiqueta", "lugar", "evento"):
        lista = {"etiqueta": etiquetas, "lugar": lugares, "evento": eventos}[canon]
        empiezan = [e for e in lista if _simple(e).startswith(v)]
        contienen = [e for e in lista if v in _simple(e) and e not in empiezan]
        valores = [_valor(e) for e in empiezan + contienen]
    elif canon == "tipo":
        valores = [t for t in ("foto", "video") if t.startswith(v)]
    elif canon in ("tiene", "sin"):
        valores = [t for t in _VALORES_TIENE if t.startswith(v)]
    else:
        return inicio, []
    opciones = [f"{neg}{clave}:{x}" for x in valores[:_MAX_SUGERENCIAS]]
    if opciones == [neg + escrito]:
        return inicio, []                     # ya está escrito entero
    return inicio, opciones
