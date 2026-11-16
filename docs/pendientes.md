# Pendientes

Ideas y tareas que van surgiendo mientras se prueba la app. Se anotan acá para no perderlas y se van tachando o moviendo a "Hecho".

## Por hacer

- **Pantallas guardadas: crear, editar y borrar** (pedido del 15/11/2026; el mensaje llegó cortado en "CRUD de pantallas (", falta completar el detalle). Lo que se entiende: en lugar de elegir una proporción suelta o escribir «Otra…» cada vez, tener una lista de pantallas con nombre y medida (p. ej. «LED de la Catedral · 8:9», «Evento de primavera · 4,5 × 1,2 m»), para elegirlas en Convertir y reusarlas. Hay que definir dónde se administran (¿opciones avanzadas?), si se comparten entre las dos PCs (con el catálogo en el NAS) y cuál es la predeterminada.

- **Darse cuenta sola cuando se desconecta el disco o el NAS, o cuando se borra o se mueve un archivo** (preguntas del 15/11/2026). Hoy la app revisa solo al buscar o al agregar. Plan acordado:
  - **Ahora:** en segundo plano, cada pocos segundos, revisar solo si cada disco o recurso del NAS responde (una consulta por raíz, con tiempo límite: nunca congela la app). Pasar a gris lo que dejó de estar y volverlo a la normalidad al reconectar. Sumar el aviso del sistema de disco enchufado o desenchufado para que un USB reaccione al instante.
  - **Lo que no sirve para el NAS:** los avisos de cambios en carpetas del sistema (FSEvents, ReadDirectoryChangesW, inotify vía `QFileSystemWatcher`) funcionan en carpetas locales, pero por SMB son poco confiables (en Mac no avisan de cambios hechos desde otra PC) y no escalan a miles de carpetas. Que un recurso de red se caiga no genera ningún aviso: hay que preguntar.
  - **Con el catálogo en el NAS:** que el NAS detecte los cambios donde están los archivos (inotify sobre sus discos locales sí es confiable), los anote en la base y avise a las dos PCs al instante con `LISTEN/NOTIFY` de PostgreSQL.
- **Sacar del catálogo una carpeta entera.** Hoy no hay forma de deshacer un catalogado hecho por error: el 15/11 se catalogó `/private/tmp` en la Mac de desarrollo (1249 archivos, incluidas fotos de un DNI). "Limpiar el catálogo" no los saca porque los archivos existen. Iría en las opciones avanzadas de Catalogar, con confirmación. (Ese caso ya se limpió a mano el 15/11; la función sigue faltando.)
- **Vista previa de videos al pasar el mouse, también en Buscar**, para los videos del catálogo.
- **Grabadora: probar "qué tiene el disco" con un disco real** enchufado al NAS. Ya está instalado en el NAS el 15/11, pero no había ningún disco para probar.
- **Revisar la jerga de los textos que vienen del NAS** (notas del modo automático y errores de copia), en el repo CDLM-Freenas.
- **Probar en la PC de la iglesia:** que se vea igual, que el ffmpeg de Windows funcione y que la descarga de videos ande.
- **Migración del catálogo al NAS (PostgreSQL)**, según `docs/catalogo-compartido.md`. Hoy el catálogo es una base local de cada PC.
- **Cambiar la contraseña de root del NAS** si sigue siendo la que estaba escrita en `Diagnostico-FreeNAS-Catedral.md`.

## Hecho

- 15/11/2026: Panel de Convertir rediseñado: «¿Cuántos en cada pantalla?» con iconos dibujados según la proporción de salida, «Fondo» aparte, vista previa de cada pantalla que se va a crear (tocándola se invierte un par) y botón que dice lo que crea («Crear 1 video»). Salida horizontal: 16:9 en la lista y «Otra…» para cualquier medida (ancho : alto); en horizontal, «2 por pantalla» va lado a lado y «Sobre un fondo» a la izquierda. Toda la ubicación sale de `convert/layout.py`, la misma para fotos, videos y vista previa.
- 15/11/2026: La app sigue el modo claro u oscuro del sistema también con la app abierta (antes se elegía solo al abrirla). Los iconos se vuelven a pintar solos.
- 15/11/2026: Revisión de textos con la skill `impeccable` (`clarify`). Las confirmaciones nombran la acción en el botón ("Reemplazar", "Detener copia", "Apagar") en lugar de Sí/No, y el botón seguro queda por defecto en lo que borra o corta; un solo verbo por concepto ("Quitar", no "Sacar"); estados vacíos y errores que dicen qué hacer; botones específicos ("Elegir carpeta…").
- 15/11/2026: Grabadora muestra el estado de la conexión (azul mientras conecta, naranja si falla, con Reintentar) y deja todo en gris hasta conectar.
- 15/11/2026: Limpiar el catálogo (⚙ de Catalogar): quita lo que ya no existe; lo que está en un disco o NAS desconectado no se toca.
