"""Pantalla «Conexión al catálogo» (docs/carpetas-y-conexion.md).

Los datos de `catalogo.ini`: servidor, clave y dónde está la carpeta multimedia
del NAS en esta PC; puerto, base y usuario plegados en «Otros datos» porque no
cambian nunca. «Probar» no modifica nada (catalog/conexion.py). «Guardar»
escribe el archivo (appdata.guardar_config_catalogo) y la app vuelve a
conectarse con los datos nuevos.

Se abre desde el ⚙ de Catalogar, desde el aviso de conexión de Buscar y
Catalogar, y sola al arrancar si esta PC no tiene configuración.
"""

import dataclasses

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from ... import appdata
from ...catalog import conexion
from ...catalog.db import CatalogoNoConfigurado, ConfigCatalogo
from ...workers import EngineWorker

RAIZ_PREDETERMINADA = r"\\truenas\multimedia"


class ConexionCatalogo(QDialog):
    guardado = pyqtSignal(str)          # dónde quedó el archivo

    def __init__(self, parent=None, cfg: ConfigCatalogo | None = None,
                 origen: tuple[str, str | None] = ("ninguno", None),
                 guardar=None, probar=None, destino: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Conexión al catálogo")
        self.setMinimumWidth(560)
        self._origen = origen
        self._guardar = guardar or appdata.guardar_config_catalogo
        self._probar = probar or conexion.probar
        self._worker: EngineWorker | None = None
        cfg = cfg or ConfigCatalogo(servidor="", raiz=RAIZ_PREDETERMINADA)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)
        ayuda = QLabel("Con estos datos la aplicación encuentra el catálogo del NAS y abre "
                       "las fotos y videos desde esta PC.")
        ayuda.setObjectName("muted")
        ayuda.setWordWrap(True)
        lay.addWidget(ayuda)
        if origen[0] == "entorno":
            aviso = QLabel("Esta PC usa la configuración de desarrollo (variables "
                           "CDLM_CATALOGO_*). Se puede probar, pero los cambios no se guardan.")
            aviso.setObjectName("nonstandard_text")
            aviso.setWordWrap(True)
            lay.addWidget(aviso)

        form = QFormLayout()
        form.setSpacing(8)
        self.txt_servidor = QLineEdit(cfg.servidor)
        self.txt_servidor.setPlaceholderText("Por ejemplo, 192.168.1.224")
        form.addRow("Servidor", self.txt_servidor)

        self.txt_clave = QLineEdit(cfg.clave)
        self.txt_clave.setEchoMode(QLineEdit.EchoMode.Password)
        self.btn_ver = QPushButton("Ver")
        self.btn_ver.setCheckable(True)
        self.btn_ver.toggled.connect(self._ver_clave)
        fila = QHBoxLayout()
        fila.addWidget(self.txt_clave, 1)
        fila.addWidget(self.btn_ver)
        form.addRow("Clave", fila)

        self.txt_raiz = QLineEdit(cfg.raiz)
        self.txt_raiz.setPlaceholderText(RAIZ_PREDETERMINADA)
        self.btn_raiz = QPushButton("Elegir…")
        self.btn_raiz.clicked.connect(self._elegir_raiz)
        fila = QHBoxLayout()
        fila.addWidget(self.txt_raiz, 1)
        fila.addWidget(self.btn_raiz)
        form.addRow("Carpeta multimedia\nen esta PC", fila)

        # Puerto, base y usuario no cambian nunca: plegados, en la misma grilla
        self.btn_otros = QPushButton("Otros datos")
        self.btn_otros.setCheckable(True)
        self.btn_otros.toggled.connect(self._mostrar_otros)
        fila = QHBoxLayout()
        fila.addWidget(self.btn_otros)
        fila.addStretch()
        form.addRow("", fila)
        self.num_puerto = QSpinBox()
        self.num_puerto.setRange(1, 65535)
        self.num_puerto.setValue(cfg.puerto)
        self.txt_base = QLineEdit(cfg.base)
        self.txt_usuario = QLineEdit(cfg.usuario)
        self._otros = [self.num_puerto, self.txt_base, self.txt_usuario]
        form.addRow("Puerto", self.num_puerto)
        form.addRow("Base", self.txt_base)
        form.addRow("Usuario", self.txt_usuario)
        self._form = form
        for w in self._otros:
            form.setRowVisible(w, False)
        lay.addLayout(form)

        self.lbl_resultado = QLabel("")
        self.lbl_resultado.setWordWrap(True)
        self.lbl_resultado.setVisible(False)
        lay.addWidget(self.lbl_resultado)

        destino = destino if destino is not None else appdata.ruta_para_guardar_config()
        if destino:
            donde = QLabel(f"Se guarda en {destino}")
            donde.setObjectName("muted")
            donde.setWordWrap(True)
            lay.addWidget(donde)

        botones = QDialogButtonBox()
        self.btn_probar = botones.addButton("Probar", QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_probar.clicked.connect(self.probar)
        botones.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(
            self.reject)
        self.btn_guardar = botones.addButton("Guardar", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_guardar.setObjectName("btn_primary")
        self.btn_guardar.clicked.connect(self.guardar)
        self.btn_guardar.setEnabled(origen[0] != "entorno")
        lay.addWidget(botones)

    # ------------------------------------------------------------- datos
    def config(self) -> ConfigCatalogo:
        return ConfigCatalogo(
            servidor=self.txt_servidor.text().strip(), clave=self.txt_clave.text(),
            usuario=self.txt_usuario.text().strip() or "catalogo",
            base=self.txt_base.text().strip() or "catalogo",
            puerto=self.num_puerto.value(),
            raiz=self.txt_raiz.text().strip() or RAIZ_PREDETERMINADA)

    def _ver_clave(self, ver: bool):
        self.txt_clave.setEchoMode(QLineEdit.EchoMode.Normal if ver
                                   else QLineEdit.EchoMode.Password)
        self.btn_ver.setText("Ocultar" if ver else "Ver")

    def _mostrar_otros(self, ver: bool):
        for w in self._otros:
            self._form.setRowVisible(w, ver)
        self._ajustar()

    def _elegir_raiz(self):
        ruta = QFileDialog.getExistingDirectory(self, "Elija la carpeta multimedia del NAS",
                                                self.txt_raiz.text())
        if ruta:
            self.txt_raiz.setText(ruta)

    def _resultado(self, texto: str, bien: bool | None = None):
        self.lbl_resultado.setObjectName("" if bien is None else
                                         "muted" if bien else "nonstandard_text")
        self.lbl_resultado.style().unpolish(self.lbl_resultado)
        self.lbl_resultado.style().polish(self.lbl_resultado)
        self.lbl_resultado.setText(texto)
        self.lbl_resultado.setVisible(bool(texto))
        self._ajustar()

    def _ajustar(self):
        """La ventana crece con el texto del resultado (que ocupa más o menos
        renglones) en lugar de aplastar los campos."""
        self.layout().activate()
        self.resize(self.width(), max(self.height(), self.layout().totalHeightForWidth(self.width())))

    # ------------------------------------------------------------- probar
    def probar(self):
        cfg = self.config()
        if not cfg.servidor:
            self._resultado("Escriba el servidor del catálogo.", False)
            return
        self.btn_probar.setEnabled(False)
        self._resultado("Probando la conexión…")
        probar = self._probar
        w = EngineWorker(lambda _cb: probar(cfg))
        w.finished_ok.connect(self._probado)
        w.fallo.connect(self._fallo_prueba)
        self._worker = w
        w.start()

    def _probado(self, r):
        self._fin_prueba()
        bien, texto = r
        self._resultado(texto, bien)

    def _fallo_prueba(self, _e):
        self._fin_prueba()
        self._resultado("No se pudo probar la conexión. Intente nuevamente.", False)

    def _fin_prueba(self):
        if self._worker is not None:
            self._worker.wait()
        self._worker = None
        self.btn_probar.setEnabled(True)

    # ------------------------------------------------------------- guardar
    def guardar(self):
        cfg = self.config()
        if not cfg.servidor:
            self._resultado("Escriba el servidor del catálogo.", False)
            return
        try:
            ruta = self._guardar(cfg)
        except OSError:
            self._resultado("No se pudo guardar la configuración en esta PC. Comuníquese con "
                            "el responsable de la aplicación.", False)
            return
        self.guardado.emit(ruta)
        self.accept()

    def done(self, r):
        if self._worker is not None:
            self._worker.wait()
        super().done(r)


def abrir_conexion(parent=None) -> bool:
    """Abre la pantalla con la configuración actual de esta PC. Al guardar, la
    app se vuelve a conectar. Devuelve True si se guardó."""
    from ..servicio_catalogo import servicio
    try:
        cfg = appdata.config_catalogo()
    except CatalogoNoConfigurado:
        cfg = _lo_que_se_pueda_leer()
    dlg = ConexionCatalogo(parent, cfg, appdata.origen_config_catalogo())
    guardado = []
    dlg.guardado.connect(lambda _ruta: guardado.append(True))
    dlg.exec()
    if guardado:
        servicio().reconectar()
    return bool(guardado)


def _lo_que_se_pueda_leer() -> ConfigCatalogo | None:
    """Un `catalogo.ini` con un dato malo (sin servidor, puerto raro): se
    muestra lo que tenga para corregirlo, en lugar de empezar de cero."""
    import configparser
    origen, ruta = appdata.origen_config_catalogo()
    if origen != "archivo":
        return None
    ini = configparser.ConfigParser(interpolation=None)
    try:
        ini.read(ruta, encoding="utf-8")
        sec = ini["catalogo"]
    except (configparser.Error, KeyError, OSError):
        return None
    base = ConfigCatalogo(servidor="", raiz=RAIZ_PREDETERMINADA)
    try:
        puerto = int(sec.get("puerto", base.puerto))
    except ValueError:
        puerto = base.puerto
    return dataclasses.replace(
        base, servidor=sec.get("servidor", "").strip(), clave=sec.get("clave", ""),
        usuario=sec.get("usuario", base.usuario).strip(), base=sec.get("base", base.base).strip(),
        puerto=puerto, raiz=sec.get("raiz", base.raiz).strip())
