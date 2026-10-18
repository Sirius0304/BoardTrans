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
import inspect

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
    # 2026-10-18：回注逻辑抽成 _restore_original（异常分支也要复用），
    # 所以这里断言"调用了回注且传的是原文"，而不是具体的 inject_text 调用。
    check("空结果分支里回注了原文", "_restore_original(text" in block)
    check("回注用的是原文 text 而非 result", "_restore_original(result" not in block)
    check("_restore_original 内部才调 inject_text",
          "inject_text(text" in inspect.getsource(service.HotkeyService._restore_original))

print("== 4) 粘贴等待按前台窗口自适应（2026-10-16 方案④）==")
import time  # noqa: E402

DESKTOP = injector.PASTE_CONFIRM_WAIT_DESKTOP
FULL = injector.PASTE_CONFIRM_WAIT_FULLSCREEN
check("桌面程序等待更短（<= 0.3s）", DESKTOP <= 0.3, str(DESKTOP))
check("全屏/游戏等待仍给足（>= 0.6s，防贴出旧剪贴板）", FULL >= 0.6, str(FULL))
check("兼容别名取保守值（= 全屏值，漏判不会丢粘贴）",
      injector.PASTE_CONFIRM_WAIT == FULL, str(injector.PASTE_CONFIRM_WAIT))
check("前台全屏判定函数可用且返回布尔",
      isinstance(injector._foreground_is_fullscreen(), bool),
      str(injector._foreground_is_fullscreen()))
check("paste_confirm_wait() 只会给出这两个值之一",
      injector.paste_confirm_wait() in (DESKTOP, FULL), str(injector.paste_confirm_wait()))

saved_probe = injector._foreground_is_fullscreen
try:
    injector._foreground_is_fullscreen = lambda: True
    check("全屏 → 用长等待 0.8s", injector.paste_confirm_wait() == FULL,
          str(injector.paste_confirm_wait()))
    injector._foreground_is_fullscreen = lambda: False
    check("桌面 → 用短等待 0.25s", injector.paste_confirm_wait() == DESKTOP,
          str(injector.paste_confirm_wait()))

    # 真实计时：两条分支的等待差必须落在 inject_text 的实际耗时上
    src = open(os.path.join(os.path.dirname(injector.__file__), "injector.py"),
               encoding="utf-8").read()
    body = src[src.index("def inject_text"):]
    check("inject_text 走自适应函数而非写死的常量",
          "paste_confirm_wait()" in body and "time.sleep(PASTE_CONFIRM_WAIT)" not in body)

    from app import capture as cap
    saved_send = cap.keyboard.send
    saved_rel = cap.release_modifiers
    saved_get, saved_set = cap.get_clipboard_text, cap.set_clipboard_text
    cap.keyboard.send = lambda k: None
    cap.release_modifiers = lambda settle=0.02: None
    cap.get_clipboard_text = lambda: "old"
    cap.set_clipboard_text = lambda t: True

    def timed(fullscreen: bool) -> float:
        injector._foreground_is_fullscreen = lambda: fullscreen
        t0 = time.monotonic()
        injector.inject_text("译好的英文", "paste", True)
        return time.monotonic() - t0

    try:
        dt_desktop = timed(False)
        dt_full = timed(True)
    finally:
        cap.keyboard.send, cap.release_modifiers = saved_send, saved_rel
        cap.get_clipboard_text, cap.set_clipboard_text = saved_get, saved_set
        injector._foreground_is_fullscreen = saved_probe

    check("桌面场景整条回写 < 0.6s", dt_desktop < 0.6, f"{dt_desktop * 1000:.0f}ms")
    check("全屏场景整条回写 >= 0.9s（仍保留游戏需要的余量）",
          dt_full >= 0.9, f"{dt_full * 1000:.0f}ms")
    check("两场景确有可观测差异（>= 0.4s）", (dt_full - dt_desktop) >= 0.4,
          f"差 {((dt_full - dt_desktop) * 1000):.0f}ms")
finally:
    injector._foreground_is_fullscreen = saved_probe

print()
print(f"通过 {PASS} 项，失败 {FAIL} 项")
sys.exit(1 if FAIL else 0)
