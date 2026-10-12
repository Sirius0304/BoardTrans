# -*- coding: utf-8 -*-
"""
键译 - 取词（拿到用户刚输入的中文）

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

# 复制超时。原为 0.6s —— 实测长文本写进剪贴板可能要 1s 以上，
# 一旦超时，旧代码会**静默降级**成"只取本行"，用户看到的就是
# 「长文本只翻译了最后一段」。现在给足余量，并且失败还会重试一次。
COPY_TIMEOUT = 1.2
COPY_ATTEMPTS = 2

# 自动取词时最多向上扩多少行（只影响"整段"取词）。
# 每多扩一行要多一次"复制 + 等剪贴板"（约 60~70ms），所以这个上限同时是
# "最坏情况下的额外延迟"：25 行 ≈ 1.7s。主流的单行输入只会多试一次就退出。
PARAGRAPH_MAX_LINES = 25

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


def _copy_once(sentinel: str, timeout: float = COPY_TIMEOUT) -> str:
    """写哨兵 → 发 Ctrl+C → 等剪贴板变化。返回复制到的文本（失败为空串）。"""
    set_clipboard_text(sentinel)
    keyboard.send("ctrl+c")
    return _wait_for_clipboard_change(sentinel, timeout)


def _copy_selection(sentinel: str) -> str:
    """取"用户已经选中的内容"，失败重试一次。

    为什么要重试（2026-10-12 修「长文本只翻译最后一段」）：
    长文本写进剪贴板较慢，一次超时并不代表"没有选中"。而这里一旦被误判为
    "没选中"，后面就降级成只取本行 —— 用户看到的就是"只翻译了最后一段"。
    多等一次 + 多试一次，成本极低，却能消掉整类偶发失败。
    """
    for attempt in range(COPY_ATTEMPTS):
        text = _copy_once(sentinel)
        if text:
            return text
        if attempt + 1 < COPY_ATTEMPTS:
            time.sleep(0.08)          # 给目标程序一点喘息，再复制一次
    return ""


def _capture_paragraph(sentinel: str, max_chars: int) -> tuple[str, int]:
    """取「光标所在的整段」而不是只取本行。返回 (文本, 行数)。

    做法：先 `shift+home` 选到本行行首，再逐行 `shift+up` 向上扩，
    **每扩一行就复制一次，靠内容判断有没有越界**：
    若复制结果以空行开头，说明已经吃进了上一段/上一行，退一行收手。

    为什么不一次 `shift+up` 到底：
      - 只选本行   → 多行长文本只翻到最后一行（本次报障的现象）
      - 一次扩到底 → 会把上一段甚至整篇都吞进来（聊天窗里会误翻上一条消息）
    逐行探测是唯一能同时避开这两个坑的做法；主流场景（单行输入）
    只会多试一次就因 `wider == text` 立即退出，几乎不增加耗时。
    """
    keyboard.send("shift+home")
    time.sleep(0.04)
    text = _copy_once(sentinel)
    if not text:
        return "", 0

    lines = 1
    while lines < PARAGRAPH_MAX_LINES and len(text) < max_chars:
        keyboard.send("shift+up")
        time.sleep(0.04)
        wider = _copy_once(sentinel)
        if not wider or wider == text:
            break                     # 已到文档开头，或选区没法再扩
        if wider.startswith(("\n", "\r")):
            # 选中的第一行是空行 → 越过了段落边界，退一行保留上一次的结果
            keyboard.send("shift+down")
            time.sleep(0.03)
            break
        text = wider
        lines += 1
    return text, lines


def capture_selected_text(mode: str = "auto", max_chars: int = 2000) -> tuple[str, str]:
    """取回待翻译文本。

    参数:
        mode: auto（先试已有选中，再自动取整段）/ selection（只认已有选中）/
              line（总是只取光标到本行行首，适合"只翻这一句"）
        max_chars: 取词上限，超过视为误选（默认值见 config.py）

    返回:
        (文本, 模式说明)；文本为空串表示失败。
    """
    backup = get_clipboard_text()
    sentinel = f"__shunyi_{uuid.uuid4().hex}__"

    try:
        release_modifiers()

        # --- 第一步：用户已经选中了文字，Ctrl+C 就能拿到（最准，优先）---
        if mode in ("auto", "selection"):
            text = _copy_selection(sentinel)
            if text:
                return _post_process(text, "selection", max_chars, sentinel, backup)

        if mode == "selection":
            return "", "当前没有选中文字（当前模式只翻译选中的内容）"

        # --- 第二步：没选中就自动取「光标所在的整段」---
        # 旧实现这里只发 shift+home（只选本行），多行长文本于是只翻到最后一行。
        if mode == "line":
            keyboard.send("shift+home")
            time.sleep(0.05)
            text = _copy_once(sentinel)
            if not text:
                return "", "没能取到文字：当前窗口可能不支持自动取词，请先手动选中文字再按快捷键"
            return _post_process(text, "line", max_chars, sentinel, backup)

        text, lines = _capture_paragraph(sentinel, max_chars)
        if not text:
            return "", "没能取到文字：当前窗口可能不支持自动取词，请先手动选中文字再按快捷键"
        source = "paragraph" if lines > 1 else "line"
        return _post_process(text, source, max_chars, sentinel, backup)

    finally:
        # 无论成功失败，都恢复用户原来的剪贴板（哪怕原来是空的）
        set_clipboard_text(backup)


def _post_process(text: str, source: str, max_chars: int, sentinel: str, backup: str) -> tuple[str, str]:
    """取到文本后的处理：删除选中内容 + 长度校验。"""
    text = text.strip()
    if not text:
        return "", "取到的内容是空的"

    if len(text) > max_chars:
        # 误选中整篇文档的保护：保持原样不动（选区仍在），只提示。
        # 注意上限已从 2000 提到 20000（见 config.py）——
        # 2000 的老值会把"一个正常的长段落 / 一个 txt 文件"直接拒掉，
        # 表现就是「TXT 文件无法翻译」（2026-10-12 报障）。
        return "", (f"取到的内容太长（{len(text)} 字，超过上限 {max_chars}），已跳过以免误操作。"
                    f"如确需翻译这么长的内容，可在配置里调大 max_chars。")

    # 删掉刚被选中的原文，光标留在原位，等着接收译文
    keyboard.send("delete")
    time.sleep(0.03)
    label = {"selection": "已选中内容",
             "paragraph": "自动选中本段",
             "line": "自动选中本行"}.get(source, "自动取词")
    return text, label


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
