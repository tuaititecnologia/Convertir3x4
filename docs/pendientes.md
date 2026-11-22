# Pendientes

Ideas y tareas que van surgiendo mientras se prueba la app. Se anotan acá para no perderlas y se van tachando o moviendo a "Hecho".

## Por hacer

- **Poner en marcha el catálogo en las PCs de la iglesia.** El NAS tiene la base y el catalogador (v0.7.4 desde el 22/11/2026). Falta: instalar el .exe v0.7.4 en la PC de edición y en la de ProPresenter (con la v0.7.3 ya no entran: el esquema es el 6), con su `catalogo.ini` (en CDLM-Freenas, fuera de git); probar etiquetar en una PC y verlo en la otra, y arrastrar a ProPresenter. Actualizar PostgreSQL en cada versión menor (feb, may, ago, nov); la 19 se evalúa cuando salga la 19.1.
- **«Sigo vivo» del catalogador a Zabbix.** Lo único que quedó del catalogado en el NAS (instalado el 21/11/2026; pico de memoria medido, ver `catalogado-en-el-nas.md`).
- **Transcribir los videos** para buscar por lo que se dice (idea del 21/11/2026). Whisper local; ver «Para decidir» en `docs/catalogado-en-el-nas.md`.

- **Receta de audio para videos muy desparejos** (15/11/2026): armar un tratamiento propio para estos casos (comprimir, limitar; el detalle se define después). Caso de referencia: un video de teléfono (`20261113_110825.mp4`: −28,8 LUFS, pico −0,1, rango 27 LU) queda en −20,9 en lugar de −16 y la grilla muestra «! Revisar». El nivelado lineal no puede subirlo sin pasarse de pico y el arreglo con limitador (el de HISTORIA 37) no lo mueve. Probado: loudnorm dinámico + limitador llega a −17,1, pero el pico después del AAC salta entre −1,8 y +4,7 según el ajuste: no es confiable así. Validarla con varios videos reales de la iglesia y con el agente de ProPresenter antes de cambiar la receta actual. Mientras tanto, el aviso dice que quedó bajo y que puede necesitar más volumen en la consola.
- **¿«Dejar lugar para texto» como elección propia?** Hoy elegir un fondo con uno por pantalla también sube la foto a la mitad de arriba (para el texto); con sus colores siempre va centrada. Es la misma clase de mezcla que se separó el 15/11: decidir si el lugar para texto pasa a ser una opción aparte.

- **Pantallas guardadas: crear, editar y borrar** (pedido del 15/11/2026; el mensaje llegó cortado en "CRUD de pantallas (", falta completar el detalle). Lo que se entiende: en lugar de elegir una proporción suelta o escribir «Otra…» cada vez, tener una lista de pantallas con nombre y medida (p. ej. «LED de la Catedral · 8:9», «Evento de primavera · 4,5 × 1,2 m»), para elegirlas en Convertir y reusarlas. Hay que definir dónde se administran (¿opciones avanzadas?), si se comparten entre las dos PCs (con el catálogo en el NAS) y cuál es la predeterminada.

- **Darse cuenta sola cuando se desconecta el disco o el NAS, o cuando se borra o se mueve un archivo** (preguntas del 15/11/2026). Ya hecho: el NAS marca lo borrado o movido en cada pasada (`missing_since`, conserva las etiquetas) y las PCs se enteran al instante por los avisos de la base. Falta lo de abajo. Plan acordado:
  - **Ahora:** en segundo plano, cada pocos segundos, revisar solo si cada disco o recurso del NAS responde (una consulta por raíz, con tiempo límite: nunca congela la app). Pasar a gris lo que dejó de estar y volverlo a la normalidad al reconectar. Sumar el aviso del sistema de disco enchufado o desenchufado para que un USB reaccione al instante.
  - **Lo que no sirve para el NAS:** los avisos de cambios en carpetas del sistema (FSEvents, ReadDirectoryChangesW, inotify vía `QFileSystemWatcher`) funcionan en carpetas locales, pero por SMB son poco confiables (en Mac no avisan de cambios hechos desde otra PC) y no escalan a miles de carpetas. Que un recurso de red se caiga no genera ningún aviso: hay que preguntar.
  - **Con el catálogo en el NAS:** que el NAS detecte los cambios donde están los archivos (inotify sobre sus discos locales sí es confiable), los anote en la base y avise a las dos PCs al instante con `LISTEN/NOTIFY` de PostgreSQL.
