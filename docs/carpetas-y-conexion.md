# Carpetas del NAS y conexión al catálogo

**Estado:** diseño acordado el 24/11/2026, implementado en la rama `carpetas-del-nas` (sin publicar; esquema 10). Cambia dos cosas de `catalogo-compartido.md` y `catalogado-en-el-nas.md`. Las carpetas a catalogar se eligen sobre un árbol que arma el NAS, con casillas que permiten excluir subcarpetas, y la conexión al catálogo se configura desde la app.

## Por qué

Hoy las carpetas se eligen con el diálogo de carpetas del sistema operativo de cada PC, abierto en su raíz (`\\truenas\multimedia`, `/Volumes/multimedia`). La app convierte lo elegido en una ruta relativa con `rutas.a_relativa()`. Lo hace en tres lugares:

- `ui/widgets/catalogo_avanzado.py:151`: agregar una carpeta a la lista (⚙ de Catalogar).
- `ui/tabs/tab_motor.py:843`: catalogar una subcarpeta.
- `ui/widgets/catalogar_por_partes.py:184`: elegir dónde en «Catalogar ▾».

Eso trae cuatro problemas:

- Depende de que la PC tenga el share montado y de cómo lo tenga montado (UNC, letra de unidad, Mac, Linux). Si la raíz está mal escrita, no se puede elegir nada.
- El diálogo deja salir de la raíz, y la app recién se queja después.
- En una Mac, los nombres con tilde llegan por SMB en NFD y no coinciden con los que anotó el NAS (NFC). Es el mismo problema por el que el catalogador no corre en macOS.
- Por Netbird, recorrer carpetas desde la PC es lento.

Además, hoy no se puede excluir una subcarpeta de una carpeta que se cataloga. La lista solo admite carpetas incluidas, y ninguna puede estar dentro de otra.

La raíz, a su vez, solo se puede cambiar editando `catalogo.ini` a mano, en el mismo archivo que tiene la contraseña de la base. Si la raíz está mal, la app abre igual, pero todos los medios se ven en gris y el operador no tiene cómo corregirlo. Copiar el `.ini` es además el paso que más se olvida al instalar una PC.

## Decisiones

| Decisión | Motivo |
|---|---|
| El catalogador del NAS publica en la base la lista de carpetas de `multimedia` | Lee `/media` desde adentro, con los nombres NFC tal como los guarda el NAS, sin red y sin depender del montaje de ninguna PC |
| La PC dibuja el árbol a partir de esa lista, sin mirar el disco | Se ve igual en Windows, Mac y Linux. Sigue la regla de la app de que el NAS publica y las PCs leen |
| El árbol se relee en cada pasada y cada vez que una PC lo abre, en un hilo aparte del catalogador | Una carpeta creada durante el culto aparece uno o dos segundos después de abrir el ⚙, aunque haya una pasada larga en curso |
| No se vigila el disco en tiempo real (inotify) | Necesita un vigilante por carpeta (miles en `Historial`) y tiene casos raros con lo que llega por SMB. Releer al abrir resuelve lo mismo |
| Una casilla de dos estados por carpeta, en todos los niveles, sin marcas calculadas hacia arriba | Ver «Por qué dos estados». Lo que se ve tildado es lo que se cataloga |
| Una carpeta sin marca propia toma la de su madre | Una carpeta nueva dentro de una tildada entra sola, sin abrir el árbol |
| Los cambios se aplican con «Guardar», después de un resumen | Con exclusiones y filtro se hacen varios clics seguidos, y destildar `/` por error dejaría todo afuera |
| Al excluir una carpeta con medios catalogados, se pregunta si se eliminan del catálogo. Si no, quedan con sus etiquetas | Si la carpeta vuelve a tildarse, se reutilizan sin procesarlas de nuevo |
| Junto a cada carpeta, cuántas fotos y videos catalogados tiene | Muestra qué falta catalogar |
| Se muestran las carpetas vacías | La carpeta de un evento recién creada todavía está vacía, y el filtro tiene que encontrarla |
| Solo se ocultan las carpetas de sistema | No se conoce ninguna otra en `multimedia` que deba esconderse (24/11/2026) |
| El árbol va en el ⚙ de Catalogar | Elegir carpetas se hace pocas veces (ver la regla de opciones avanzadas) |
| Pantalla «Conexión al catálogo» con los seis datos del `.ini`, clave incluida | Una PC nueva queda lista sin copiar ningún archivo, y la raíz se corrige desde la app |
| La pantalla escribe `catalogo.ini`, no `QSettings` | La configuración queda en un solo lugar, y la app sigue siendo portable con su `.ini` al lado |

