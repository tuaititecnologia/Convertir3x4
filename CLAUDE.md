# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Convertir a 3:4 — CDLM** is a Python GUI desktop app that converts horizontal photos into 3:4 portrait format for a church display screen. It uses tkinter for the interface and Pillow for image processing. The final deliverable is a single Windows `.exe` built with PyInstaller and distributed via GitHub Actions.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
python Convertir3x4.py

# Build the Windows executable
pyinstaller Convertir3x4.spec
# or: pyinstaller --onefile --windowed --name Convertir3x4 Convertir3x4.py
```

## Architecture

- **`Convertir3x4.py`** — Single-file application containing all GUI and image processing logic
- **`Convertir3x4.spec`** — PyInstaller build spec (`--onefile --windowed`)
- **`.github/workflows/build.yml`** — CI: builds `.exe` on Windows runner, uploads artifact, creates release on `v*` tags

### Image Processing Pipeline

- Single photo conversion: original centered on 3:4 canvas, letterbox areas filled with a blurred (GaussianBlur radius 40) scaled version of the same image
- Two-photo combination: photos stacked vertically on a single 3:4 canvas, scaled proportionally to fit, blur fill in remaining gaps
- Output format: JPEG, quality 92
- Supported inputs: jpg, jpeg, png, bmp, webp

### UI Conventions

- Dark theme: background `#1e1e1e`, panels/cells `#2d2d2d`, selection highlight `#0078d4`
- Thumbnail grid with single-click and Ctrl+Click multi-selection
- Batch processing runs in a separate thread with progress bar to avoid freezing the UI
- Output folder selected via dialog before processing

## Language

The UI and user-facing text are in **Spanish**. INSTRUCTIONS.md is also in Spanish.
