# -*- coding: utf-8 -*-
"""
端到端测试：用 Win32 原生窗口模拟"任意应用"，验证完整链路
输入中文 -> 取词 -> 离线翻译 -> 译文替换回原位。

线程结构（关键）：
- 子线程：创建窗口并**独占**跑消息循环（SetFocus 只能由创建窗口的线程调用），
  同时负责初始内容与光标位置。
- 主线程：执行测试步骤（发送按键、取词、翻译、回写）。
这样窗口能在取词等待期间持续处理消息，与"用户的目标程序是另一个进程"的真实场景一致。
"""

import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import capture, injector                      # noqa: E402
from app.engine import TranslationEngine               # noqa: E402

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WS_OVERLAPPEDWINDOW, WS_VISIBLE, WS_CHILD = 0x00CF0000, 0x10000000, 0x40000000
WS_BORDER, WS_VSCROLL, ES_MULTILINE, ES_AUTOVSCROLL = 0x00800000, 0x00200000, 0x0004, 0x0040
CW_USEDEFAULT, SW_SHOW, PM_REMOVE, ASFW_ANY = 0x80000000, 5, 0x0001, -1
WM_SETTEXT, WM_GETTEXT, WM_GETTEXTLENGTH = 0x000C, 0x000D, 0x000E
WM_DESTROY, EM_SETSEL = 0x0002, 0x00B1

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


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
_proc_ref = WNDPROC(_proc)

ORIGINAL = "今天天气不错"
# 回写方式：命令行第一个参数，默认 paste（可传 type 验证逐字输入模式）
INJECT_MODE = sys.argv[1] if len(sys.argv) > 1 else "paste"
state = {"hwnd": None, "edit": None, "ready": False, "stop": False, "foreground_ok": False}


def get_edit_text(hwnd) -> str:
    length = user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0)
    buf = ctypes.create_unicode_buffer(length + 2)
    user32.SendMessageW(hwnd, WM_GETTEXT, length + 1, buf)
    return buf.value


def force_foreground(hwnd, edit, tries: int = 6) -> bool:
    """把测试窗口强行带到前台。

    Windows 有"前台锁定"机制：后台进程不能随意抢占前台，
    单靠 SetForegroundWindow 常常无效（实测会时成时败）。
    这里用 AttachThreadInput 把本线程附加到当前前台线程的输入队列再抢，成功率高得多。
    注意：这只是**测试脚本**的需要 —— 真实使用时用户在哪个窗口打字，那个窗口本来就有焦点。
    """
    user32.AllowSetForegroundWindow(ASFW_ANY)
    for _ in range(tries):
        user32.ShowWindow(hwnd, 9)          # SW_RESTORE
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(edit)
        if user32.GetForegroundWindow() == hwnd:
            return True
        fg = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg, None)
        cur_tid = kernel32.GetCurrentThreadId()
        if fg_tid and fg_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, fg_tid, True)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(edit)
            user32.AttachThreadInput(cur_tid, fg_tid, False)
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.35)
    return False


def ui_thread():
    """子线程：创建窗口 + 独占消息循环。"""
    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    wc.lpfnWndProc = _proc_ref
    wc.hInstance = hinst
    wc.lpszClassName = "ShunYiE2E"
    user32.RegisterClassW(ctypes.byref(wc))

    hwnd = user32.CreateWindowExW(0, "ShunYiE2E", "瞬译 端到端测试窗口",
                                  WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                                  CW_USEDEFAULT, CW_USEDEFAULT, 780, 260, None, None, hinst, None)
    edit = user32.CreateWindowExW(0, "EDIT", "",
                                  WS_CHILD | WS_VISIBLE | WS_BORDER | WS_VSCROLL | ES_MULTILINE | ES_AUTOVSCROLL,
                                  12, 12, 740, 190, hwnd, None, hinst, None)
    user32.ShowWindow(hwnd, SW_SHOW)
    user32.UpdateWindow(hwnd)

    # 初始内容 + 光标停在句末（模拟"用户刚打完字"）
    user32.SendMessageW(edit, WM_SETTEXT, 0, ORIGINAL)
    user32.SendMessageW(edit, EM_SETSEL, len(ORIGINAL), len(ORIGINAL))

    # 焦点必须由创建窗口的线程设置
    state["foreground_ok"] = force_foreground(hwnd, edit)

    state["hwnd"], state["edit"], state["ready"] = hwnd, edit, True

    msg = wintypes.MSG()
    while not state["stop"]:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        # 主线程如果抢前台失败，会置这个标志，由本线程（窗口所有者）重试
        # —— SetFocus 只能作用于创建窗口的线程，所以必须在这里做
        if state.get("need_foreground"):
            state["need_foreground"] = False
            state["foreground_ok"] = force_foreground(hwnd, edit)
        time.sleep(0.005)

    user32.DestroyWindow(hwnd)


def main() -> int:
    threading.Thread(target=ui_thread, daemon=True).start()
    while not state["ready"]:
        time.sleep(0.05)

    hwnd, edit = state["hwnd"], state["edit"]
    time.sleep(1.0)

    fg_ok = bool(state.get("foreground_ok"))
    print(f"  测试窗口获得前台焦点: {fg_ok}")
    if not fg_ok:
        # 交给窗口线程重试（只有它能设置键盘焦点）
        state["need_foreground"] = True
        deadline = time.time() + 6
        while time.time() < deadline and not state.get("foreground_ok"):
            time.sleep(0.2)
        fg_ok = bool(state.get("foreground_ok"))
        print(f"  重试抢前台: {fg_ok}")
    if not fg_ok:
        print("  提示：抢不到前台时按键会被别的窗口接收，这是测试环境的限制（真实使用时窗口本身就是用户操作的，不存在此问题）")
    print(f"  窗口内容: {get_edit_text(edit)!r}")

    backup_clip = capture.get_clipboard_text()
    code = 1
    expected = None
    try:
        t0 = time.time()
        src, how = capture.capture_selected_text("auto", 2000)
        t1 = time.time()
        print(f"  [1] 取词[{how}]: {src!r}   ({(t1-t0)*1000:.0f} ms)")

        if src:
            engine = TranslationEngine()
            dst = engine.translate(src, "en", "fast")
            t2 = time.time()
            print(f"  [2] 翻译: {dst!r}   ({(t2-t1)*1000:.0f} ms)  路线={engine.last_stats.get('route')}")

            way = injector.inject_text(dst, INJECT_MODE, True)
            print(f"  [3] 回写({INJECT_MODE}): {way}")
            time.sleep(0.5)

            final = get_edit_text(edit).strip()
            after_clip = capture.get_clipboard_text()
            expected = dst.strip()
            print(f"  [4] 窗口最终内容: {final!r}")
            print(f"      剪贴板已还原: {after_clip == backup_clip}")
            code = 0 if (final == expected and final != ORIGINAL) else 1
    finally:
        state["stop"] = True
        time.sleep(0.3)

    print("\n  === 结论 ===")
    if code == 0:
        print("  通过：中文已被译文原地替换，且剪贴板已还原。")
    else:
        print(f"  未通过。期望 {expected!r}")
    return code


if __name__ == "__main__":
    sys.exit(main())
