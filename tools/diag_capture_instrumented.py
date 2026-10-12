# -*- coding: utf-8 -*-
"""插桩复现：逐步打印 capture_selected_text 内部每一步的真实结果（2026-10-12）

目的：定位"文本已全选，却仍回退到 shift+home（只取本行）"的确切原因。
做法：猴补丁包装 capture 内部函数，记录下来回内容与耗时。
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
    wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = proc, hinst, "BoardTransInstDiag"
    user32.RegisterClassW(ctypes.byref(wc))
    S["hwnd"] = user32.CreateWindowExW(0, "BoardTransInstDiag", "插桩诊断",
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

# ---------- 插桩 ----------
_real_set = capture.set_clipboard_text
_real_wait = capture._wait_for_clipboard_change


def spy_set(text):
    t0 = time.time()
    r = _real_set(text)
    shown = text if len(text) < 30 else text[:24] + "…"
    print(f"        [set] {shown!r} -> {r}  ({time.time()-t0:.3f}s)")
    return r


def spy_wait(sentinel, timeout=capture.COPY_TIMEOUT):
    t0 = time.time()
    r = _real_wait(sentinel, timeout)
    print(f"        [wait] 哨兵={sentinel[:20]}… 等待 {timeout}s -> 取到 {len(r)} 字 "
          f"(用时 {time.time()-t0:.3f}s)")
    if not r:
        cur = pyperclip.paste()
        print(f"        [wait] 超时时剪贴板实际是: {cur[:40]!r}")
    return r


capture.set_clipboard_text = spy_set
capture._wait_for_clipboard_change = spy_wait


def sel():
    a, b = ctypes.c_uint(0), ctypes.c_uint(0)
    user32.SendMessageW(S["edit"], EM_GETSEL, ctypes.byref(a), ctypes.byref(b))
    return a.value, b.value


def main():
    threading.Thread(target=_thread, daemon=True).start()
    S["ready"].wait(5)
    time.sleep(0.6)

    print("=" * 70)
    print("场景：文本已【全选】，调用 capture_selected_text（预期应取到全文）")
    print("=" * 70)
    user32.SendMessageW(S["edit"], WM_SETTEXT, 0, TEXT)
    user32.SendMessageW(S["edit"], EM_SETSEL, 0, -1)
    user32.SetForegroundWindow(S["hwnd"])
    user32.SetFocus(S["edit"])
    time.sleep(0.3)
    print(f"  调用前选区={sel()}  文本长度={len(TEXT)}")
    print("  --- 进入 capture_selected_text ---")
    text, how = capture.capture_selected_text("auto", 2000)
    print("  --- 返回 ---")
    print(f"  取到 {len(text)} 字  方式={how}")
    print(f"  内容前 30 字: {text[:30]!r}")
    print()
    verdict = "全文" if len(text) == len(TEXT) else f"只有 {len(text)} 字（不是全文）"
    print(f"  判定：取到的是 {verdict}")
    time.sleep(1.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
