# Catalogado en el NAS

**Estado:** implementado en la v0.7.0 e instalado en el NAS el 21/11/2026 (manual en CDLM-Freenas, `Catalogo-PostgreSQL.md`, "Catalogador"). La v0.7.1 agrega los cambios en vivo. Cambia una decisión de `catalogo-compartido.md`: el catalogado y el análisis con IA pasan de las PCs al NAS. Lo demás de ese documento sigue vigente (base PostgreSQL, rutas relativas, miniaturas en la base, faltantes, arrastre a ProPresenter).

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

- **Imagen:** se arma en GitHub Actions con el mismo código del `.exe` y se publica en `ghcr.io/tuaititecnologia/convertir3x4-catalogador` con la misma etiqueta de versión (`v0.7.0`, …). El repositorio es público, pero GitHub suele crear el paquete como privado la primera vez. Antes de instalar hay que confirmar en el NAS que `docker pull` funciona sin credenciales; si no, se hace público el paquete. Las dependencias van fijas por versión.
- **Código:** un comando sin interfaz, `python -m convertir3x4.catalogador`, que usa `catalog/` y `engine/` tal como están. `appdata.py` importa Qt, así que el catalogador lee su configuración de variables de entorno del compose (servidor `postgres`, clave, raíz `/media`, carpeta de modelos).
- **Montajes:** `/mnt/tank/multimedia` → `/media` en **solo lectura**. El catalogado nunca escribe en los archivos. Los modelos de IA en un volumen propio (616 MB; ya bajados en `/mnt/tank/ingreso/_bench/models`).
- **Rutas:** la base guarda rutas relativas a la raíz. El NAS cataloga `/media/Publicar/…` y las PCs abren el mismo archivo en `\\truenas\multimedia\Publicar\…`.
- **Acceso a la base:** por la red interna de la App (`ix-catalogo_default`), con la línea `samenet` en `pg_hba.conf`, solo para el usuario `catalogo`. `samenet` es la subred de esa red, así que no hace falta fijarla en el compose. Las PCs siguen entrando por sus propias líneas, porque Docker conserva su IP.
- **Recursos:** tope de memoria de 1,5 GB (a confirmar con la primera medición real), `nice` 10 y 2 hilos para ONNX Runtime. `ionice` no se usa: ZFS no respeta las prioridades de disco por proceso.
- **Videos:** se leen con imageio-ffmpeg directo (`catalog/video_ffmpeg.py`). La capa de imageio elige el lector por la extensión y no abría `.mts` ni `.m2t`.
- **Telemetría:** ONNX Runtime trae envío de datos de uso; se apaga al crear la sesión (`engine/vision.py`). Además, desde el 21/11/2026 el catalogador corre en una red de Docker interna (`interna`, `internal: true`) que solo llega a PostgreSQL: no tiene salida a internet, así que nada puede salir del NAS aunque una librería lo intente. Los modelos ya están en `/modelos`; si cambia su versión en `ai_models.py`, hay que bajarlos a mano.

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
- **Lista de carpetas:** la define el catalogador (`CDLM_CATALOGO_CARPETAS` en el compose) y la publica en `catalogado_estado.carpetas`, de donde la leen las PCs. Así hay una sola lista. `catalogo.ini` se queda con la conexión y la raíz.
- **Personas para reconocer** (v0.7.2): se cargan desde la app, pero la firma la calcula el NAS. La PC achica cada foto a 1.600 px y la sube a `person_reference` como `pendiente` (`Repo.subir_fotos_de_referencia`, que despierta al catalogador). El catalogador la procesa en la siguiente vuelta, o cada 30 s si está en medio de una pasada, y la deja `lista`, `sin_cara` o `ilegible`. La lista de la app lo muestra por los avisos de la base. La foto queda guardada para recalcular la firma si cambia el modelo de caras. La PC no baja ni carga modelos, y el `.exe` ya no lleva ONNX Runtime ni OpenCV. La persona se reconoce en el catálogo desde la próxima pasada.
- **Revisión de las fotos de referencia** (esquema 6): al calcular la firma, el NAS anota también qué vio en cada foto (`caras`: rostros de tamaño parecido al elegido, `cara_caja`: dónde está el elegido, en píxeles de la foto subida, y `confianza` del detector). Las fotos que ya estaban `lista` antes del esquema 6 se completan una vez (`Repo.referencias_sin_revisar` y `guardar_revision`) **sin tocar la firma ni el estado**: así `people_signature` no cambia y no se repasan las caras de todo el archivo. En Catalogar, al elegir una persona, se ve cada foto con el rostro marcado y su juicio (`catalog/referencias.py`). Ese módulo también compara las firmas entre sí: una foto que no se parece a las demás puede ser de otra persona. Como todo cambio de esquema, el NAS y las PCs se actualizan juntos: un catalogador anterior no arranca contra el esquema 6 (`CatalogoIncompatible`). «El NAS todavía no revisó esta foto en detalle» solo se ve entre la migración y la primera vuelta del catalogador nuevo.
- **Zabbix:** el «sigo vivo» lo manda el NAS al terminar cada pasada, no la PC de edición. Pendiente: la primera versión no lo manda todavía; mientras tanto, el latido y `ultima_fin` en `catalogado_estado` muestran si corre.

### Cambios en vivo (v0.7.1)

La app es cliente de un servidor: lo que cataloga el NAS y las etiquetas que pone una PC aparecen en Buscar de la otra sin que el operador haga nada.

