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
POLL_INTERVAL = 0.005

# 复制超时（**已知有内容**的复制：自动取段、回写校验等）。
# 原为 0.6s —— 实测长文本写进剪贴板可能要 1s 以上，一旦超时旧代码会
# **静默降级**成"只取本行"，用户看到的就是「长文本只翻译了最后一段」。
# 注意：这个 1.2s 不再用于"探测用户有没有选中"（那是下面的快速探测）。
COPY_TIMEOUT = 1.2
COPY_ATTEMPTS = 2

# 「用户是否已经选中文字」的快速探测（2026-10-16 提速，方案①）：
# 旧做法用 COPY_TIMEOUT(1.2s) 去"等剪贴板变化"，但「没选中」和「复制很慢」
# 在剪贴板层面是同一个现象 —— 只能等满超时才敢放弃。实测常态（打完字直接按
# 快捷键、没有选区）每次按键要白等 1.2 + 0.08 + 0.25 + 1.2 ≈ 2.73s 才开始选字。
# 现在改为短探测：配合剪贴板序列号（GetClipboardSequenceNumber，读取零成本）
# 判定"目标程序根本没往剪贴板写东西"，150ms 内无变化就立刻转自动取段。
# 真正有选区时复制通常在 10~60ms 内完成，因此不受影响；
# 个别超慢应用可用 config 的 selection_probe_ms 调大。
SELECTION_PROBE_TIMEOUT = 0.15
SELECTION_PROBE_ATTEMPTS = 1

# 重试前的额外等待（2026-10-14 游戏场景修复）：
# 热键是 Alt+J 时，回调触发瞬间用户的 Alt 往往还**物理按着**。
# 普通程序里 release_modifiers 注入的"抬起"事件能立刻清掉系统修饰键状态，
# 但游戏（尤其反作弊环境）常按帧轮询输入、甚至忽略注入的抬起事件——
# 结果紧跟着发出的 Ctrl+C 在游戏看来是 Ctrl+Alt+C，复制不生效。
# 第一次尝试保持快速（不拖累普通场景）；失败后重试前多等这一段，
# 给手指足够时间物理松开 Alt。
COPY_RETRY_SETTLE = 0.25

# 自动取词时最多向上扩多少行（只影响"整段"取词）。
# 逐行探测的兜底路径：每多扩一行要多一次"复制 + 等剪贴板"（约 60~70ms），
# 所以这个上限同时是"最坏情况下的额外延迟"：25 行 ≈ 1.7s。
# 2026-10-16 起正常路径不再走这里（改为一键 Ctrl+Shift+Up），
# 只有目标程序不认这个组合键时才退回逐行扩展。
PARAGRAPH_MAX_LINES = 25

# 一键扩到段首的组合键（2026-10-16）：
# Windows 的 Edit / RichEdit / 主流编辑器统一支持 「Ctrl+Shift+Up = 选中到本段开头」，
# 一次按键就能拿到整段，不必再逐行 Shift+Up 探测（N 次复制 → 1 次，省 0.6~1.7s）。
PARAGRAPH_EXTEND_KEY = "ctrl+shift+up"

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


def clipboard_seq() -> int:
    """剪贴板序列号：每次剪贴板内容被写入都会 +1。

    GetClipboardSequenceNumber 只读一个内核计数，**不需要打开剪贴板**，
    比读剪贴板内容（OpenClipboard/paste）便宜得多，且不受"剪贴板被别的
    程序短暂占用"影响 —— 用来判定"目标程序到底有没有往剪贴板写东西"。
    取不到时返回 0（调用方回退到纯超时判定，功能不受影响）。
    """
    try:
        return int(ctypes.windll.user32.GetClipboardSequenceNumber())
    except Exception:  # noqa: BLE001
        return 0