## Árbol de carpetas

### Cómo se marca

Cada fila del árbol tiene una casilla con el estado de **esa** carpeta:

- **☑** la carpeta se cataloga, con sus fotos y videos sueltos.
- **☐** la carpeta no se cataloga.

Las reglas son estas:

- Tildar o destildar una carpeta cambia también todo lo de abajo.
- Después se puede cambiar una hija sin tocar a la madre. Destildar `Videos/Terminados` deja `Videos` tildada.
- Una carpeta que nadie marcó toma el estado de su madre. Eso vale también para las carpetas que se creen más adelante.
- Si una carpeta tiene adentro algo con otro estado, la fila lo dice al costado: «2 excluidas adentro» o «1 elegida adentro». Es texto, no una marca, y se ve aunque la carpeta esté cerrada.

Ejemplos. A la izquierda, todo tildado y `Terminados` destildada. A la derecha, nada tildado salvo `Temporal`:

```
☑ /         1 excluida adentro         ☐ /          1 elegida adentro
  ☑ Videos  1 excluida adentro           ☐ Fotos    1 elegida adentro
    ☐ Terminados                           ☐ Día 2  1 elegida adentro
                                             ☑ Temporal
```

En el de la derecha, las fotos sueltas de `Fotos` y `Día 2` no se catalogan, y se ve en sus casillas.

### Por qué dos estados

Se revisaron otros programas el 24/11/2026. Dropbox, Synology Hyper Backup, Veeam y Duplicati usan una casilla de tres estados, con un guion para «adentro hay de todo». El guion no dice si los archivos sueltos de esa carpeta entran ni si entran las subcarpetas que se creen después; en Synology eso depende de cómo se armó la selección y la casilla no lo muestra. Backblaze, Time Machine e Immich usan una lista de incluidas y otra de excluidas, sin árbol. Las Opciones de indización de Windows usan una casilla de dos estados por carpeta y un resumen abajo, y es el modelo que se toma acá.

### Con el filtro

Hay un campo para buscar por nombre. Al escribir «temp», el árbol muestra `Temporal` con su camino hacia arriba (`/`, `Fotos`, `Día 2`) y lo que tiene adentro (plegado), y esconde lo demás. Mientras el filtro está puesto, tildar o destildar una carpeta cambia esa carpeta y lo que se ve de ella hacia abajo, nada más. En el ejemplo de la derecha, tildar `Fotos` con «temp» tilda `Fotos`, `Día 2` y `Temporal`. `Día 1` no se ve y queda ☐, y `Fotos` pasa a decir «1 excluida adentro».

### Guardar

El botón «Guardar» muestra antes un resumen de lo que cambia, por ejemplo:

> Se agrega Fotos (excepto Fotos/Día 1).
> Se excluye Videos/Terminados. Tiene 340 fotos y 12 videos catalogados. ¿Los elimina del catálogo, con sus etiquetas, o los deja?

Si los deja, quedan en el catálogo y en Buscar con sus etiquetas. Mientras la carpeta esté excluida, el catalogador no la recorre. Si en ese tiempo se borra o se mueve un archivo, no se marca como faltante, y al abrirlo desde Buscar da error. Se pone al día cuando la carpeta vuelve a tildarse.

Cerrar sin guardar descarta los cambios.

Debajo del árbol hay además un resumen fijo de lo que se cataloga, en el mismo formato: «/ (excepto Videos/Terminados)».

### Otras cosas del árbol

