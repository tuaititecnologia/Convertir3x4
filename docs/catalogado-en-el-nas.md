# Catalogado en el NAS

**Estado:** propuesta del 21/11/2026, sin implementar. Cambia una decisión de `catalogo-compartido.md`: el catalogado y el análisis con IA pasan de las PCs al NAS. Lo demás de ese documento sigue vigente (base PostgreSQL, rutas relativas, miniaturas en la base, faltantes, arrastre a ProPresenter).

## Por qué

Catalogar desde una PC obliga a leer cada foto por la red, y desde afuera de la iglesia (Netbird) eso depende de la subida de internet. En el NAS los archivos están en el disco local. Se había descartado el NAS por su procesador (AMD A4-4000, 2 núcleos), pero la medición del 21/11/2026 muestra que alcanza, IA incluida.

## Medición del 21/11/2026

Contenedor temporal en el NAS (`python:3.12-slim`, 2 GB de tope), `multimedia` montado en solo lectura, sin tocar la base. Muestra al azar de 80 fotos (10,5 MB de media) y 30 videos. Código de la app tal como está en `main`.

**Biblioteca:** 4.027 fotos y 1.562 videos con formatos que la app reconoce (6,6 TB en total, casi todo video). Recorrer las carpetas y leer los datos de cada archivo tarda menos de un segundo.

| Paso | Por archivo (media) | Toda la biblioteca |
|---|---|---|
| Fotos, código actual (datos + miniatura + dHash) | 0,82 s | 55 min |
| Fotos, con el dHash sobre la imagen achicada | 0,52 s | 35 min |
| Videos (datos + miniatura) | 1,3 s | 34 min |
| IA en fotos (CLIP + caras) | 1,6 s | 1 h 48 min |
| IA en videos (CLIP sobre la miniatura) | 0,65 s | 17 min |
| sha256 (solo archivos del mismo tamaño) | 113 MB/s | según cuántos coincidan |

**Primer catalogado completo:** unas 3 horas, una sola noche. Las pasadas siguientes procesan solo lo nuevo o cambiado.

**Memoria:** el proceso de Python llegó a 1 GB con CLIP cargado. Es un mínimo, porque no incluye los ffmpeg que se abren por cada video. El NAS no tiene swap. TrueNAS no lo ofrece desde la versión 24.04, y swap sobre un zvol de ZFS puede trabar el sistema justo cuando falta memoria, así que no se agrega. En cambio, el 21/11/2026 se bajó el tope de la caché de ZFS (ARC) de 3 GB a 1,5 GB (Init Script id 1): los metadatos ocupan unos 500 MB y la tasa de aciertos era del 99,7 %. Con eso quedan unos 2,6 GB libres. El tope de memoria del contenedor sigue siendo obligatorio (ver más abajo), y en la primera pasada real se mide el pico verdadero en el cgroup (`memory.peak`).

## Decisiones propuestas

| Decisión | Motivo |
|---|---|
| El NAS hace todo el catalogado: recorrido, faltantes, movidos, datos, miniaturas, sha256, dHash y la IA de escenas y caras | Lee del disco local y la medición muestra que el procesador alcanza |
| La IA sigue siendo local (CLIP, YuNet, SFace) | Alcanza en el NAS y no saca fotos de la iglesia a ningún servicio. Las caras nunca salen del NAS |
| Corre en un contenedor dentro de la App «catalogo» de TrueNAS, al lado de PostgreSQL | El sistema de TrueNAS no permite instalar Pillow, numpy ni ONNX Runtime. En la misma App comparte la red con la base |
| Las PCs piden y miran el catalogado a través de la base | No hace falta otro puerto ni otra clave, y funciona igual por Netbird |
| El dHash se calcula sobre la imagen achicada, antes del primer catalogado | Ahorra un 35 % en fotos. La base está vacía (`media` = 0 filas el 21/11/2026), así que no hay que recalcular nada |

## Cómo funciona

```
  PC de edición / PC de ProPresenter        NAS Catedral (TrueNAS)
  ┌───────────────────────────┐             ┌─────────────────────────────────┐
  │ App: Buscar, etiquetar,   │── SQL ────► │ App «catalogo»                  │
  │ arrastrar, Convertir      │             │  ├ PostgreSQL                   │
  │ Catalogar = estado +      │             │  └ catalogador ◄── disco local  │
  │ «Catalogar ahora»         │── SMB ────► │ /mnt/tank/multimedia (RAIDZ2)   │
  └───────────────────────────┘             └─────────────────────────────────┘
```

### El catalogador

- **Imagen:** se arma en GitHub Actions con el mismo código del `.exe` y se publica en `ghcr.io/tuaititecnologia/convertir3x4-catalogador` con la misma etiqueta de versión (`v0.7.0`, …). El repositorio es público, así que el NAS la baja sin credenciales. Las dependencias van fijas por versión.
- **Código:** un comando sin interfaz, `python -m convertir3x4.catalogador`, que usa `catalog/` y `engine/` tal como están. `appdata.py` importa Qt, así que el catalogador lee su configuración de variables de entorno del compose (servidor `postgres`, clave, raíz `/media`, carpeta de modelos).
- **Montajes:** `/mnt/tank/multimedia` → `/media` en **solo lectura**. El catalogado nunca escribe en los archivos. Los modelos de IA en un volumen propio (616 MB; ya bajados en `/mnt/tank/ingreso/_bench/models`).
- **Rutas:** la base guarda rutas relativas a la raíz. El NAS cataloga `/media/Publicar/…` y las PCs abren el mismo archivo en `\\truenas\multimedia\Publicar\…`.
- **Acceso a la base:** por la red interna de la App (`ix-catalogo_default`). `pg_hba.conf` hoy acepta al usuario `catalogo` solo desde 192.168.1.0/24 y Netbird. Hay que fijar la subred de la App en el compose y agregar esa subred, solo para `catalogo`.
- **Recursos:** tope de memoria de 1,5 GB (a confirmar con la primera medición real), `nice` e `ionice` bajos y 2 hilos para ONNX Runtime.

