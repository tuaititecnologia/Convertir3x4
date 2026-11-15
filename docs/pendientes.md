# Pendientes

Ideas y tareas que van surgiendo mientras se prueba la app. Se anotan acá para no perderlas y se van tachando o moviendo a "Hecho".

## Por hacer

- **Rediseñar el panel de Convertir para que se entienda qué sale.** Hoy, con dos videos, no queda claro si se crean dos videos o uno con los dos. Definido el 15/11/2026:
  - Selector **"¿Cuántos en cada pantalla?"** con dos iconos dibujados según la proporción de salida: "1 por pantalla" y "2 por pantalla" (bocetos en `~/Downloads/CDLM-bocetos`). Reemplaza Centrada/Doble.
  - **Fondo** aparte: de sus colores, o uno que elijas (este último solo con 1 por pantalla).
  - **Vista previa** de cada pantalla que se va a crear, con las fotos y videos ubicados (qué va arriba y qué abajo), y poder invertir un par.
  - **El botón dice lo que crea:** "Crear 1 video", "Crear 2 videos", no la cantidad seleccionada.
  - Falta decidir si hace falta una salida horizontal (16:9). Si sí, "2 por pantalla" tiene que armarse uno al lado del otro, como muestra el icono.

- **Sacar del catálogo una carpeta entera.** Hoy no hay forma de deshacer un catalogado hecho por error: el 15/11 se catalogó `/private/tmp` en la Mac de desarrollo (1249 archivos, incluidas fotos de un DNI). "Limpiar el catálogo" no los saca porque los archivos existen. Iría en las opciones avanzadas de Catalogar, con confirmación.
- **Vista previa de videos al pasar el mouse, también en Buscar**, para los videos del catálogo.
- **Grabadora: probar "qué tiene el disco" con un disco real** enchufado al NAS. Ya está instalado en el NAS el 15/11, pero no había ningún disco para probar.
- **Revisar la jerga de los textos que vienen del NAS** (notas del modo automático y errores de copia), en el repo CDLM-Freenas.
- **Probar en la PC de la iglesia:** que se vea igual, que el ffmpeg de Windows funcione y que la descarga de videos ande.
- **Migración del catálogo al NAS (PostgreSQL)**, según `docs/catalogo-compartido.md`. Hoy el catálogo es una base local de cada PC.
- **Cambiar la contraseña de root del NAS** si sigue siendo la que estaba escrita en `Diagnostico-FreeNAS-Catedral.md`.

## Hecho

- 15/11/2026: La app sigue el modo claro u oscuro del sistema también con la app abierta (antes se elegía solo al abrirla). Los iconos se vuelven a pintar solos.
- 15/11/2026: Revisión de textos con la skill `impeccable` (`clarify`). Las confirmaciones nombran la acción en el botón ("Reemplazar", "Detener copia", "Apagar") en lugar de Sí/No, y el botón seguro queda por defecto en lo que borra o corta; un solo verbo por concepto ("Quitar", no "Sacar"); estados vacíos y errores que dicen qué hacer; botones específicos ("Elegir carpeta…").
- 15/11/2026: Grabadora muestra el estado de la conexión (azul mientras conecta, naranja si falla, con Reintentar) y deja todo en gris hasta conectar.
- 15/11/2026: Limpiar el catálogo (⚙ de Catalogar): quita lo que ya no existe; lo que está en un disco o NAS desconectado no se toca.
