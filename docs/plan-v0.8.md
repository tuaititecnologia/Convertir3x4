# Plan de la v0.8.0

**Estado:** aprobado el 22/11/2026 (decisiones abajo); en curso en la rama `v0.8`. Junta en una sola versión los pendientes de `pendientes.md` que tocan Buscar, Catalogar y el catalogador, con **un solo cambio de esquema (7)** y una sola actualización del NAS y de las PCs.

## Decisiones (22/11/2026)

1. Entra también lo que había quedado afuera (ver «También en esta versión»).
2. El GPS se guarda, sin mostrarlo todavía.
3. La tira de cuadros de los videos la hace el NAS y va en la base.
4. Fotos hermanas: misma carpeta y mismo día de toma.

## Antes de empezar (no espera a esta versión)

- **Instalar el `.exe` v0.7.4 en las dos PCs de la iglesia.** El NAS está en el esquema 6 desde hoy y la v0.7.3 ya no entra al catálogo (`CatalogoIncompatible`).

## Cómo se trabaja

- Todo en una rama (`v0.8`). **Nunca se abre contra la base del NAS**, porque la migraría al esquema 7 y detendría el catalogador v0.7.4.
- Las pruebas a mano se hacen con el respaldo de hoy (`catalogo-2026-11-22.dump`) restaurado en el PostgreSQL de Docker como `catalogo_prueba`: datos reales, sin tocar producción. Para que el catalogador corra contra esa base se levanta su imagen local (`Dockerfile.catalogador`) con `/Volumes/multimedia` montado como `/media`.
- No hay etiqueta ni actualización del NAS hasta que el usuario lo apruebe. Al aprobarse: respaldo, etiqueta v0.8.0, catalogador del NAS primero (aplica la migración) y después las dos PCs.

## Datos medidos (22/11/2026)

- 4.053 fotos: **3.806 (94 %) tienen fecha de toma** en el EXIF y **191 (4,7 %) tienen GPS**.
- 1.744 videos. La fecha de los `.mts`/`.m2t` probablemente no esté en los metadatos: se mide en la rama.

## Partes

### 1. Ordenar `pendientes.md` (solo texto)

Pasar a «Hecho» o reescribir lo que quedó viejo:
- **Catalogado en el NAS:** hecho; queda solo el «sigo vivo» a Zabbix.
- **Modelos de IA al abrir la app:** ya no corresponde, porque desde la v0.7.2 la PC no usa modelos.
- **Videos `.mpg`, `.mts` y `.m2t`:** confirmar en la base que están catalogados y sin errores.
- **Darse cuenta sola cuando se desconecta…:** la parte del NAS está hecha (`missing_since` + avisos); quedan los discos locales.
- **Poner en marcha el catálogo en las PCs:** reescribir con lo que falte en la iglesia.

### 2. Arreglos de pantalla

- **Carga al elegir una persona.** El aviso «Trayendo las fotos del NAS…» ya existe (`tab_motor._on_person_sel`), pero el usuario no lo vio. Primero reproducir: si tarda en aparecer, si es muy tenue (`muted`) o si el tiempo se va en otro lado (traer las fotos por Netbird, juzgarlas). Según eso: un indicador visible y, si se puede, traer menos datos.
- **Texto claro en el desplegable de persona.** Primero confirmar cuál es, con capturas en los temas claro y oscuro. Lo más probable es el de «¿De quién son estas fotos?» (lista editable dentro de un `QInputDialog`). Sospechoso: el cambio de las flechas de los desplegables (`50f9e41`). Agregar a `tests_ui.py` el contraste del texto de una lista editable.

### 3. Esquema 7 (una sola migración)

**a. Catalogar por partes** (diseño en `pendientes.md`)
- `catalogado_pedido` suma `pasos text[]` (NULL = todo) y `rehacer boolean`.
- `pedir_catalogado` deja de duplicar un pedido solo si coinciden carpeta, pasos y rehacer.
- El catalogador corre los pasos elegidos: archivos nuevos, miniaturas, escenas, personas. Si se pide un paso de IA, siempre agrega antes lo nuevo de esa carpeta.
- **La IA respeta la carpeta.** Hoy `run_ai` analiza todo lo pendiente del catálogo. Si después de cambiar las fotos de una persona alguien pide «una carpeta», se repasaría todo el archivo (≈1 h). `media_for_ai` recibe la carpeta y los pasos.
- «Rehacer» ignora, dentro de lo elegido, las marcas de «ya hecho»: `people_sig`, el modelo y `miniatura_version`.
- La pasada nocturna sigue siendo «Todo».
- Interfaz: «Catalogar ▾» (clic = Todo; la flecha abre la ventana). «Una carpeta» sale del ⚙.

