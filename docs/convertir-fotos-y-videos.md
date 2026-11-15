# Fotos y videos en la pestaña Convertir

**Estado:** diseño acordado el 15/11/2026. Implementado el mismo día; ver "Cómo quedó" al final.

Este documento explica cómo la pestaña Convertir pasa a trabajar con fotos y videos en la misma grilla, y cómo entra a la app la conversión de videos a 8:9 que hoy se hace desde el agente de ProPresenter (`~/Documents/Agents/CDLM-Proresenter/tools/video_8x9.py`, receta en su `docs/12-videos-8x9.md`).

## Punto de partida

- La pestaña Convertir solo carga fotos. "Exportar como video" genera un MP4 a partir de una foto, con el fondo animado.
- Los videos de internet o del celular se pasan a 8:9 con el agente, desde la Mac. Los operadores no lo pueden hacer solos.

## Decisiones

| Decisión | Motivo |
|---|---|
| Fotos y videos en **la misma grilla**, con un filtro Todo · Fotos · Videos | El operador no tiene que saber en qué pestaña está cada cosa. Es como trabaja Lightroom, que los operadores ya conocen |
| Las acciones dependen de lo seleccionado | Hay un solo panel de acciones, no uno por tipo de archivo |
| **Si en la selección hay un video, la salida es video** | Una foto y un video juntos en la pantalla solo se pueden mostrar como video |
| El estilo de Lightroom se aplica a **Convertir y después a Buscar** | Las dos son grillas de medios y tienen que verse iguales. Primero Convertir |
| La conversión de video usa **el ffmpeg que ya trae la app** (`imageio-ffmpeg`) | Viene para Windows, Mac y Linux. Funciona sin instalar nada y sin internet |
| **Sin ffprobe** | Igual que en el catálogo. Lo que hace falta (tamaño, duración, rotación, si tiene audio) sale de `ffmpeg -i` |
| **yt-dlp y deno se bajan la primera vez que se usan**, a la carpeta de datos de la app | YouTube rompe yt-dlp seguido. Si fuera dentro del `.exe`, cada arreglo obligaría a sacar una versión nueva de la app. Así se actualiza solo con `yt-dlp -U` |
| **Las opciones avanzadas quedan escondidas** y vuelven a lo estándar al cerrar la app | Los operadores sin experiencia no tienen que poder cambiar por accidente lo que siempre queda igual. Ver "Opciones avanzadas" |
| El resultado queda **al lado del original** | Mismo criterio que el agente (decisión del 01/11/2026). Lo bajado de internet va a una carpeta configurable |

## Interfaz

```
┌─ Convertir ─────────────────────────────────────────────┬──────────────────────┐
│ [+ Agregar]  [🔗 Pegar link]     Todo · Fotos · Videos  │  ▾ CONVERTIR         │
├─────────────────────────────────────────────────────────┤                      │
│ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐                 │  Centrada            │
│ │foto │ │▶ 0:44│ │foto │ │▶ 5:36│ │⬇ 42%│                │  Doble               │
│ └─────┘ └─────┘ └─────┘ └─────┘ └─────┘                 │  Sobre fondo         │
│                                                         │                      │
│                                                         │                      │
├─────────────────────────────────────────────────────────┤                      │
│ Ordenar: Nombre · Fecha            ─────●──── tamaño    │  [ Convertir 3 ]  ⚙  │
└─────────────────────────────────────────────────────────┴──────────────────────┘
```

- **Filtro arriba.** Solo cambia lo que se ve, no lo que hay cargado.
- **Los videos muestran su duración.** Los links que se están bajando aparecen en la grilla con el porcentaje y pasan a ser un video más cuando terminan.
- **Vista previa al pasar el mouse**, como en YouTube. Al agregar un video, ffmpeg saca 16 cuadros chicos repartidos a lo largo del video y los guarda en una sola imagen (240 px de ancho por cuadro, unos 100 KB). Al pasar el mouse, la miniatura los recorre a unos 4 cuadros por segundo. Solo lee los cuadros clave, así que con un video de 5 minutos en 1080p tarda alrededor de 0,1 s. No hace falta reproducir el video ni sumar Qt Multimedia. La imagen se guarda en caché junto a las miniaturas, indexada por ruta y fecha de modificación. Cuando esté el catálogo en el NAS, se guarda en la base como las miniaturas, y Buscar la usa igual.
- **El panel derecho muestra solo las acciones.** No hay ajustes a la vista.
- **Un solo botón al pie, "Convertir N".**