def _wait_for_clipboard_change(sentinel: str, timeout: float = COPY_TIMEOUT) -> str:
    """轮询剪贴板，直到内容不再是哨兵值，返回新内容（超时返回空串）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = get_clipboard_text()
        if current and current != sentinel:
            return current
        time.sleep(POLL_INTERVAL)
    # 超时说明 Ctrl+C 没被目标程序消费（游戏里常见：注入键被忽略/被当成组合键）。
    log.info("复制超时：Ctrl+C 后剪贴板没有变化（目标程序可能忽略了注入的按键）")
    return ""


def _copy_once(sentinel: str, timeout: float = COPY_TIMEOUT) -> str:
    """写哨兵 → 发 Ctrl+C → 等剪贴板变化。返回复制到的文本（失败为空串）。"""
    set_clipboard_text(sentinel)
    keyboard.send("ctrl+c")
    return _wait_for_clipboard_change(sentinel, timeout)


def _copy_selection(sentinel: str, timeout: float | None = None,
                    attempts: int | None = None) -> str:
    """取"用户已经选中的内容"——**快速探测**（默认 0.15s × 1 次，2026-10-16 方案①）。

    与旧实现的差别：旧版等 1.2s × 2 次（含重试等待合计约 2.7s），而"没有选中"
    恰恰是常态 —— 打完字直接按快捷键，每次都白等这么久才开始选字。
    现在：
      1. 记下剪贴板序列号（GetClipboardSequenceNumber，读取零成本）；
      2. 短等 SELECTION_PROBE_TIMEOUT，期间一旦有内容就立即返回；
      3. 超时且序列号**一动没动** → 判定"目标程序根本没往剪贴板写东西"
         = 用户没选中，立刻转自动取段，不再做第二次探测。

    精确性：真正有选区时 Ctrl+C 通常在 10~60ms 内完成复制，150ms 足够；
    个别超慢应用把 config.selection_probe_ms 调大即可恢复保守行为
    （≥600ms 时自动恢复"两次尝试 + 重试等待"的老策略）。
    """
    timeout = SELECTION_PROBE_TIMEOUT if timeout is None else timeout
    if attempts is None:
        attempts = COPY_ATTEMPTS if timeout >= 0.6 else SELECTION_PROBE_ATTEMPTS
    t0 = time.time()
    seq0 = clipboard_seq()
    for attempt in range(attempts):
        if attempt:
            time.sleep(COPY_RETRY_SETTLE)   # 等手指物理松开修饰键
        text = _copy_once(sentinel, timeout)
        if text:
            log.info("选区探测命中（%d 字，%.0fms）", len(text), (time.time() - t0) * 1000)
            return text
        if clipboard_seq() == seq0 and attempt + 1 >= attempts:
            break                           # 没有任何内容写进剪贴板 → 判定未选中
        if attempt + 1 < attempts:
            time.sleep(0.08)
    log.info("选区探测未命中（%.0fms，剪贴板序列号未变）→ 判定未选中，转自动取段",
             (time.time() - t0) * 1000)
    return ""


def _capture_paragraph(sentinel: str, max_chars: int) -> tuple[str, int]:
    """取「光标所在的整段」。返回 (文本, 行数)。

    主路径（2026-10-16 改）：`shift+home` 选到本行行首 → **一次
    `ctrl+shift+up` 直接扩到段首** → 复制一次收工。整段一次到手，
    不再逐行试探（N 次复制 → 2 次，多行长文本省 0.6~1.7 秒）。

    为什么保留"逐行扩展"（旧路径）：
      `ctrl+shift+up` 是 Windows 编辑控件的标准行为，但不是所有程序都实现
      （部分自绘控件、老终端、个别游戏聊天框）。若发现按了没扩动
      （复制结果与扩之前完全相同），就退回逐行 `shift+up` 的老办法。
      逐行形态同时是**越界探测**：每扩一行复制一次，若复制结果以空行开头，
      说明已吃进上一段，退一行收手 —— 保证不会把上一条消息/上一段吞进来。
    """
    keyboard.send("shift+home")
    time.sleep(0.04)
    text = _copy_once(sentinel)
    if not text:
        return "", 0

    # --- 主路径：一键扩到段首 ---
    keyboard.send(PARAGRAPH_EXTEND_KEY)
    time.sleep(0.05)
    wider = _copy_once(sentinel)
    if wider and wider != text:
        if not wider.startswith(("\n", "\r")):
            return wider, wider.count("\n") + 1
        # 越过段界（光标本就在段首，一键扩到了上一段）：保留本行结果更安全
        log.info("一键扩段越过了段落边界，保留本行结果")
        return text, 1

    # --- 兜底：目标程序不支持 Ctrl+Shift+Up，退回逐行扩展 ---
    log.info("Ctrl+Shift+Up 未生效（目标程序可能不支持），退回逐行扩展")
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


def capture_selected_text(mode: str = "auto", max_chars: int = 2000,
                          probe_ms: int | None = None) -> tuple[str, str]:
    """取回待翻译文本。

    参数:
        mode: auto（先试已有选中，再自动取整段）/ selection（只认已有选中）/
              line（总是只取光标到本行行首，适合"只翻这一句"）
        max_chars: 取词上限，超过视为误选（默认值见 config.py）
        probe_ms: 「用户是否已选中」的探测窗口（毫秒）。None = 用默认
                  SELECTION_PROBE_TIMEOUT(150ms)；由 service 从
                  config.selection_probe_ms 传入

    返回:
        (文本, 模式说明)；文本为空串表示失败。
    """
    backup = get_clipboard_text()
    sentinel = f"__shunyi_{uuid.uuid4().hex}__"
    probe_timeout = (probe_ms / 1000.0) if probe_ms else None

    try:
        release_modifiers()
        t_start = time.time()
        log.info("开始取词（mode=%s, probe=%s）", mode,
                 f"{probe_ms}ms" if probe_ms else "默认")

        # --- 第一步：用户已经选中了文字，Ctrl+C 就能拿到（最准，优先）---
        # 注意这一步是**短探测**（默认 150ms）：没选中是常态，
        # 不能为了等"很慢的复制"把每次按键都拖慢 2.7 秒。
        if mode in ("auto", "selection"):
            text = _copy_selection(sentinel, timeout=probe_timeout)
            if text:
                return _post_process(text, "selection", max_chars, sentinel, backup)

        if mode == "selection":
            return "", "当前没有选中文字（当前模式只翻译选中的内容）"

        # --- 第二步：没选中就自动取「光标所在的整段」---
        # 旧实现这里只发 shift+home（只选本行），多行长文本于是只翻到最后一行。
        if mode == "auto":
            log.info("用户未选中（或复制失败），降级为自动选中整段")
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
        log.info("取词完成：%.0fms（%s, %d 行）", (time.time() - t_start) * 1000, source, lines)
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
    log.info("已发送 Delete 删除原文，取词环节完成（source=%s, %d 字）", source, len(text))
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
