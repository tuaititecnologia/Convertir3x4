# Python Desktop App Template — Multi-OS with PyInstaller + GitHub Actions

Reference template extracted from an existing project. Use this as a starting point for new Python GUI desktop apps distributed as standalone executables.

---

## Stack

| Layer | Tool |
|-------|------|
| GUI | **PyQt6** (dark theme via stylesheet, `QSettings` for persistence) |
| Image/Processing | **Pillow**, or whatever the domain needs |
| Packaging | **PyInstaller** (single `.exe` / binary, no installer) |
| CI/CD | **GitHub Actions** — build on push, release on tag |
| Dev workflow | **venv** + `entr` hot-reload |
| Tests | **pytest** |

---

## Project Structure

```
MyApp/
├── MyApp.py              # Single-file app (GUI + logic)
├── MyApp.spec            # PyInstaller spec (controls build)
├── requirements.txt      # Dependencies
├── tests.py              # Tests
├── dev.sh                # Hot-reload dev script
├── CLAUDE.md             # Instructions for Claude Code
├── .github/
│   └── workflows/
│       └── build.yml     # CI: build + release
└── venv/                 # Local virtualenv (not committed)
```

---

## Key Files

### `requirements.txt`

```
PyQt6
pyinstaller
pytest
# ... domain-specific deps
```

### `MyApp.spec` (PyInstaller)

```python
# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['MyApp.py'],
    pathex=[],
    binaries=[],
    datas=[],            # Add data files here: [('src', 'dest')]
    hiddenimports=[],    # Add imports PyInstaller misses
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MyApp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,       # False = no terminal window (GUI app)
    windowed=True,       # True = GUI mode
    # icon='icon.ico',   # Uncomment to set app icon
)
```

### `dev.sh` (hot-reload)

```bash
#!/bin/bash
# Hot-reload dev mode: restarts the app when .py files change
# Requires: brew install entr
ls *.py | entr -r ./venv/bin/python MyApp.py
```

### `.github/workflows/build.yml`

```yaml
name: Build Executable

on:
  push:
    branches: [main]
    tags: ['v*']
  pull_request:
    branches: [main]

jobs:
  build:
    runs-on: windows-latest    # Change per target OS, or use matrix

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Build executable
        run: pyinstaller MyApp.spec

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: MyApp-windows
          path: dist/MyApp.exe

  release:
    needs: build
    runs-on: ubuntu-latest
    if: startsWith(github.ref, 'refs/tags/v')
    permissions:
      contents: write

    steps:
      - name: Download artifact
        uses: actions/download-artifact@v4
        with:
          name: MyApp-windows

      - name: Create release
        uses: softprops/action-gh-release@v2
        with:
          files: MyApp.exe
          generate_release_notes: true
```

#### Multi-OS variant (matrix build)

Replace the `build` job with:

```yaml
  build:
    strategy:
      matrix:
        include:
          - os: windows-latest
            artifact: MyApp.exe
            name: MyApp-windows
          - os: macos-latest
            artifact: MyApp
            name: MyApp-macos
          - os: ubuntu-latest
            artifact: MyApp
            name: MyApp-linux
    runs-on: ${{ matrix.os }}

    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: pyinstaller MyApp.spec
      - uses: actions/upload-artifact@v4
        with:
          name: ${{ matrix.name }}
          path: dist/${{ matrix.artifact }}
```

And update `release` to download all artifacts.

---

## Commands (for CLAUDE.md)

```bash
# Setup
python -m venv venv && source venv/bin/activate && pip install -r requirements.txt

# Run — always use venv python directly
./venv/bin/python MyApp.py

# Tests
./venv/bin/python -m pytest tests.py -v

# Build executable
pyinstaller MyApp.spec

# Dev mode (hot-reload, requires entr)
chmod +x dev.sh && ./dev.sh
```

---

## CLAUDE.md Template

```markdown
# CLAUDE.md

## Project Overview

**MyApp** is a Python GUI desktop app that [description]. Uses PyQt6 for the interface. Distributed as a standalone executable via GitHub Actions.

## Commands

\```bash
# Setup
python -m venv venv && source venv/bin/activate && pip install -r requirements.txt

# Run — always use the venv python, `python` may not resolve correctly
./venv/bin/python MyApp.py

# Tests — use the venv python directly (shell activation doesn't work in non-interactive shells)
./venv/bin/python -m pytest tests.py -v

# Build executable
pyinstaller MyApp.spec
\```

## Architecture

Single-file app (`MyApp.py`). [Describe structure.]

## Language

UI and user-facing text are in **Spanish**.
```

---

## Workflow

1. **Develop** on macOS with `dev.sh` (hot-reload via `entr`)
2. **Test** with `pytest`
3. **Push** to `main` — GitHub Actions builds the `.exe` automatically
4. **Release** by pushing a tag: `git tag v1.0.0 && git push --tags` — creates a GitHub Release with the binary attached

---

## Tips

- **`console=False` + `windowed=True`** in the spec file = no terminal window on launch
- **`upx=True`** compresses the binary (smaller download)
- **`QSettings`** is great for persisting user preferences across runs (cross-platform)
- **`threading.Thread` + `pyqtSignal`** for background processing without freezing the UI
- Use **venv python directly** (`./venv/bin/python`) in scripts and Claude Code — shell activation is unreliable in non-interactive contexts
- **`entr`** (`brew install entr` on macOS) gives you hot-reload for free
