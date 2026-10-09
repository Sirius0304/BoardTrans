# -*- coding: utf-8 -*-
"""键译 - 统一通知入口（唯一出口）

设计约定（2026-09-28 修复"通知开关形同虚设"缺陷后确立，改动前请先读）：

1. 所有面向用户的提醒都必须调用本模块的 `notify()`；
2. 「通知提醒」开关（配置项 `notify_enabled`）**只在本模块内判定一次**，
   任何调用点都不许自己再判断开关 —— 否则必然出现"有的提醒受控、有的不受控"；
3. 真正的发送方式由外部注入（托盘模式注入托盘气泡，控制台模式回退为打印），
   注入的只是"怎么发"，不参与"要不要发"的决策。

修复前的缺陷（供日后排查对照）：设置里的开关实际语义是"下载完成用弹窗还是用气泡"，
每个调用点直接用 `service._notify`（托盘在启动时把它替换成裸的气泡发送函数）
自行决定怎么弹，于是关掉开关后，气泡提醒照旧弹出，开关看起来形同虚设。
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from . import config
from .logger import get_logger

log = get_logger()

# 同一事件（标题 + 内容完全相同）在这个秒数内只发一次，避免重复打扰
DEDUP_WINDOW = 3.0
# 去重表的上限保护，避免程序长期运行后无限增长
_DEDUP_MAX = 200

_lock = threading.Lock()
_sink: Callable[[str, str], None] | None = None
_recent: dict[tuple[str, str], float] = {}


def set_sink(fn: Callable[[str, str], None] | None) -> None:
    """注入"实际怎么发"的实现（托盘气泡）。由托盘在启动时调用一次。"""
    global _sink
    with _lock:
        _sink = fn


def is_enabled() -> bool:
    """「通知提醒」开关的当前状态。

    用 config.load_if_changed()：如果配置文件被别的实例或手工编辑改过，
    这里会立刻读到新值（不再拿启动时的旧快照），所以：
      - 在设置窗口切换开关后立即生效（无需重启）；
      - 多实例同时运行、或用户手改 config.json 时同样立即生效。
    """
    return bool(config.load_if_changed().get("notify_enabled", True))


def notify(title: str, message: str = "") -> bool:
    """唯一的通知出口。返回 True 表示确实发出去了。

    开关判定、去重判定都在这里，调用点只管调用。
    """
    if not is_enabled():
        log.info("「通知提醒」已关闭，跳过提醒：%s %s", title, message)
        return False

    key = (title, message)
    now = time.time()
    with _lock:
        last = _recent.get(key)
        if last is not None and now - last < DEDUP_WINDOW:
            log.info("同一提醒 %.1f 秒内已发过，跳过：%s", DEDUP_WINDOW, title)
            return False
        _recent[key] = now
        if len(_recent) > _DEDUP_MAX:
            for k, t in list(_recent.items()):
                if now - t > DEDUP_WINDOW * 10:
                    _recent.pop(k, None)
        sink = _sink

    try:
        if sink is not None:
            sink(title, message)
        else:
            _fallback(title, message)
    except Exception:  # noqa: BLE001
        log.exception("通知发送失败（已忽略）：%s", title)
        return False
    return True


def _fallback(title: str, message: str) -> None:
    """没有托盘时的兜底：只打印到控制台，不弹任何 UI（控制台版走这条路）。"""
    from .console import safe_print

    safe_print(f"[{title}] {message}" if message else f"[{title}]")


def reset_dedup() -> None:
    """清空去重记录（测试用）。"""
    with _lock:
        _recent.clear()
