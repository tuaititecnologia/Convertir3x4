# Catálogo compartido en el NAS

**Estado:** diseño acordado el 26/09/2026. Sin implementar.

Este documento explica cómo pasa el catálogo de medios de la app (pestañas Buscar y Catalogar) de funcionar en una sola PC a funcionar en dos, con la base en el NAS de la Catedral de los Milagros.

## Punto de partida

Hoy el catálogo es un archivo SQLite en la carpeta de datos de cada usuario (`appdata.py:22`), con las miniaturas al lado (`appdata.py:26`). Lo lee y lo escribe una sola PC, sobre carpetas locales. Con ese esquema ya funcionan la búsqueda, los tags por lote y el arrastre a ProPresenter.

El entorno donde va a correr es otro:

- **El archivo vive en el NAS** (TrueNAS, RAIDZ2, IP fija 192.168.1.224), y la red local entra por **SMB**. Nada de lo que se use a diario puede depender de Nextcloud.
- **Nextcloud es secundario:** acceso remoto puntual y subida de archivos grandes.
- **La app corre en dos PCs:** la de edición y la de ProPresenter.
- **El NAS tiene poco procesador:** un AMD A4-4000 de 2 núcleos y 8 GB de RAM. El trabajo pesado no puede correr ahí.

## Decisiones

| Decisión | Motivo |
|---|---|
| Se sigue con esta app | Ya tiene catálogo, búsqueda, tags y arrastre a ProPresenter, y todo funciona |
| La base pasa a **PostgreSQL** en el NAS | Soporta varias PCs que escriben a la vez y trae búsqueda de texto y de fragmentos con índice. Nextcloud puede usar la misma instancia |
| Cualquier PC con la app hace todas las tareas | Es la misma app en todas. Catalogar, analizar, buscar, etiquetar y arrastrar no dependen de la PC |
| El catalogado nocturno se programa en la PC de edición | Tiene un Ryzen 5 8600G y puede quedar prendida de noche. Es una tarea programada, no una restricción: cualquier PC puede catalogar a mano cuando quiera |
| Nextcloud no participa de la búsqueda | El acceso diario es por SMB, y quien entra por el Explorador no vería nada guardado en Nextcloud |

### Alternativas descartadas

