# -*- coding: utf-8 -*-
"""精确定位取词失败环节：Shift+Home 选中 / Ctrl+C 复制。"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import keyboard                      # noqa: E402
import pyperclip                     # noqa: E402
from app import capture              # noqa: E402

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
WS_CHILD = 0x40000000
WS_BORDER = 0x00800000
ES_MULTILINE = 0x0004
CW_USEDEFAULT = 0x80000000
SW_SHOW = 5
PM_REMOVE = 0x0001
ASFW_ANY = -1
WM_SETTEXT, WM_GETTEXTLENGTH = 0x000C, 0x000E
WM_DESTROY = 0x0002
EM_SETSEL = 0x00B1
EM_GETSEL = 0x00B0

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


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
wc.lpfnWndProc = proc
wc.hInstance = hinst
wc.lpszClassName = "ShunYiDiag"
user32.RegisterClassW(ctypes.byref(wc))
hwnd = user32.CreateWindowExW(0, "ShunYiDiag", "取词诊断", WS_OVERLAPPEDWINDOW | WS_VISIBLE,
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


def get_sel():
    """返回 (起始, 结束) 选区位置。"""
    out = ctypes.c_uint(0)
    out2 = ctypes.c_uint(0)
    user32.SendMessageW(edit, EM_GETSEL, ctypes.byref(out), ctypes.byref(out2))
    return out.value, out2.value


user32.SendMessageW(edit, WM_SETTEXT, 0, ORIGINAL)
user32.SendMessageW(edit, EM_SETSEL, len(ORIGINAL), len(ORIGINAL))
pump(0.6)

print(f"  前台窗口一致: {user32.GetForegroundWindow() == hwnd}")
print(f"  初始选区: {get_sel()}  (光标应在 {len(ORIGINAL)} 处，无选中)")

capture.release_modifiers()
print("\n  [A] 测试 Ctrl+C（无选中时）")
capture.set_clipboard_text("__sentinel__")
keyboard.send("ctrl+c")
pump(0.4)
print(f"      剪贴板: {pyperclip.paste()!r}")

print("\n  [B] 测试 Shift+Home 选中")
keyboard.send("shift+home")
pump(0.3)
sel = get_sel()
print(f"      选区: {sel}  → {'选中成功' if sel[0] != sel[1] else '未选中'}")

print("\n  [C] 再测试 Ctrl+C（有选中时）")
capture.set_clipboard_text("__sentinel__")
keyboard.send("ctrl+c")
pump(0.5)
clip = pyperclip.paste()
print(f"      剪贴板: {clip!r}  → {'复制成功' if clip == ORIGINAL else '复制失败'}")

print("\n  [D] 测试 keyboard 库的 shift 状态是否卡住")
try:
    print("      shift 是否被判定为按下:", keyboard.is_pressed("shift"))
except Exception as exc:  # noqa: BLE001
    print("      查询失败:", exc)

print("\n  [E] 用同一次 send 直接发 shift+home 再 ctrl+c（模拟程序逻辑）")
capture.set_clipboard_text("__sentinel__")
keyboard.send("shift+home")
time.sleep(0.05)
keyboard.send("ctrl+c")
pump(0.6)
clip = pyperclip.paste()
print(f"      剪贴板: {clip!r}  → {'成功' if clip == ORIGINAL else '失败'}")

print("\n  [F] 调用完整的 capture_selected_text('auto')")
capture.set_clipboard_text("")
text, how = capture.capture_selected_text("auto", 2000)
print(f"      结果: {text!r}   说明: {how}")

user32.PostMessageW(hwnd, WM_DESTROY, 0, 0)
pump(0.3)