- Una carpeta con marca propia que ya no existe en el NAS (renombrada o borrada) se muestra en su lugar con el aviso «No está en el NAS». Se puede destildar para quitarla.
- Se puede desplegar y plegar todo.
- Debajo del árbol se indica de cuándo es la lectura, por ejemplo «Carpetas del NAS leídas hoy a las 10:42». Si a los 10 segundos del pedido no llegó el aviso del NAS, dice: «El NAS no respondió. Se muestran las carpetas leídas el 24/11 a las 02:10».
- Un botón «Actualizar» vuelve a pedir la lectura.

### Elegir una subcarpeta para catalogar (`tab_motor`, «Catalogar ▾»)

Mismo árbol, sin casillas y con una sola selección. Solo se pueden elegir carpetas que se catalogan (☑), y las demás se ven en gris. Con eso desaparece el mensaje «Esa carpeta no está entre las que se catalogan».

## En la base (esquema 10)

```sql
-- Las carpetas de `multimedia`, tal como las ve el NAS. Relativas a la raíz, con "/".
CREATE TABLE carpeta_nas (
    ruta text PRIMARY KEY
);
ALTER TABLE catalogado_estado ADD COLUMN carpetas_leidas timestamptz;

-- Las marcas del árbol: solo las carpetas que difieren de su madre.
-- "" es la raíz. Las filas que ya hay quedan como incluidas.
ALTER TABLE carpeta_catalogada ADD COLUMN incluir boolean NOT NULL DEFAULT true;
```

`carpeta_catalogada` pasa a guardar **marcas** y deja de ser una lista de carpetas incluidas. El ejemplo de la izquierda queda en dos filas: `("", true)` y `("Videos/Terminados", false)`. Al guardar, la app borra las marcas que repiten el estado de su madre. La raíz `""` pasa a ser una marca válida. Hoy `normalizar_carpetas` la rechaza.

## El alcance del catalogado

Todo lo que hoy recibe la lista de carpetas pasa a recibir el **alcance**: las raíces desde donde se recorre y las carpetas que se saltean.

- **`Repo.alcance()`** lee las marcas y devuelve las raíces (cada carpeta ☑ cuya madre está ☐, o `""` si la raíz está tildada) y las excluidas que hay debajo de cada raíz.
- **`rutas.en_el_alcance(rel, alcance)`** dice si un archivo se cataloga. Reemplaza a `rutas.dentro_de` con la lista.
- **`engine/inventory.scan()`** recorre cada raíz y no entra en las carpetas excluidas.
- **`engine/pipeline.catalogar()`** marca como faltantes solo los archivos que están en el alcance. Lo excluido no se toca (ver «Guardar»).
- El bloqueo por carpeta (`tomar_carpeta`) se toma por raíz, como hoy por carpeta de la lista.
- `catalogador.pasada()` y `tab_motor` usan el alcance para saber si una subcarpeta pedida se cataloga y cuál es su raíz.
- La IA (`pendientes_de_ia`) y el texto en las fotos (`fotos_sin_texto`) siguen como antes: trabajan sobre lo que está en el catálogo (todo, o la subcarpeta pedida). Lo que quedó de una carpeta excluida ya se había analizado.
- Las carpetas del sistema se saltean también al catalogar (`inventory.scan`), con la misma regla que el árbol (`alcance.es_de_sistema`).

## Lectura de las carpetas del NAS

### En el catalogador

- **`leer_carpetas(raiz)`** (Qt-free, en `engine/inventory.py` o junto a él) recorre solo carpetas con `os.scandir`, sin abrir archivos y sin seguir enlaces simbólicos. Omite las carpetas de sistema: las que empiezan con punto (`.zfs`, `.recycle`), `@eaDir`, `#recycle`, `$RECYCLE.BIN`, `System Volume Information` e `ix-applications`. Una carpeta que no se puede leer se anota en el registro y el recorrido sigue.
- **`Repo.reemplazar_carpetas_nas(rutas)`** compara con lo que hay, agrega y borra solo la diferencia en una transacción y actualiza `carpetas_leidas`. Al terminar avisa siempre con `NOTIFY carpetas_nas_leidas`, haya cambiado algo o no. Sin ese aviso, una PC que abre el árbol sin cambios nunca se enteraría de que el NAS respondió.
- **Hilo `Carpetas`**, al lado de `Latido`, con su propia conexión. Hace `LISTEN carpetas_nas` y relee al recibir un pedido. Si llegan varios pedidos seguidos, relee una sola vez. No espera a la pasada en curso ni a la copia de un disco de la grabadora, porque leer carpetas no choca con ninguna de las dos.
- **Al terminar cada pasada**, el hilo principal relee también. De ese modo el árbol queda al día aunque nadie lo abra.

