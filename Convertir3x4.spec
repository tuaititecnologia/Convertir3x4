# -*- mode: python ; coding: utf-8 -*-

import imageio_ffmpeg
from PyInstaller.utils.hooks import copy_metadata

_ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

_metadata = []
for pkg in ('imageio', 'imageio_ffmpeg', 'numpy', 'Pillow'):
    _metadata += copy_metadata(pkg)

a = Analysis(
    ['Convertir3x4.py'],
    pathex=[],
    binaries=[(_ffmpeg_exe, 'imageio_ffmpeg/binaries')],
    datas=_metadata,
    hiddenimports=['imageio', 'imageio_ffmpeg', 'imageio.plugins.ffmpeg', 'numpy'],
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
