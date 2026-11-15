"""Tests del módulo Discos: textos/acciones de la UI y el cliente de panel-copias.

La prueba de punta a punta levanta el panel-copias.py real (repo CDLM-Freenas) con
lsblk, systemctl y copiar-disco.py falsos. Se saltea si ese repo no está a mano;
la ruta se puede cambiar con CDLM_FREENAS_REPO.
"""

import json
import os
import socket
import stat
import subprocess
import sys
import textwrap
import time

import pytest

from convertir3x4.nas.copias import (
    PanelClient, PanelError, PanelSinConexion, acciones_para, resumir,
)

FREENAS = os.environ.get("CDLM_FREENAS_REPO",
                         os.path.expanduser("~/Documents/Agents/CDLM-Freenas"))
PANEL = os.path.join(FREENAS, "scripts", "panel-copias.py")
UUID = "23B8-12F6"


# ------------------------------------------------------------------ lógica pura
def test_resumir_copiando_calcula_porcentaje_y_eta():
    r = resumir({"fase": "COPIANDO", "corriendo": True, "copiado_gb": 250, "falta_gb": 750,
                 "mb_s": 175, "eta_min": 78, "errores": 0})
    assert r.porcentaje == 25
    assert r.tono == "activo"
    assert "250 GB de 1000 GB" in r.detalle
    assert "falta 1 h 18 min aprox." in r.detalle
    assert "MB/s" not in r.detalle          # la velocidad no le sirve al operador


@pytest.mark.parametrize("copia, disco", [
    (None, {"admitido": False, "fs": "ntfs"}),
    ({"fase": "CON_ERRORES", "errores": 3}, None),
    ({"fase": "COPIANDO", "corriendo": True, "copiado_gb": 1, "falta_gb": 2, "mb_s": 90}, None),
])
def test_resumir_sin_vocabulario_tecnico(copia, disco):
    r = resumir(copia, None, disco)
    texto = (r.titulo + " " + r.detalle).lower()
    for palabra in ("ntfs", "exfat", "ddrescue", "uuid", "mb/s", "netbird", "/mnt/"):
        assert palabra not in texto, (palabra, texto)


def test_resumir_terminado_corriendo_es_auto_formateando():
    r = resumir({"fase": "TERMINADO", "corriendo": True})
    assert r.titulo == "Verificando y formateando"


def test_resumir_disco_formateado_con_uuid_nuevo():
    r = resumir(None, None, {"admitido": True, "formateado": {"fecha": "2026-11-15 11:38:12",
                                                              "archivos": 39}})
    assert r.titulo == "Vacío" and "39 archivos" in r.detalle


def test_resumir_ntfs_y_tarea_fallida():
    assert resumir(None, None, {"admitido": False, "fs": "ntfs"}).tono == "aviso"
    r = resumir({"fase": "TERMINADO"}, {"accion": "formatear", "estado": "error",
                                         "mensaje": "ERROR: algo"})
    assert r.titulo == "No se formateó" and r.tono == "error"


@pytest.mark.parametrize("copia,tarea,esperado", [
    (None, None, {"copiar"}),
    ({"fase": "COPIANDO", "corriendo": True}, None, {"detener"}),
    ({"fase": "TERMINADO"}, None, {"copiar", "verificar", "formatear"}),
    ({"fase": "CON_ERRORES"}, None, {"copiar", "reintentar"}),
    ({"fase": "TERMINADO"}, {"estado": "corriendo"}, set()),
])
def test_acciones_para(copia, tarea, esperado):
    assert acciones_para({"admitido": True}, copia, tarea) == esperado


def test_acciones_para_ntfs_no_ofrece_nada():
    assert acciones_para({"admitido": False}, None, None) == set()


def test_cliente_sin_conexion_prueba_todas_las_urls():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    puerto = s.getsockname()[1]
    s.close()   # puerto cerrado: conexión rechazada al instante
    c = PanelClient([f"http://127.0.0.1:{puerto}", f"http://127.0.0.1:{puerto}"], "x", timeout=1)
    with pytest.raises(PanelSinConexion):
        c.estado()
    assert c.url_activa is None


# ------------------------------------------------------------------ punta a punta
def _ejecutable(ruta, cuerpo):
    with open(ruta, "w") as f:
        f.write(cuerpo)
    os.chmod(ruta, os.stat(ruta).st_mode | stat.S_IEXEC)


