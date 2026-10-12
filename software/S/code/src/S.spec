# -*- mode: python ; coding: utf-8 -*-

import os

#: 图鉴代码所在的 tools/ 目录（本 spec 在 src/，与它同级）。
#: 从 SPECPATH 推绝对路径，而不是写 '../tools' —— spec 里的相对路径基准
#: 会跟着调用 PyInstaller 时的工作目录变，换台机器构建就指错地方。
TOOLS = os.path.normpath(os.path.join(SPECPATH, '..', 'tools'))

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
    # 图鉴那套代码住在 ../tools 里（atlas_server / style_atlas / build_atlas /
    # build_thumbs 是平级模块，互相按顶层名 import）。pathex 不指过去，
    # PyInstaller 就跟不进这四份文件，打出来的 exe 里没有图鉴 ——
    # 那正是「S 启动了、图鉴还得手动点」能存在的根本原因。
    pathex=[TOOLS],
    binaries=[],
    datas=[
        ('index.html', '.'),
        ('app.css', '.'),
        ('app.js', '.'),
        ('default_presets.json', '.'),
        # 机读能力清单：让下一个改这个程序的人或 AI 先看清已有能力，
        # 不再重复实现（例如另写一套 LLM 调用器或画幅分批脚本）。
        ('capabilities.json', '.'),
        # 图鉴的对比页模板（atlas_server 的 /compare 路由读它）。
        (os.path.join(TOOLS, 'compare_live.html'), '.'),
        (os.path.join(TOOLS, 'atlas_management.js'), '.'),
        # 空的首次安装模板与系统评级；升级读取 AppData 中的现有收藏。
        (os.path.join(TOOLS, 'atlas_initial_data.json'), '.'),
        (os.path.join(TOOLS, '_grades_system.json'), '.'),
    ],
    hiddenimports=['atlas_server', 'style_atlas', 'build_atlas', 'build_thumbs', 'atlas_host', 'atlas_resources', 'comfybatch_lifecycle', 'atlas_sources', 'comfybatch_launcher'],
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
