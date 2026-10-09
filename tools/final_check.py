# -*- coding: utf-8 -*-
"""最终确认：模型完好、项目里已无副本、清理排查残留。"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models   # noqa: E402

print("=== 确认真实模型完好 ===")
for k in ("zh_en", "en_ja"):
    print(f"  {k}: is_installed={models.is_installed(k)}, "
          f"{models.installed_size(k)/1024/1024:.0f} MB")

print()
print("=== 项目目录里已无模型包（不会再被当副本删除）===")
print(f"  models/ 目录存在: {os.path.isdir(r'F:\shunyi\models')}")
for k in ("zh_en", "en_ja"):
    print(f"  find_extra_copies('{k}') = {models.find_extra_copies(k)}")

print()
print("=== 清理排查用的临时备份 ===")
p = r"F:\键译_备份_20260927\排查删除问题_zh_en"
if os.path.isdir(p):
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _x, fs in os.walk(p) for f in fs)
    shutil.rmtree(p, ignore_errors=True)
    print(f"  已删除 {p}（{size/1024/1024:.1f} MB）")
else:
    print("  （不存在，无需清理）")