**b. Fecha de toma y ubicación**
- Columnas `tomada timestamptz`, `lat`/`lon double precision` y `metadatos_version smallint` en `media`.
- `engine/probe.py`: `DateTimeOriginal` (si no está, `DateTime`) y GPS en las fotos. En los videos, `creation_time` leído de la salida de ffmpeg en `video_ffmpeg.py` (sin ffprobe).
- Lo ya catalogado se completa una vez, igual que `miniatura_version`: solo se leen los metadatos, sin rehacer miniaturas ni IA.
- Buscar: ordenar por fecha de toma y mostrarla en el detalle. El GPS se guarda sin mostrarlo todavía (pregunta 2).

**c. Fotos de referencia leídas en el NAS**
- `person_reference` suma `media_id` (referencia a `media`). Desde Buscar, la PC manda solo los `media_id`, con `foto` vacía y estado `pendiente`.
- El catalogador lee el archivo de `/media`, lo achica a 1.600 px, **lo guarda en `foto`** (el detalle y los recálculos de firma lo siguen usando) y calcula la firma.
- El detalle de la persona muestra «pendiente, sin foto todavía». Los videos elegidos en Buscar no se mandan.
- Las fotos que vienen del disco de la PC se siguen subiendo como hoy.

**d. Vista previa de videos al pasar el puntero en Buscar** (pregunta 3)
- La tira de cuadros de Convertir (`make_storyboard`) lee unos 16 × 4 MB por video. En Buscar eso sería trabajo pesado por SMB/Netbird en la PC, contra la regla de que la PC no hace ese trabajo.
- Propuesta: la hace el NAS al catalogar y la guarda en la base, como la miniatura (tabla `tira`, con su sello y copia local en caché). La primera vez es una pasada por los 1.744 videos.

### 4. Fotos hermanas

- Al etiquetar en Buscar, ofrecer la misma etiqueta al grupo, mostrando cuántas son antes de confirmar.
- Grupo propuesto: misma carpeta y mismo día de toma (con la columna de 3b). Si no hay fecha de toma, solo la misma carpeta.
- Etiquetas confirmadas (`humano`). Se respeta `tag_rejection` y no se repite en las que ya la tienen.
- No necesita esquema propio: usa el de 3b.

## Primera pasada en el NAS después de actualizar

- Fechas y GPS de las fotos: unos minutos, porque solo se leen los encabezados.
- Fecha de los videos (ffmpeg por video): entre 10 y 30 min, a medir.
- Tiras de cuadros, si se aprueban: unos 35 min.
- **Total estimado: alrededor de 1 h.** Conviene que sea la pasada nocturna.

## Orden de trabajo

1. Parte 1 (texto) y la base de prueba con el respaldo de hoy.
2. Parte 2 (arreglos chicos, se prueban enseguida).
3. Esquema 7 con sus pruebas: 3a, 3b, 3c y 3d.
4. Parte 4 (fotos hermanas) sobre la fecha de 3b.
5. Prueba completa con la base de prueba y el catalogador local; medir la primera pasada.
6. Revisión con el usuario. Con su aprobación: release y actualización.

## También en esta versión (decisión 1)

Definido el 22/11/2026:
- **Carpetas que se catalogan desde la app** (⚙ de Catalogar), guardadas en la base (`carpeta_catalogada`). Sin lista en el compose ni en `catalogo.ini`. La migración copia la lista que publicaba el NAS. Al quitar una carpeta se ofrece sacar también sus fotos y videos del catálogo. Esto reemplaza «sacar una carpeta entera del catálogo». **Hecho.**
- **Pantallas guardadas** (nombre y medida), compartidas por las PCs en la base (`pantalla`). Se eligen en «Proporción» del ⚙ de Convertir y se administran con «Pantallas guardadas…». **Hecho.**
- **Avisar cuando se cae el NAS en Buscar:** ya estaba hecho (revisa la raíz cada 5 s en un hilo aparte).

Quedan afuera:
- «Sigo vivo» a Zabbix (decisión del usuario: por ahora no).
- Receta de audio para videos desparejos (queda pendiente).
- Transcribir los videos: falta decidir el equipo (ver `pendientes.md`).
- «Dejar lugar para texto»: falta que el usuario lo defina.

## Al instalar

1. Respaldo de la base.
2. Etiqueta v0.8.0 y la imagen del catalogador.
3. En el compose del NAS: cambiar la etiqueta y **sacar `CDLM_CATALOGO_CARPETAS`** (la lista ya está en la base).
4. Instalar el .exe nuevo en las dos PCs (el esquema 7 deja afuera a la v0.7.x).
5. La primera noche completa las fechas; las tiras de los videos que ya estaban se arman de noche o con «Catalogar ▾ → Miniaturas».
