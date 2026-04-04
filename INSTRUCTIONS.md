Necesito crear un proyecto Python para una app GUI (tkinter + Pillow) que convierte fotos horizontales a formato 3:4 portrait para una pantalla de iglesia. El proyecto tiene que estar listo para compilar con PyInstaller y distribuirse como .exe de Windows via GitHub Actions.

La app tiene estas funciones:
1. Botón para cargar imágenes (jpg, jpeg, png, bmp, webp)
2. Grilla de thumbnails con selección (click simple y Ctrl+Click para múltiple)
3. Convertir 1 o más fotos a 3:4: la foto original queda centrada, las franjas de arriba y abajo se rellenan con la misma imagen escalada y con GaussianBlur (radio 40)
4. Combinar 2 fotos seleccionadas: apilarlas verticalmente en un solo canvas 3:4, con fondo blur en los huecos. Si las 2 fotos no entran, reducir proporcionalmente para que encajen
5. Botón "Convertir todas" para procesamiento por lote
6. Barra de progreso, procesamiento en hilo separado para no congelar la UI
7. Pide carpeta de salida con diálogo antes de procesar

El archivo principal ya existe en Convertir3x4.py. Necesito que:
- Creés un requirements.txt (pillow, pyinstaller)
- Creés un .github/workflows/build.yml que compile el .exe con PyInstaller en un runner Windows, y lo suba como artifact descargable. También que genere un release automático cuando se pushee un tag con formato v*
- Creés un .spec de PyInstaller si hace falta para que el exe quede --onefile --windowed con un nombre limpio tipo "Convertir3x4.exe"
- Creés un README.md mínimo con instrucciones de uso para la gente de multimedia

La app se llama "Convertir a 3:4 — CDLM". Calidad JPEG de salida: 92. La interfaz es tema oscuro (#1e1e1e fondo, #2d2d2d celdas, #0078d4 selección).