### Opciones avanzadas: escondidas a propósito

Las usa casi nadie, y quien no sabe puede romper el resultado de todos los domingos sin darse cuenta (por ejemplo, dejar la proporción en 3:4). Por eso no se pliegan en el panel: se esconden.

- **Se abren desde el ⚙ chico, en una ventana aparte** titulada "Opciones avanzadas". No están en el panel de acciones.
- **Qué hay ahí:** la proporción, "Exportar fotos como video", y la carpeta donde se guarda lo bajado de internet.
- **Lo que cambia ahí dura hasta cerrar la app.** Al volver a abrirla, todo vuelve a lo estándar (8:9, salida en foto, carpeta por defecto). Si alguien lo cambió y se olvidó, el domingo siguiente no queda mal.
- **Mientras algo está fuera de lo estándar, se ve.** Una etiqueta naranja arriba del botón ("Proporción 3:4") y un botón "Volver a lo estándar".
- **El nivel de audio (−16 LUFS, pico −1) no se puede cambiar desde la app.** La consola se calibra una sola vez contra ese nivel, así que es una constante del código, no una opción.

### Qué hace cada acción

| Seleccionás | Centrada | Doble | Sobre fondo |
|---|---|---|---|
| Foto | Fondo con texturas de sus colores (como hoy) | — | Como hoy |
| Video | 8:9 con el mismo video ampliado y desenfocado de fondo, y el audio nivelado | — | El video en la mitad de arriba, sobre el fondo elegido |
| Dos fotos | — | Como hoy | — |
| Foto y video | — | Sale un **video**: la foto queda quieta, el video corre y el fondo de colores se mueve como en "Exportar como video". Dura lo que dura el video | — |
| Dos videos | — | Sale un video con los dos. Dura lo que el más largo y el más corto queda congelado en su último cuadro. El audio es el del primero que tenga | — |

En Doble se mantiene la regla de hoy: el elemento más ancho va arriba, con hasta 45 % del alto, y el más alto ocupa el resto.

## Receta de video

Es la misma que usa el agente, que ya probaste en siete videos (ver su registro).

**Imagen**
- Salida en **896×1008** para 8:9, el doble de la nativa del LED. Con otra proporción se mantiene la misma cantidad de píxeles, en números pares.
- En Centrada, el fondo es el mismo video ampliado hasta cubrir, desenfocado (`gblur=sigma=40`) y un poco más oscuro (`eq=brightness=-0.08`). El video va entero y centrado. No hay barras negras.
- En Doble y en Sobre fondo, el fondo es una imagen que arma la app (los colores de las fotos, o el fondo elegido) y se pasa a ffmpeg. En Doble se mueve con el mismo recorrido que hoy usa "Exportar como video".
- H.264 (CRF 18, `yuv420p`) y `+faststart`. Se evita AV1, que la PC de la iglesia puede no reproducir bien.

**Audio**
- **−16 LUFS integrado, true peak −1 dBFS**, para que en la consola el fader quede siempre en 0 dB.
- Dos pasadas de `loudnorm`: primero mide y después aplica una ganancia fija (`linear=true`, LRA 20) con el limitador en −1,5. El AAC sube los picos unos 0,3 dB al codificar.
- AAC 192k a 48 kHz.
- **Verificación al final** con `ebur128`. Si el pico quedó arriba de −1 dBFS, la app **rehace solo el audio**, copiando el video, con ganancia fija y un limitador de pico real sobremuestreado a 192 kHz. Es lo que en el agente quedó pendiente después de HISTORIA 37 (15/11/2026), donde se hizo a mano.
- Si después de eso el volumen quedó a más de 1 dB de −16 o el pico sigue alto, se avisa en la grilla. No se esconde.

**Progreso y cancelación:** ffmpeg corre con `-progress pipe:1`, que da el tiempo procesado para mostrar una barra real. Cancelar mata el proceso y borra el archivo a medio escribir.

## Herramientas por plataforma

| Herramienta | Windows (`.exe`) | Mac (desarrollo) | Cómo llega |
|---|---|---|---|
| ffmpeg | Incluido | Incluido | `imageio-ffmpeg`, ya empaquetado en el `.spec` |
| yt-dlp | Se baja la primera vez | Se baja la primera vez | Ejecutable oficial de GitHub (`yt-dlp.exe` / `yt-dlp_macos`). Trae `yt-dlp-ejs` adentro |
| deno | Se baja la primera vez | Se baja la primera vez | Zip oficial de GitHub. Solo hace falta para YouTube, Facebook no lo usa |

