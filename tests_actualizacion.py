"""Aviso de versión nueva y actualización del .exe (pedido del 23/11/2026).

Corre también en el CI de GitHub en Windows: ahí `test_reemplazo_real_en_windows`
prueba el .cmd que reemplaza un .exe que todavía está abierto.
"""

import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from convertir3x4 import actualizacion as act


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setOrganizationName("CDLM-tests")             # no toca la configuración real
    a.setApplicationName("Convertir3x4-tests")
    return a


def _respuesta(version="v0.8.1", **extra):
    return {
        "tag_name": version, "html_url": f"https://github.com/x/releases/tag/{version}",
        "draft": False, "prerelease": False,
        "assets": [
            {"name": f"Convertir3x4-{version}-macos.dmg", "browser_download_url": "https://m",
             "size": 10, "digest": "sha256:AA"},
            {"name": f"Convertir3x4-{version}-windows.exe", "browser_download_url": "https://w",
             "size": 20, "digest": "sha256:BB"},
            {"name": f"Convertir3x4-{version}-linux", "browser_download_url": "https://l",
             "size": 30},
        ], **extra}


# ---------------------------------------------------------------- versiones

def test_numeros_y_comparacion():
    assert act.numeros("v0.8.1") == (0, 8, 1)
    assert act.numeros("0.10.0") == (0, 10, 0)
    assert act.numeros("dev") is None and act.numeros("dev-1a2b3c4") is None
    assert act.hay_nueva("v0.8.0", "v0.8.1")
    assert act.hay_nueva("v0.8.9", "v0.10.0")            # no es orden alfabético
    assert not act.hay_nueva("v0.8.1", "v0.8.1")
    assert not act.hay_nueva("v0.9.0", "v0.8.1")
    assert not act.hay_nueva("dev", "v0.8.1")             # en desarrollo nunca


def test_elige_el_archivo_de_cada_sistema():
    w = act.leer_publicada(_respuesta(), "windows")
    assert (w.version, w.url, w.tamano, w.sha256) == ("v0.8.1", "https://w", 20, "bb")
    assert act.leer_publicada(_respuesta(), "macos").url == "https://m"
    linux = act.leer_publicada(_respuesta(), "linux")
    assert linux.url == "https://l" and linux.sha256 is None
    assert act.leer_publicada(_respuesta(prerelease=True), "windows") is None
    assert act.leer_publicada(_respuesta(draft=True), "windows") is None
    assert act.leer_publicada({"tag_name": "v1.0.0", "assets": []}, "windows").url is None


def test_sin_internet_no_hay_aviso(monkeypatch):
    def falla(*a, **k):
        raise OSError("sin red")
    monkeypatch.setattr(act.urllib.request, "urlopen", falla)
    assert act.ultima() is None


def test_se_instala_sola_solo_en_windows_con_el_exe(tmp_path):
    p = act.leer_publicada(_respuesta(), "windows")
    exe = str(tmp_path / "Convertir3x4.exe")
    assert act.se_instala_sola(p, "windows", exe)
    assert not act.se_instala_sola(p, "macos", exe)
    assert not act.se_instala_sola(p, "windows", "")                 # en desarrollo
    sin_archivo = act.Publicada("v0.8.1", "https://p", None)
    assert not act.se_instala_sola(sin_archivo, "windows", exe)


# ---------------------------------------------------------------- descarga

def _publicada_local(tmp_path, contenido=b"exe nuevo" * 1000, sha=None, tamano=None):
    origen = tmp_path / "publicado.exe"
    origen.write_bytes(contenido)
    return act.Publicada("v0.8.1", "https://p", origen.as_uri(),
                         len(contenido) if tamano is None else tamano,
                         sha or hashlib.sha256(contenido).hexdigest())


def test_descarga_verificada(tmp_path):
    p = _publicada_local(tmp_path)
    destino = str(tmp_path / "Convertir3x4.exe.nuevo")
    avance = []
    assert act.descargar(p, destino, progreso=lambda h, t: avance.append((h, t))) == destino
    assert pathlib.Path(destino).read_bytes() == b"exe nuevo" * 1000
    assert avance and avance[-1][0] == avance[-1][1]
    assert not os.path.exists(destino + ".parcial")


@pytest.mark.parametrize("cambio", [{"sha": "0" * 64}, {"tamano": 5}])
def test_descarga_que_no_coincide_no_deja_nada(tmp_path, cambio):
    p = _publicada_local(tmp_path, **cambio)
    destino = str(tmp_path / "Convertir3x4.exe.nuevo")
    with pytest.raises(act.ErrorDeActualizacion):
        act.descargar(p, destino)
    assert not os.path.exists(destino) and not os.path.exists(destino + ".parcial")


def test_limpia_un_nuevo_que_quedo(tmp_path):
    exe = tmp_path / "Convertir3x4.exe"
    pathlib.Path(act.ruta_nueva(str(exe))).write_bytes(b"x")
    act.limpiar_restos(str(exe))
    assert not os.path.exists(act.ruta_nueva(str(exe)))


