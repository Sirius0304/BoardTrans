# -*- mode: python ; coding: utf-8 -*-
"""
键译 · 精简打包配置（自检版，带控制台）
=========================================

与 键译_精简.spec 完全相同的精简参数（排除 numpy / Pillow 解码器等），
只是把入口换成 selftest.py 并保留控制台输出，
用来验证「精简后的 exe 里，翻译引擎和设置窗口是不是真的还能用」。
"""

import os

from PyInstaller.utils.hooks import collect_dynamic_libs

PROJECT = os.path.dirname(os.path.abspath(SPEC)) if "SPEC" in globals() \
    else os.path.dirname(os.path.abspath("自检_精简.spec"))

binaries = collect_dynamic_libs("ctranslate2")
binaries += collect_dynamic_libs("sentencepiece")

hiddenimports = ["pystray._win32"]

excludes = [
    "numpy",
    "ctranslate2.converters",
    "ctranslate2.specs",
    "pyyaml",
    "PyInstaller",
    "setuptools",
    "pkg_resources",
    "pip",
    "unittest",
    "pytest",
    "doctest",
    "pydoc",
    "lib2to3",
    "distutils",
    "test",
    "tkinter.test",
    "tkinter.dnd",
    "idlelib",
    "turtle",
    "turtledemo",
]

a = Analysis(
    [os.path.join(PROJECT, "selftest.py")],
    pathex=[PROJECT],
    binaries=binaries,
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

UNUSED_PIL_CODECS = (
    "_avif", "_webp", "_imagingcms", "_imagingmath", "_imagingmorph",
    "_webpdec", "_raqm", "_imagingtk", "_tkinter_finder",
)
a.binaries = [
    b for b in a.binaries
    if not any(codec in b[0].lower() for codec in UNUSED_PIL_CODECS)
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="精简自检",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,              # 自检要看输出
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