### Los avisos a las PCs

El aviso de la relectura va por un canal propio, `carpetas_nas_leidas`, y no por `catalogo`. `AvisosCatalogo` hoy emite `cambio` ante cualquier aviso de `catalogo` sin mirar su contenido, y Buscar vuelve a consultar con cada uno. Si la relectura usara ese canal, cada vez que alguien abriera el árbol en una PC, Buscar volvería a consultar en todas. `AvisosCatalogo` escucha además `carpetas_nas_leidas` y lo emite como una señal aparte, `carpetas`, que solo usa el árbol.

### En la PC

- **`Repo.pedir_carpetas_nas()`** hace `NOTIFY carpetas_nas`. No deja fila. Si el catalogador no está corriendo, el pedido se pierde y la PC muestra el último árbol con su fecha.
- **`Repo.arbol_carpetas()`** devuelve, en una sola consulta, las carpetas del NAS, `carpetas_leidas`, las marcas y cuántas fotos y videos catalogados hay directamente en cada carpeta (agrupando `media` por carpeta). El árbol suma hacia arriba.
- **Al abrir el árbol**, la PC pide la relectura y dibuja enseguida lo que ya hay en la base. Cuando llega la señal `carpetas`, vuelve a consultar y redibuja sin perder lo desplegado, la selección, el desplazamiento ni las marcas cambiadas todavía sin guardar.

## Conexión al catálogo

### La pantalla

Campos, en este orden: **Servidor**, **Clave** (oculta, con un botón para verla) y **Carpeta multimedia en esta PC**. Esta última tiene «Elegir…», que abre el diálogo de carpetas del sistema; acá sí corresponde, porque es una ruta de esta PC. Puerto, base y usuario no cambian nunca y van plegados bajo «Otros datos».

- **«Probar»** corre fuera del hilo de la interfaz y no modifica nada. Se conecta con `conectar(..., preparar=False)` y el tiempo de espera de siempre (3 s), sin tomar el bloqueo del esquema ni migrar. Lee la versión del esquema y la compara con `VERSION_ESQUEMA`. Después comprueba que la carpeta multimedia exista y que contenga al menos una de las raíces del alcance. Cada falla se explica en palabras del operador, por ejemplo «No se pudo llegar al servidor 192.168.1.224. Revise que la PC esté en la red de la iglesia o conectada a Netbird», o «La carpeta /Volumes/multimedia existe, pero no tiene ninguna de las carpetas que se catalogan (Publicar, Historial)».
- **«Guardar»** escribe el archivo y la app vuelve a conectarse con los datos nuevos. Se puede guardar sin haber probado.

### Dónde se abre

- En el ⚙ de Catalogar, como «Conexión al catálogo…».
- En el aviso de conexión de Buscar y Catalogar cuando falla (`caja_conexion.py`).
- Sola al arrancar, una vez, si no hay configuración o el archivo tiene un dato que no sirve. Tiene «Más tarde», y el aviso de conexión queda con el botón para volver a abrirla.

### Dónde se guarda

- Si la configuración se leyó de un `catalogo.ini`, se guarda en el mismo archivo.
- Si no había ninguno, se crea junto al `.exe`. Si esa carpeta no deja escribir (por ejemplo, `Program Files`), se crea en la carpeta de datos del usuario y la pantalla dice dónde quedó.
- Con las variables `CDLM_CATALOGO_*` (desarrollo), la pantalla muestra los datos pero no guarda, y lo indica arriba.
- Sin empaquetar y sin esas variables, «junto al `.exe`» es la raíz del repositorio. `catalogo.ini` ya está en `.gitignore`, de modo que la clave no se sube.
- El archivo se escribe completo, primero a un temporal en la misma carpeta y después con `os.replace`. Un corte a mitad de la escritura no lo deja roto. Se pierden los comentarios de `catalogo.ini.ejemplo`, que sigue en el repositorio para quien prefiera copiarlo a mano.
- La clave queda en texto plano, igual que hoy.