- **En la base:** la migración 3 agrega disparadores en `media`, `media_tag` y `tag` que avisan por el canal `catalogo` (`NOTIFY`). Avisan los cambios de cualquiera, el NAS o una PC. Un `UPDATE` que no cambia nada no avisa, y los avisos de una misma transacción llegan como uno solo.
- **En la PC:** un hilo con su propia conexión escucha el canal (`ui/avisos_catalogo.py`). Si se corta, reintenta cada 10 s, y al volver avisa igual por si se perdió algo. Cada 60 s manda una consulta mínima: si la conexión quedó medio cortada (se cayó Netbird, se reinició el NAS), así se entera y reconecta, y el servidor no la corta por inactiva.
- **En Buscar:** junta los avisos y vuelve a consultar a lo sumo cada 20 s, con la misma búsqueda. Conserva lo seleccionado y el lugar de la grilla. Si el operador está arrastrando, eligiendo con el mouse o tiene un menú abierto, espera a que termine. Con la pestaña escondida no consulta; se pone al día al volver a verla.
- **Sin consultas periódicas:** las PCs no le preguntan a la base cada tantos segundos si hay algo nuevo. Eso cargaría la red, sobre todo por Netbird.

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

Prueba con los archivos reales del NAS (21/11/2026): se leen bien los 59 `.mts`, los 16 `.m2t` y 91 de los 100 `.mpg`. Los 9 restantes pesan 0 bytes (están vacíos desde 2014, en `Historial/Archivo 2013-2019/VIDEOS03/…/congreso aviva el fuego 2014/…/2 jk/`) y quedan marcados como dañados.

## Primera pasada real (21/11/2026)

Pedido desde la base a las 13:52 con la v0.7.0, todas las carpetas. Terminó a las 16:56 (**3 h 04 min**), sin errores ni reinicios del contenedor.

| | |
|---|---|
| Catalogado | 5.764 archivos (4.027 fotos, 1.737 videos), 37 duplicados, 28 dañados. Datos y miniaturas hasta las 15:30 aprox. |
| IA | 5.736 analizados en 1 h 26 min, 4.102 etiquetas de escena propuestas (sin personas todavía: la lista está vacía) |
| Memoria del contenedor | Pico de 1.339 MB (`memory.peak`) con tope de 1.536 MB. Esa cifra incluye la caché de archivos del cgroup; en reposo, con CLIP cargado, quedan 720 MB propios del proceso. Se deja el tope como está y se vuelve a mirar en las próximas pasadas |

**Los 28 dañados:**

| Cantidad | Qué son |
|---|---|
| 18 | `.MOV` de una cámara Canon (`Historial/…/CONGRESO JOVENES 2015/VIERNES/MOVIL NOCHE/[02D290]/MVI_00xx.MOV`, de 26 a 118 MB). La cabecera está bien, pero ningún cuadro se puede decodificar, tampoco con el ffmpeg de Homebrew. La carpeta con nombre entre corchetes parece una recuperación de tarjeta |
| 9 | `.MPG` de 0 bytes (2014), ya vistos en la prueba de formatos |
| 1 | `Historial/Reuniones/2025-01-12_20-11_HyperDeck_0024.mp4`, de 4.748 bytes |

Etiquetas más propuestas: acción social (1.109), predicación (1.030), alabanza (878), oración (285), congregación (203). «Acción social» tan arriba sugiere revisar ese texto en `engine/vocab.py`.

A las 17:00 se pasó el NAS a la v0.7.1 (esquema 3, cambios en vivo).

## Imágenes en negro (esquema 5)

En la primera pasada, 139 de los 145 videos que la IA marcó «logo» tenían la miniatura negra: era el primer cuadro, y las grabaciones empiezan en negro. En todo el catálogo había 140 videos con miniatura negra y una sola foto negra.

- **Miniatura de video:** el primer cuadro que no sea negro entre el 10 %, el 30 % y el 50 % de la duración (`video_ffmpeg.cuadro_representativo`). Probado con 10 de esos videos reales: los 10 quedaron con imagen (escenario, alabanza), unos 2,5 s cada uno leyendo por SMB.
- **En negro:** oscuro y parejo (brillo medio < 16 y variación < 8, de 0 a 255). Una foto así, o un video negro en los tres puntos, queda con calidad `en_negro`, que pesa más que la baja resolución. La IA no los analiza.
- **Lo ya catalogado:** `media.miniatura_version` y `thumbs.MINIATURA_VERSION = 2`. En la pasada siguiente a instalar la versión, el NAS rehace una vez la miniatura de cada video desde el archivo (unos 1.700, de una a dos horas esa noche). Las fotos se revisan desde la miniatura guardada, sin leer el archivo. Se descarta lo que la IA había sugerido sobre la miniatura vieja, sin confirmar, y se vuelve a analizar; lo que confirmó una persona queda.

## Para decidir

- **Transcripción de los videos** (idea del 21/11/2026): buscar por lo que se dice en los cultos. Whisper local. El NAS saca el audio (lectura de disco, poco procesador) y la transcripción corre en la PC de edición o en el NAS con una placa de video. Hay placas viejas disponibles. Antes de decidir hay que confirmar el modelo, la memoria de video y la fuente del NAS. Sin confirmar todavía: TrueNAS 25.10 parece soportar NVIDIA solo desde GTX 16xx/RTX 20xx, y una AMD de la serie HD 7000 o posterior podría andar por Vulkan con whisper.cpp. El texto entraría en `search_doc`, con los tiempos de cada frase para abrir el video en ese minuto.
