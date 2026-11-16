# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Convertir a 3:4 — CDLM** is a Python GUI desktop app for the Catedral de los Milagros church. It started as a photo aspect-ratio converter (default 8:9, matching the LED screen) and is evolving into an AI-powered media manager (catalog, search, tagging, drag-out to ProPresenter). The aspect-ratio converter remains as one tool ("Convertir" tab). Uses PyQt6 for the interface and Pillow for image processing. Distributed as a single `.exe` via GitHub Actions.

The original evolution plan (`/Users/alejandro/.claude/plans/necesito-que-dise-es-un-tranquil-crystal.md`) no longer exists.
The current design for the catalog lives in `docs/catalogo-compartido.md`: moving the local SQLite catalog to a
PostgreSQL database on the church NAS, shared by the editing PC and the ProPresenter PC over SMB. Read it before
touching `catalog/`, `engine/` or `appdata.py`.

## Commands

```bash
# Setup
python -m venv venv && source venv/bin/activate && pip install -r requirements.txt

# Run — always use the venv python, `python` may not resolve correctly
./venv/bin/python Convertir3x4.py      # shim entry point (also: python -m convertir3x4)

# Tests — pass both files explicitly (pytest's default discovery skips the tests.py name)
./venv/bin/python -m pytest tests.py tests_catalog.py tests_ai.py tests_clips.py tests_ui.py tests_convertir.py tests_discos.py -v
# tests_discos.py also runs the real panel-copias.py (fake lsblk/systemctl) when ~/Documents/Agents/CDLM-Freenas is present
# tests_ai.py also runs real-model tests when CDLM_MODELS points to a folder with the downloaded models

# Build executable
pyinstaller Convertir3x4.spec
```

## Architecture

Modular package `convertir3x4/`. The root `Convertir3x4.py` is a thin **shim**: it re-exports the public API (so `tests.py` and the PyInstaller spec keep working unchanged) and calls `convertir3x4.__main__.main()`. Entry point: `python -m convertir3x4` or `python Convertir3x4.py`.