### Volver a conectarse

Hoy varias partes leen la configuración una sola vez. Al guardar hay que avisarles:

- `servicio_catalogo` y `AvisosCatalogo` cierran su conexión y abren otra con los datos nuevos. Ya saben reconectarse solos; falta forzarlo.
- `tab_motor.py:740` guarda la raíz al arrancar, y Buscar revisa la raíz cada 5 s para el aviso de NAS desconectado. Las dos tienen que tomar la raíz nueva.
- Las rutas completas de los medios ya cargados en Buscar se armaron con la raíz vieja, así que Buscar vuelve a consultar.

`appdata` gana `guardar_config_catalogo(cfg)` y `origen_config_catalogo()`, que dice de dónde se leyó (entorno, cuál archivo o ninguno). `ConfigCatalogo` gana `a_ini()`.

## Pruebas

Todas en `tests_carpetas.py` (agregado al comando de CLAUDE.md):

- **Marcas y árbol, sin base:** los dos ejemplos de arriba, una carpeta tildada dentro de una excluida, marcas que repiten a su madre, filtro (con tildes y mayúsculas, y lo de adentro de lo encontrado), cantidades y un árbol de más de 5.000 carpetas.
- **Base:** migración 9 → 10 (las filas de antes quedan tildadas), guardar marcas, lo que sale y eliminarlo, la raíz `""` en las consultas por carpeta, `reemplazar_carpetas_nas` (avisa siempre) y `arbol_carpetas`.
- **Recorrido:** `leer_carpetas` (sin carpetas del sistema ni enlaces), todo menos una, solo una honda, una carpeta nueva que entra sola, lo excluido que queda no se marca faltante, una tildada dentro de una excluida conserva sus etiquetas aunque haya una copia en otra carpeta, subcarpeta pedida, y la relectura del hilo `LectorDeCarpetas` al recibir el pedido.
- **Interfaz:** tildar, filtrar, carpeta nueva leída por el NAS sin perder lo cambiado, resumen al guardar (dejar, eliminar, cancelar), elegir una sola carpeta, la relectura no hace consultar a Buscar.
- **Conexión:** dónde se guarda (junto al `.exe`, carpeta de datos si no se puede escribir, nada con variables de entorno), «Probar» (bien, clave mala, carpeta que no está, servidor que no responde) y que no migra una base vieja.

## Puesta en marcha

- Lleva migración (esquema 10). La rama se prueba solo con la base local de Docker, nunca contra el NAS. Correrla contra el NAS migraría la base de producción y el catalogador actual se detendría por incompatible.
- El catalogador del NAS y las PCs se actualizan juntos, como en las migraciones anteriores. Una PC con la versión anterior deja de conectarse hasta actualizarse (en Windows se actualiza sola).
- Las carpetas que hoy están en la lista quedan como marcas ☑, y el catalogado sigue igual hasta que alguien cambie algo.
- Hasta que el catalogador nuevo haga su primera lectura, `carpeta_nas` está vacía. El árbol lo indica («El NAS todavía no leyó las carpetas») y muestra solo las carpetas que tienen marca.

## Fuentes de «Por qué dos estados»

- [Synology Hyper Backup – origen de la copia](https://kb.synology.com/en-af/DSM/help/HyperBackup/data_backup_source)
- [Veeam – pedido para que se vea la selección parcial](https://forums.veeam.com/veeam-agent-for-windows-f33/feature-or-bug-request-partial-dir-selections-indication-t25290.html)
- [Dropbox – las carpetas nuevas se sincronizan solas](https://www.dropboxforum.com/t5/View-download-and-export/Stop-Selective-Sync-from-automatically-adding-new-files-folders/m-p/572302)
- [Backblaze – exclusiones](https://www.backblaze.com/computer-backup/docs/configure-exclusions-windows)
- [Immich – bibliotecas externas y exclusiones](https://docs.immich.app/features/libraries/)
