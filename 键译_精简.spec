# -*- mode: python ; coding: utf-8 -*-
"""
键译 · 精简打包配置
====================

和原来「一条 --collect-all ctranslate2」的区别：

1. 不再整体收集 ctranslate2 包，只收它的**原生库**（ctranslate2.dll / libiomp5md.dll）。
   原因：--collect-all 会把 ctranslate2/converters（模型转换工具）一起收进来，
   而那个子模块在模块级 import numpy —— 光是 numpy 就占了 26.4 MB，
   可是它只用于「转换模型」，推理路径根本不需要
   （ctranslate2 官方注释：Those dependencies are only needed to convert models,
   not to run inference）。实测屏蔽 numpy 后翻译完全正常。

2. 过滤掉 Pillow 里用不到的图片解码器，最大的是 _avif.pyd（7.5 MB）。
   本程序只用 Pillow 画那个 64×64 的圆形图标，从不打开任何图片文件，
   所以各种格式解码器（avif / webp / ...）全都不需要。

3. 顺带排除打包工具自身与测试框架，避免它们被间接收集。

结果：exe 里的未压缩内容从 132.4 MB 降到约 98 MB。
"""

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

PROJECT = r"F:\shunyi"

# 只收 ctranslate2 的原生库（DLL / pyd），不收它的 Python 子模块
binaries = collect_dynamic_libs("ctranslate2")
binaries += collect_dynamic_libs("sentencepiece")

hiddenimports = [
    "pystray._win32",          # 托盘在 Windows 下的后端，PyInstaller 有时分析不到
    "certifi",                 # HTTPS 证书校验的 CA 包（见 requirements 里的说明）
]

excludes = [
    # —— 本次瘦身的主角 ——
    "numpy",                   # 只有 ctranslate2 的模型转换工具才需要
    "ctranslate2.converters",  # 模型转换工具（import numpy / torch）
    "ctranslate2.specs",       # 同上，推理路径用不到（已验证）
    "pyyaml",                  # 只被 converters 用来读配置
    # —— 打包/测试工具，不该进产物 ——
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
    # —— 图形界面的无关部分 ——
    "tkinter.test",
    "tkinter.dnd",
    "idlelib",
    "turtle",
    "turtledemo",
]

a = Analysis(
    [PROJECT + r"\run_stage2.py"],
    pathex=[PROJECT],
    binaries=binaries,
    # app_icon.ico 同时作为「本程序自有标记」放进包内：
    # 启动时据此识别并清理自己遗留的 _MEI* 解包目录（只删带此标记的）
    datas=[
        (PROJECT + r"\app_icon.ico", "."),
        # certifi 的 CA 证书包：不带它，exe 里的 TLS 校验只能靠 Windows 系统证书库，
        # 遇到公司网络/杀软的 TLS 中间人会直接 CERTIFICATE_VERIFY_FAILED（模型下不下来）
        *collect_data_files("certifi"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

# 关键一步：把用不到的图片解码器从产物里剔除（_avif 单个就有 7.5 MB）
UNUSED_PIL_CODECS = (
    "_avif", "_webp", "_imagingcms", "_imagingmath", "_imagingmorph",
    "_webpdec", "_raqm", "_imagingtk", "_tkinter_finder",
)
before = len(a.binaries)
a.binaries = [
    b for b in a.binaries
    if not any(codec in b[0].lower() for codec in UNUSED_PIL_CODECS)
]
removed = before - len(a.binaries)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="键译",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # 不用 UPX：会把杀软误报概率抬高
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,             # 不弹黑窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=PROJECT + r"\app_icon.ico",
)

# 打包时把这行提示打出来，方便核对过滤是否生效
print(f"[精简打包] 已剔除 {removed} 个用不到的 Pillow 解码器文件")
