# -*- coding: utf-8 -*-
"""二分定位：capture_selected_text 里到底哪一步让 Ctrl+C 失效（2026-10-12）

插桩已确认：函数内部 Ctrl+C 完全不生效（剪贴板始终停在哨兵值），
但手工同样两步却成功。本脚本把函数内部的调用**逐项拆开**，
在同一个窗口、同一轮运行里对比，排除环境差异。

变体：
  V1  set + send(ctrl+c)                                  —— 基线
  V2  get_clipboard_text + set + send(ctrl+c)             —— 加"先读剪贴板"
  V3  release_modifiers + set + send(ctrl+c)              —— 加"释放修饰键"
  V4  get + release + set + send(ctrl+c)                  —— 等同函数内部顺序
"""
import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import keyboard                      # noqa: E402
import pyperclip                     # noqa: E402
from app import capture              # noqa: E402

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
WS_OVERLAPPEDWINDOW, WS_VISIBLE, WS_CHILD, WS_BORDER = 0x00CF0000, 0x10000000, 0x40000000, 0x00800000
ES_MULTILINE = 0x0004
CW_USEDEFAULT, SW_SHOW, PM_REMOVE = 0x80000000, 5, 0x0001
ASFW_ANY = -1
WM_SETTEXT, EM_SETSEL, EM_GETSEL = 0x000C, 0x00B1, 0x00B0

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
proc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
S = {"hwnd": None, "edit": None, "ready": threading.Event()}


def _thread():
    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = proc, hinst, "BoardTransBisect"
    user32.RegisterClassW(ctypes.byref(wc))
    S["hwnd"] = user32.CreateWindowExW(0, "BoardTransBisect", "二分诊断",
                                       WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                                       CW_USEDEFAULT, CW_USEDEFAULT, 700, 300,
                                       None, None, hinst, None)
    S["edit"] = user32.CreateWindowExW(0, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_BORDER | ES_MULTILINE,
                                       10, 10, 670, 240, S["hwnd"], None, hinst, None)
    user32.AllowSetForegroundWindow(ASFW_ANY)
    user32.ShowWindow(S["hwnd"], SW_SHOW)
    user32.UpdateWindow(S["hwnd"])
    user32.SetForegroundWindow(S["hwnd"])
    user32.SetFocus(S["edit"])
    S["ready"].set()
    msg = wintypes.MSG()
    while True:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.005)


TEXT = "第一行中文内容。\n第二行中文内容。\n第三行中文内容。\n最后一行中文内容。"


def prepare():
    """重置内容并全选，确保焦点在编辑框。"""
    user32.SendMessageW(S["edit"], WM_SETTEXT, 0, TEXT)
    user32.SendMessageW(S["edit"], EM_SETSEL, 0, -1)
    user32.SetForegroundWindow(S["hwnd"])
    user32.SetFocus(S["edit"])
    time.sleep(0.25)


def sel():
    a, b = ctypes.c_uint(0), ctypes.c_uint(0)
    user32.SendMessageW(S["edit"], EM_GETSEL, ctypes.byref(a), ctypes.byref(b))
    return a.value, b.value


def probe(label, do_read_first, do_release):
    prepare()
    if do_read_first:
        _ = capture.get_clipboard_text()
    if do_release:
        capture.release_modifiers()
    sentinel = "__probe__"
    capture.set_clipboard_text(sentinel)
    keyboard.send("ctrl+c")
    got = ""
    deadline = time.time() + 0.6
    while time.time() < deadline:
        cur = pyperclip.paste()
        if cur and cur != sentinel:
            got = cur
            break
        time.sleep(0.01)
    ok = got == TEXT
    flags = ("先读剪贴板 " if do_read_first else "") + ("释放修饰键" if do_release else "")
    print(f"  {label:38s} 选区={sel()}  {'✅ 成功' if ok else '❌ 失败'}  取到 {len(got)} 字"
          + (f"   [{flags}]" if flags else "   [基线]"))
    return ok


def main():
    threading.Thread(target=_thread, daemon=True).start()
    S["ready"].wait(5)
    time.sleep(0.6)
    capture.set_clipboard_text("")
    print("=" * 74)
    print("二分：哪一步让 Ctrl+C 失效？（同一窗口、同一轮运行内对比）")
    print("=" * 74)
    r1 = probe("V1 基线", False, False)
    r2 = probe("V2 +先读剪贴板", True, False)
    r3 = probe("V3 +释放修饰键", False, True)
    r4 = probe("V4 +先读 +释放（=函数内部顺序）", True, True)
    print()
    print("=" * 74)
    if r1 and r4:
        print("结论：四种变体结果不一致但基线成功 —— 疑似时序/状态污染，需再看")
    elif r1 and not r2:
        print("结论：**先调用 get_clipboard_text() 会让紧随其后的 Ctrl+C 失效** ← 根因")
    elif r1 and not r3:
        print("结论：**release_modifiers() 会让紧随其后的 Ctrl+C 失效** ← 根因")
    elif r1 and not r4:
        print("结论：**先读剪贴板 + 释放修饰键的组合会让 Ctrl+C 失效** ← 根因")
    elif not r1:
        print("结论：基线也失败 —— 说明与窗口/时序有关，需换真实记事本验证")
    print("=" * 74)
    time.sleep(1.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
