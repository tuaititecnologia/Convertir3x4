"""Qué carpetas del NAS se catalogan. Sin Qt.

Diseño: docs/carpetas-y-conexion.md. Lo esencial:

- La base guarda **marcas** (`carpeta_catalogada`: ruta + incluir). Solo se
  guardan las carpetas que difieren de su madre; "" es la raíz.
- Una carpeta sin marca toma el estado de la más cercana hacia arriba. Sin
  ninguna marca arriba, no se cataloga. Así una carpeta nueva dentro de una
  tildada entra sola.
- Los archivos sueltos de una carpeta se catalogan si la carpeta se cataloga.
- `Arbol` es lo que dibuja la ventana de carpetas: el estado de cada carpeta
  que mostró el NAS, las cantidades y el filtro por nombre.
"""

import unicodedata

from . import rutas

# Carpetas que nunca se muestran ni se recorren (además de las que empiezan con
# punto, como .zfs o .recycle). Se comparan sin distinguir mayúsculas.
_SISTEMA = {"@eadir", "#recycle", "$recycle.bin", "system volume information",
            "ix-applications", "lost+found", "#snapshot"}


def es_de_sistema(nombre: str) -> bool:
    """¿Es una carpeta del sistema (papelera, miniaturas de Synology, ZFS…)?"""
    return nombre.startswith(".") or nombre.casefold() in _SISTEMA


def madre(carpeta: str) -> str:
    """Carpeta madre (relativa): `a/b` -> `a`, `a` -> "" (la raíz)."""
    return rutas.carpeta_de(carpeta)


def _profundidad(carpeta: str) -> int:
    return 0 if not carpeta else carpeta.count("/") + 1


def limpiar(carpeta: str) -> str:
    return carpeta.strip().replace("\\", "/").strip("/")


def _estado(marcas: dict[str, bool], carpeta: str) -> bool:
    c = carpeta
    while True:
        if c in marcas:
            return marcas[c]
        if not c:
            return False
        c = madre(c)


def minimas(estados: dict[str, bool]) -> dict[str, bool]:
    """Las marcas justas para esos estados: cada carpeta que difiere de su madre."""
    marcas: dict[str, bool] = {}
    for c in sorted(estados, key=lambda x: (_profundidad(x), x)):
        arriba = _estado(marcas, madre(c)) if c else False
        if estados[c] != arriba:
            marcas[c] = estados[c]
    return marcas


class Alcance:
    """Las marcas de la base, con las preguntas que hace el catalogado."""

    def __init__(self, marcas: dict[str, bool] | None = None):
        self.marcas = minimas({limpiar(k): bool(v) for k, v in (marcas or {}).items()})

    def __eq__(self, otro):
        return isinstance(otro, Alcance) and self.marcas == otro.marcas

    def __repr__(self):
        return f"Alcance({self.marcas!r})"

    def __bool__(self):
        return any(self.marcas.values())

    def estado(self, carpeta: str) -> bool:
        """¿Se cataloga esta carpeta (sus archivos sueltos)?"""
        return _estado(self.marcas, limpiar(carpeta))

    def incluye_archivo(self, rel: str) -> bool:
        return self.estado(rutas.carpeta_de(rel))

    def raices(self) -> tuple[str, ...]:
        """Desde dónde se recorre: cada carpeta tildada cuya madre no lo está."""
        out = [c for c, v in self.marcas.items()
               if v and (not c or not self.estado(madre(c)))]
        return tuple(sorted(out, key=lambda c: (c.lower(), c)))

    def excluidas(self) -> tuple[str, ...]:
        """Las carpetas destildadas dentro de una tildada (donde se corta el recorrido)."""
        out = [c for c, v in self.marcas.items() if c and not v and self.estado(madre(c))]
        return tuple(sorted(out, key=lambda c: (c.lower(), c)))

    def raiz_de(self, carpeta: str) -> str | None:
        """La raíz del recorrido que contiene esta carpeta (None si no se cataloga)."""
        c = limpiar(carpeta)
        if not self.estado(c):
            return None
        while c and self.estado(madre(c)):
            c = madre(c)
        return c

    def describir(self) -> list[str]:
        """Una línea por raíz, para el operador: «Publicar (excepto Publicar/Viejo)»."""
        lineas = []
        excluidas = self.excluidas()
        for r in self.raices():
            nombre = r or "Toda la carpeta multimedia"
            fuera = [e for e in excluidas if self.raiz_de(madre(e)) == r]
            if fuera:
                nombre += " (excepto " + _y(fuera) + ")"
            lineas.append(nombre)
        return lineas


