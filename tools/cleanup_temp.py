# -*- coding: utf-8 -*-
"""清理打包临时产物，并输出最终状态。"""
import os
import shutil

PROJECT = r"F:\shunyi"

print("=== 清理打包临时产物 ===")
prev = os.path.join(PROJECT, "dist", "瞬译_上一版.exe")
if os.path.exists(prev):
    size = os.path.getsize(prev) / 1024 / 1024
    os.remove(prev)
    print(f"  已删除 dist/瞬译_上一版.exe ({size:.1f} MB)")

for name in ("build", "build_selftest", "dist_new"):
    d = os.path.join(PROJECT, name)
    if os.path.isdir(d):
        total = sum(os.path.getsize(os.path.join(x, f)) for x, _s, fs in os.walk(d) for f in fs)
        shutil.rmtree(d, ignore_errors=True)
        print(f"  已清理 {name}/ ({total/1024/1024:.0f} MB)")

print()
print("=== 最终状态 ===")
exe = os.path.join(PROJECT, "dist", "瞬译.exe")
print(f"  exe 体积: {os.path.getsize(exe)/1024/1024:.1f} MB")
print(f"  dist 内容: {os.listdir(os.path.join(PROJECT, 'dist'))}")

print()
print("=== 本次修复涉及的文件 ===")
for path in ("app/models.py", "app/settings_window.py"):
    full = os.path.join(PROJECT, path)
    print(f"  {path}  ({os.path.getsize(full)} 字节)")
