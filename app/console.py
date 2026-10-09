# -*- coding: utf-8 -*-
"""
键译 - 控制台输出辅助

打包成无控制台的 exe 后 sys.stdout 为 None，直接 print 会报错，
所以统一走 safe_print。
"""

from __future__ import annotations

import sys


def safe_print(*args, **kwargs) -> None:
    """安全打印：没有控制台时静默跳过，绝不让它影响主流程。"""
    try:
        if sys.stdout is not None:
            print(*args, **kwargs)
            sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass


def progress_line(label: str, done: int, total: int) -> None:
    """单行进度显示（\\r 覆盖刷新）。"""
    if not total:
        safe_print(f"\r  {label} ...", end="")
        return
    pct = done * 100 // total
    bar_len = 28
    filled = int(bar_len * pct / 100)
    bar = "#" * filled + "-" * (bar_len - filled)
    safe_print(f"\r  {label} [{bar}] {pct:3d}%  {done/1024/1024:6.1f}/{total/1024/1024:.1f} MB", end="")
