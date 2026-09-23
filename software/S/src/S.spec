# -*- mode: python ; coding: utf-8 -*-

# numpy is excluded on purpose, and it is worth 26 MB (6 MB compressed).
# Nothing in this program imports it: the only image work is ``Image.open``,
# ``image.size``, ``image.verify()``, ``ImageChops`` and ``ImageStat``. It arrives
# because Pillow's own ``PIL/Image.py`` contains ``import numpy as np`` inside two
# methods (for the array-interface helpers), and PyInstaller resolves imports
# statically -- so it followed a line this program never executes and applied
# numpy's hook, which collects the package *and its BLAS library*. The baseline
# build did the same thing and happened to sit in an environment whose numpy was
# linked against Intel MKL: sixteen ``mkl_*.dll`` files, 405.8 MB uncompressed,
# which is the whole reason that exe was 168.9 MB against this one's 27.3 MB.
# Verified by importing a real PNG through the frozen build afterwards.
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
    name='S',
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
