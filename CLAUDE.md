# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Convertir a 3:4 — CDLM** is a Python GUI desktop app for the Catedral de los Milagros church. It started as a photo aspect-ratio converter (default 8:9, matching the LED screen) and is evolving into an AI-powered media manager (catalog, search, tagging, drag-out to ProPresenter). The aspect-ratio converter remains as one tool ("Convertir" tab). Uses PyQt6 for the interface and Pillow for image processing. Distributed as a single `.exe` via GitHub Actions.

The original evolution plan (`/Users/alejandro/.claude/plans/necesito-que-dise-es-un-tranquil-crystal.md`) no longer exists.
The catalog lives in a PostgreSQL database on the church NAS (TrueNAS App), shared by the editing PC and the
ProPresenter PC, which read the media over SMB from `\\truenas\multimedia`. SQLite is gone (18/11/2026).
Design and decisions: `docs/catalogo-compartido.md`. Read it before touching `catalog/`, `engine/` or `appdata.py`.

## Commands

```bash
# Setup
python -m venv venv && source venv/bin/activate && pip install -r requirements.txt

# Run — always use the venv python, `python` may not resolve correctly
./venv/bin/python Convertir3x4.py      # shim entry point (also: python -m convertir3x4)

# Tests — pass both files explicitly (pytest's default discovery skips the tests.py name)
./venv/bin/python -m pytest tests.py tests_catalog.py tests_ai.py tests_clips.py tests_ui.py tests_convertir.py tests_discos.py tests_catalogador.py tests_v08.py tests_actualizacion.py tests_ocr.py -v
# tests_discos.py also runs the real panel-copias.py (fake lsblk/systemctl) when ~/Documents/Agents/CDLM-Freenas is present
# tests_ai.py also runs real-model tests when CDLM_MODELS points to a folder with the downloaded models
# Catalog tests (tests_catalog/tests_ai/tests_ui) need the dev PostgreSQL; fixtures in conftest.py create a
# throwaway DB per test and skip without the server (CDLM_TEST_PG overrides). Same image as the NAS:
docker run -d --name cdlm-catalogo-dev --restart unless-stopped -p 127.0.0.1:55432:5432 \
  -e POSTGRES_USER=catalogo -e POSTGRES_PASSWORD=catalogo-dev -e POSTGRES_DB=catalogo \
  -v cdlm-catalogo-dev:/var/lib/postgresql postgres:18.7-trixie

# Run against the dev catalog (instead of a catalogo.ini next to the .exe):
CDLM_CATALOGO_SERVIDOR=127.0.0.1 CDLM_CATALOGO_PUERTO=55432 CDLM_CATALOGO_CLAVE=catalogo-dev \
  CDLM_CATALOGO_RAIZ=/Volumes/multimedia ./venv/bin/python Convertir3x4.py   # folders: Catalogar ⚙

# Build executable (venv/bin/pip has a stale shebang: use python -m)
./venv/bin/python -m PyInstaller Convertir3x4.spec

# NAS catalogador image (CI publishes it on tags as ghcr.io/tuaititecnologia/convertir3x4-catalogador:vX.Y.Z)
docker build -f Dockerfile.catalogador -t cdlm-catalogador:dev .
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
                         explicar(): what happened / what to do for each copiar-disco.py error; ficha(): copy
                         data rows; copiados()/filas_copiados(): files copied (panel v2 /v1/copiados, None if older)
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
                           Verificar/Formatear, auto mode, history + copy detail (state, explanation, data, copied files,
                           log). Adaptive: history and detail side by side only if the list's natural width + 480 px fit
                           (decided by the scroll viewport width, with hysteresis), otherwise stacked; whole tab scrolls.
                           Polls every 3 s while visible and every 20 s in the background (started by
                           __main__, not tests) to show a badge on the rail for unseen disks.
                           Dev without NAS: CDLM_PANEL_URLS / CDLM_PANEL_TOKEN (see tests_discos.py)
    tabs/convert_workers.py  ThumbWorker / ConvertWorker / DownloadWorker (QThread, cancellable)
    widgets/advanced_dialog.py, widgets/help_panel.py (overlay help, first run + F1)
    widgets/detalle_medio.py  PanelDetalles: Buscar's details panel beside the grid (QSplitter; key I or «Detalles»
                         toggles it; open state + width in QSettings buscar/detalles*). Replaces the item tooltip.
                         One item: preview (original photo read off-thread), data, tags, OCR text, file path;
                         several: summary («5 seleccionados: 4 fotos y 1 video», dates, size, tags n/N) + list (click
                         = show it in the grid). Built only from the loaded Media + local thumbnails: no queries
    widgets/adaptable.py   Adaptable: two parts side by side if the width allows, else stacked (Grabadora history +
                           detail, Catalogar people + detail; both decide by the scroll viewport width)
```

