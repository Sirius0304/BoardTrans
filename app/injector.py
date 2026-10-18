# -*- coding: utf-8 -*-
"""
键译 - 回写（把译文送回原来的输入位置）

两种回写方式（可在配置里切换）：

1. paste（默认，推荐）
   备份剪贴板 → 写入译文 → Ctrl+V → 等目标程序粘贴完 → 恢复原剪贴板。
   优点：瞬间完成，对任何输入框兼容性最好。
   取舍：Windows 的"剪贴板历史(Win+V)"仍会记录一次译文；
        如果原剪贴板里是图片/文件（不是文字），恢复时无法还原，
        程序会在日志里记一笔，并在首次使用时提示。

2. type
   用 SendInput 逐字符发送 Unicode，完全不碰剪贴板，
   连剪贴板历史都不会被污染。
   取舍：速度略慢（每字符一次键盘事件），部分应用的自动补全/输入法
        可能对逐字符输入有反应。适合"剪贴板里常年放重要内容"的用户。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

import keyboard

from . import capture
from .logger import get_logger

log = get_logger()

# ---------------- SendInput Unicode 逐字输入 ----------------
#
# 注意（踩过的坑）：INPUT 结构体里的联合体必须把 MOUSEINPUT 也写进来。
# Windows 的 INPUT 联合体大小由最大的成员 MOUSEINPUT（64 位下 32 字节）决定，
# 如果只声明 KEYBDINPUT（24 字节），sizeof(INPUT) 会算成 32 而不是 40，
# SendInput 会直接返回 0 并报错误码 87（参数错误），键盘事件一个都发不出去。

_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_RETURN = 0x0D

# use_last_error=True 才能用 ctypes.get_last_error() 拿到真实错误码
_user32 = ctypes.WinDLL("user32", use_last_error=True)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def input_struct_size() -> int:
    """供自检使用：64 位系统上正确值应为 40。"""
    return ctypes.sizeof(_INPUT)


def _make_key_input(scan: int, flags: int) -> _INPUT:
    item = _INPUT()
    item.type = 1   # INPUT_KEYBOARD
    item.ki = _KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return item


def send_unicode_text(text: str, chunk_size: int = 48) -> bool:
    """用 Unicode 键盘事件逐段发送文本（不经过剪贴板）。

    返回是否**全部**发送成功（2026-10-18 修「回写失败不可感知」）：
    过去 SendInput 返回值不足只写一条 warning，调用方拿不到信号，
    于是一边"原文已删、译文没进去"，一边还给用户报成功。
    游戏等不收注入键的环境里这种情况真实存在。
    """
    events: list[_INPUT] = []
    for ch in text:
        if ch == "\n":
            events.append(_make_key_input(VK_RETURN, 0))
            events.append(_make_key_input(VK_RETURN, KEYEVENTF_KEYUP))
            continue
        encoded = ch.encode("utf-16-le")
        for i in range(0, len(encoded), 2):
            unit = int.from_bytes(encoded[i:i + 2], "little")
            events.append(_make_key_input(unit, KEYEVENTF_UNICODE))
            events.append(_make_key_input(unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))

    struct_size = ctypes.sizeof(_INPUT)
    ok = True
    for start in range(0, len(events), chunk_size):
        batch = events[start:start + chunk_size]
        array = (_INPUT * len(batch))(*batch)
        ctypes.set_last_error(0)
        sent = _user32.SendInput(len(batch), array, struct_size)
        if sent != len(batch):
            log.warning("SendInput 只发送了 %s/%s 个事件（错误码 %s，结构体大小 %s）",
                        sent, len(batch), ctypes.get_last_error(), struct_size)
            ok = False
        time.sleep(0.005)
    return ok


# ---------------- 对外接口 ----------------

# 发 Ctrl+V 前后的等待（2026-10-14 修「游戏里贴出旧剪贴板」）：
# 普通程序 16ms 内就完成粘贴，但游戏（守望先锋等）在帧循环里异步处理输入，
# 等它真正去读剪贴板时，原来的 0.06+0.15 秒早过了 —— 而我们已经把剪贴板
# 还原成旧内容，游戏贴出来的自然是"粘贴前的剪贴板"。
PASTE_PREP_WAIT = 0.15

# 粘贴后的确认等待，**按前台窗口类型自适应**（2026-10-16 提速方案④）：
#   普通桌面程序：16ms 一帧，0.25s 早已粘贴完毕，多等纯属浪费；
#   全屏 / 游戏    ：异步帧循环 + 输入排队，实测 0.15s 不够（会贴出旧剪贴板），给 0.8s。
# 判定见 _foreground_is_fullscreen()：前台窗口是否铺满所在显示器。
PASTE_CONFIRM_WAIT_DESKTOP = 0.25
PASTE_CONFIRM_WAIT_FULLSCREEN = 0.8
# 兼容旧引用的别名：取**保守值**，任何漏判都退回到游戏那套较长等待，不会丢粘贴。
PASTE_CONFIRM_WAIT = PASTE_CONFIRM_WAIT_FULLSCREEN


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _foreground_is_fullscreen() -> bool:
    """前台窗口是否铺满整个显示器（= 全屏游戏 / 无边框全屏视频）。

    为什么需要它：游戏聊天框在帧循环里异步读剪贴板，粘贴后必须多等一会儿
    才能还原剪贴板（否则贴出旧内容）；而普通桌面程序根本不需要等这么久。
    任何异常/取不到都返回 False（退化为"桌面"的快路径，最长也就等 0.25s，
    出问题还有 paste 失败时的原文回注兜底）。
    """
    try:
        user32 = _user32
        # 句柄是 64 位指针，必须显式声明返回值类型，否则会被截断成 32 位
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.MonitorFromWindow.restype = ctypes.c_void_p
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        monitor = user32.MonitorFromWindow(hwnd, 2)      # 2 = MONITOR_DEFAULTTONEAREST
        if not monitor:
            return False
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
        m = info.rcMonitor
        return (rect.left <= m.left and rect.top <= m.top
                and rect.right >= m.right and rect.bottom >= m.bottom)
    except Exception:  # noqa: BLE001
        return False


def paste_confirm_wait() -> float:
    """粘贴后该等多久再还原剪贴板：全屏/游戏 0.8s，普通桌面程序 0.25s。"""
    return PASTE_CONFIRM_WAIT_FULLSCREEN if _foreground_is_fullscreen() \
        else PASTE_CONFIRM_WAIT_DESKTOP


def inject_text(text: str, mode: str = "paste", restore_clipboard: bool = True) -> tuple[bool, str]:
    """把译文写回当前光标位置。返回 (是否可能成功, 方式说明)。

    返回值的意义（2026-10-18 修「回写失败不可感知」）：
      - paste 模式：True 表示剪贴板已写入且 Ctrl+V 已发出；**无法确证**目标程序
        真的消费了这次粘贴（Windows 没有回执），所以是"可能成功"而不是"确认成功"；
      - type 模式：True/False 是 SendInput 的真实回执（全部事件都发出去了才算成功）。
    调用方在拿不到"确认"时要靠提示文案说清楚，不能再一律报成功。
    """
    if not text:
        return False, "译文为空，未写入"

    capture.release_modifiers()

    if mode == "type":
        ok = send_unicode_text(text)
        return ok, "逐字输入（未使用剪贴板）" if ok else "逐字输入未全部发出（目标程序可能吞了按键）"

    # paste 模式
    backup = capture.get_clipboard_text()
    if not capture.set_clipboard_text(text):
        # 剪贴板被占用时降级为逐字输入
        log.warning("剪贴板写入失败，降级为逐字输入")
        ok = send_unicode_text(text)
        return ok, "逐字输入（剪贴板被占用，已降级）" if ok \
            else "逐字输入降级后仍未发出（剪贴板被占用 + 目标程序可能吞键）"

    time.sleep(PASTE_PREP_WAIT)
    keyboard.send("ctrl+v")
    wait = paste_confirm_wait()      # 按前台窗口自适应（见常量注释）
    log.info("粘贴后等待 %.2fs 再还原剪贴板（前台窗口：%s）",
             wait, "全屏/游戏" if wait == PASTE_CONFIRM_WAIT_FULLSCREEN else "桌面程序")
    time.sleep(wait)

    if restore_clipboard:
        if not capture.set_clipboard_text(backup):
            log.warning("剪贴板恢复失败")
        # 提示：Win+V 剪贴板历史里仍会留下一次译文记录，这是系统行为，无法程序化清除
    return True, "粘贴（剪贴板已还原）"


def notify_user(title: str, message: str) -> None:
    """控制台模式下的简易提示（托盘模式下由托盘气泡接管）。"""
    print(f"[{title}] {message}", flush=True)
