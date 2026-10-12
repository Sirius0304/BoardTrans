# -*- coding: utf-8 -*-
"""定位"第一步 Ctrl+C 取选中内容"为什么失败（2026-10-12）

现象：即使文本已全选，capture_selected_text 也拿不到选中内容，
而是回退到 shift+home（只取本行）—— 长文本因此只翻译了最后一行。

嫌疑：release_modifiers() 里对 "windows" 键做 keyboard.release，
      可能被系统当成"按了一下 Win 键"，把焦点抢走（弹出开始菜单），
      导致随后的 Ctrl+C 发给了错误的窗口。

本脚本对比"调用 release_modifiers / 不调用"两种情况下的取词结果。
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
WM_SETTEXT, EM_SETSEL = 0x000C, 0x00B1

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
    wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = proc, hinst, "BoardTransCaptureDiag"
    user32.RegisterClassW(ctypes.byref(wc))
    S["hwnd"] = user32.CreateWindowExW(0, "BoardTransCaptureDiag", "取词定位诊断",
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


def reset_select_all():
    user32.SendMessageW(S["edit"], WM_SETTEXT, 0, TEXT)
    user32.SendMessageW(S["edit"], EM_SETSEL, 0, -1)
    user32.SetForegroundWindow(S["hwnd"])
    user32.SetFocus(S["edit"])
    time.sleep(0.25)


def fg_is_ours():
    return user32.GetForegroundWindow() == S["hwnd"]


def trial(name, do_release):
    reset_select_all()
    fg_before = fg_is_ours()
    if do_release:
        capture.release_modifiers()          # ← 嫌疑点
    fg_after = fg_is_ours()
    sentinel = "__probe__"
    capture.set_clipboard_text(sentinel)
    keyboard.send("ctrl+c")
    deadline = time.time() + 0.6
    got = ""
    while time.time() < deadline:
        cur = pyperclip.paste()
        if cur and cur != sentinel:
            got = cur
            break
        time.sleep(0.01)
    ok = got == TEXT
    print(f"  {name}")
    print(f"      释放修饰键前焦点属于目标窗口: {fg_before}")
    print(f"      释放修饰键后焦点属于目标窗口: {fg_after}"
          + ("   ← 焦点被抢走了！" if fg_before and not fg_after else ""))
    print(f"      Ctrl+C 取到: {got[:30]!r}  {'✅ 成功' if ok else '❌ 失败'}")
    return ok


def main():
    threading.Thread(target=_thread, daemon=True).start()
    S["ready"].wait(5)
    time.sleep(0.6)
    capture.set_clipboard_text("")
    print("=" * 68)
    print("定位：第一步 Ctrl+C 为什么取不到已选中的内容")
    print("=" * 68)
    a = trial("A) 不调用 release_modifiers（对照组）", do_release=False)
    print()
    b = trial("B) 调用 release_modifiers（当前实现）", do_release=True)
    print()
    print("=" * 68)
    print("结论："
          + ("release_modifiers 会抢走焦点，导致 Ctrl+C 失效 —— 这就是根因"
             if a and not b else
             "release_modifiers 不是根因（两种情况下结果相同，需继续排查）"))
    print("=" * 68)
    time.sleep(1.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