**Media-manager layers (catalog on the NAS):**

```
convertir3x4/
  appdata.py             per-user data dir + catalog config: config_catalogo() reads CDLM_CATALOGO_* (dev) or
                         catalogo.ini next to the .exe / in the data dir (see catalogo.ini.ejemplo; never commit
                         catalogo.ini). abrir_repo() = conectar + Repo; catalog_thumb_dir() = local thumb cache
  catalogador.py         the NAS cataloger (headless, Qt-free; `python -m convertir3x4.catalogador`, Docker image
                         Dockerfile.catalogador): nightly pass + requests from the PCs via catalogado_pedido/
                         catalogado_estado (+ NOTIFY), heartbeat thread, waits while a recorder disk is copied.
                         Config from env (CDLM_CATALOGO_*, CDLM_MODELOS, CDLM_HORA…). See docs/catalogado-en-el-nas.md
                         The folder list is in the DB (carpeta_catalogada, managed from Catalogar's ⚙), not env.
                         Pedidos carry pasos (archivos/miniaturas/escenas/personas/texto) + rehacer; the AI honors the folder.
                         NEVER run it on a Mac against real data: macOS SMB returns accented names as NFD, they
                         don't match the NAS's NFC paths and get "moved" (main() refuses on darwin)
  actualizacion.py       update check (Qt-free): latest GitHub release (public repo, no token); on Windows the
                         frozen .exe downloads <exe>.nuevo (size + sha256 digest checked) and, on close, a .cmd
                         (paths as args, retries until the .exe is free) swaps it and reopens. Mac/Linux: link only.
                         UI: ui/widgets/aviso_actualizacion.py (bar above the modules, started by __main__, never in dev)
                         tests_actualizacion.py also runs in CI on Windows (real swap of a running .exe)
  workers.py             EngineWorker(QThread): runs fn(progress_cb) off the UI thread; `fallo` carries the exception
  catalog/               the truth — PostgreSQL (Qt-free):
    db.py                ConfigCatalogo (servidor, clave, raiz, carpetas), conectar() (autocommit, 3 s timeout,
                         CatalogoNoDisponible / CatalogoIncompatible / CatalogoNoConfigurado), numbered MIGRACIONES
                         as text. Search: tsvector `search_doc` (config `catalogo` = simple+unaccent, palabras())
                         + pg_trgm index on texto_nombre(). Thumbnails in `thumbnail` (bytea + sello md5)
    repo.py              Repo(conn, raiz): ALL SQL lives here. Paths stored as rel_path (relative to raiz, "/");
                         Media.path = full path on this PC. Missing files get missing_since (never auto-deleted);
                         mover() keeps tags. tomar_carpeta(): advisory lock per folder
                         buscar(): one paged query for Buscar (two-layer search, missing last, kind filter, total,
                         tags per item, no search_doc); Buscar loads 120 at a time as you scroll
    rutas.py             a_relativa / a_completa (UNC, drive letters, posix)
    referencias.py       do a person's reference photos work? Judges each one (no face, several faces of similar
                         size, small/unclear face, and cosine vs the person's other photos: an outlier may be someone
                         else) + thumbnail with the chosen face boxed and the face crop. Data: Repo.detalle_persona;
                         face count/box/score written by the NAS (schema v6, faces.revisar_referencia)
    cache_miniaturas.py  local copy of DB thumbnails as <id>-<sello>.jpg; Buscar reads from disk
    thumbs.py            PIL thumbnail -> JPEG bytes (no Qt → safe in worker threads). Videos use the first
                         non-black frame at 10/30/50 %; en_negro() flags black thumbs (quality 'en_negro', schema v5,
                         AI skips them). Bump MINIATURA_VERSION when thumbs change: the NAS redoes them once
    video_ffmpeg.py      video metadata + frames via imageio-ffmpeg directly (imageio picks by extension
                         and refuses .mts/.m2t)
    ubicacion.py         storage_root / availability: one exists() per ROOT only (offline NAS, ~80 ms per SMB stat
                         over Netbird); deleted/moved files come from the NAS (missing_since)
  engine/                the engine — cheap→expensive cascade (Qt-free):
    inventory.py         scan() folders for media (reports unreadable dirs); kind_for()
    probe.py             technical metadata: PIL for images, imageio (bundled ffmpeg, NO ffprobe) for video.
                         Computes quality_flag (ok/low_res/corrupt) inline — no separate quality module.
    dedup.py             sha256 (hashlib) + dHash (numpy, NO imagehash/scipy) + hamming()
    pipeline.py          catalogar(repo, carpetas): lock each folder → scan ALL + mark missing (only if the folder
                         was read whole) → per file skip/move/probe+thumb+upsert → sha256 ONLY for same-size files.
                         Idempotent. run_stage0(repo, root) = one folder. CatalogoOcupado if another PC has it
    ai_models.py         model files (pinned HF revision + sha256), downloaded once to appdata.models_dir()
    vocab.py             church scene vocabulary: Spanish tag -> English CLIP prompts. Bump VOCAB_VERSION on change
    vision.py            SceneClassifier: CLIP ViT-B/32 on ONNX Runtime (CPU). Proposes a tag only if prob >= 0.60
    faces.py             FaceEngine: YuNet + SFace (OpenCV). Recognizes ONLY people in the `person` list
                         (cosine >= 0.42); other faces are discarded, never stored or clustered
    ai_pipeline.py       run_ai(): scene + faces over the catalog (videos via their DB thumbnail). Idempotent
    ocr.py               text in photos (v0.8.1, schema 8 `texto_foto`): PaddleOCR det v3 mobile + rec v5 latin ONNX
                         (ai_models), own DB/CTC pre/post (no paddle/pyclipper). Photos only. leer_fotos() = the
                         «texto» step; text goes into search_doc (weight B) and Buscar's tooltip. Bump OCR_VERSION
                         to re-read. ~1.5 s/photo on the NAS (4,053 photos ≈ 1 h 45 min once), 400 MB peak
  ui/avisos_catalogo.py  AvisosCatalogo(QThread): own connection, LISTEN catalogo (schema v3 triggers on media,
                         media_tag, tag) → `cambio`; reconnects alone and emits on reconnect. Started by __main__
                         (MainWindow.iniciar_avisos), not by tests. BuscarTab.cambio_en_el_catalogo re-queries keeping
                         selection + scroll, waits while dragging/selecting/menu open, catches up when shown again.
                         The app is client-server: the NAS pushes changes, PCs never poll the catalog for them
  ui/servicio_catalogo.py  ServicioCatalogo: ONE thread + connection for the UI; pedir(fn(repo), ok, error);
                         reconnects alone; estado_cambiado("conectando"/"ok"/"error", text). servicio() = app-wide
  ui/widgets/caja_conexion.py  connection banner (same look as Grabadora); catalogo_avanzado.py = ⚙ of Catalogar
                         (catalog one subfolder, remove missing)
```

