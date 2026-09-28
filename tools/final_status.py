# -*- coding: utf-8 -*-
"""精简瘦身后的最终状态核对。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config                 # noqa: E402
from app.engine import TranslationEngine   # noqa: E402

PROJECT = r"F:\shunyi"
BACKUP = r"F:\瞬译_备份_20260927"
RUN = os.path.join(os.environ["LOCALAPPDATA"], "ShunYi")
EXE = os.path.join(PROJECT, "dist", "瞬译.exe")


def size_of(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    if not os.path.isdir(path):
        return 0
    return sum(os.path.getsize(os.path.join(d, f)) for d, _s, fs in os.walk(path) for f in fs)


def mb(b):
    return b / 1024 / 1024


print("=" * 62)
print("  精简瘦身 · 最终状态")
print("=" * 62)
print(f"  最终 exe        {mb(size_of(EXE)):9.1f} MB")
print(f"  项目目录        {mb(size_of(PROJECT)):9.1f} MB")
print(f"  运行数据目录    {mb(size_of(RUN)):9.1f} MB")
print(f"  备份目录        {mb(size_of(BACKUP)):9.1f} MB   {BACKUP}")
print()
print("  必须保留的东西是否都在：")
checks = [
    ("翻译模型（运行用）", os.path.isdir(os.path.join(RUN, "packages"))),
    ("用户配置 config.json", os.path.exists(os.path.join(RUN, "config.json"))),
    ("最终 exe", os.path.exists(EXE)),
    ("项目数据 .workbuddy", os.path.isdir(os.path.join(PROJECT, ".workbuddy"))),
    (".git 版本库", os.path.isdir(os.path.join(PROJECT, ".git"))),
    ("源码 app/", os.path.isdir(os.path.join(PROJECT, "app"))),
]
for label, ok in checks:
    print(f"    {'[在]' if ok else '[缺失]'} {label}")

print()
print("=" * 62)
print("  功能快检")
print("=" * 62)
cfg = config.load()
print(f"  配置：快捷键={cfg['hotkey']}  语言={cfg['target_lang']}  "
      f"档位={cfg['quality']}  空闲释放={cfg['idle_unload_minutes']} 分钟")
engine = TranslationEngine()
for text, target in (("你好，今天天气不错。", "en"), ("请把这份文件打印出来。", "ja")):
    t0 = time.time()
    out = engine.translate(text, target, "fast")
    print(f"  [{target}] {text}")
    print(f"        -> {out}   ({(time.time()-t0)*1000:.0f} ms)")
