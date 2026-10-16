# -*- coding: utf-8 -*-
"""回归：游戏场景「贴出旧剪贴板 + 误删原文」修复（2026-10-14）。

两个缺陷：
1. paste 模式里 Ctrl+V 后只等 0.15s 就还原剪贴板 —— 游戏帧循环异步粘贴，
   等它读剪贴板时已被还原，于是贴出旧内容。修为 PASTE_CONFIRM_WAIT=0.8。
2. 取词成功后原文已被 Delete 删除，翻译结果为空时直接 return —— 用户输入
   永久丢失。修为把原文回注到原位置。
"""
import os
import sys
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))


print("== 1) injector：粘贴等待常量 ==")
from app import injector  # noqa: E402

check("PASTE_PREP_WAIT 存在且 >= 0.1", injector.PASTE_PREP_WAIT >= 0.1,
      str(injector.PASTE_PREP_WAIT))
check("PASTE_CONFIRM_WAIT 存在且 >= 0.6（覆盖游戏帧循环延迟）",
      injector.PASTE_CONFIRM_WAIT >= 0.6, str(injector.PASTE_CONFIRM_WAIT))

print("== 2) injector：还原必须发生在 Ctrl+V 之后（源码顺序）==")
src = open(os.path.join(os.path.dirname(injector.__file__), "injector.py"),
           encoding="utf-8").read()
paste_mode = src[src.index("def inject_text"):]
i_write = paste_mode.index("set_clipboard_text(text)")
i_paste = paste_mode.index('keyboard.send("ctrl+v")')
i_restore = paste_mode.index("set_clipboard_text(backup)")
check("顺序：写译文 → Ctrl+V → 还原", i_write < i_paste < i_restore)
check("硬编码 0.15/0.06 老延时已移除", "time.sleep(0.15)" not in paste_mode
      and "time.sleep(0.06)" not in paste_mode)

print("== 3) service：翻译结果为空时必须回注原文（防误删）==")
from app import service  # noqa: E402

ssrc = open(os.path.join(os.path.dirname(service.__file__), "service.py"),
            encoding="utf-8").read()
m = re.search(r"if not result:(.*?)return", ssrc, re.S)
check("空结果分支存在", m is not None)
if m:
    block = m.group(1)
    check("空结果分支里调用了 inject_text 回注原文", "inject_text(text" in block)
    check("回注用的是原文 text 而非 result", "inject_text(result" not in block)

print()
print(f"通过 {PASS} 项，失败 {FAIL} 项")
sys.exit(1 if FAIL else 0)
