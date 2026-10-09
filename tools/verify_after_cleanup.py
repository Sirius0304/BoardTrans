# -*- coding: utf-8 -*-
"""清理后验证：模型可用、翻译正常、exe 与备份完好。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models, paths                     # noqa: E402
from app.engine import TranslationEngine          # noqa: E402


def dir_size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    if not os.path.isdir(path):
        return 0
    return sum(os.path.getsize(os.path.join(d, f)) for d, _s, fs in os.walk(path) for f in fs)


print("=" * 66)
print("  1. 运行所需的模型是否还在")
print("=" * 66)
for key in ("zh_en", "en_ja"):
    ok = models.is_installed(key)
    size = models.installed_size(key) / 1024 / 1024
    print(f"  {key:<8} 已安装={ok}   {size:.0f} MB   {'[正常]' if ok else '[异常]'}")

print()
print("=" * 66)
print("  2. 翻译功能实测（清理后）")
print("=" * 66)
engine = TranslationEngine()
cases = [
    ("你好，今天天气不错。", "en", "fast"),
    ("我们明天下午三点开个会。", "en", "quality"),
    ("请把这份文件打印出来。", "ja", "fast"),
]
for text, target, quality in cases:
    t0 = time.time()
    out = engine.translate(text, target, quality)
    ms = (time.time() - t0) * 1000
    print(f"  [{target}/{quality:<7}] {text}")
    print(f"             -> {out}   ({ms:.0f} ms, {engine.last_stats.get('route')})")

print()
print("=" * 66)
print("  3. 文件完整性")
print("=" * 66)
proj = dir_size(r"F:\shunyi")
backup = dir_size(r"F:\键译_备份_20260927")
exe = r"F:\shunyi\dist\键译.exe"
print(f"  项目目录      : {proj/1024/1024:8.1f} MB")
print(f"  备份目录      : {backup/1024/1024:8.1f} MB   {r'F:\键译_备份_20260927'}")
print(f"  exe 是否存在  : {os.path.exists(exe)}   {os.path.getsize(exe)/1024/1024:.1f} MB" if os.path.exists(exe) else "  exe 不存在！")
print(f"  models/ 剩余  : {os.listdir(r'F:\shunyi\models')}")
print(f"  运行数据目录  : {os.listdir(os.path.join(os.environ['LOCALAPPDATA'], 'BoardTrans'))}")
print(f"  配置是否还在  : {os.path.exists(paths.config_path())}")
