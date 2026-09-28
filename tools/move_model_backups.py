# -*- coding: utf-8 -*-
"""把项目 models/ 里的模型包备份移到外部备份目录。

原因：这些备份（186 MB）留在项目目录里，会被"删除模型"功能当成同一模型的副本一并清理掉。
移出后，程序扫描不到它们，离线部署材料得以保留。
"""
import os
import shutil

SRC = r"F:\shunyi\models"
DST = r"F:\瞬译_备份_20260927\离线部署用模型包"

print("=" * 66)
print("  把项目里的模型备份移到外部备份目录")
print("=" * 66)

if not os.path.isdir(SRC):
    print(f"  源目录不存在：{SRC}")
    raise SystemExit(0)

os.makedirs(DST, exist_ok=True)
moved = 0
for name in sorted(os.listdir(SRC)):
    src = os.path.join(SRC, name)
    dst = os.path.join(DST, name)
    if os.path.exists(dst):
        print(f"  目标已存在，跳过：{name}")
        continue
    size = os.path.getsize(src) / 1024 / 1024 if os.path.isfile(src) else 0
    shutil.move(src, dst)
    moved += 1
    print(f"  已移动：{name}（{size:.1f} MB）")

# 源目录空了就删掉，避免留个空壳让人困惑
if os.path.isdir(SRC) and not os.listdir(SRC):
    os.rmdir(SRC)
    print(f"  已删除空目录：{SRC}")

print()
print(f"  共移动 {moved} 个文件")
print(f"  新位置：{DST}")
print()
print("  备份目录内容：")
total = 0
for name in sorted(os.listdir(os.path.dirname(DST))):
    p = os.path.join(os.path.dirname(DST), name)
    if os.path.isdir(p):
        s = sum(os.path.getsize(os.path.join(d, f)) for d, _x, fs in os.walk(p) for f in fs)
        total += s
        print(f"    {s/1024/1024:8.1f} MB  {name}/")
    else:
        s = os.path.getsize(p)
        total += s
        print(f"    {s/1024/1024:8.1f} MB  {name}")
print(f"    {'-'*8}")
print(f"    {total/1024/1024:8.1f} MB  合计")