- **Sacar del catálogo una carpeta entera.** Hoy no hay forma de deshacer un catalogado hecho por error: el 15/11 se catalogó `/private/tmp` en la Mac de desarrollo (1249 archivos, incluidas fotos de un DNI). "Limpiar el catálogo" no los saca porque los archivos existen. Iría en las opciones avanzadas de Catalogar, con confirmación. (Ese caso ya se limpió a mano el 15/11; la función sigue faltando.)
- **Vista previa de videos al pasar el mouse, también en Buscar**, para los videos del catálogo.
- **Grabadora: probar "qué tiene el disco" con un disco real** enchufado al NAS. Ya está instalado en el NAS el 15/11, pero no había ningún disco para probar.
- **Revisar los textos que vienen del NAS** (notas del modo automático y errores de copia), en el repo CDLM-Freenas: sacar la jerga y pasarlos a usted, como el resto de la app.
- **Probar en la PC de la iglesia:** que se vea igual, que el ffmpeg de Windows funcione y que la descarga de videos ande.
- **Cambiar la contraseña de root del NAS** si sigue siendo la que estaba escrita en `Diagnostico-FreeNAS-Catedral.md`.
- **Catalogar: mostrar que carga al elegir una persona** (22/11/2026). Al hacer clic en una persona de la lista, el detalle (fotos de referencia, rostro marcado, avisos) tarda en aparecer y no se ve nada mientras tanto. Agregar un indicador de carga en el detalle hasta que lleguen los datos.
- **El desplegable de persona tiene el texto blanco o muy claro** (22/11/2026): casi no se lee. Probablemente es el de «¿De quién son estas fotos?» (`_elegir_persona` en `tab_motor.py`, un `QInputDialog` con lista editable, que se usa desde Buscar y Catalogar). Revisar el color del texto del campo editable y de la lista que se abre, en el tema claro y en el oscuro.
- **Fotos de referencia elegidas en Buscar: que las lea el NAS** (22/11/2026). Hoy la PC lee el original entero por SMB, lo achica a 1.600 px (`foto_de_referencia`) y sube el JPEG a `person_reference`, aunque la foto ya esté en el NAS. Por Netbird, con fotos grandes, tarda. Para las fotos del catálogo, mandar solo el `media_id` y que el catalogador lea el archivo de `/media` y lo achique ahí. Las fotos que vienen del disco de la PC (Catalogar → «Agregar fotos de la persona…») siguen subiéndose como hoy. Probablemente pide un cambio de esquema (referencia a `media` en `person_reference`, o la foto vacía hasta que el NAS la complete) y actualizar el NAS y las PCs juntos.
- **Ofrecer la misma etiqueta a las fotos hermanas** (idea del 22/11/2026). Al etiquetar una foto en Buscar (p. ej. con el nombre de un evento), preguntar si se agrega la misma etiqueta al grupo al que pertenece: «¿Agregar «Retiro 2026» también a las otras 38 fotos de esta carpeta?». Falta definir qué es una foto hermana y si el operador elige entre varias opciones:
  - misma carpeta (lo más simple; en `Historial` las carpetas suelen ser un evento);
  - mismo comienzo de nombre o numeración seguida (`IMG_4501…4580`, `HyperDeck_0012…`);
  - mismo día de toma (fecha de la foto o del archivo), o un rango de horas alrededor;
  - fotos y videos juntos, o solo del mismo tipo.

  Antes de agregar, mostrar cuántas fotos son (y quizá dejar verlas o quitar algunas). Las etiquetas agregadas así son confirmadas, como las que pone una persona. Respetar las que alguien quitó a mano (`tag_rejection`) y no repetir las que ya tienen la etiqueta.
