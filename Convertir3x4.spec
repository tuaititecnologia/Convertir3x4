# -*- mode: python ; coding: utf-8 -*-

import sys

import imageio_ffmpeg
from PyInstaller.utils.hooks import copy_metadata

_ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

_metadata = []
for pkg in ('imageio', 'imageio_ffmpeg', 'numpy', 'Pillow', 'psycopg', 'psycopg-binary'):
    _metadata += copy_metadata(pkg)

a = Analysis(
    ['Convertir3x4.py'],
    pathex=[],
    binaries=[(_ffmpeg_exe, 'imageio_ffmpeg/binaries')],
    # Tipografía Inter e iconos Lucide del tema (ui/style.py, ui/icons.py)
    datas=_metadata + [('convertir3x4/ui/assets', 'convertir3x4/ui/assets')],
    hiddenimports=['imageio', 'imageio_ffmpeg', 'imageio.plugins.ffmpeg', 'numpy',
                   'onnxruntime', 'cv2', 'tokenizers', 'PyQt6.QtSvg',
                   # Catálogo en PostgreSQL: psycopg carga su parte compilada por nombre
                   'psycopg', 'psycopg_binary', 'psycopg_binary.pq', 'psycopg_binary._psycopg'],
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
    name='Convertir3x4',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    windowed=True,
)

if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='Convertir3x4.app',
        icon=None,
        bundle_identifier='com.cdlm.convertir3x4',
        info_plist={
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '11.0',
        },
    )
