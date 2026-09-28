# -*- coding: utf-8 -*-
"""
把项目文本文件里的 F:\shunyi 统一替换为 F:\\shunyi。

关键细节：F:\\瞬译_备份_20260927 是独立目录、名字不变，
所以替换时必须排除「后面跟着 _备份」的情况，否则会把备份目录的引用改错。
"""
import os
import re

ROOT = r"F:\shunyi"
SKIP_DIRS = {".venv", ".git", "dist", "__pycache__", "build", ".workbuddy"}
EXTS = (".py", ".md", ".bat", ".spec", ".txt", ".json", ".gitignore", ".gitattributes")

# 四种写法变体；每个都带负向前瞻，避免误伤 F:\瞬译_备份_20260927
PATTERNS = [
    (re.compile(r"F:\shunyi(?!_备份)"), r"F:\\shunyi"),
    (re.compile(r"F:/shunyi(?!_备份)"), "F:/shunyi"),
    (re.compile(r"/f/shunyi(?!_备份)"), "/f/shunyi"),
    (re.compile(r"F:\\\\瞬译(?!_备份)"), "F:\\\\shunyi"),
]

changed = []
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
        new = text
        for pat, rep in PATTERNS:
            new = pat.sub(rep, new)
        if new != text:
            open(p, "w", encoding="utf-8", newline="").write(new)
            changed.append(os.path.relpath(p, ROOT))

print(f"已更新 {len(changed)} 个文件：")
for rel in sorted(changed):
    print(f"  {rel}")

# 清理两个测试残留的空目录
for empty in (os.path.join(ROOT, "models"), os.path.join(ROOT, "测试 目录")):
    if os.path.isdir(empty) and not os.listdir(empty):
        os.rmdir(empty)
        print(f"已删除空目录: {empty}")
