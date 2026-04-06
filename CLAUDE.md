# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Convertir a 3:4 — CDLM** is a Python GUI desktop app that converts photos into a configurable aspect ratio (default 8:9, matching the LED screen) for a church display screen. Uses PyQt6 for the interface and Pillow for image processing. Distributed as a single `.exe` via GitHub Actions.

## Commands

```bash
# Setup
python -m venv venv && source venv/bin/activate && pip install -r requirements.txt

# Run — always use the venv python, `python` may not resolve correctly
./venv/bin/python Convertir3x4.py

# Tests — use the venv python directly (shell activation doesn't work in non-interactive shells)
./venv/bin/python -m pytest tests.py -v

# Build executable
pyinstaller Convertir3x4.spec
```

## Architecture

Single-file app (`Convertir3x4.py`) with all GUI and image processing logic. Three processing functions at module level, UI in the `App` class.

### Image Processing Functions

- `convert_centered(src, out)` — Photo on 3:4 canvas with textured background from dominant colors (not blur of the image itself)
- `convert_double(paths, out)` — Two photos stacked vertically on 3:4 canvas. Wider photo gets up to 45% height, taller photo fills remaining space. Background uses `_make_zoned_bg` (colors from each photo dominate their respective half)
- `convert_on_background(src, bg, out)` — Photo in upper half of 3:4 canvas over a user-provided background image, lower half left free for text overlay

### Background Generation

- `_dominant_colors(img)` — Extracts palette via FASTOCTREE quantization
- `_make_textured_bg(w, h, colors)` — Random soft blobs with dominant colors, heavily blurred
- `_make_zoned_bg(w, h, colors_top, colors_bottom)` — Same as above but spatially coherent (top colors dominate top, bottom colors dominate bottom)

### UI Structure

- **PyQt6** with dark theme stylesheet and custom `ReorderListWidget` (manual drag & drop since Qt's `InternalMove` is broken in `IconMode`)
- **Left panel**: toolbar (load/clear), image grid (`QListWidget` in `IconMode`), bottom bar (sort buttons + zoom slider)
- **Right sidebar**: action sections separated by lines (Foto centrada, Doble foto, Foto y fondo with background preview)
- State persistence via `QSettings` (image paths, window geometry, thumb size, last directories)
- Processing runs in `threading.Thread`, progress communicated via `pyqtSignal`

## Language

UI and user-facing text are in **Spanish**.