- **Leer la fecha de toma y la ubicación de las fotos y los videos** (22/11/2026). Hoy el catalogador no guarda nada del EXIF (solo lo usa para girar la miniatura); la única fecha es la del archivo (`file_mtime`), que cambia al copiar o editar. Agregar en `engine/probe.py`:
  - **Fecha y hora de toma:** `DateTimeOriginal` en fotos y `creation_time` en videos (metadatos que lee ffmpeg). Sirve para las fotos hermanas (mismo día u horas del evento) y para buscar y ordenar por fecha.
  - **Ubicación (GPS):** las fotos de cámara de la iglesia seguramente no la tienen; las de teléfono, sí. Antes de sumarla, contar cuántas del archivo la traen. Si se guarda, decidir qué se muestra (¿un nombre de lugar?, sin internet en el catalogador).

  Pide columnas nuevas en `media` (cambio de esquema) y una pasada que complete lo ya catalogado sin rehacer miniaturas ni IA.
- **Catalogar por partes** (definido el 22/11/2026). El botón «Catalogar ahora» pasa a ser «Catalogar ▾»: la flecha abre una ventana para elegir qué se hace. Sirve sobre todo para catalogar rápido una carpeta recién copiada.

  ```
  ☑ Todo                                    (marcado apaga las demás; desmarcado, se elige cualquiera)
  ☐ Archivos nuevos, movidos o borrados     (recorrer, datos + miniatura de lo nuevo, faltantes)
  ☐ Miniaturas                              (rehacerlas: p. ej. videos en negro; ~1 h 10 min todo el archivo)
  ☐ Escenas                                 (etiquetas de la IA por tipo de foto; ~2 h)
  ☐ Personas                                (reconocer a las de la lista; ~1 h)

  Dónde:  ◉ Todas las carpetas  ○ Una carpeta [Elegir carpeta…]   (sale del ⚙)
  ☐ Rehacer también lo que ya está hecho    (mostrar cuánto tarda antes de confirmar)
  ```

  - Si se marca un paso de IA y hay archivos que todavía no están en el catálogo, primero se agregan (es barato; si no, la IA no los ve).
  - Más adelante se suman «Fecha y ubicación» y «Transcribir videos». No van como casillas: duplicados (sale junto con los archivos nuevos), fotos de referencia (el NAS las procesa solo) y quitar lo que falta (sigue en el ⚙).
  - El pedido viaja en `catalogado_pedido` (hoy solo lleva la carpeta): agregar qué pasos y si se rehace. Cambio de esquema; el NAS y las PCs se actualizan juntos.
  - Hacer clic en «Catalogar» (no en la flecha) corre «Todo» directamente, como hoy «Catalogar ahora».

## Hecho

- 22/11/2026: confirmados en la base los videos de cámara: 16 `.m2t`, 59 `.mts` y 100 `.mpg` catalogados, sin faltantes. Los 9 `.mpg` marcados como dañados son archivos vacíos (0 bytes) de `congreso aviva el fuego 2014/2 sabado/2 jk`. Las fotos RAW (`.nef`, `.cr2`) no se catalogan.
- 22/11/2026: ya no hace falta revisar en la PC los modelos de la IA: desde la v0.7.2 la PC no los usa (los tiene el catalogador del NAS).
- 21/11/2026: catalogado en el NAS instalado (v0.7.0; v0.7.4 desde el 22/11). Primera pasada completa en 3 h 04 min, pico de 1.339 MB.

- 18/11/2026: el catálogo pasó a PostgreSQL (`docs/catalogo-compartido.md`). Rutas relativas a `\\truenas\multimedia`; lista de carpetas a catalogar en `catalogo.ini`; miniaturas en la base con copia local en cada PC; lo que falta se ve en gris y conserva sus etiquetas si reaparece en otra carpeta; Buscar y Catalogar avisan si el NAS no responde y vuelven solos. Ya no se catalogan carpetas sueltas de la PC.

