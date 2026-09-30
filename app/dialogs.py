# -*- coding: utf-8 -*-
"""瞬译 - 统一风格的对话框（替代 tkinter.messagebox）

为什么要自己画（2026-09-30）：
  原生 messagebox 是**系统样式**（直角、系统配色、可能宋体），与设置窗口的自绘视觉
  （微软雅黑、圆角卡片、#F7F8FA 页面底、蓝色主按钮）完全割裂，同一个软件里两种观感。
  本模块用 ui_kit 的**同一套令牌与控件**重画，保证配色、字体、圆角、间距、按钮样式一致。

用法（必须在**创建窗口的那个线程**里调用，tkinter 非线程安全）：
    from app import dialogs
    ok = dialogs.ask(self.root, "删除模型", "...", tone="err", ok_text="删除")
    dialogs.tell(self.root, "删除完成", "...", tone="ok")

其它线程（例如托盘线程）要弹框，请走 SettingsWindow.run_in_ui() 回到窗口线程。
"""

from __future__ import annotations

import tkinter as tk

from . import ui_kit as ui

# 说明文字折行宽度（像素，随 DPI 缩放）
_TEXT_WRAP = 380


def _accent_color(tone: str) -> str:
    """语义色 → 左侧强调条颜色。"""
    _bg, _border, fg = ui.TONES.get(tone, ui.TONES["info"])
    return fg


def _center_on_parent(top: tk.Toplevel, master) -> None:
    """把对话框摆到父窗口正中（父窗口还没测量出来时就退回屏幕居中）。"""
    try:
        top.update_idletasks()
        w, h = top.winfo_reqwidth(), top.winfo_reqheight()
        try:
            px, py = master.winfo_rootx(), master.winfo_rooty()
            pw, ph = master.winfo_width(), master.winfo_height()
            if pw < 50 or ph < 50:
                raise ValueError("父窗口尺寸不可用")
        except Exception:  # noqa: BLE001
            px, py = 0, 0
            pw, ph = top.winfo_screenwidth(), top.winfo_screenheight()
        x = max(0, px + (pw - w) // 2)
        y = max(0, py + (ph - h) // 3)
        top.geometry(f"+{x}+{y}")
    except Exception:  # noqa: BLE001
        pass


def _build(master, title: str, message: str, buttons, tone: str, default: str):
    """构建对话框并返回 (Toplevel, 结果字典, 关闭函数)。"""
    top = tk.Toplevel(master)
    top.title(title)
    top.configure(bg=ui.COLOR_PAGE)
    top.resizable(False, False)
    try:
        top.transient(master.winfo_toplevel())
    except Exception:  # noqa: BLE001
        pass

    result: dict = {"value": None}

    def _close(value: str) -> None:
        result["value"] = value
        try:
            top.grab_release()
        except Exception:  # noqa: BLE001
            pass
        top.destroy()

    # 白卡片：与设置窗口里的卡片完全同一套（圆角、1px 描边、轻阴影、内边距随 DPI）。
    # fit_width=True：对话框没有父窗口撑宽，卡片必须按内容自己请求宽度
    card = ui.RoundedCard(top, pad=ui.S(16), shadow=True, fit_width=True)
    card.pack(fill="both", expand=True, padx=ui.S(8), pady=ui.S(8))
    body = card.body

    # 左侧语义色强调条 + 右侧文字（配色取自 TONES，与界面提示框一致）
    row = tk.Frame(body, bg=ui.COLOR_CARD)
    row.pack(fill="both", expand=True)
    tk.Frame(row, bg=_accent_color(tone), width=ui.S(4)).pack(side="left", fill="y",
                                                              padx=(0, ui.S(12)))
    text_box = tk.Frame(row, bg=ui.COLOR_CARD)
    text_box.pack(side="left", fill="both", expand=True)

    ui.TextLabel(text_box, text=title, bg=ui.COLOR_CARD, fg=ui.COLOR_TEXT,
             font=ui.UI_FONT_BOLD, anchor="w", justify="left").pack(fill="x")
    ui.TextLabel(text_box, text=message, bg=ui.COLOR_CARD, fg=ui.COLOR_SUBTEXT,
             font=ui.UI_FONT, anchor="w", justify="left",
             wraplength=ui.S(_TEXT_WRAP)).pack(fill="x", pady=(ui.S(6), 0))

    bar = tk.Frame(body, bg=ui.COLOR_CARD)
    bar.pack(fill="x", pady=(ui.S(14), 0))
    made = {}
    for spec in buttons:
        btn = ui.RoundedButton(bar, text=spec["text"], kind=spec.get("kind", "secondary"),
                               height=30, padx=16, page_bg=ui.COLOR_CARD,
                               command=lambda v=spec["value"]: _close(v))
        btn.pack(side="right", padx=(ui.S(8), 0))
        made[spec["value"]] = btn
    if made:
        try:
            made.get(default, next(iter(made.values()))).focus_set()
        except Exception:  # noqa: BLE001
            pass

    # 键盘习惯：Esc = 取消/关闭，Enter = 默认按钮
    top.bind("<Escape>", lambda _e: _close("cancel"))
    top.bind("<Return>", lambda _e: _close(default))
    top.protocol("WM_DELETE_WINDOW", lambda: _close("cancel"))
    return top, result, _close


def _run(master, title, message, buttons, tone, default) -> str:
    top, result, _close = _build(master, title, message, buttons, tone, default)
    try:
        top.grab_set()                 # 模态：必须先处理这个框
    except Exception:  # noqa: BLE001
        pass
    _center_on_parent(top, master)
    try:
        top.wait_window(top)
    except Exception:  # noqa: BLE001
        pass
    finally:
        if top.winfo_exists():
            top.destroy()
    return result["value"] or "cancel"


def ask(master, title: str, message: str, ok_text: str = "确定",
        cancel_text: str = "取消", tone: str = "info", danger: bool = False) -> bool:
    """确认框（模态）：确定返回 True，取消/关闭返回 False。

    danger=True 时确定按钮用红色（删除这类不可逆操作）。
    """
    value = _run(master, title, message,
                 [{"text": cancel_text, "value": "cancel", "kind": "secondary"},
                  {"text": ok_text, "value": "ok",
                   "kind": "danger" if danger else "primary"}],
                 tone, "ok")
    return value == "ok"


def tell(master, title: str, message: str, ok_text: str = "知道了",
         tone: str = "info") -> None:
    """提示框（模态，只有一个按钮）：用于必须让用户看到的失败/结果说明。"""
    _run(master, title, message,
         [{"text": ok_text, "value": "ok", "kind": "primary"}], tone, "ok")
