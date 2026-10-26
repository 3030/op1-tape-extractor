# -*- mode: python ; coding: utf-8 -*-
# Windows build (onedir) using QtMultimedia playback

import os
from PyInstaller.utils.hooks import collect_submodules, collect_dynamic_libs

version = "1.0.dev"
if os.path.exists('version.py'):
    with open('version.py', 'r') as f:
        for line in f:
            if line.startswith('__version__'):
                version = line.split('=')[1].strip().strip('"').strip("'")
                break

datas = [('font', 'font'), ('assets', 'assets'), ('version.py', '.')]
hidden = ['numpy', 'soundfile', 'pyqtgraph'] + collect_submodules('PySide6.QtMultimedia')
libsndfile_bins = collect_dynamic_libs('soundfile')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=libsndfile_bins,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'pandas', 'PIL', 'cv2', 'sounddevice'],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='OP-1 Loop Extractor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon='icon/icon.ico' if os.path.exists('icon/icon.ico') else None,
)
