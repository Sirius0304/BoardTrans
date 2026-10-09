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


def send_unicode_text(text: str, chunk_size: int = 48) -> None:
    """用 Unicode 键盘事件逐段发送文本（不经过剪贴板）。"""
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
    for start in range(0, len(events), chunk_size):
        batch = events[start:start + chunk_size]
        array = (_INPUT * len(batch))(*batch)
        ctypes.set_last_error(0)
        sent = _user32.SendInput(len(batch), array, struct_size)
        if sent != len(batch):
            log.warning("SendInput 只发送了 %s/%s 个事件（错误码 %s，结构体大小 %s）",
                        sent, len(batch), ctypes.get_last_error(), struct_size)
        time.sleep(0.005)


# ---------------- 对外接口 ----------------


def inject_text(text: str, mode: str = "paste", restore_clipboard: bool = True) -> str:
    """把译文写回当前光标位置。返回实际使用的方式说明。"""
    if not text:
        return "译文为空，未写入"

    capture.release_modifiers()

    if mode == "type":
        send_unicode_text(text)
        return "逐字输入（未使用剪贴板）"

    # paste 模式
    backup = capture.get_clipboard_text()
    if not capture.set_clipboard_text(text):
        # 剪贴板被占用时降级为逐字输入
        log.warning("剪贴板写入失败，降级为逐字输入")
        send_unicode_text(text)
        return "逐字输入（剪贴板被占用，已降级）"

    time.sleep(0.06)
    keyboard.send("ctrl+v")
    time.sleep(0.15)   # 等目标程序完成粘贴

    if restore_clipboard:
        if not capture.set_clipboard_text(backup):
            log.warning("剪贴板恢复失败")
        # 提示：Win+V 剪贴板历史里仍会留下一次译文记录，这是系统行为，无法程序化清除
    return "粘贴（剪贴板已还原）"


def notify_user(title: str, message: str) -> None:
    """控制台模式下的简易提示（托盘模式下由托盘气泡接管）。"""
    print(f"[{title}] {message}", flush=True)
