"""Ayuda para operadores nuevos: un panel al costado, no una ventana que interrumpe.

Se abre solo la primera vez que se usa la app en esa PC y después con el botón
"Ayuda" de la barra lateral. Muestra la ayuda del módulo en el que se está.
Frases cortas, con lo que hay que hacer, en el idioma de los operadores.
"""

import sys

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from .. import icons
from ..style import T

_MOD = "⌘" if sys.platform == "darwin" else "Ctrl"

# (título, [(subtítulo, texto)])
HELP = [
    ("Buscar", [
        ("Buscar", "Escriba parte del nombre, de la carpeta o una etiqueta. Con «Fotos» o "
                   "«Videos» se muestra solo ese tipo."),
        ("Usar en ProPresenter", "Arrastre la foto o el video directamente a ProPresenter. "
                                 "ProPresenter usa el archivo original; no crea una copia."),
        ("Ver el original", "Con doble clic, el video se abre en el reproductor de la PC y "
                            "la foto en el visor de imágenes."),
        ("Enviar a Convertir", "Seleccione uno o varios elementos y elija «Enviar a "
                               "Convertir» (o use el menú del botón derecho). Se agregan a "
                               "la grilla de Convertir ya seleccionados."),
        ("Etiquetas", "Seleccione uno o varios elementos (con Shift o " + _MOD + ") y "
                      "escriba la etiqueta abajo. Si no existe, se crea."),
        ("Sugerencias de la IA", "Las etiquetas violetas son sugerencias de la IA. Haga "
                                 "clic en la etiqueta para confirmarla o en la ✕ para "
                                 "descartarla: no volverá a sugerirse."),
    ]),
    ("Catalogar", [
        ("Agregar al catálogo", "El NAS revisa las carpetas todas las noches. Para no "
                                "esperar, haga clic en «Catalogar ahora»: solo se agrega "
                                "lo que todavía no estaba."),
        ("Análisis con IA", "El NAS sugiere etiquetas y reconoce a las personas de la "
                            "lista al catalogar. Todo queda como sugerencia hasta que se "
                            "confirma en Buscar."),
        ("Personas", "Solo se reconoce a las personas de la lista. Cargue de 3 a 5 fotos "
                     "por persona en las que el rostro se vea con claridad."),
    ]),
    ("Convertir", [
        ("Agregar", "Agregue fotos o videos de la PC o del NAS, o pegue el enlace de un "
                    "video de YouTube o Facebook para descargarlo."),
        ("Convertir", "Seleccione los elementos en la grilla y elija cuántos van en cada "
                      "pantalla: uno o dos. En «Resultado» se muestra cada pantalla que se "
                      "creará, y el botón indica cuántas fotos y videos se generan."),
        ("Dos por pantalla", "Se ubican en el orden de la grilla: el primero arriba (o a la "
                             "izquierda en una pantalla horizontal). Para cambiar el orden, "
                             "arrastre los elementos en la grilla. Si uno de ellos es un "
                             "video, el resultado es un video."),
        ("Fondo", "«Automático» genera el fondo con los colores de los elementos. "
                  "«Imagen propia» usa una imagen elegida, con uno o dos elementos por "
                  "pantalla; con uno, deja espacio abajo (o a la derecha) para texto."),
        ("Nueva lista", "La lista se guarda automáticamente y se recupera al abrir la "
                        "aplicación. Para comenzar una nueva, use «Vaciar»: los archivos no "
                        "se borran."),
        ("Audio", "Los videos se generan con el volumen nivelado: en la consola, el fader "
                  "queda siempre en 0 dB."),
        ("Vista previa de videos", "Pase el puntero sobre un video para ver su contenido. "
                                   "Con doble clic se abre en el reproductor de la PC."),
        ("Errores", "Pase el puntero sobre el elemento marcado para ver qué ocurrió. «No "
                    "disponible» generalmente indica que el NAS o un disco está desconectado."),
        ("Opciones avanzadas", "El botón ⚙ contiene opciones que rara vez es necesario "
                               "cambiar. Los cambios se restablecen al cerrar la aplicación."),
    ]),
    ("Grabadora", [
        ("Función", "Guarda en el NAS lo grabado con la grabadora y deja el disco vacío "
                    "para la próxima grabación."),
        ("Reconocimiento del disco", "El NAS reconoce automáticamente los discos que "
                                     "contienen únicamente grabaciones de la grabadora. Si "
                                     "un disco tiene otros archivos, aparece como «Otro "
                                     "disco» y no se copia automáticamente."),
        ("Copiar el disco", "Conecte el disco de la grabadora al NAS. Cuando aparezca en la "
                            "lista, haga clic en Copiar y elija la carpeta."),
        ("Seguridad", "La copia nunca modifica el disco ni reemplaza archivos que ya "
                      "estén en el NAS."),
        ("Formatear", "Borra todo el contenido del disco para que la grabadora vuelva a "
                      "usarlo. Solo está disponible cuando la copia está completa y "
                      "verificada."),
        ("Copia automática", "Si está activada, los discos de la grabadora se copian, se "
                             "verifican y se borran en cuanto se conectan al NAS."),
    ]),
]
SHORTCUTS = [(f"{_MOD}+1 a 4", "Cambiar de módulo"), ("F1", "Abrir o cerrar la ayuda"),
             ("Supr", "Quitar de Convertir"),
             (f"{_MOD}+Enter", "Enviar a Convertir (en Buscar)")]


class _Parrafo(QLabel):
    """Texto de varias líneas que pide el alto que necesita para su ancho real.
    Dentro de un panel con desplazamiento, Qt calcula el alto con otro ancho y
    los párrafos se cortaban en tres líneas."""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        alto = self.heightForWidth(self.width())
        if alto > 0 and alto != self.minimumHeight():
            self.setMinimumHeight(alto)


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
        icons.set_icon(close, "x", 16, "text_2")
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
        t = _Parrafo(text)
        t.setWordWrap(True)
        t.setObjectName("help_text")
        t.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._body_lay.addWidget(t)
