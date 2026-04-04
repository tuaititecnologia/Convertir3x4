# Convertir a 3:4 — CDLM

App para convertir fotos a formato 3:4 portrait para la pantalla de la iglesia.

## Descargar

Ir a [Releases](../../releases) y descargar `Convertir3x4.exe`. No requiere instalación.

## Uso

1. **Cargar imágenes**: Seleccionar las fotos a procesar
2. Elegir la acción en el panel derecho:
   - **Foto centrada**: Convierte cada foto a 3:4 con fondo de colores de la imagen
   - **Doble foto**: Apila pares de fotos en un canvas 3:4 (seleccionar cantidad par)
   - **Foto y fondo**: Monta las fotos sobre una imagen de fondo, dejando espacio inferior para versículos

Al procesar se pide la carpeta de salida y se abren los resultados automáticamente.

## Atajos

- **Click**: Seleccionar una foto
- **Ctrl+Click**: Agregar/quitar de la selección
- **Shift+Click**: Seleccionar rango
- **Arrastrar**: Reordenar fotos en la grilla

## Desarrollo

```bash
python -m venv venv
source venv/bin/activate  # o venv\Scripts\activate en Windows
pip install -r requirements.txt
python Convertir3x4.py
```
