# -*- coding: utf-8 -*-
"""拆解 exe：统计各组件占用的体积，找出可以精简的部分。"""
import collections
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".venv", "Lib", "site-packages"))

from PyInstaller.archive.readers import CArchiveReader  # noqa: E402

EXE = sys.argv[1] if len(sys.argv) > 1 else r"F:\瞬译\dist\瞬译.exe"


def classify(name: str) -> str:
    low = name.lower().replace("\\", "/")
    base = low.split("/")[-1]
    if "numpy" in low:
        return "numpy（科学计算库）"
    if "ctranslate2" in low:
        return "ctranslate2（翻译引擎，必需）"
    if base.startswith("pil") or "_imaging" in low or "pil/" in low:
        return "Pillow（画图标用）"
    if "sentencepiece" in low:
        return "sentencepiece（分词，必需）"
    if "pystray" in low:
        return "pystray（托盘，必需）"
    if "tkinter" in low or "_tkinter" in low or "tcl" in low or "/tk8" in low or base.startswith("tk"):
        return "tkinter / tcl-tk（设置界面）"
    if "setuptools" in low or "pkg_resources" in low or base.startswith("pip"):
        return "setuptools / pip（打包工具残留）"
    if "pyinstaller" in low:
        return "PyInstaller（打包工具自身）"
    if "yaml" in low:
        return "pyyaml（ctranslate2 的配置解析）"
    if base.startswith("python3") and base.endswith(".dll"):
        return "Python 解释器本体"
    if base.endswith((".pyd", ".dll", ".so")):
        return "其他原生扩展"
    return "其他（标准库 / 资源）"


def main():
    reader = CArchiveReader(EXE)
    toc = reader.toc
    groups = collections.Counter()
    counts = collections.Counter()
    big_items = []
    total = 0

    for name, entry in toc.items():
        # PyInstaller 6.x 的 toc 条目是 (typecode, position, length) 三元组，
        # length 是该条目在包里的实际占用（可能已压缩），这就是我们要的口径
        try:
            size = int(entry[2])
        except (IndexError, TypeError, ValueError):
            size = 0
        total += size
        g = classify(name)
        groups[g] += size
        counts[g] += 1
        if size > 2 * 1024 * 1024:
            big_items.append((name, size))

    print("=" * 74)
    print(f"  exe 内部构成：{os.path.basename(EXE)}")
    print("=" * 74)
    print(f"{'分组':<34}{'条目':>5}{'未压缩体积':>14}")
    print("-" * 74)
    for g, size in groups.most_common():
        print(f"{g:<34}{counts[g]:>5}{size/1024/1024:>11.1f} MB")
    print("-" * 74)
    print(f"{'合计':<34}{len(toc):>5}{total/1024/1024:>11.1f} MB")
    print()
    print(f"exe 文件实际大小: {os.path.getsize(EXE)/1024/1024:.1f} MB（已压缩）")

    print()
    print("=" * 74)
    print("  超过 2 MB 的大块（按体积排序）")
    print("=" * 74)
    for name, size in sorted(big_items, key=lambda x: -x[1])[:20]:
        print(f"  {size/1024/1024:8.1f} MB   {name}")

    # 重点看：有没有打包工具自身、测试模块等明显不该出现的东西
    print()
    print("=" * 74)
    print("  可疑条目（打包工具残留 / 测试模块 / 无用的科学计算组件）")
    print("=" * 74)
    suspicious = []
    for name in toc:
        low = name.lower()
        if any(k in low for k in ("pyinstaller", "setuptools", "pkg_resources", "pip/",
                                  "numpy/f2py", "numpy/distutils", "numpy/tests",
                                  "test_", "/tests/", "tkinter/test", "idlelib",
                                  "lib2to3", "numpy/testing")):
            try:
                size = toc[name][3] or toc[name][2]
            except (IndexError, TypeError):
                size = 0
            suspicious.append((name, size))
    if suspicious:
        tot = sum(s for _n, s in suspicious)
        for name, size in sorted(suspicious, key=lambda x: -x[1])[:25]:
            print(f"  {size/1024/1024:8.2f} MB   {name}")
        print(f"  合计可疑体积: {tot/1024/1024:.1f} MB")
    else:
        print("  （未发现）")


if __name__ == "__main__":
    main()
