# -*- coding: utf-8 -*-
"""产品改名：瞬译/ShunYi → 键译/BoardTrans

用法：
    python tools/rename_product.py          # 干跑，只打印将要改的位置
    python tools/rename_product.py --apply  # 真正写入

替换规则（区分「产品名」与「真实路径」，后者绝不改）：
    瞬译            → 键译           （所有出现，含示例路径 F:\\瞬译\\）
    ShunYi          → BoardTrans     （APP_NAME / APP_ID / 字体名 / UA / 文档）
    shunyi          → 保持不动        （F:\\shunyi 是真实目录；gitee 仓库 URL 也先保留）
    SHUNYI_DATA_DIR / SHUNYI_MODELS_DIR
                    → 文档改称 BOARDTRANS_*，代码里**同时兼容旧名**（见 paths.py 手工改）

为什么要脚本而不是手敲：涉及 100+ 处、跨 .py/.bat/.spec/.md，
手敲必漏；脚本可复跑、可核对，改完还能再跑一次干跑确认"已无残留"。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPLY = "--apply" in sys.argv

# 文本替换对（顺序有意义：先长后短，避免 SHUNYI_ 被误伤）
PAIRS = [
    ("瞬译", "键译"),
    ("ShunYi", "BoardTrans"),
]

# 只处理这些扩展名
EXTS = {".py", ".bat", ".spec", ".md", ".txt"}

# 额外指定的无扩展名文件
EXTRA_FILES = {"LICENSE"}

# 跳过的目录（历史/环境/产物/记忆日志）
SKIP_DIRS = {".git", ".venv", ".workbuddy", "build", "build_selftest",
             "dist", "models", "__pycache__", "docs"}   # docs 单独处理

# 这些文件里出现的是**真实路径/历史备份名**，不做替换
SKIP_FILES = {
    "tools/check_integrity.py",     # 引用历史备份目录 瞬译_备份_20260927
    "tools/cleanup_backup.py",      # 同上
    "tools/cleanup_zero_risk.py",   # 同上
    "tools/rename_product.py",      # 本脚本自身
}

# docs/ 下这些是**决策/审计记录**：它们讨论的就是"旧名 瞬译"这件事，
# 改了会让文档自相矛盾（"现名 瞬译 已与小- 撞车"变成废话）。保留原貌。
DOCS_SKIP = {"项目更名候选方案.md", "开源许可合规审计报告.md"}


def iter_files():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            rel = os.path.relpath(os.path.join(base, fn), ROOT).replace("\\", "/")
            if rel in SKIP_FILES:
                continue
            if ext in EXTS or fn in EXTRA_FILES:
                yield os.path.join(base, fn), rel
    # docs/ 单独走一遍（只改非历史记录）
    docs = os.path.join(ROOT, "docs")
    if os.path.isdir(docs):
        for fn in os.listdir(docs):
            if fn in DOCS_SKIP or not fn.lower().endswith(".md"):
                continue
            yield os.path.join(docs, fn), f"docs/{fn}"


def main() -> int:
    changed_files = 0
    changed_hits = 0
    report = []
    for path, rel in iter_files():
        try:
            # newline="" 保留原始行尾：.bat 必须是 CRLF，
            # 用默认的通用换行读取会把 \r\n 折成 \n，写回去就破坏了批处理。
            with open(path, encoding="utf-8", newline="") as f:
                src = f.read()
        except (UnicodeDecodeError, OSError):
            continue
        out = src
        hits = 0
        for old, new in PAIRS:
            n = out.count(old)
            if n:
                hits += n
                out = out.replace(old, new)
        if not hits:
            continue
        changed_files += 1
        changed_hits += hits
        report.append((rel, hits))
        if APPLY:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(out)

    print("=" * 60)
    print("产品改名：" + ("已写入" if APPLY else "【干跑预览】"))
    print("=" * 60)
    for rel, hits in sorted(report, key=lambda r: -r[1]):
        print(f"  {hits:4d} 处  {rel}")
    print("-" * 60)
    print(f"  合计 {changed_hits} 处，涉及 {changed_files} 个文件")
    if not APPLY:
        print("\n  加 --apply 参数才会真正写入。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
