# -*- coding: utf-8 -*-
"""逐步复现 capture_selected_text 的内部逻辑，找出失败的那一步。"""
import ctypes
import os
import sys
import time
import uuid
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import keyboard                      # noqa: E402
import pyperclip                     # noqa: E402
from app import capture              # noqa: E402

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WS_OVERLAPPEDWINDOW, WS_VISIBLE, WS_CHILD, WS_BORDER = 0x00CF0000, 0x10000000, 0x40000000, 0x00800000
ES_MULTILINE = 0x0004
CW_USEDEFAULT, SW_SHOW, PM_REMOVE, ASFW_ANY = 0x80000000, 5, 0x0001, -1
WM_SETTEXT, WM_DESTROY, EM_SETSEL, EM_GETSEL = 0x000C, 0x0002, 0x00B1, 0x00B0

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


def _proc(hwnd, msg, wp, lp):
    if msg == WM_DESTROY:
        return 0
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_longlong
proc = WNDPROC(_proc)
ORIGINAL = "今天天气不错"

hinst = kernel32.GetModuleHandleW(None)
wc = WNDCLASSW()
wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = proc, hinst, "ShunYiDiag2"
user32.RegisterClassW(ctypes.byref(wc))
hwnd = user32.CreateWindowExW(0, "ShunYiDiag2", "复核诊断", WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                              CW_USEDEFAULT, CW_USEDEFAULT, 600, 160, None, None, hinst, None)
edit = user32.CreateWindowExW(0, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_BORDER | ES_MULTILINE,
                              10, 10, 570, 100, hwnd, None, hinst, None)
user32.AllowSetForegroundWindow(ASFW_ANY)
user32.ShowWindow(hwnd, SW_SHOW)
user32.UpdateWindow(hwnd)
user32.SetForegroundWindow(hwnd)
user32.SetFocus(edit)


def pump(seconds):
    msg = wintypes.MSG()
    end = time.time() + seconds
    while time.time() < end:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.01)


def sel():
    a, b = ctypes.c_uint(0), ctypes.c_uint(0)
    user32.SendMessageW(edit, EM_GETSEL, ctypes.byref(a), ctypes.byref(b))
    return a.value, b.value


def reset():
    user32.SendMessageW(edit, WM_SETTEXT, 0, ORIGINAL)
    user32.SendMessageW(edit, EM_SETSEL, len(ORIGINAL), len(ORIGINAL))
    pump(0.4)


print("=" * 60)
print("场景一：窗口里已有内容，光标在末尾（无选中）—— 最典型的日常场景")
print("=" * 60)
reset()
print(f"  初始选区: {sel()}")
print(f"  前台一致: {user32.GetForegroundWindow() == hwnd}")

sentinel = f"__shunyi_{uuid.uuid4().hex}__"

print("\n  --- 第一步：Ctrl+C 试探（预期：失败，因为无选中）---")
capture.release_modifiers()
wrote = capture.set_clipboard_text(sentinel)
print(f"      哨兵写入返回: {wrote}")
print(f"      写入后剪贴板: {pyperclip.paste()!r}")
keyboard.send("ctrl+c")
pump(0.3)
print(f"      Ctrl+C 后剪贴板: {pyperclip.paste()!r}")

print("\n  --- 第二步：Shift+Home 选中再 Ctrl+C ---")
wrote = capture.set_clipboard_text(sentinel)
print(f"      哨兵写入返回: {wrote}  剪贴板: {pyperclip.paste()!r}")
keyboard.send("shift+home")
pump(0.2)
print(f"      Shift+Home 后选区: {sel()}")
keyboard.send("ctrl+c")
pump(0.4)
got = pyperclip.paste()
print(f"      Ctrl+C 后剪贴板: {got!r}   → {'成功' if got == ORIGINAL else '失败'}")

print("\n  --- 第三步：直接调用 capture_selected_text（同一场景）---")
reset()
print(f"      调用前选区: {sel()}")
text, how = capture.capture_selected_text("auto", 2000)
print(f"      结果: {text!r}")
print(f"      说明: {how}")

print("\n  --- 第四步：先用鼠标/键盘选中，再调用 capture_selected_text ---")
reset()
keyboard.send("shift+home")      # 模拟"用户自己选中了"
pump(0.3)
print(f"      调用前选区: {sel()}")
text, how = capture.capture_selected_text("auto", 2000)
print(f"      结果: {text!r}")
print(f"      说明: {how}")

user32.PostMessageW(hwnd, WM_DESTROY, 0, 0)
pump(0.3)
