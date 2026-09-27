# -*- coding: utf-8 -*-
"""
瞬译 - 取词（拿到用户刚输入的中文）

方案选择说明
------------
需求给出两个候选方案，这里采用「混合方案」，理由是实测最稳：

A. 只依赖"先选中文字再按快捷键"：最精确，但需要用户养成先选中的习惯，
   打断"打字即翻译"的流畅感。
B. 快捷键触发后自动回删并读取：无需用户操作，但在聊天窗口、
   浏览器地址栏等不同控件上，"整行选中"的边界行为不一致。

混合方案 = 先尝试 A（如果用户已经选中了文字，Ctrl+C 就能拿到，语义最准），
拿不到再自动执行 B（Shift+Home 选中光标以前的整行内容再 Ctrl+C），
两条路都取到文本后统一用 Delete 删掉选中内容，再把译文写回同一位置。

安全性：
- 取词前先把剪贴板写成随机哨兵字符串，通过"剪贴板是否变化"判断 Ctrl+C 是否成功；
- 取词结束后恢复用户原剪贴板内容；
- 全流程有超时（默认 600 ms），任何一步失败都返回失败原因而不是卡死。
"""

from __future__ import annotations

import ctypes
import time
import uuid

import keyboard
import pyperclip

from .logger import get_logger

log = get_logger()

# Ctrl+C 后剪贴板可能延迟写入，轮询等待
POLL_INTERVAL = 0.01
COPY_TIMEOUT = 0.6

_MODIFIER_KEYS = ("alt", "left alt", "right alt", "ctrl", "left ctrl", "right ctrl",
                  "shift", "left shift", "right shift", "windows")


class CaptureError(Exception):
    """取词失败，附带给用户看的提示。"""


def release_modifiers(settle: float = 0.02) -> None:
    """释放可能仍被按住的修饰键。

    热键是 Alt+J 时，回调触发瞬间 Alt 往往还没抬起，
    此时直接发 Ctrl+C 会变成 Ctrl+Alt+C 而失效，必须先清掉修饰键状态。
    """
    for key in _MODIFIER_KEYS:
        try:
            keyboard.release(key)
        except Exception:  # noqa: BLE001
            pass
    if settle:
        time.sleep(settle)


def get_clipboard_text() -> str:
    """读取剪贴板文本（非文本内容返回空串）。"""
    try:
        return pyperclip.paste() or ""
    except Exception:  # noqa: BLE001
        return ""


def set_clipboard_text(text: str) -> bool:
    """写入剪贴板文本，失败重试几次（剪贴板可能被其他程序短暂占用）。"""
    for _ in range(5):
        try:
            pyperclip.copy(text)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.03)
    return False


def _wait_for_clipboard_change(sentinel: str, timeout: float = COPY_TIMEOUT) -> str:
    """轮询剪贴板，直到内容不再是哨兵值，返回新内容（超时返回空串）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = get_clipboard_text()
        if current and current != sentinel:
            return current
        time.sleep(POLL_INTERVAL)
    return ""


def capture_selected_text(mode: str = "auto", max_chars: int = 2000) -> tuple[str, str]:
    """取回待翻译文本。

    参数:
        mode: auto（先试已有选中，再自动选行）/ selection（只认已有选中）/ line（总是选行）
        max_chars: 取词上限，超出视为误操作

    返回:
        (文本, 模式说明)；文本为空串表示失败。
    """
    backup = get_clipboard_text()
    sentinel = f"__shunyi_{uuid.uuid4().hex}__"

    try:
        release_modifiers()

        # --- 第一步：如果用户已经选中了文字，Ctrl+C 就能拿到 ---
        if mode in ("auto", "selection"):
            set_clipboard_text(sentinel)
            keyboard.send("ctrl+c")
            text = _wait_for_clipboard_change(sentinel)
            if text:
                return _post_process(text, "selection", max_chars, sentinel, backup)

        if mode == "selection":
            return "", "当前没有选中文字（当前模式只翻译选中的内容）"

        # --- 第二步：自动选中光标以前的整行内容 ---
        set_clipboard_text(sentinel)
        keyboard.send("shift+home")
        time.sleep(0.05)
        keyboard.send("ctrl+c")
        text = _wait_for_clipboard_change(sentinel)
        if not text:
            return "", "没能取到文字：当前窗口可能不支持自动取词，请先手动选中文字再按快捷键"
        return _post_process(text, "line", max_chars, sentinel, backup)

    finally:
        # 无论成功失败，都恢复用户原来的剪贴板（哪怕原来是空的）
        set_clipboard_text(backup)


def _post_process(text: str, source: str, max_chars: int, sentinel: str, backup: str) -> tuple[str, str]:
    """取到文本后的处理：删除选中内容 + 长度校验。"""
    text = text.strip()
    if not text:
        return "", "取到的内容是空的"

    if len(text) > max_chars:
        # 误选中整篇文档的保护：保持原样不动（选区仍在），只提示
        return "", f"取到的内容太长（{len(text)} 字，超过上限 {max_chars}），已跳过以免误操作"

    # 删掉刚被选中的原文，光标留在原位，等着接收译文
    keyboard.send("delete")
    time.sleep(0.03)
    return text, ("已选中内容" if source == "selection" else "自动选中本行")


def is_admin() -> bool:
    """当前进程是否有管理员权限（用于提示权限不足导致的取词失败）。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def restore_clipboard(text: str) -> None:
    """外部恢复剪贴板用。"""
    if text:
        set_clipboard_text(text)