def test_script_de_reemplazo():
    s = act.SCRIPT_REEMPLAZO
    s.encode("ascii")                                     # rutas por argumento, no en el texto
    assert "\r\n" in s and "\n" not in s.replace("\r\n", "")
    assert '"%~1" "%~2"' in s and 'start "" "%~2"' in s


@pytest.mark.skipif(os.name != "nt", reason="el .cmd solo corre en Windows")
def test_reemplazo_real_en_windows(tmp_path):
    """Un .exe abierto no se puede reemplazar: el .cmd espera a que se cierre."""
    sistema = pathlib.Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    exe = tmp_path / "Convertir á 3x4.exe"                # con tilde, como una carpeta real
    shutil.copy(sistema / "PING.EXE", exe)
    nuevo = pathlib.Path(act.ruta_nueva(str(exe)))
    shutil.copy(sistema / "HOSTNAME.EXE", nuevo)
    esperado = nuevo.read_bytes()
    abierto = subprocess.Popen([str(exe), "-n", "4", "127.0.0.1"],
                               stdout=subprocess.DEVNULL)           # ~3 s abierto
    try:
        time.sleep(0.5)
        act.lanzar_reemplazo(act.preparar_reemplazo(str(nuevo), str(exe), abrir=False),
                             str(tmp_path))
        time.sleep(1)
        assert nuevo.exists(), "no tenía que reemplazarlo mientras está abierto"
        abierto.wait(timeout=20)
        limite = time.time() + 30
        while nuevo.exists() and time.time() < limite:
            time.sleep(0.5)
        assert not nuevo.exists()
        assert exe.read_bytes() == esperado
    finally:
        abierto.kill()


# ---------------------------------------------------------------- interfaz

def test_aviso_ofrece_solo_si_es_mas_nueva(app):
    from convertir3x4.ui.widgets.aviso_actualizacion import AvisoActualizacion
    a = AvisoActualizacion(version_actual="v0.8.0")
    a.recibir(None)
    assert a.isHidden()
    a.recibir(act.Publicada("v0.8.0", "https://p", "https://w"))
    assert a.isHidden()
    a.recibir(act.Publicada("v0.8.1", "https://p", "https://w"))
    assert not a.isHidden() and "v0.8.1" in a.lbl.text()
    # Acá no es el .exe empaquetado: se ofrece descargar, no instalar solo
    assert a.btn_actualizar.text() == "Descargar"
    a.btn_luego.click()
    assert a.isHidden()
    a.recibir(act.Publicada("v0.8.1", "https://p", "https://w"))
    assert a.isHidden()                                   # «Más tarde» vale para esa versión
    a.recibir(act.Publicada("v0.8.2", "https://p", "https://w"))
    assert not a.isHidden()


def test_en_desarrollo_no_consulta(app, monkeypatch):
    from convertir3x4.ui.widgets.aviso_actualizacion import AvisoActualizacion
    monkeypatch.setattr(act, "ultima", lambda: pytest.fail("no tenía que consultar"))
    a = AvisoActualizacion(version_actual="dev")
    a.empezar()
    assert a._worker is None and not a._timer.isActive()


def test_descargada_se_instala_al_cerrar(app, monkeypatch, tmp_path):
    from convertir3x4.ui.widgets.aviso_actualizacion import AvisoActualizacion
    lanzadas = []
    monkeypatch.setattr(act, "lanzar_reemplazo", lambda orden, carpeta: lanzadas.append(
        (orden, carpeta)))
    a = AvisoActualizacion(version_actual="v0.8.0")
    a.recibir(act.Publicada("v0.8.1", "https://p", "https://w"))
    avisos = []
    a.lista.connect(lambda: avisos.append(1))
    exe = str(tmp_path / "Convertir3x4.exe")
    a.descargada(exe + ".nuevo", exe)
    assert avisos == [1] and a.btn_actualizar.isHidden()
    a.cierre_cancelado()
    assert "cuando cierre" in a.lbl.text()
    a.instalar_al_cerrar()
    assert len(lanzadas) == 1
    orden, carpeta = lanzadas[0]
    assert orden[-2:] == [exe + ".nuevo", exe] and carpeta == str(tmp_path)
    os.remove(orden[2])                                   # el .cmd temporal
    a.instalar_al_cerrar()
    assert len(lanzadas) == 1                             # una sola vez


def test_la_ventana_tiene_el_aviso(app):
    from convertir3x4.app import MainWindow
    w = MainWindow()
    assert w.aviso_version.isHidden()
    w.close()


def test_catalogo_mas_nuevo_busca_la_actualizacion_enseguida(app, monkeypatch):
    from convertir3x4.app import MainWindow
    from convertir3x4.catalog.db import CatalogoIncompatible
    from convertir3x4.ui.servicio_catalogo import servicio
    revisadas = []
    w = MainWindow()
    monkeypatch.setattr(w.aviso_version, "revisar", lambda: revisadas.append(1))
    servicio().estado_cambiado.emit("error", "El NAS no responde.")
    assert revisadas == []
    servicio().estado_cambiado.emit("error", CatalogoIncompatible.MENSAJE)
    assert revisadas == [1]
    w.close()
