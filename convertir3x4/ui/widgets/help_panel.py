"""Ayuda para operadores nuevos: un panel al costado, no una ventana que interrumpe.

Se abre solo la primera vez que se usa la app en esa PC y después con el botón
"Ayuda" de la barra lateral. Muestra la ayuda del módulo en el que se está.
Frases cortas, con lo que hay que hacer, en el idioma de los operadores.
"""

import sys

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .. import icons
from ..style import T

_MOD = "⌘" if sys.platform == "darwin" else "Ctrl"

# (título, [(subtítulo, texto)])
HELP = [
    ("Buscar", [
        ("Encontrar", "Escribí parte del nombre, de la carpeta o un tag. Con Fotos y "
                      "Videos ves solo uno de los dos."),
        ("Llevar a ProPresenter", "Arrastrá la foto o el video directo a ProPresenter. "
                                  "ProPresenter usa el archivo original, no hace una copia."),
        ("Poner tags", "Seleccioná una o varias (con Shift o " + _MOD + ") y escribí el tag "
                       "abajo. Si no existe, se crea."),
        ("Sugerencias de la IA", "Los tags violeta los propuso la IA. Tocá el tag para "
                                 "confirmarlo o la ✕ para sacarlo: no lo vuelve a proponer."),
    ]),
    ("Catalogar", [
        ("Sumar al catálogo", "Elegí la carpeta y tocá Catalogar. Se puede repetir "
                              "cuando quieras: solo agrega lo que todavía no estaba."),
        ("Análisis con IA", "Propone tags y reconoce a las personas de la lista. Todo queda "
                            "como sugerencia hasta que alguien lo confirma en Buscar."),
        ("Personas", "Solo se reconoce a quien esté en la lista. Cargá de 3 a 5 fotos "
                     "por persona donde se le vea bien la cara."),
    ]),
    ("Convertir", [
        ("Agregar", "Agregá fotos o videos de la PC o del NAS, o pegá un link de "
                    "YouTube o Facebook para bajarlo."),
        ("Convertir", "Seleccioná en la grilla, elegí cómo armarla y tocá Convertir. "
                      "Si hay un video en la selección, sale un video."),
        ("Empezar de nuevo", "La lista se guarda sola y vuelve al abrir la app. Para "
                             "arrancar otra, tocá Vaciar: los archivos no se borran."),
        ("Audio", "Los videos salen con el volumen nivelado: en la consola el fader "
                  "queda siempre en 0 dB."),
        ("Ver un video", "Pasá el mouse por arriba para ver de qué se trata, sin abrirlo."),
        ("Si algo falla", "Pasá el mouse por arriba del que está marcado: dice qué pasó. "
                          "«No disponible» casi siempre es el NAS o un disco desconectado."),
        ("Opciones avanzadas", "El ⚙ tiene opciones que casi nunca hacen falta. "
                               "Lo que cambies ahí vuelve a lo estándar al cerrar la app."),
    ]),
    ("Grabadora", [
        ("Para qué es", "Para guardar en el NAS lo que se grabó con la grabadora, y "
                        "dejar el disco vacío para la próxima vez."),
        ("Cómo sabe que es de la grabadora", "El NAS lo reconoce solo: es un disco que "
                                             "tiene únicamente grabaciones de la grabadora. "
                                             "Si tiene otros archivos, aparece como «Otro "
                                             "disco» y no se toca solo."),
        ("Copiar el disco", "Enchufá el disco de la grabadora al NAS. Cuando aparece en "
                            "la lista, tocá Copiar y elegí la carpeta."),
        ("Sin riesgo", "Copiar nunca cambia nada del disco ni reemplaza archivos que ya "
                       "estén en el NAS."),
        ("Formatear", "Borra todo el disco para que la grabadora lo vuelva a usar. Solo se "
                      "puede cuando la copia está completa y revisada."),
        ("Copia automática", "Si está activada, los discos de la grabadora se copian, se "
                             "revisan y se borran apenas se enchufan al NAS."),
    ]),
]
SHORTCUTS = [(f"{_MOD}+1 a 4", "Cambiar de módulo"), ("F1", "Abrir o cerrar la ayuda"),
             ("Supr", "Quitar de Convertir")]


class HelpPanel(QFrame):
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("help_panel")
        self.setFixedWidth(300)
        self.setAccessibleName("Ayuda")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 12, 16)
        lay.setSpacing(10)

        head = QHBoxLayout()
        self.lbl_title = QLabel("Ayuda")
        self.lbl_title.setObjectName("help_title")
        head.addWidget(self.lbl_title, 1)
        close = QPushButton()
        close.setObjectName("btn_icon")
        close.setIcon(icons.icon("x", 16, T.text_2))
        close.setFixedSize(28, 28)
        close.setToolTip("Cerrar la ayuda")
        close.setAccessibleName("Cerrar la ayuda")
        close.clicked.connect(self.closed.emit)
        head.addWidget(close)
        lay.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # no es una parada de Tab
        self._body = QWidget()
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 0, 6, 0)
        self._body_lay.setSpacing(4)
        scroll.setWidget(self._body)
        lay.addWidget(scroll, 1)
        self.set_module(0)

    def set_module(self, index: int):
        title, sections = HELP[index]
        self.lbl_title.setText(f"Ayuda · {title}")
        while self._body_lay.count():
            w = self._body_lay.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
        for sub, text in sections:
            self._add(sub, text)
        self._body_lay.addSpacing(10)
        self._add("Atajos", "\n".join(f"{k}: {v}" for k, v in SHORTCUTS))
        self._body_lay.addStretch(1)

    def _add(self, sub: str, text: str):
        s = QLabel(sub)
        s.setObjectName("section_title")
        s.setContentsMargins(0, 8, 0, 0)
        self._body_lay.addWidget(s)
        t = QLabel(text)
        t.setWordWrap(True)
        t.setObjectName("help_text")
        self._body_lay.addWidget(t)