### Cuándo corre

- **Todas las noches** a una hora fija, antes del respaldo de la base (03:20). Si la pasada no terminó, el respaldo corre igual. La base queda consistente porque cada archivo se guarda apenas se procesa.
- **Cuando alguien lo pide** desde la app con «Catalogar ahora».
- **No corre mientras se copia un disco de la grabadora** (`copiar-disco.py`), porque las dos cosas compiten por el disco y la memoria. El catalogador lee el estado de las copias (`/mnt/tank/ingreso/_copias/`, montado en solo lectura) y espera a que terminen.

### Pedido y estado, a través de la base

Dos tablas nuevas, con su migración numerada en `catalog/db.py`:

- **`catalogado_pedido`:** una fila por pedido («Catalogar ahora», o una subcarpeta desde el ⚙). El catalogador la toma, la marca en curso y la cierra. Si el catalogador no estaba corriendo, el pedido queda en la base y se atiende al arrancar. `NOTIFY` lo despierta al instante, pero lo que vale es la fila.
- **`catalogado_estado`:** una sola fila que el catalogador actualiza: qué está haciendo, cuánto lleva, cuándo terminó la última pasada y con qué resultado, y un latido cada pocos segundos.

### Qué cambia en la app

- **Catalogar** deja de catalogar en la PC. Muestra el estado que informa el NAS («Catalogando Publicar: 1.250 de 4.027 fotos», «Última pasada: hoy 02:10, sin errores») y tiene el botón «Catalogar ahora». En el ⚙ quedan catalogar una subcarpeta y quitar los faltantes, ahora como pedidos al NAS.
- **Si el latido tiene más de un minuto,** Catalogar avisa que el NAS no está catalogando, con la hora de la última señal. Un pedido hecho en ese momento queda en espera y se atiende cuando el catalogador vuelve.
- **Lista de carpetas:** pasa de `catalogo.ini` de cada PC a la base, para que el NAS y las dos PCs usen la misma. `catalogo.ini` se queda con la conexión y la raíz.
- **Personas para reconocer:** se siguen cargando desde la app. La firma de la foto de referencia la calcula la PC y la guarda en la base, como hoy. Los modelos son los mismos en la PC y en el NAS (versión fija en `ai_models.py`).
- **Zabbix:** el «sigo vivo» lo manda el NAS al terminar cada pasada, no la PC de edición.

### Versiones

El catalogador y las dos PCs comparten el esquema de la base. Al publicar una versión que lo cambia:

1. Actualizar la imagen del catalogador en el NAS (cambiar la etiqueta en el compose de la App). Al arrancar aplica la migración.
2. Instalar el `.exe` nuevo en las dos PCs.

Mientras una PC tenga la versión vieja, esa PC no toca el catálogo (`CatalogoIncompatible`, como hoy). Si el catalogador es el que quedó atrás, se detiene, lo informa en `catalogado_estado` y Catalogar lo muestra.

## Orden de trabajo

1. dHash sobre la imagen achicada (`engine/dedup.py`, `engine/pipeline.py`), con su prueba.
2. Comando `convertir3x4.catalogador` con configuración por variables de entorno, tablas de pedido y estado, y su prueba contra la base de desarrollo.
3. Imagen en GitHub Actions y servicio en el compose de la App «catalogo» (copia maestra en CDLM-Freenas, `scripts/catalogo/compose.yaml`), con la entrada de `pg_hba.conf`.
4. Pestaña Catalogar: estado, «Catalogar ahora» y pedidos desde el ⚙. Se quita el catalogado en la PC.
5. Extensiones `.mpg`, `.mts` y `.m2t`, probadas con archivos reales del NAS.
6. Primera pasada real en el NAS, de noche, midiendo el pico de memoria.

## Decidido el 21/11/2026

- **Las PCs no catalogan.** Cataloga solo el NAS. Se saca de la app el catalogado local.
- **Se agregan los videos de cámara** `.mpg`, `.mts` y `.m2t` (89, 59 y 16 archivos, unos 86 GB) a `VIDEO_EXTS`, después de probar que ffmpeg los lee.
- **Las fotos RAW no se catalogan** (246 `.nef` y 23 `.cr2`).

## Para decidir

- **Transcripción de los videos** (idea del 21/11/2026): buscar por lo que se dice en los cultos. Whisper local. El NAS saca el audio (lectura de disco, poco procesador) y la transcripción corre en la PC de edición o en el NAS con una placa de video. Hay placas viejas disponibles. Antes de decidir hay que confirmar el modelo, la memoria de video y la fuente del NAS. Sin confirmar todavía: TrueNAS 25.10 parece soportar NVIDIA solo desde GTX 16xx/RTX 20xx, y una AMD de la serie HD 7000 o posterior podría andar por Vulkan con whisper.cpp. El texto entraría en `search_doc`, con los tiempos de cada frase para abrir el video en ese minuto.
