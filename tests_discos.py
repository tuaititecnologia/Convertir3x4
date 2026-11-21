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
        if sys.argv[1:2] == ["contenido"]:
            print(json.dumps({{"ocupado": False, "total_archivos": 2, "total_bytes": 8200000000,
                              "solo_grabadora": True, "grabaciones": 2, "desde": 1763247000,
                              "hasta": 1763250600, "errores": 0, "carpetas": [],
                              "archivos": [{{"nombre": "HyperDeck_0001.mov", "bytes": 4100000000, "fecha": 1763247000}},
                                           {{"nombre": "HyperDeck_0002.mov", "bytes": 4100000000, "fecha": 1763250600}}]}}))
            sys.exit(0)
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
           "multimedia": tmp_path / "multimedia", "lsblk": binario / "lsblk", "lsblk_json": lsblk}
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


@pytest.mark.parametrize("disco, tipo", [
    ({"nota_auto": "x (UUID A): disco de HyperDeck, 39 archivos, 120.5 GB. Lanzo copia a /mnt/x"}, "grabadora"),
    ({"nota_auto": "x (UUID A): vacio, listo para grabar"}, "grabadora"),
    ({"formateado": {"fecha": "2026-11-15"}}, "grabadora"),
    ({"nota_auto": "x (UUID A): no es un disco de HyperDeck (3 archivos ajenos, por ejemplo 'a.jpg')"}, "otro"),
    ({"nota_auto": "x (UUID A): es ntfs, no exFAT; no es de la HyperDeck"}, "otro"),
    ({"nota_auto": "sdh2: ignorado, el modo automatico esta apagado (/x)"}, None),
    ({}, None),
])
def test_tipo_disco_segun_lo_que_reconoce_el_nas(disco, tipo):
    from convertir3x4.nas.copias import tipo_disco
    assert tipo_disco(disco) == tipo


@pytest.mark.parametrize("nota", [
    "x (UUID A): no es un disco de HyperDeck (3 archivos ajenos, por ejemplo 'a.jpg'); se deja para copiar a mano",
    "x (UUID A): es ntfs, no exFAT; no es de la HyperDeck",
    "sdh2: ignorado, el modo automatico esta apagado (/mnt/tank/ingreso/_copias/AUTO_APAGADO)",
    "x (UUID A): esta montado en otro lado, no lo toco",
    "x (UUID A): vacio, listo para grabar",
    "x (UUID A): algo raro de mount",
])
def test_nota_sin_vocabulario_tecnico(nota):
    from convertir3x4.nas.copias import nota_operador
    texto = nota_operador({"nota_auto": nota}).lower()
    assert texto
    for palabra in ("hyperdeck", "exfat", "ntfs", "uuid", "/mnt/", "montado", "mount"):
        assert palabra not in texto, (palabra, texto)



# ---------------------------------------------------------------- qué tiene el disco
def test_panel_contenido_se_lee_una_sola_vez(panel):
    c = PanelClient([panel["url"]], panel["token"])
    for _ in range(3):
        datos = c.contenido(UUID)
    assert datos["grabaciones"] == 2 and datos["solo_grabadora"]
    contenidos = [x for x in _llamadas(panel) if x["args"][:1] == ["contenido"]]
    assert len(contenidos) == 1      # el NAS lo monta una vez, no cada vez que se pide


def test_panel_contenido_se_vuelve_a_leer_al_reenchufar(panel):
    c = PanelClient([panel["url"]], panel["token"])
    c.contenido(UUID)
    _ejecutable(panel["lsblk"], "#!/bin/sh\necho '{\"blockdevices\": []}'\n")   # se desenchufa
    c.estado()
    _ejecutable(panel["lsblk"], f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(panel['lsblk_json'])}\nEOF\n")
    c.estado()
    c.contenido(UUID)
    contenidos = [x for x in _llamadas(panel) if x["args"][:1] == ["contenido"]]
    assert len(contenidos) == 2


def test_panel_contenido_de_disco_no_enchufado(panel):
    c = PanelClient([panel["url"]], panel["token"])
    with pytest.raises(PanelError):
        c.contenido("0000-0000")


