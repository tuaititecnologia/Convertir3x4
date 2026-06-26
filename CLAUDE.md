# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Convertir a 3:4 — CDLM** is a Python GUI desktop app for the Catedral de los Milagros church. It started as a photo aspect-ratio converter (default 8:9, matching the LED screen) and is evolving into an AI-powered media manager (catalog, search, tagging, drag-out to ProPresenter). The aspect-ratio converter remains as one tool ("Convertir" tab). Uses PyQt6 for the interface and Pillow for image processing. Distributed as a single `.exe` via GitHub Actions.

The full evolution plan lives at `/Users/alejandro/.claude/plans/necesito-que-dise-es-un-tranquil-crystal.md`.

## Commands

```bash
# Setup
python -m venv venv && source venv/bin/activate && pip install -r requirements.txt

# Run — always use the venv python, `python` may not resolve correctly
./venv/bin/python Convertir3x4.py      # shim entry point (also: python -m convertir3x4)

# Tests — pass both files explicitly (pytest's default discovery skips the tests.py name)
./venv/bin/python -m pytest tests.py tests_catalog.py -v

# Build executable
pyinstaller Convertir3x4.spec
```

## Architecture

Modular package `convertir3x4/`. The root `Convertir3x4.py` is a thin **shim**: it re-exports the public API (so `tests.py` and the PyInstaller spec keep working unchanged) and calls `convertir3x4.__main__.main()`. Entry point: `python -m convertir3x4` or `python Convertir3x4.py`.

```
convertir3x4/
  __main__.py            entry point (QApplication + DARK_STYLE + MainWindow)
  app.py                 MainWindow: QMainWindow with a QTabWidget; owns window
                         geometry + closeEvent. Currently one tab ("Convertir").
  config.py              constants (APP_VERSION, ASPECT_RATIOS, VIDEO_*, etc.)
  utils.py               open_folder()
  convert/               the aspect-ratio converter tool (pure, no Qt):
    images.py            convert_centered / convert_double / convert_on_background
                         + bg helpers (_dominant_colors, _make_textured_bg, _make_zoned_bg)
    video.py             convert_centered_video / convert_double_video / _write_video_frames
    project.py           read_project_file() — .c3x4 JSON load/validate
  ui/
    style.py             DARK_STYLE stylesheet (+ QTabWidget styling)
    widgets/media_grid.py  ReorderListWidget + PATH_ROLE / MTIME_ROLE
    tabs/tab_convertir.py  ConvertTab (QWidget): the former `App`, now a tab.
                           Emits titleChanged(str); exposes save_state/restore_state/
                           confirm_close for MainWindow. `App` is aliased to ConvertTab
                           in the shim (tests call App._read_project_file).
```

**Media-manager layers (Fase 2, present):**

```
convertir3x4/
  appdata.py             resolves per-user data dir (catalog.db + thumbs/) via QStandardPaths
  workers.py             EngineWorker(QThread): runs fn(progress_cb) off the UI thread
  catalog/               the truth — SQLite (Qt-free):
    db.py                connect()/schema/migrations; FTS5 table media_fts (rowid = media.id)
    models.py            Media, ProbeResult dataclasses
    repo.py              Repo: all SQL lives here (upsert_media, dedup, tags, search). Keeps FTS in sync.
    thumbs.py            PIL thumbnail generation (no Qt → safe in worker threads)
  engine/                the engine — cheap→expensive cascade (Qt-free):
    inventory.py         scan() folders for media; kind_for(); folder_origin()
    probe.py             technical metadata: PIL for images, imageio (bundled ffmpeg, NO ffprobe) for video.
                         Computes quality_flag (ok/low_res/corrupt) inline — no separate quality module.
    dedup.py             sha256 (hashlib) + dHash (numpy, NO imagehash/scipy) + hamming()
    pipeline.py          run_stage0(): inventory→probe→hash→thumb→upsert. Idempotent (skips unchanged).
```

Tabs wired in `app.py`: **Buscar** (search catalog), **Catalogar** (run Stage 0 via MotorTab),
**Convertir**. SQLite rule: never share a connection across threads — each worker opens its own
inside `fn`; WAL lets the worker write while the UI reads. No new runtime deps were added
(ffprobe avoided via imageio; perceptual hash done in numpy).

Future phases add AI vision (`engine/vision.py`), tagging UI, quarantine, and optional local
embeddings (`search/`) per the plan. **When extending, keep the shim's re-exports in sync** so
the tests and `.spec` entry point don't break.

### Image Processing Functions

- `convert_centered(src, out)` — Photo on 3:4 canvas with textured background from dominant colors (not blur of the image itself)
- `convert_double(paths, out)` — Two photos stacked vertically on 3:4 canvas. Wider photo gets up to 45% height, taller photo fills remaining space. Background uses `_make_zoned_bg` (colors from each photo dominate their respective half)
- `convert_on_background(src, bg, out)` — Photo in upper half of 3:4 canvas over a user-provided background image, lower half left free for text overlay

### Video Export Functions

- `convert_centered_video(src, out, ...)` — Same as `convert_centered` but outputs MP4 with animated background (subtle pan via Lissajous path over oversized background)
- `convert_double_video(paths, out, ...)` — Same as `convert_double` but outputs MP4 with animated background
- `_write_video_frames(oversized_bg, canvas_w, canvas_h, paste_fn, out_path, ...)` — Shared helper that generates frames with Lissajous camera movement and writes MP4 via imageio/ffmpeg

### Background Generation

- `_dominant_colors(img)` — Extracts palette via FASTOCTREE quantization
- `_make_textured_bg(w, h, colors)` — Random soft blobs with dominant colors, heavily blurred. For video, called at 1.3x canvas size to allow pan animation
- `_make_zoned_bg(w, h, colors_top, colors_bottom)` — Same as above but spatially coherent (top colors dominate top, bottom colors dominate bottom)

### UI Structure

The "Convertir" tab (`ui/tabs/tab_convertir.py`) holds all of the converter UI:

- **PyQt6** with dark theme stylesheet (`ui/style.py`) and custom `ReorderListWidget` (manual drag & drop since Qt's `InternalMove` is broken in `IconMode`)
- **Left panel**: toolbar (load/clear), image grid (`QListWidget` in `IconMode`), bottom bar (sort buttons + zoom slider)
- **Right sidebar**: action sections separated by lines (Foto centrada, Doble foto, Foto y fondo with background preview)
- State persistence via `QSettings` (image paths, thumb size, last directories) on the tab; window geometry on `MainWindow`
- Processing runs in `threading.Thread`, progress communicated via `pyqtSignal`

## Language

UI and user-facing text are in **Spanish**.