- **SQLite compartido por SMB.** Con dos PCs escribiendo, el bloqueo de archivos por red no es confiable y la base termina corrupta.
- **Tags en Nextcloud** (etiquetas propias, Recognize, Memories). Quien usa el NAS por SMB no los ve. Además, Recognize necesita unos 4 GB libres y el NAS tiene 8 GB en total.
- **Carpetas generadas con enlaces simbólicos** (`Buscar\bautismo\`, etc.). No requieren instalar nada, pero no permiten combinar criterios de búsqueda. Además, las rutas cambian cada vez que se regeneran, y ProPresenter guarda la ruta exacta que recibió.
- **Tags escritos dentro de los archivos** (metadatos XMP o IPTC). Modifican los originales del archivo histórico, les cambian el hash, y el Explorador de Windows no indexa carpetas de red.
- **MariaDB.** Sería igual de viable. Se eligió PostgreSQL por `pg_trgm`, que reemplaza con un índice la búsqueda por fragmento que hoy se hace con `LIKE`.

## Arquitectura

```
  PC de edición (8600G)            NAS Catedral (TrueNAS)           PC de ProPresenter
  ┌──────────────────────┐         ┌────────────────────────┐        ┌──────────────────────┐
  │ App completa         │── SMB ─►│ Archivos (RAIDZ2)      │◄─ SMB ─│ App completa         │
  │ + catalogado         │         │                        │        │                      │
  │   nocturno           │── SQL ─►│ PostgreSQL             │◄─ SQL ─│                      │
  └──────────────────────┘         │  catálogo + miniaturas │        └──────────────────────┘
                                   │ Nextcloud (secundario) │
                                   └────────────────────────┘
```

Las dos PCs leen los archivos por SMB, comparten la misma base y pueden hacer todo lo que hace la app. La única diferencia es que la PC de edición tiene programado el catalogado nocturno.

## Cambios en el código

La regla de `repo.py` sigue vigente: todo el SQL vive ahí. Por eso el cambio de base queda casi todo en `catalog/`.

### 1. Conexión y esquema (`catalog/db.py`)

- Se reemplaza `sqlite3` por `psycopg` (con `psycopg[binary]`, que no necesita compilar nada). Hay que confirmar que PyInstaller lo empaqueta sin ajustes en el `.spec`.
- La regla de no compartir una conexión entre hilos se mantiene: cada worker abre la suya dentro de `fn`.
- Las migraciones pasan de `schema_version` en SQLite a scripts SQL numerados.
- La tabla `media_fts` (FTS5, `db.py:110`) desaparece. En su lugar va una columna `tsvector` en `media`, con configuración `simple` más `unaccent`. Se evita la configuración `spanish` porque recorta las palabras a su raíz, y en nombres de archivo eso confunde más de lo que ayuda.
- Se agrega un índice `pg_trgm` sobre nombre y carpeta, para buscar fragmentos en el medio de nombres opacos (por ejemplo `4782` dentro de `FB_IMG_…4782…`).

### 2. Búsqueda (`catalog/repo.py`)

- `search()` (`repo.py:166`) mantiene su contrato: primero los resultados de texto, ordenados por relevancia, y después los de fragmento, sin repetir.
- `_substring_rows()` (`repo.py:199`) pasa a `ILIKE` sobre el índice `pg_trgm`.
- `_sync_fts()` (`repo.py:219`) se reemplaza por una actualización del `tsvector`, dentro de la misma transacción que el alta o el tag.

### 3. Rutas relativas al recurso compartido

Hoy `media.path` guarda la ruta absoluta de la PC que catalogó (`db.py:25`), y eso no sirve con dos PCs.

- En la base se guarda la ruta **relativa a la raíz del recurso compartido**.
- Cada PC tiene configurada su raíz y arma la ruta completa al leer.
- La raíz tiene que ser **`\\NOMBRE-DEL-NAS\recurso`**, no una letra de unidad ni la IP. ProPresenter guarda la ruta completa que recibió en el arrastre. Si cambia la IP, o alguien mapea otra letra, las presentaciones pierden sus archivos.
- `folder_origin` (`pipeline.py:71`) ya se calcula relativo a la raíz. Con el recurso compartido como raíz, queda con la carpeta del evento (por ejemplo `Congreso Aviva el Fuego 2014`), y eso permite buscar por evento.

### 4. Miniaturas en la base

Hoy son archivos JPEG en una carpeta local nombrados por hash (`thumbs.py:16`). Las otras PCs no las tendrían.

- Pasan a una columna `bytea` en una tabla aparte (`media_id`, `jpeg`). Una miniatura de 320 px en JPEG ocupa del orden de 20 KB, así que varios miles no llegan a 200 MB.
- `_icon_for()` (`tab_buscar.py:126`) carga la imagen desde esos bytes en lugar de hacerlo desde un archivo.
- Así queda un solo lugar que respaldar y no aparecen rutas nuevas que configurar.

### 5. Hash solo cuando hace falta (`engine/pipeline.py`)

Hoy se calcula el sha256 de cada archivo nuevo o cambiado (`pipeline.py:60`). Leer los 6 TB del archivo por la red, a unos 110 MB/s, llevaría unas 15 horas.

- El sha256 pasa a calcularse **solo para archivos que comparten tamaño con otro**. Dos archivos idénticos pesan exactamente lo mismo, así que el resto no puede tener duplicados exactos.
- El dHash de las imágenes se mantiene, porque lee poco.
- Las miniaturas dejan de depender del hash para su nombre (`pipeline.py:64`), porque pasan a la base.

### 6. Archivos movidos o renombrados

Es el riesgo más serio del cambio. Hoy un medio se identifica por su ruta (`repo.py:29`). Con el NAS compartido, alguien va a reordenar carpetas por SMB. El catálogo vería un archivo nuevo en la ruta nueva, y **los tags del viejo se perderían**.

- Cuando una ruta catalogada desaparece, el medio pasa a estado `missing` en vez de borrarse.
- Cuando aparece un archivo nuevo, se busca entre los `missing` uno con el mismo nombre, tamaño y fecha de modificación. Si hay más de un candidato, se desempata por sha256.
- Si coincide, se actualiza la ruta del medio existente y conserva sus tags.
- Los `missing` que no aparecen en ninguna pasada quedan a la vista para que alguien los revise. No se borran solos.

### 7. Arrastre a ProPresenter (`ui/widgets/media_grid.py`)

`startDrag` usa `QUrl.fromLocalFile(path)` (`media_grid.py:45`). En Windows, con una ruta `\\NAS\recurso\...`, eso genera `file://NAS/recurso/...`. El spike de la Fase 0 (`spikes/spike_dragout.py`) lo validó con archivos locales. **Falta validarlo con rutas de red.**

### 8. Dos catalogados a la vez

Como cualquier PC puede catalogar, dos pasadas pueden coincidir. Por ejemplo, alguien cataloga a mano una carpeta nueva mientras corre el catalogado nocturno.

- Cada pasada toma un bloqueo en PostgreSQL (`pg_try_advisory_lock`) por carpeta raíz. Si otra PC ya está catalogando esa carpeta, la app avisa y no arranca una segunda pasada.
- Carpetas distintas se pueden catalogar en paralelo.
- El alta de un medio usa `INSERT … ON CONFLICT`. Aunque dos PCs lleguen al mismo archivo al mismo tiempo, queda un solo registro.
- Poner tags desde dos PCs a la vez no necesita nada especial. Cada tag es una fila, y la base resuelve la concurrencia.

### 9. Configuración de cada PC (`appdata.py`)

`db_path()` y `thumb_dir()` se reemplazan por una configuración con cuatro datos: servidor de la base, usuario, contraseña y raíz del recurso compartido.

**La configuración viene incluida en el instalador.** Quien usa estas PCs no tiene por qué saber completarla.

## Operación

- **Si la base no responde,** la app avisa en pocos segundos que el catálogo no está disponible y sigue abierta, sin colgarse. Un domingo, el operador siempre puede ir a la carpeta del NAS por el Explorador. El catálogo es un atajo, no el único camino.
- **Respaldo:** `pg_dump` diario hacia el pool del NAS, más los snapshots de ZFS. Si la base se rompe, se pierden como mucho los tags de un día.
- **Seguridad:** PostgreSQL escucha solo en la red local, con un usuario sin privilegios de administración. El acceso remoto de Alejandro es por Netbird, como con el resto de los equipos.
- **Monitoreo:** la PC que corre el catalogado nocturno manda un "sigo vivo" a Zabbix al terminar. Si pasan tres días sin señal, llega una alerta.
- **Memoria del NAS:** PostgreSQL necesita del orden de 100 a 200 MB para un catálogo de este tamaño. Si Nextcloud usa la misma instancia, hay un solo servicio de base en los 8 GB.

## Más adelante: análisis con IA

Queda fuera del alcance inmediato, pero el diseño lo contempla:

- **Dónde corre:** en cualquier PC con la app. El catalogado nocturno de la PC de edición lo incluye, y en otra PC funciona igual, con la velocidad que dé su procesador. El 8600G no tiene CUDA, y ROCm en Windows no soporta oficialmente la Radeon 760M, así que el camino es ONNX Runtime en CPU, con DirectML como extra. La NPU del 8600G queda para más adelante.
- **Qué hace:** CLIP compara cada imagen contra un vocabulario propio de la iglesia (bautismo, alabanza, fondo, logo…). Reemplaza los tags genéricos en inglés que dan herramientas como Recognize. Pone un tag solo cuando está muy seguro, porque un tag equivocado hace perder confianza en el buscador.
- **Dónde guarda:** en `ai_result` y en los tags con `source = 'ia'`, que ya están en el esquema.
- **Qué aporta:** poco en el archivo histórico, que ya está ordenado en carpetas por evento y fecha. Mucho en el material nuevo con nombres opacos (`FB_IMG_…`, `WhatsApp Image…`).
- **Caras:** el reconocimiento facial queda pendiente de una decisión de la iglesia, porque implica identificar a la congregación, chicos incluidos.

## Pruebas antes de implementar

1. **Arrastre con ruta de red:** desde `spikes/spike_dragout.py`, arrastrar a ProPresenter un archivo de cualquier carpeta compartida de Windows (`\\PC\carpeta\foto.jpg`). Confirmar que queda **enlazado** y no copiado, y que sigue abriéndose después de cerrar y volver a abrir ProPresenter. No hace falta esperar al NAS nuevo.
2. **PostgreSQL en TrueNAS:** confirmar la app disponible en la versión instalada, dónde guarda los datos dentro del pool y que se pueda poner `unaccent` y `pg_trgm`.
3. **Nombre del NAS en la red:** confirmar que las dos PCs resuelven `\\NOMBRE-DEL-NAS` sin depender de la IP.

## Preguntas abiertas

- **Tags de las personas:** quedan dentro de la app, pero falta definir quién los pone y con qué criterio. Sin alguien a cargo, lo más probable es que se dejen de cargar.
- **Instancia compartida con Nextcloud:** usar la misma instancia ahorra memoria, pero ata el catálogo a las actualizaciones de Nextcloud. Hay que decidirlo al instalar el NAS.
- **Versión única de la app:** si las dos PCs tienen versiones distintas, una migración de esquema puede romper a la que quedó atrás. La app debería comparar su versión con la del esquema y negarse a escribir si no coinciden.