def _y(cosas: list[str]) -> str:
    if len(cosas) == 1:
        return cosas[0]
    return ", ".join(cosas[:-1]) + " y " + cosas[-1]


def _normal(texto: str) -> str:
    s = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in s if not unicodedata.combining(c))


class Arbol:
    """El árbol de la ventana de carpetas.

    `carpetas`: las que leyó el NAS (relativas, sin la raíz). Se agregan las que
    tienen marca aunque el NAS no las tenga (renombradas o borradas: se ven con
    el aviso «No está en el NAS») y todas sus madres. `conteos`: {carpeta:
    (fotos, videos)} catalogados directamente en cada una."""

    def __init__(self, carpetas, marcas: dict[str, bool] | None = None,
                 conteos: dict[str, tuple[int, int]] | None = None):
        self.en_nas = {limpiar(c) for c in carpetas if limpiar(c)}
        alcance = Alcance(marcas)
        todas = set(self.en_nas) | set(alcance.marcas) | {""}
        for c in list(todas):
            while c:
                c = madre(c)
                todas.add(c)
        self.hijos: dict[str, list[str]] = {c: [] for c in todas}
        for c in todas:
            if c:
                self.hijos[madre(c)].append(c)
        for h in self.hijos.values():
            h.sort(key=lambda c: (_normal(nombre(c)), c))
        self.estados = {c: alcance.estado(c) for c in todas}
        self._conteos = {limpiar(k): tuple(v) for k, v in (conteos or {}).items()}
        self._totales: dict[str, tuple[int, int]] = {}
        self._sumar("")

    def _sumar(self, c: str) -> tuple[int, int]:
        f, v = self._conteos.get(c, (0, 0))
        for h in self.hijos[c]:
            hf, hv = self._sumar(h)
            f, v = f + hf, v + hv
        self._totales[c] = (f, v)
        return f, v

    # ------------------------------------------------------------- consultas
    def carpetas(self) -> list[str]:
        """Todas, en el orden del árbol (madre antes que sus hijas)."""
        out, pila = [], [""]
        while pila:
            c = pila.pop()
            out.append(c)
            pila.extend(reversed(self.hijos[c]))
        return out

    def totales(self, carpeta: str) -> tuple[int, int]:
        """(fotos, videos) catalogados en la carpeta y todo lo de adentro."""
        return self._totales.get(carpeta, (0, 0))

    def descendientes(self, carpeta: str) -> list[str]:
        out, pila = [], list(self.hijos.get(carpeta, ()))
        while pila:
            c = pila.pop()
            out.append(c)
            pila.extend(self.hijos[c])
        return out

    def marcas(self) -> dict[str, bool]:
        return minimas(self.estados)

    def alcance(self) -> Alcance:
        return Alcance(self.marcas())

    def contrarias_adentro(self) -> dict[str, int]:
        """{carpeta: cuántas de adentro tienen marca con el estado contrario}:
        «2 excluidas adentro» en una tildada, «1 elegida adentro» en una
        destildada. Todo el árbol de una vez (son miles de carpetas)."""
        cuenta: dict[str, int] = {}
        for m, valor in self.marcas().items():
            a = m
            while a:
                a = madre(a)
                if self.estados[a] != valor:
                    cuenta[a] = cuenta.get(a, 0) + 1
        return cuenta

    # ------------------------------------------------------------- cambios
    def cambiar(self, carpeta: str, valor: bool, visibles: set[str] | None = None):
        """Tilda o destilda una carpeta y lo de adentro. Con el filtro puesto
        (`visibles`), solo lo que se ve; lo escondido queda como estaba."""
        for c in [carpeta] + self.descendientes(carpeta):
            if visibles is None or c in visibles:
                self.estados[c] = valor

    def filtrar(self, texto: str) -> set[str] | None:
        """Qué se ve con el filtro (None = todo): las carpetas cuyo nombre
        contiene el texto (sin distinguir mayúsculas ni tildes), su camino hacia
        arriba y todo lo de adentro de ellas."""
        t = _normal(texto.strip())
        if not t:
            return None
        visibles: set[str] = set()
        for c in self.hijos:
            if c and t in _normal(nombre(c)):
                visibles.add(c)
                visibles.update(self.descendientes(c))
                m = c
                while m:
                    m = madre(m)
                    visibles.add(m)
        return visibles

    def coincide(self, carpeta: str, texto: str) -> bool:
        t = _normal(texto.strip())
        return bool(t) and bool(carpeta) and t in _normal(nombre(carpeta))


def nombre(carpeta: str) -> str:
    """El último tramo de la ruta (lo que se ve en el árbol)."""
    return carpeta.rsplit("/", 1)[-1]
