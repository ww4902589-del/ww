# -*- mode: python ; coding: utf-8 -*-


# numpy is excluded on purpose: nothing here imports it, and it only arrives
# because Pillow's ``PIL/Image.py`` contains ``import numpy as np`` inside two
# unused methods, which a static analysis follows anyway. See S.spec for the full
# note and the measurements.
a = Analysis(
    ['comfybatch_v2_app.py'],
    pathex=[],
    binaries=[],
    datas=[('index.html', '.'), ('app.css', '.'), ('app.js', '.'), ('default_presets.json', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ComfyBatch-V2',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
