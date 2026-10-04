# -*- coding: utf-8 -*-
"""核对打包产物里是否真的含最新改动（字节码级证据）。

为什么要专门写这个：光看 build\\瞬译_精简\\PYZ-00.pyz 的时间戳"晚于源码"
只能说明 PyInstaller 重编过，不能证明**改的那几行确实进了包**。
这里把 exe 里的 PYZ 解出来，直接在字节码常量池里找特征字符串。

用法：python tools/verify_pack.py瞬译.exe
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader  # noqa: E402

# (模块, 特征字符串, 说明)
CHECKS = [
    ("app.models", "PHASE_DOWNLOAD", "下载阶段常量"),
    ("app.models", "PHASE_EXTRACT", "解压阶段常量"),
    ("app.models", "_Progress", "进度上报器类"),
    ("app.models", "finish", "收尾强制 100%（修'停在98%'）"),
    ("app.models", "_adapt_progress", "旧回调适配层"),
    ("app.models", "正在解压", "解压文案"),
    ("app.settings_window", "phase", "界面按阶段分流"),
    ("app.settings_window", "_dl_high", "界面单调水位线"),
    ("app.settings_window", "正在解压", "解压阶段界面文案"),
    # 2026-10-04 胶囊截断修复
    ("app.ui_kit", "force_redraw", "内容变化后主动重算宽高"),
    ("app.ui_kit", "update_idletasks", "重算前刷新请求尺寸"),
    ("app.ui_kit", "pad_x", "横向内缩独立参数（修描边被盖）"),
    ("app.settings_window", "状态：下载完成，正在解压…", "解压文案（已去重复括号）"),
]


def consts_of(code):
    """收集字节码里出现的所有名字与字符串常量。

    两个池都要看（踩过的坑）：
      - `co_consts` 放字符串字面量（"正在解压" 这类文案）
      - `co_names`   放被引用的名字（PHASE_EXTRACT、_dl_high 这类标识符）
    早先只查 co_consts，于是一批明明在包里的标识符被误报成"没进包"。
    """
    names, strings = set(), set()

    def walk(c):
        # 三个池都要看（每个都踩过坑）：
        #   co_names    模块级/全局名字     —— PHASE_EXTRACT、_dl_high
        #   co_varnames 局部变量名         —— phase（函数参数）
        #   co_consts   字符串字面量       —— "正在解压"
        names.update(c.co_names)
        names.update(c.co_varnames)
        for k in c.co_consts:
            if isinstance(k, str):
                strings.add(k)
            elif hasattr(k, "co_consts"):
                walk(k)
    walk(code)
    return names | strings


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else "瞬译.exe"
    exe = os.path.abspath(exe)
    print("=" * 60)
    print(f"核对打包产物：{exe}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as td:
        # ZlibArchiveReader 只接受文件路径，得先落盘
        archive = CArchiveReader(exe)
        pyz_path = os.path.join(td, "PYZ.pyz")
        with open(pyz_path, "wb") as f:
            f.write(archive.extract("PYZ.pyz"))
        z = ZlibArchiveReader(pyz_path)

        cache = {}
        ok = True
        for module, needle, desc in CHECKS:
            if module not in cache:
                try:
                    cache[module] = consts_of(z.extract(module))
                except Exception as exc:  # noqa: BLE001
                    cache[module] = None
                    print(f"  [FAIL] 取出 {module} 失败: {exc}")
                    ok = False
            pool = cache[module]
            if pool is None:
                ok = False
                continue
            hit = any(needle in c for c in pool)
            print(f"  [{'OK' if hit else 'FAIL'}] {module:22s} {desc}  ({needle})")
            ok = ok and hit

    # 体积与时间
    size_mb = os.path.getsize(exe) / 1024 / 1024
    print(f"\n  产物大小：{size_mb:.2f} MB")
    print(f"  产物时间：{os.path.getmtime(exe):.0f}")
    print("\n" + ("全部通过：最新改动确实在包内" if ok else "有项目未进包，需重新打包"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())