# -*- coding: utf-8 -*-
"""
异常排查：备份目录里两项（models_zh_en / packages_cache）在本步执行前就消失了。
本脚本全面核对「必须存在」的文件，确认有没有误删重要东西。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import paths   # noqa: E402

RUN = os.path.join(os.environ["LOCALAPPDATA"], "ShunYi")
PROJ = r"F:\shunyi"
BACKUP = r"F:\瞬译_备份_20260927"


def size_of(p):
    if os.path.isfile(p):
        return os.path.getsize(p)
    if not os.path.isdir(p):
        return 0
    return sum(os.path.getsize(os.path.join(d, f)) for d, _s, fs in os.walk(p) for f in fs)


print("=" * 70)
print("  全面核对：关键文件是否都在")
print("=" * 70)

checks = [
    ("最终 exe", os.path.join(PROJ, "dist", "瞬译.exe"), "file"),
    ("源码 app/", os.path.join(PROJ, "app"), "dir"),
    ("文档 docs/", os.path.join(PROJ, "docs"), "dir"),
    ("工具 tools/", os.path.join(PROJ, "tools"), "dir"),
    ("开发环境 .venv", os.path.join(PROJ, ".venv"), "dir"),
    ("版本库 .git", os.path.join(PROJ, ".git"), "dir"),
    ("项目数据 .workbuddy", os.path.join(PROJ, ".workbuddy"), "dir"),
    ("打包配置 瞬译_精简.spec", os.path.join(PROJ, "瞬译_精简.spec"), "file"),
    ("模型包备份 models/", os.path.join(PROJ, "models"), "dir"),
    ("★ 运行用模型 packages/", os.path.join(RUN, "packages"), "dir"),
    ("★ 中译英模型", os.path.join(RUN, "packages", "zh_en"), "dir"),
    ("★ 中译日模型", os.path.join(RUN, "packages", "en_ja"), "dir"),
    ("★ 用户配置 config.json", os.path.join(RUN, "config.json"), "file"),
]

all_ok = True
for label, path, kind in checks:
    exists = os.path.isfile(path) if kind == "file" else os.path.isdir(path)
    size = size_of(path) / 1024 / 1024
    mark = "[在]" if exists else "[缺失]"
    if not exists:
        all_ok = False
    print(f"  {mark:<8}{label:<26}{size:9.1f} MB")

print()
print("=" * 70)
print("  备份目录现状")
print("=" * 70)
if os.path.isdir(BACKUP):
    for name in sorted(os.listdir(BACKUP)):
        p = os.path.join(BACKUP, name)
        kind = "目录" if os.path.isdir(p) else "文件"
        print(f"  {size_of(p)/1024/1024:8.1f} MB  [{kind}] {name}")
else:
    print("  （备份目录不存在）")

print()
print("=" * 70)
print("  运行时模型的实际状态（程序真在用这两个）")
print("=" * 70)
from app import models   # noqa: E402
for key in ("zh_en", "en_ja"):
    installed = models.is_installed(key)
    size = models.installed_size(key) / 1024 / 1024
    print(f"  {key:<8} 已安装={installed}  {size:.0f} MB  {'[正常]' if installed else '[异常]'}")
    if not installed:
        all_ok = False

print()
print("=" * 70)
print("  结论：" + ("所有关键文件都在，没有误删 ✓" if all_ok else "!! 有文件缺失，需要处理"))
print("=" * 70)
sys.exit(0 if all_ok else 1)
