# -*- coding: utf-8 -*-
"""项目文件体检：算清每个目录占多少体积，并按"必需 / 可重建 / 可删"分类。

只读扫描，不删任何东西。
（统计目录大小时跳过 reparse point，避免 junction 指回父目录造成递归成环）
"""
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def dir_size(path):
    """目录总体积，跳过 junction/symlink（否则 Windows 上会无限递归）。"""
    total = 0
    files = 0
    for dirpath, dirnames, filenames in os.walk(path):
        try:
            st = os.stat(dirpath)
            if getattr(st, "st_file_attributes", 0) & 0x400:   # reparse point
                dirnames[:] = []
                continue
        except OSError:
            pass
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            try:
                s = os.stat(fp)
                if getattr(s, "st_file_attributes", 0) & 0x400:
                    continue
                total += s.st_size
                files += 1
            except OSError:
                pass
    return total, files


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


print("=" * 68)
print(f"瞬译 项目文件体检  ({ROOT})")
print("=" * 68)

# ---- 顶层条目
entries = []
for name in sorted(os.listdir(ROOT)):
    p = os.path.join(ROOT, name)
    if os.path.isdir(p):
        size, cnt = dir_size(p)
        entries.append((name, size, cnt, "dir"))
    else:
        try:
            entries.append((name, os.path.getsize(p), 1, "file"))
        except OSError:
            entries.append((name, 0, 1, "file"))

entries.sort(key=lambda e: -e[1])
print("\n【顶层条目】按体积排序")
print(f"  {'名称':<28}{'体积':>12}{'文件数':>8}")
print("  " + "-" * 46)
for name, size, cnt, kind in entries:
    print(f"  {name:<28}{human(size):>12}{cnt:>8}")

# ---- 总量
total = sum(e[1] for e in entries)
print(f"\n  合计：{human(total)}")

# ---- app/ 与 tools/ 明细（这两个是代码主体）
for sub in ("app", "tools", "docs"):
    p = os.path.join(ROOT, sub)
    if not os.path.isdir(p):
        continue
    print(f"\n【{sub}/】明细")
    rows = []
    for name in sorted(os.listdir(p)):
        fp = os.path.join(p, name)
        if os.path.isdir(fp):
            s, c = dir_size(fp)
        else:
            try:
                s, c = os.path.getsize(fp), 1
            except OSError:
                s, c = 0, 1
        rows.append((name, s, c))
    rows.sort(key=lambda r: -r[1])
    for name, s, c in rows[:18]:
        print(f"  {name:<34}{human(s):>12}{c:>8}")
    if len(rows) > 18:
        rest = sum(r[1] for r in rows[18:])
        print(f"  {'（其余 ' + str(len(rows) - 18) + ' 项）':<30}{human(rest):>12}")

# ---- 缓存明细
print("\n【可重建的缓存/中间产物】")
cache_dirs = ["__pycache__", "build", "build_selftest", "dist", "models"]
for d in cache_dirs:
    p = os.path.join(ROOT, d)
    if os.path.isdir(p):
        s, c = dir_size(p)
        print(f"  {d+'/':<24}{human(s):>12}{c:>6} 个文件")

# 散落的 __pycache__
total_pycache = 0
cnt_pycache = 0
for dirpath, dirnames, filenames in os.walk(ROOT):
    if "__pycache__" in dirnames:
        s, c = dir_size(os.path.join(dirpath, "__pycache__"))
        total_pycache += s
        cnt_pycache += c
        dirnames.remove("__pycache__")
if total_pycache:
    print(f"  {'全部 __pycache__':<22}{human(total_pycache):>12}{cnt_pycache:>6} 个文件")
