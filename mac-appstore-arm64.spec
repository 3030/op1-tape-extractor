# -*- mode: python ; coding: utf-8 -*-
# Mac App Store build (onedir) using QtMultimedia playback

import os
from PyInstaller.utils.hooks import collect_submodules, collect_dynamic_libs

version = "1.0.dev"
if os.path.exists('version.py'):
    with open('version.py', 'r') as f:
        for line in f:
            if line.startswith('__version__'):
                version = line.split('=')[1].strip().strip('"').strip("'")
                break

datas = [('font', 'font'), ('assets', 'assets')]
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
    [],
    exclude_binaries=True,
    name='OP-1 Loop Extractor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=True,
    target_arch='arm64',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='OP-1 Loop Extractor',
)

app = BUNDLE(
    coll,
    name='OP-1 Loop Extractor.app',
    icon='icon.icns',
    bundle_identifier='com.3030.op1loopextractor',
    info_plist={
        'CFBundleIdentifier': 'com.3030.op1loopextractor',
        'CFBundleName': 'OP-1 Loop Extractor',
        'CFBundleDisplayName': 'OP-1 Loop Extractor',
        'CFBundleVersion': version,
        'CFBundleShortVersionString': version,
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.15.0',
        'LSApplicationCategoryType': 'public.app-category.music',
        'ITSAppUsesNonExemptEncryption': False,
    },
)
