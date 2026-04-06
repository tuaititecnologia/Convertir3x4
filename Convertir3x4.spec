# -*- mode: python ; coding: utf-8 -*-

import imageio_ffmpeg
_ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

a = Analysis(
    ['Convertir3x4.py'],
    pathex=[],
    binaries=[(_ffmpeg_exe, 'imageio_ffmpeg/binaries')],
    datas=[],
    hiddenimports=['imageio', 'imageio_ffmpeg', 'numpy'],
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
