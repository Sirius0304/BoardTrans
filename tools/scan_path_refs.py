# -*- coding: utf-8 -*-
"""扫描项目里所有硬编码 F:\shunyi 的文本文件（改名前需要同步更新）。"""
import os

ROOT = r"F:\shunyi"
VARIANTS = [r"F:\shunyi", "F:/shunyi", "/f/shunyi", "F:\\\\瞬译"]
SKIP_DIRS = {".venv", ".git", "dist", "__pycache__", "build", ".workbuddy"}
EXTS = (".py", ".md", ".bat", ".spec", ".txt", ".json", ".gitignore", ".gitattributes")

hits = {}
for d, dirs, files in os.walk(ROOT):
    dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
    for name in files:
        if not name.endswith(EXTS):
            continue
        p = os.path.join(d, name)
        try:
            text = open(p, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        n = sum(text.count(v) for v in VARIANTS)
        if n:
            hits[os.path.relpath(p, ROOT)] = n

print("=== 硬编码旧路径的文本文件 ===")
total = 0
for rel, n in sorted(hits.items()):
    total += n
    print(f"  {n:3d} 处  {rel}")
print(f"  ----")
print(f"  合计 {len(hits)} 个文件 / {total} 处")