```
convertir3x4/
  __main__.py            entry point (QApplication + DARK_STYLE + MainWindow)
  nas/copias.py          Qt-free urllib client for panel-copias (HTTP service on the NAS, port 8790,
                         token) that drives external-disk copies; resumir()/acciones_para() are pure
                         tipo_disco()/nota_operador(): recorder disk or not, read from what the NAS
                         already decided (USB + exFAT + only HyperDeck_NNNN files), never re-checked here
                         contenido()/resumir_contenido(): root of a plugged disk via the NAS
                         /v1/contenido (needs panel-copias.py + copiar-disco.py `contenido`, CDLM-Freenas)
  app.py                 MainWindow: left nav rail (Buscar/Catalogar/Convertir/Grabadora, Ctrl+1..4 via
                         QShortcut) + QStackedWidget; owns window geometry + closeEvent.
  config.py              constants (APP_VERSION, ASPECT_RATIOS, VIDEO_*, etc.)
  utils.py               open_folder()
  convert/               the aspect-ratio converter tool (pure, no Qt):
    images.py            convert_centered / convert_double / convert_on_background
                         + bg helpers (_dominant_colors, _make_textured_bg, _make_zoned_bg)
    video.py             convert_centered_video / convert_double_video / _write_video_frames
    clips.py             video sources (the ProPresenter agent's 8:9 recipe): centered with blurred
                         self-background, double (photo+video or 2 videos -> always video), on
                         background; audio to -16 LUFS / -1 dBTP with automatic limiter redo;
                         hover storyboard. All via imageio-ffmpeg subprocess (NO ffprobe).
                         See docs/convertir-fotos-y-videos.md
    layout.py            where each photo/video goes (Qt-free): 2 per screen stacked in vertical,
                         side by side in horizontal; first in grid goes top/left. Used by photos,
                         videos AND the Convertir preview, so the preview is exactly the output
    batch.py             Convertir jobs (Qt-free): plan() picks function + numbered output name
                         (0001_…) per mode centrada/doble/fondo; any video in a group -> MP4; run()
                         with progress/cancel, errors returned in operator words
    download.py          links via yt-dlp + deno kept in appdata.tools_dir(). yt-dlp is the UNPACKED
                         build (yt-dlp_macos.zip/yt-dlp_win.zip; the one-file build took ~16 s per
                         start on macOS), verified vs SHA2-256SUMS, own daily update (no -U), warmed
                         up once after install; deno pinned sha256. Prepared/updated at app start
                         (ConvertTab.revisar_herramientas); failed download -> per-cell "Reintentar"
                         that updates yt-dlp first if a newer one is out
  ui/
    style.py             theme: DARK/LIGHT palettes -> `T` (contrast-checked in tests_ui.py; follows
                         the OS light/dark at startup), apply_theme() = Fusion
                         + bundled Inter + palette + DARK_STYLE. Same look on every OS. Never put a
                         background on every QWidget (it paints boxes behind labels)
    icons.py             Lucide SVG icons recolored at load (they use currentColor)
    assets/              Inter TTFs (OFL) + Lucide SVGs (ISC); packaged by the .spec `datas`
    widgets/media_grid.py  ThumbDelegate (uniform Lightroom-like cells, video duration badge,
                         focus ring) + DragOutListWidget / ReorderListWidget; _fit_columns shares
                         leftover width (Qt reserves the hidden scrollbar's width: keep that margin)
    tabs/tab_convertir.py  ConvertTab: mixed photo+video grid, filter, links, modes, single
                           "Convertir N", item states, ⚙ advanced options (SessionOptions:
                           session-only, NEVER persisted; audio level is not an option).
                           No projects (.c3x4 removed 15/11/2026, nobody used them): the grid is
                           saved to QSettings on every change; "Vaciar" clears it (files untouched).
                           save_state/restore_state/confirm_close/shutdown for MainWindow.
                           `App` aliases it in the shim.
    tabs/tab_discos.py     DiscosTab, shown as "Grabadora" (operators call the HyperDeck recorder
                           "la grabadora"; never say HyperDeck/Discos in UI): its disks plugged into the NAS, copy progress, Copiar/Detener/
                           Verificar/Formatear, auto mode, history + log. Polls every 3 s while visible and every 20 s in the background (started by
                           __main__, not tests) to show a badge on the rail for unseen disks.
                           Dev without NAS: CDLM_PANEL_URLS / CDLM_PANEL_TOKEN (see tests_discos.py)
    tabs/convert_workers.py  ThumbWorker / ConvertWorker / DownloadWorker (QThread, cancellable)
    widgets/advanced_dialog.py, widgets/help_panel.py (overlay help, first run + F1)
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
    ai_models.py         model files (pinned HF revision + sha256), downloaded once to appdata.models_dir()
    vocab.py             church scene vocabulary: Spanish tag -> English CLIP prompts. Bump VOCAB_VERSION on change
    vision.py            SceneClassifier: CLIP ViT-B/32 on ONNX Runtime (CPU). Proposes a tag only if prob >= 0.60
    faces.py             FaceEngine: YuNet + SFace (OpenCV). Recognizes ONLY people in the `person` list
                         (cosine >= 0.42); other faces are discarded, never stored or clustered
    ai_pipeline.py       run_ai(): scene + faces over the catalog. Idempotent via ai_result.model / people_sig
```

**AI tags (Fase 5, present):** everything the AI proposes is `media_tag.source='ia', confirmed=0`
(purple chips in Buscar: ✓ confirms, ✕ removes). `suggest_tag()` never overrides an existing tag and
never re-adds one a person removed (`tag_rejection`). Face recognition is whitelist-only by church
decision (pastors, guests) — do not add clustering of unknown faces or store their embeddings.
Validated on real photos with `spikes/spike_vision.py`.

Tabs wired in `app.py`: **Buscar** (search catalog), **Catalogar** (run Stage 0 via MotorTab),
**Convertir**. SQLite rule: never share a connection across threads — each worker opens its own
inside `fn`; WAL lets the worker write while the UI reads. No new runtime deps were added
(ffprobe avoided via imageio; perceptual hash done in numpy).

Future phases add quarantine and optional local embeddings (`search/`). **When extending, keep the shim's re-exports in sync** so
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

- **PyQt6** with the app theme (`ui/style.py`) and custom `ReorderListWidget` (manual drag & drop since Qt's `InternalMove` is broken in `IconMode`; movement must stay `Static`)
- **Left panel**: toolbar (load/clear), image grid (`QListWidget` in `IconMode`), bottom bar (sort buttons + zoom slider)
- **Right sidebar**: action sections separated by lines (Foto centrada, Doble foto, Foto y fondo with background preview)
- State persistence via `QSettings` (image paths, thumb size, last directories) on the tab; window geometry on `MainWindow`
- Processing runs in `threading.Thread`, progress communicated via `pyqtSignal`

## Language

UI and user-facing text are in **Spanish**, formal register with **usted** ("Seleccione", "Elija",
"Haga clic"), careful vocabulary ("descargar", "enviar", "enlace", "etiqueta", "puntero") and no
technical jargon (decision of 15/11/2026; it replaced the earlier voseo). `tests_ui.py`
(`TestRegistroDeLosTextos`) fails if voseo or colloquial words come back into UI strings.