@pytest.fixture
def panel(tmp_path):
    if not os.path.exists(PANEL):
        pytest.skip(f"no está {PANEL}")
    raiz = tmp_path / "_copias"
    (raiz / UUID).mkdir(parents=True)
    (raiz / UUID / "estado.json").write_text(json.dumps({
        "fase": "TERMINADO", "actualizado": "2026-11-15 11:37:00", "archivos": 39,
        "destino": "/mnt/tank/multimedia/Historial/Reuniones", "modelo": "KINGSTON", "serie": "S1"}))
    (raiz / UUID / "copia.log").write_text("linea 1\nlinea 2\n==== fin OK\n")
    binario = tmp_path / "bin"
    binario.mkdir()
    lsblk = {"blockdevices": [
        {"name": "sda", "type": "disk", "tran": "sata", "size": 3e12, "children": [
            {"name": "sda1", "path": "/dev/sda1", "type": "part", "fstype": "zfs_member", "uuid": "Z"}]},
        {"name": "sdh", "type": "disk", "tran": "usb", "size": 960e9, "model": "KINGSTON",
         "serial": "S1", "children": [
             {"name": "sdh2", "path": "/dev/sdh2", "type": "part", "size": 959e9, "fstype": "exfat",
              "label": "Media", "uuid": UUID}]}]}
    _ejecutable(binario / "lsblk", f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(lsblk)}\nEOF\n")
    _ejecutable(binario / "systemctl", "#!/bin/sh\n[ \"$1\" = is-active ] && "
                "for u in \"$@\"; do [ \"$u\" = is-active ] || echo inactive; done; exit 3\n")
    llamadas = tmp_path / "llamadas.jsonl"
    falso = tmp_path / "copiar-disco.py"
    falso.write_text(textwrap.dedent(f"""
        import json, os, sys
        reg = {{"args": sys.argv[1:], "invocation": os.environ.get("INVOCATION_ID"),
               "tty": sys.stdin.isatty()}}
        if sys.argv[1:2] == ["formatear"]:
            reg["respuesta"] = input("escribir FORMATEAR y Enter: ")
        open({str(llamadas)!r}, "a").write(json.dumps(reg) + "\\n")
        if sys.argv[1:2] == ["formatear"]:
            print("Listo: /dev/sdh2 quedo vacio" if reg["respuesta"] == "FORMATEAR" else "Cancelado")
        else:
            print("Copia lanzada como copia-disco-X.\\n  Estado:   ...")
    """))
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    puerto = s.getsockname()[1]
    s.close()
    env = dict(os.environ, PATH=f"{binario}:{os.environ['PATH']}", CDLM_COPIAS_RAIZ=str(raiz),
               CDLM_COPIAR_DISCO=str(falso), CDLM_PANEL_PUERTO=str(puerto),
               CDLM_DESTINOS_RAIZ=str(tmp_path / "multimedia"),
               INVOCATION_ID="simula-systemd")
    proc = subprocess.Popen([sys.executable, "-I", PANEL], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{puerto}"
    for _ in range(50):
        if (raiz / "panel.token").exists():
            try:
                socket.create_connection(("127.0.0.1", puerto), timeout=0.2).close()
                break
            except OSError:
                pass
        time.sleep(0.1)
    token = (raiz / "panel.token").read_text().strip()
    yield {"url": url, "token": token, "raiz": raiz, "llamadas": llamadas,
           "multimedia": tmp_path / "multimedia"}
    proc.terminate()
    proc.wait(5)


def _llamadas(p):
    return [json.loads(l) for l in p["llamadas"].read_text().splitlines()] \
        if p["llamadas"].exists() else []


def test_panel_token_invalido(panel):
    with pytest.raises(PanelError, match="no reconoce a esta PC"):
        PanelClient([panel["url"]], "otro").estado()


def test_panel_estado_lista_solo_usb(panel):
    c = PanelClient([panel["url"]], panel["token"])
    est = c.estado()
    assert [d["uuid"] for d in est["discos"]] == [UUID]
    assert est["discos"][0]["admitido"] and est["discos"][0]["etiqueta"] == "Media"
    assert est["copias"][0]["fase"] == "TERMINADO" and est["copias"][0]["corriendo"] is False
    assert est["auto"] is True
    assert "==== fin OK" in c.log(UUID)


def test_panel_copiar_valida_destino_y_no_pasa_invocation_id(panel):
    c = PanelClient([panel["url"]], panel["token"])
    with pytest.raises(PanelError, match="dentro de"):
        c.copiar(UUID, "/etc")
    with pytest.raises(PanelError, match="no está enchufado"):
        c.copiar("FFFF-0000", str(panel["multimedia"] / "X"))
    c.copiar(UUID, str(panel["multimedia"] / "Reuniones"))
    (llamada,) = _llamadas(panel)
    assert llamada["args"] == [f"UUID={UUID}", str(panel["multimedia"] / "Reuniones")]
    # Si le llegara INVOCATION_ID, copiar-disco.py copiaría dentro del servicio.
    assert llamada["invocation"] is None


def test_panel_formatear_confirma_por_terminal(panel):
    c = PanelClient([panel["url"]], panel["token"])
    with pytest.raises(PanelError, match="no es el que se confirmó"):
        c.formatear(UUID, "OTRA-SERIE")
    c.formatear(UUID, "S1")
    for _ in range(50):
        t = c.estado()["tareas"].get(UUID, {})
        if t.get("estado") != "corriendo":
            break
        time.sleep(0.1)
    assert t["estado"] == "ok", t
    (llamada,) = _llamadas(panel)
    assert llamada["args"] == ["formatear", f"UUID={UUID}"]
    assert llamada["tty"] and llamada["respuesta"] == "FORMATEAR"


def test_panel_auto_on_off(panel):
    c = PanelClient([panel["url"]], panel["token"])
    assert c.set_auto(False) is False
    assert (panel["raiz"] / "AUTO_DESACTIVADO").exists()
    assert c.estado()["auto"] is False
    assert c.set_auto(True) is True
    assert not (panel["raiz"] / "AUTO_DESACTIVADO").exists()


@pytest.mark.parametrize("entrada, salida", [
    ("2026-11-15", "15/11/2026"),
    ("2026-11-15 19:42:10", "15/11/2026 19:42"),
    ("", ""),
    ("ayer", "ayer"),
])
def test_fechas_como_las_lee_el_operador(entrada, salida):
    from convertir3x4.nas.copias import fecha
    assert fecha(entrada) == salida


def test_detalle_tecnico_escondido_por_defecto():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from convertir3x4.ui.tabs.tab_discos import DiscosTab
    t = DiscosTab()
    t._timer.stop()
    assert not t.txt_log.isVisibleTo(t)
    t.btn_log.setChecked(True)
    assert t.txt_log.isVisibleTo(t)
    t.deleteLater()