- yt-dlp recibe con `--ffmpeg-location` el ffmpeg de la app, para unir video y audio sin depender de otro ffmpeg.
- Las versiones de deno quedan fijadas con su sha256, igual que los modelos de IA (`engine/ai_models.py`). yt-dlp se actualiza solo, porque es lo que se rompe.
- Hay que confirmar en el CI que el ffmpeg de Windows de `imageio-ffmpeg` trae los filtros que usa la receta (`loudnorm`, `ebur128`, `gblur`, `alimiter`) y `libx264`. El de Mac (7.1) los trae.

## Cambios en el código

1. **`convert/clips.py` (sin Qt).** Lee los datos del video desde `ffmpeg -i`, corre ffmpeg con progreso y cancelación, nivela y verifica el audio, y arma Centrada, Doble y Sobre fondo cuando hay un video en la selección. Devuelve un informe de audio (antes, después y avisos). También arma la tira de cuadros para la vista previa.
2. **`convert/download.py` (sin Qt).** Baja yt-dlp y deno si faltan, los actualiza y baja los links. Limpia los nombres de los reels de Facebook ("4.7K views · 138 reactions ｜").
3. **Pestaña Convertir.** Grilla mixta, filtro, panel plegable y botón único. Los proyectos `.c3x4` guardan también los videos.
4. **Pestaña Buscar.** Mismo estilo, en una etapa posterior.

## Preguntas abiertas

- **Calibración de la consola:** sigue pendiente relevar a qué canal entra la PC de ProPresenter y si los volúmenes de ProPresenter y de Windows ya están fijos (ver `12-videos-8x9.md` en el agente). Sin eso, la nivelación no sirve del todo.
- **Videos largos en Doble:** con dos videos de varios minutos, la conversión tarda. Hay que ver en la PC de la iglesia si hace falta avisar del tiempo estimado.

## Cómo quedó (15/11/2026)

Lo implementado sigue el diseño, con estos cambios que surgieron al construirlo:

- **Dónde se guarda lo convertido.** Las fotos ya se guardaban en una carpeta elegida y numeradas (`0001_…`) para conservar el orden de la grilla, que es el de las presentaciones. Se mantiene: la app pregunta la carpeta, pero propone la del original. Aceptar sin cambiar nada deja el resultado al lado del original, como pedía el agente; se puede elegir otra.
- **Cómo armarla** usa botones de opción (uno entre tres) con la descripción debajo, en lugar de botones grandes.
- **La ayuda** es un panel que se abre por encima del contenido, a la derecha. Se abre solo la primera vez en cada PC y después con el botón Ayuda de la barra lateral o con F1. Muestra la del módulo en el que se está.
- **Tema claro u oscuro según la PC** (decisión del 15/11/2026). Se toma del sistema al abrir la app. Las dos paletas pasan las mismas pruebas de contraste (`tests_ui.py`).
- **Archivos que no están.** En Convertir aparecen como "No disponible" y no se pueden convertir. En Buscar, si falta el disco o el NAS entero, las miniaturas se atenúan y un único aviso arriba explica la causa. Si falta un archivo suelto, lleva la insignia en su celda. Se pregunta una vez por cada raíz (`\\NAS\recurso`, `D:\`, `/Volumes/…`), no una por archivo: en Windows, con el NAS caído, cada consulta puede tardar segundos.
- **Descarga probada de verdad** con un video público de YouTube: yt-dlp y deno se bajan y se verifican la primera vez (unos 40 s en total), y el video sale unido en MP4 con el ffmpeg de la app.
- **Prueba con un video real:** HISTORIA 37 sale igual que con el agente (−23,4 → −16,1 LUFS, pico −2,9 dBFS) y la app rehace el audio sola, sin el paso manual.

Código: `convert/batch.py` (qué sale y cómo se llama), `convert/clips.py`, `convert/download.py`, `ui/tabs/tab_convertir.py`, `ui/tabs/convert_workers.py`, `ui/widgets/advanced_dialog.py`, `ui/widgets/help_panel.py`. Pruebas: `tests_clips.py`, `tests_convertir.py`, `tests_ui.py`.

Pendiente:
- Vista previa al pasar el mouse también en Buscar, para los videos del catálogo.
- Confirmar en la PC de la iglesia que el ffmpeg de Windows trae los filtros, que se ve igual y que la descarga funciona.
