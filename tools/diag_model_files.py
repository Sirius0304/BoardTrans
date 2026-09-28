# -*- coding: utf-8 -*-
"""查看模型相关的所有磁盘位置，找出"删了但文件还在"的究竟是什么。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models, paths   # noqa: E402

RUN_DIR = os.path.join(os.environ["LOCALAPPDATA"], "ShunYi")
PROJECT = r"F:\shunyi"


def walk_report(root, title):
    print(f"\n  【{title}】")
    print(f"  {root}")
    if not os.path.isdir(root):
        print("    （目录不存在）")
        return
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if os.path.isdir(p):
            files = sum(len(fs) for _d, _s, fs in os.walk(p))
            size = sum(os.path.getsize(os.path.join(d, f))
                       for d, _s, fs in os.walk(p) for f in fs)
            print(f"    [目录] {name:<28} {size/1024/1024:8.1f} MB  ({files} 个文件)")
        else:
            print(f"    [文件] {name:<28} {os.path.getsize(p)/1024/1024:8.1f} MB")


print("=" * 74)
print("  模型相关文件的全部落点")
print("=" * 74)

print(f"\n  程序自己管理的两个路径（重点看这两个）:")
print(f"    packages_dir() = {paths.packages_dir()}")
print(f"    models_dir()   = {paths.models_dir()}   ← 下载缓存（.argosmodel 压缩包）")
print(f"    data_dir()     = {paths.data_dir()}")

walk_report(RUN_DIR, "运行数据目录 %LOCALAPPDATA%\\ShunYi")

# 程序认为的安装状态
print("\n  【程序判断的安装状态】")
for key in ("zh_en", "en_ja"):
    root = models.model_dir(key)
    print(f"    {key:<8} is_installed={models.is_installed(key)!s:<6} "
          f"目录={root} {'存在' if os.path.isdir(root) else '不存在'}")
    if os.path.isdir(root):
        size = models.installed_size(key) / 1024 / 1024
        for f in sorted(os.listdir(root)):
            print(f"        - {f}")
    cache = os.path.join(paths.models_dir(), f"{key}.argosmodel")
    print(f"        缓存包: {'存在' if os.path.exists(cache) else '不存在'}  {cache}")

walk_report(os.path.join(PROJECT, "models"), "项目里的 models/（程序不读这里，是开发期留下的备份）")

# 全盘搜一下还有没有别处的 .argosmodel（用户可能看的是这些）
print("\n  【项目目录下的其它 .argosmodel 文件】")
found = []
for d, dirs, files in os.walk(PROJECT):
    if ".venv" in d or ".git" in d:
        continue
    for f in files:
        if f.endswith((".argosmodel", ".bin", ".model")):
            p = os.path.join(d, f)
            found.append((p, os.path.getsize(p)))
for p, s in sorted(found, key=lambda x: -x[1])[:12]:
    print(f"    {s/1024/1024:8.1f} MB  {p}")
if not found:
    print("    （无）")