def _copiar_disco():
    import importlib.util
    ruta = os.path.join(FREENAS, "scripts", "copiar-disco.py")
    if not os.path.exists(ruta):
        pytest.skip(f"no está {ruta}")
    spec = importlib.util.spec_from_file_location("copiar_disco", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resumen_raiz_disco_de_la_grabadora():
    cd = _copiar_disco()
    r = cd.resumen_raiz([("HyperDeck_0001.mov", 100, 1000.0), ("HyperDeck_0002.mov", 200, 5000.0),
                         ("HyperDeck_0002.mcc", 1, 5000.0)], [])
    assert r["solo_grabadora"] and r["grabaciones"] == 2          # el .mcc no cuenta
    assert (r["desde"], r["hasta"], r["total_bytes"]) == (1000, 5000, 301)


def test_resumen_raiz_otro_disco_agrupa_carpetas():
    cd = _copiar_disco()
    sep = os.sep
    r = cd.resumen_raiz([("IMG_1.jpg", 10, 1.0), (f"DCIM{sep}a.jpg", 5, 1.0),
                         (f"DCIM{sep}sub{sep}b.jpg", 5, 1.0)], [{"origen": "x"}])
    assert not r["solo_grabadora"]
    assert [a["nombre"] for a in r["archivos"]] == ["IMG_1.jpg"]
    assert r["carpetas"] == [{"nombre": "DCIM", "archivos": 2, "bytes": 10}]
    assert r["errores"] == 1


@pytest.mark.parametrize("datos, esperado", [
    ({"total_archivos": 0}, "Está vacío."),
    ({"ocupado": True}, ""),
    ({"total_archivos": 214, "total_bytes": 6.4e9, "archivos": [{"nombre": "IMG_0001.jpg"}],
      "carpetas": [{"nombre": "DCIM"}]}, "Tiene 214 archivos (6,4 GB), por ejemplo IMG_0001.jpg, y la carpeta DCIM."),
    ({"total_archivos": 3, "total_bytes": 9e9, "archivos": [], "carpetas": [{"nombre": "Culto"}]},
     "Tiene 3 archivos (9,0 GB) en la carpeta Culto."),
])
def test_resumir_contenido(datos, esperado):
    from convertir3x4.nas.copias import resumir_contenido
    assert resumir_contenido(datos) == esperado


def test_resumir_contenido_grabadora_con_fechas():
    from convertir3x4.nas.copias import resumir_contenido
    t1 = time.mktime((2026, 11, 12, 19, 30, 0, 0, 0, -1))
    t2 = time.mktime((2026, 11, 15, 11, 0, 0, 0, 0, -1))
    assert resumir_contenido({"total_archivos": 52, "total_bytes": 1e12, "solo_grabadora": True,
                              "grabaciones": 52, "desde": t1, "hasta": t2}) == \
        "Tiene 52 grabaciones (1000 GB), del 12/11 al 15/11."
    t3 = time.mktime((2026, 11, 8, 10, 0, 0, 0, 0, -1))
    assert resumir_contenido({"total_archivos": 1, "total_bytes": 4.1e9, "solo_grabadora": True,
                              "grabaciones": 1, "desde": t3, "hasta": t3}) == \
        "Tiene 1 grabación (4,1 GB), del 8/11."


# ---------------------------------------------------------------- pantalla
@pytest.fixture
def tab_discos():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from convertir3x4.ui.tabs.tab_discos import DiscosTab
    t = DiscosTab()
    t._timer.stop()
    t._leer_contenido = lambda uuid: None     # sin NAS: el contenido se inyecta a mano
    yield app, t
    t.deleteLater()


def _est(*uuids):
    return {"hora": "2026-11-15T19:42:10", "auto": True, "copias": [], "tareas": {},
            "discos": [{"uuid": u, "modelo": "SanDisk", "disco_gb": 1000, "fs": "exfat",
                        "admitido": True} for u in uuids]}


def test_aviso_de_disco_nuevo_hasta_que_se_mira(tab_discos):
    app, t = tab_discos
    avisos = []
    t.pendientes.connect(avisos.append)
    t._aplicar(_est("A"), None)                  # módulo escondido
    assert avisos[-1] == 1
    t._aplicar(_est("A", "B"), None)
    assert avisos[-1] == 2
    t.show()
    app.processEvents()
    t._timer.stop()
    t._aplicar(_est("A", "B"), None)             # se mira: se apaga el aviso
    assert avisos[-1] == 0
    t.hide()
    t._aplicar(_est("A", "B"), None)
    assert avisos[-1] == 0                       # ya vistos: no vuelve a avisar
    t._aplicar(_est("A", "B", "C"), None)
    assert avisos[-1] == 1


def test_tarjeta_muestra_que_tiene_y_la_lista_a_pedido(tab_discos):
    app, t = tab_discos
    t._aplicar(_est("A"), None)
    card = t._cards["A"]
    card.set_contenido({"total_archivos": 2, "total_bytes": 8.2e9, "solo_grabadora": True,
                        "grabaciones": 2, "carpetas": [],
                        "archivos": [{"nombre": "HyperDeck_0001.mov", "bytes": 4.1e9, "fecha": 1763247000},
                                     {"nombre": "HyperDeck_0002.mov", "bytes": 4.1e9, "fecha": 1763250600}]},
                       False)
    assert card.lbl_contenido.text().startswith("Tiene 2 grabaciones (8,2 GB)")
    assert card.btn_archivos.text() == "Mostrar archivos"
    assert not card.lst_archivos.isVisibleTo(card)
    card.btn_archivos.setChecked(True)
    assert card.lst_archivos.isVisibleTo(card) and card.lst_archivos.count() == 2
    assert card.btn_archivos.text() == "Ocultar archivos"


def test_mientras_conecta_todo_en_gris_y_sin_datos_falsos(tab_discos):
    app, t = tab_discos
    assert t._estado_conexion == "conectando"
    assert not t.body.isEnabled()
    assert not t.lbl_sin_discos.isVisibleTo(t)       # no se sabe todavía
    assert "Conectando con el NAS" in t.lbl_conexion.text()


def test_sin_conexion_se_ve_y_se_puede_reintentar(tab_discos):
    app, t = tab_discos
    t._sin_conexion("PanelSinConexion: No se pudo conectar con el NAS.")
    assert t._estado_conexion == "error" and not t.body.isEnabled()
    assert t.btn_retry.isVisibleTo(t)
    t._aplicar(_est(), None)
    assert t._estado_conexion == "ok" and t.body.isEnabled()
    assert not t.btn_retry.isVisibleTo(t)
    assert t.lbl_sin_discos.isVisibleTo(t)


# ---------------------------------------------------------------- errores explicados
@pytest.mark.parametrize("copia, que, hacer", [
    ({"fase": "ERROR", "error": "el disco se desconecto (/dev/sdh2 ya no existe)"},
     "se desconectó", "«Copiar…»"),
    ({"fase": "ERROR", "error": "faltan copiar 123 bytes y no hay lugar en /mnt/tank/x"},
     "no queda lugar", "responsable del NAS"),
    ({"fase": "ERROR", "error": "no se pudo montar /dev/sdb1"}, "no pudo abrir", "vuelva a conectarlo"),
    ({"fase": "VERIFICACION_FALLIDA", "error": "3 archivos no coinciden con el pool; ver copia.log"},
     "no coinciden", "verificar"),
    ({"fase": "CON_ERRORES", "errores": 2,
      "error": "2 archivos no se pudieron copiar; ver errores.jsonl"}, "no se pudieron leer",
     "Volver a intentar"),
    ({"fase": "DETENIDA", "error": "detenida a mano"}, "detuvo", "continuar"),
    ({"fase": "TERMINADO", "error": "no se formateo: /dev/sdb1 esta montado; desmontarlo primero"},
     "otro programa", "Formatear"),
    ({"fase": "ERROR", "error": "algo que nadie previó: errno 5"}, "problema en el NAS",
     "detalle técnico"),
])
def test_explica_los_errores_en_palabras_del_operador(copia, que, hacer):
    from convertir3x4.nas.copias import explicar
    q, h = explicar(copia)
    assert que in q and hacer in h
    for palabra in ("pool", "/dev/", "/mnt/", "montar", "montado", "errores.jsonl", "copia.log",
                    "uuid", "errno"):
        assert palabra not in (q + " " + h).lower(), palabra


@pytest.mark.parametrize("copia", [
    None, {"fase": "TERMINADO"}, {"fase": "FORMATEADO"}, {"fase": "COPIANDO", "corriendo": True}])
def test_sin_problemas_no_hay_explicacion(copia):
    from convertir3x4.nas.copias import explicar
    assert explicar(copia) is None


def test_ficha_de_una_copia_sin_vocabulario_tecnico():
    from convertir3x4.nas.copias import ficha
    formateada = {"fase": "FORMATEADO", "actualizado": "2026-11-21 20:38:01", "uuid": "25B6-1F03",
                  "tipo": "exfat", "destino": "/mnt/tank/multimedia/Historial/Reuniones",
                  "particion": "/dev/sdh2", "modelo": "MSI S270 960GB", "serie": "5112510",
                  "tamano": "894.3G", "archivos": 32, "pendientes": 0, "errores": 0,
                  "verificado": {"fecha": "2026-11-21 20:38:01", "archivos": 32, "fallos": []}}
    assert dict(ficha(formateada)) == {
        "Disco": "MSI S270 960GB · 894 GB", "Número de serie": "5112510",
        "Archivos": "32 copiados",
        "Verificación": "El 21/11/2026 20:38: los 32 archivos coinciden con lo copiado",
        "Vaciado": "El 21/11/2026 20:38. Listo para volver a grabar",
        "Última novedad": "21/11/2026 20:38"}
    cortada = {"fase": "ERROR", "modelo": "ADATA SU630", "tamano": "447.1G", "archivos": 20,
               "pendientes": 20, "falta_gb": 404.0, "copiado_gb": 7.4, "errores": 1}
    f = dict(ficha(cortada))
    assert f["Archivos"] == "0 de 20 copiados · 1 con error"
    assert f["Copiado"] == "7,4 GB de 411 GB"
    texto = " ".join(v for _, v in ficha(formateada) + ficha(cortada)).lower()
    for palabra in ("/dev/", "/mnt/", "exfat", "uuid", "25b6"):
        assert palabra not in texto, palabra


def test_historial_muestra_la_copia_completa_al_elegirla():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication, QLabel
    app = QApplication.instance() or QApplication([])
    from convertir3x4.ui.tabs.tab_discos import DiscosTab
    t = DiscosTab()
    t._timer.stop()
    t._llenar_historial([
        {"uuid": "135E-0818", "modelo": "ADATA SU630", "fase": "ERROR", "archivos": 20,
         "pendientes": 20, "destino": "/mnt/tank/multimedia/Reuniones",
         "error": "el disco se desconecto (/dev/sdh2 ya no existe)", "actualizado": "2026-09-28"},
        {"uuid": "17AA-1F10", "modelo": "Disco 2", "fase": "FORMATEADO"}])
    it = t.tree.topLevelItem(0)
    assert all(not it.toolTip(col) for col in range(4))      # lo del aviso pasó al panel
    assert not t.panel_copia.isVisibleTo(t)
    it.setSelected(True)
    app.processEvents()
    assert t.panel_copia.isVisibleTo(t)
    assert t.lbl_copia_estado.text() == "Error"
    assert "se desconectó en medio de la copia" in t.lbl_copia_que.text()
    assert "Qué hacer:" in t.lbl_copia_hacer.text()
    datos = [t.grid_copia.itemAt(i).widget().text() for i in range(t.grid_copia.count())]
    assert datos[:4] == ["Disco", "ADATA SU630", "Carpeta en el NAS", "Reuniones"]
    assert "0 de 20 copiados" in datos
    assert not any("/dev/" in d or "/mnt/" in d for d in datos)
    # una copia sin problemas: su estado, sin «Qué hacer»
    t.tree.topLevelItem(1).setSelected(True)
    it.setSelected(False)
    app.processEvents()
    assert t.lbl_copia_estado.text() == "Vacío" and not t.lbl_copia_hacer.isVisibleTo(t)
    t.deleteLater()
