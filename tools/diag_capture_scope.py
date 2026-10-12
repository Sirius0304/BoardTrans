# -*- coding: utf-8 -*-
"""在"目标窗口独立跑消息泵"的条件下复现取词行为（2026-10-12）

为什么要重做：tools/diag_capture2.py 把目标窗口和调用方放在**同一线程**，
而它中间只用 time.sleep（不泵消息），于是注入的 Ctrl+C 根本没被目标窗口处理
→ 看起来"取词失败"。那是**测试装置的缺陷**，不代表真实场景。

真实场景里目标程序（记事本等）是**独立进程、自己泵消息**。这里用
**后台线程建窗口 + 持续泵消息**来近似，才能看到取词的真实行为。

用法：
    python tools/diag_capture_scope.py            # 跑全部场景

⚠️ 重要：**这台测试台本身不可靠**（2026-10-12 实测）
    目标窗口虽然放在独立线程并持续泵消息，但**同进程注入按键仍有竞态**：
    同一份代码多次运行，各场景时好时坏（甚至全部失败）。
    → 它可以用来**看趋势**（修复前后"取到多少字"的量级对比），
      **不能用来判定"修复是否成功"**。
    → 要 100% 确证，必须用**真实记事本**（独立进程）手测。
    确定性逻辑请以 tools/test_capture_fix.py 为准。
"""
import ctypes
import os
import sys
import time
import threading
import uuid
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import keyboard                      # noqa: E402
import pyperclip                     # noqa: E402
from app import capture              # noqa: E402

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WS_OVERLAPPEDWINDOW, WS_VISIBLE, WS_CHILD, WS_BORDER = 0x00CF0000, 0x10000000, 0x40000000, 0x00800000
ES_MULTILINE, ES_AUTOVSCROLL = 0x0004, 0x0040
CW_USEDEFAULT, SW_SHOW, PM_REMOVE = 0x80000000, 5, 0x0001
ASFW_ANY = -1
WM_SETTEXT, WM_DESTROY, EM_SETSEL, EM_GETSEL = 0x000C, 0x0002, 0x00B1, 0x00B0

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_longlong


def _proc(hwnd, msg, wp, lp):
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


proc = WNDPROC(_proc)

_state = {"hwnd": None, "edit": None, "ready": threading.Event()}


def _window_thread():
    """后台线程：建窗口 + 持续泵消息（模拟真实程序的独立消息循环）。"""
    user32.SetProcessDPIAware()
    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    wc.lpfnWndProc, wc.hInstance = proc, hinst
    wc.lpszClassName = "BoardTransScopeDiag"
    user32.RegisterClassW(ctypes.byref(wc))
    hwnd = user32.CreateWindowExW(0, "BoardTransScopeDiag", "取词范围诊断",
                                  WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                                  CW_USEDEFAULT, CW_USEDEFAULT, 700, 300,
                                  None, None, hinst, None)
    edit = user32.CreateWindowExW(0, "EDIT", "",
                                  WS_CHILD | WS_VISIBLE | WS_BORDER | ES_MULTILINE | ES_AUTOVSCROLL,
                                  10, 10, 670, 240, hwnd, None, hinst, None)
    _state["hwnd"], _state["edit"] = hwnd, edit
    user32.AllowSetForegroundWindow(ASFW_ANY)
    user32.ShowWindow(hwnd, SW_SHOW)
    user32.UpdateWindow(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(edit)
    _state["ready"].set()
    msg = wintypes.MSG()
    while True:                                  # 永远泵消息
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.005)


def _sel():
    a, b = ctypes.c_uint(0), ctypes.c_uint(0)
    user32.SendMessageW(_state["edit"], EM_GETSEL, ctypes.byref(a), ctypes.byref(b))
    return a.value, b.value


def set_text(text: str, caret: int):
    """设置编辑框内容并把光标放到 caret 处（不选中）。"""
    user32.SendMessageW(_state["edit"], WM_SETTEXT, 0, text)
    user32.SendMessageW(_state["edit"], EM_SETSEL, caret, caret)
    _refocus()
    time.sleep(0.3)


def select_all():
    user32.SendMessageW(_state["edit"], EM_SETSEL, 0, -1)
    _refocus()
    time.sleep(0.2)


def _refocus():
    user32.SetForegroundWindow(_state["hwnd"])
    user32.SetFocus(_state["edit"])
    time.sleep(0.1)


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def main():
    threading.Thread(target=_window_thread, daemon=True).start()
    _state["ready"].wait(5)
    time.sleep(0.5)
    print("=" * 66)
    print("取词范围诊断（目标窗口独立泵消息）")
    print("=" * 66)

    long_text = (
        "今天早上我六点半就起床了，因为要赶八点的火车去上海出差。\n"
        "出门的时候天还没完全亮，路上几乎没有行人，只有环卫工人在清扫街道。\n"
        "到了火车站，我发现候车大厅里已经挤满了人，看来大家都是赶早班车的。\n"
        "上车之后我找了一个靠窗的位置坐下，打算趁这两个小时把方案再改一遍。"
    )

    print(f"\n【场景1】多行长文本（{len(long_text)} 字，光标在末尾，**没有选中**）")
    set_text(long_text, len(long_text))
    text, how = capture.capture_selected_text("auto", 20000)
    print(f"    取到 {len(text)} 字  |  方式: {how}")
    print(f"    取到内容: {text!r}")
    check("未选中时应取到**全文**（当前实现只取最后一行）", len(text) > len(long_text) * 0.8,
          f"实际 {len(text)}/{len(long_text)} 字")
    check("取到的内容应从开头开始（不是只剩结尾）", text.startswith(long_text[:6]),
          f"开头是 {text[:10]!r}")

    print(f"\n【场景2】同文本，**已全选**（模拟用户 Ctrl+A 后按快捷键）")
    set_text(long_text, len(long_text))
    select_all()
    text2, how2 = capture.capture_selected_text("auto", 20000)
    print(f"    取到 {len(text2)} 字  |  方式: {how2}")
    check("全选后应取到全文", len(text2) == len(long_text), f"{len(text2)}/{len(long_text)}")

    print(f"\n【场景3】TXT 风格：多行、含 \\r\\n、长内容（{len(long_text)} 字）")
    txt_like = long_text.replace("\n", "\r\n")
    set_text(txt_like, len(txt_like))
    text3, how3 = capture.capture_selected_text("auto", 20000)
    print(f"    取到 {len(text3)} 字  |  方式: {how3}")
    check("TXT 多行内容未选中时应取到全文", len(text3) > len(txt_like) * 0.8,
          f"实际 {len(text3)}/{len(txt_like)}")

    print(f"\n【场景4】超长文本（4800 字，新默认上限 20000 应可放行）")
    huge = "这是一段用于测试超长文本的句子。" * 300
    set_text(huge, len(huge))
    select_all()
    text4, how4 = capture.capture_selected_text("auto", 20000)
    print(f"    取到 {len(text4)} 字  |  说明: {how4}")
    check("超长文本不应被简单拒绝（用户要的是完整翻译）", len(text4) > 0, how4[:40])

    print()
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print(f"  - {f}")
    print("\n（诊断结束，窗口将在 2 秒后关闭）")
    time.sleep(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