**AI tags (Fase 5, present):** everything the AI proposes is `media_tag.source='ia', confirmed=0`
(purple chips in Buscar: ✓ confirms, ✕ removes). `suggest_tag()` never overrides an existing tag and
never re-adds one a person removed (`tag_rejection`). Face recognition is whitelist-only by church
decision (pastors, guests) — do not add clustering of unknown faces or store their embeddings.
Validated on real photos with `spikes/spike_vision.py`.

Tabs wired in `app.py`: **Buscar** (search catalog), **Catalogar** (MotorTab: since v0.7.0 the PCs do NOT catalog;
it asks the NAS «Catalogar ahora» and shows catalogado_estado, polling every 3 s while visible; people list stays here, with a
Grabadora-like detail of the selected person (each reference photo, the face used, warnings, «Quitar esta foto»):
since v0.7.2 the PC only uploads reduced reference photos (schema v4, person_reference.estado 'pendiente') and the NAS
catalogador computes the face signatures — the PC never downloads or loads AI models, and the .spec excludes onnxruntime/cv2/tokenizers),
**Convertir**. Connection rules: the UI thread NEVER touches the DB or the NAS (an offline NAS can block for
seconds) — short queries go through `servicio_catalogo`, long passes through EngineWorker with their own
`with abrir_repo() as repo:`; never share a connection across threads. Missing/offline media are drawn in
grayscale (`media_grid._en_gris`). Runtime dep: psycopg[binary] (in the .spec hiddenimports).

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