- 15/11/2026: Todos los textos de la app pasan a un registro serio, con trato de usted y vocabulario formal («descargar», «enviar», «enlace», «etiqueta», «puntero»; nada de «tocá», «bajar», «mandar», «la app»). Una prueba (`TestRegistroDeLosTextos`) avisa si vuelve el voseo o una palabra coloquial. De paso: el panel de Convertir se desplaza cuando no entra (se encimaba con muchos elementos) y la ayuda ya no corta los párrafos.
- 15/11/2026: Buscar tiene «Enviar a Convertir» (botón, menú del botón derecho y Ctrl+Enter): lo elegido pasa a la grilla de Convertir ya seleccionado; lo que no está disponible no se envía y se avisa.

- 15/11/2026: Videos grandes en Convertir: la tira de cuadros de la vista previa recorría el archivo entero (con uno de 4 GB, se leían los 4 GB: casi un minuto desde un disco externo o el NAS). Ahora salta a los 16 puntos: ~4 MB por cuadro (medido: 2 GB leídos antes, ~70 MB ahora).

- 15/11/2026: Bajar videos: el programa de descargas arrancaba en ~16 s cada vez en la Mac (la versión de un solo archivo se descomprime en cada uso y el sistema la revisa); ahora se usa la versión ya descomprimida, que arranca en ~0,2 s, y la app la actualiza una vez por día. Cancelar corta al instante (antes, cerrar la app con una descarga arrancando la cerraba con error) y hay una barra «Bajando 1 video…» con Cancelar; quitar de la grilla un video que se está bajando también lo cancela. Cada video que se baja tiene en la miniatura «Cancelar» y, si falla, «Reintentar»; si salió una versión nueva del programa, «Reintentar» la instala primero (pasa en medio del servicio). Al abrir la app se revisa el programa: si falta, Convertir ofrece prepararlo; si está, se actualiza solo.

- 15/11/2026: El fondo y «cuántos por pantalla» son elecciones separadas. Antes, por cómo estaban armados los modos viejos: el fondo elegido solo existía con uno por pantalla; «Sobre fondo» ignoraba «Exportar fotos como video»; y la que quedaba sola en un par impar perdía el fondo elegido. Ahora el fondo elegido vale con 1 o con 2 por pantalla (fotos, videos y mezcla), y la foto sobre un fondo puede salir como video.

- 15/11/2026: Panel de Convertir rediseñado: «¿Cuántos en cada pantalla?» con iconos dibujados según la proporción de salida, «Fondo» aparte, vista previa de cada pantalla que se va a crear (solo para mirar: el orden lo decide la grilla) y botón que dice lo que crea («Crear 1 video»). Salida horizontal: 16:9 en la lista y «Otra…» para cualquier medida (ancho : alto); en horizontal, «2 por pantalla» va lado a lado y «Sobre un fondo» a la izquierda. Toda la ubicación sale de `convert/layout.py`, la misma para fotos, videos y vista previa.
- 15/11/2026: La app sigue el modo claro u oscuro del sistema también con la app abierta (antes se elegía solo al abrirla). Los iconos se vuelven a pintar solos.
- 15/11/2026: Revisión de textos con la skill `impeccable` (`clarify`). Las confirmaciones nombran la acción en el botón ("Reemplazar", "Detener copia", "Apagar") en lugar de Sí/No, y el botón seguro queda por defecto en lo que borra o corta; un solo verbo por concepto ("Quitar", no "Sacar"); estados vacíos y errores que dicen qué hacer; botones específicos ("Elegir carpeta…").
- 15/11/2026: Grabadora muestra el estado de la conexión (azul mientras conecta, naranja si falla, con Reintentar) y deja todo en gris hasta conectar.
- 15/11/2026: Limpiar el catálogo (⚙ de Catalogar): quita lo que ya no existe; lo que está en un disco o NAS desconectado no se toca.
