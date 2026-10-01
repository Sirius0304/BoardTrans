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

import ctypes
import os
import tkinter as tk

from . import ui_kit as ui

# 说明文字折行宽度（像素，随 DPI 缩放）
_TEXT_WRAP = 380

# 窗口样式相关常量（隐藏标题栏关闭按钮时用）
_GWL_STYLE = -16
_WS_SYSMENU = 0x00080000
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOZORDER, _SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x20


def _hide_close_button(top: tk.Toplevel) -> None:
    """去掉标题栏右上角的关闭按钮（标题栏与其余布局保持不变）。

    做法：清掉窗口样式里的 `WS_SYSMENU`（系统菜单）—— Windows 会把"×"连同
    标题栏左键菜单一起去掉，标题文字和标题栏本身都还在，所以**布局一点不变**。

    为什么不 `overrideredirect(True)`：那会把整个标题栏干掉，窗口位置/层级/外观
    全变，不符合"样式布局不变"。
    """
    if os.name != "nt":
        return
    try:
        top.update_idletasks()
        hwnd = int(top.frame(), 16)          # wm_frame() 给的是顶层窗口句柄（十六进制串）
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, _GWL_STYLE)
        if not style:
            return
        user32.SetWindowLongW(hwnd, _GWL_STYLE, style & ~_WS_SYSMENU)
        # 通知系统按新样式重画非客户区，否则"×"要等下一次尺寸变化才消失
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                            _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOZORDER | _SWP_FRAMECHANGED)
    except Exception:  # noqa: BLE001
        pass


def _accent_color(tone: str) -> str:
    """语义色 → 左侧强调条颜色。"""
    _bg, _border, fg = ui.TONES.get(tone, ui.TONES["info"])
    return fg


def _center_on_parent(top: tk.Toplevel, master) -> None:
    """把对话框摆到父窗口正中（父窗口测不到时退回屏幕居中）。

    三个坑（2026-10-01 一次性修掉，之前弹窗会明显偏出中心）：
      1) 卡片是 Canvas 自绘、高度靠 `<Configure>` 自适应，尺寸要一两轮事件循环才收敛
         —— 按中间值（实测 26×56）算位置必然歪，所以尺寸没撑开前先不动手；
      2) `<Configure>` 由窗口管理器**异步**派发，得绑事件等它来，而不是"跑几轮 update"；
      3) Tk 的 `wm geometry` 位置指的是**窗口框架**（含标题栏/边框）左上角，而
         `winfo_rootx/rooty` 返回**客户区**坐标，两者差一个边框 —— 实测横向 8px、纵向 31px。
         关键：这个差值 = `winfo_rootx − winfo_x`，**与窗口当前位置无关**，
         量一次直接补偿即可（不要拿"目标位置"去反复迭代，那会来回漂移）。
    """
    state = {"done": False}

    def _place(*_event) -> None:
        if state["done"]:
            return
        try:
            w, h = top.winfo_reqwidth(), top.winfo_reqheight()
            if w < 40 or h < 40:
                return                          # 卡片还没撑开，等下一轮
            try:
                px, py = master.winfo_rootx(), master.winfo_rooty()
                pw, ph = master.winfo_width(), master.winfo_height()
                if pw < 50 or ph < 50:
                    raise ValueError("父窗口尺寸不可用")
            except Exception:  # noqa: BLE001
                px, py = 0, 0
                pw, ph = top.winfo_screenwidth(), top.winfo_screenheight()
            # 想让"客户区中心"落在父窗口中心
            want_x = max(0, px + (pw - w) // 2)
            want_y = max(0, py + (ph - h) // 2)
            off_x = top.winfo_rootx() - top.winfo_x()
            off_y = top.winfo_rooty() - top.winfo_y()
            top.geometry(f"+{max(0, want_x - off_x)}+{max(0, want_y - off_y)}")
            state["done"] = True
        except Exception:  # noqa: BLE001
            pass

    try:
        top.update_idletasks()
        _place()
        top.bind("<Configure>", _place, add="+")
        top.after(200, _place)          # 兜底：万一 <Configure> 没来（例如尺寸恰好没变）
    except Exception:  # noqa: BLE001
        pass


def _build(master, title: str, message: str, buttons, tone: str, default: str,
           closable: bool = True):
    """构建对话框并返回 (Toplevel, 结果字典, 关闭函数)。

    closable=False：去掉标题栏右上角的关闭按钮，只留「确认 / 取消」两个操作
    （用于需要用户明确表态的确认框，避免"点 × 就悄悄取消"）。
    """
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
    # message 为空时不占位：用于"只有一个明确表态"的简洁确认框
    # （例如「恢复默认设置」—— 标题+两个按钮已经说清楚了，再堆一段说明反而啰嗦）。
    if message:
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
    if not closable:
        _hide_close_button(top)
    return top, result, _close


def _run(master, title, message, buttons, tone, default, closable: bool = True) -> str:
    top, result, _close = _build(master, title, message, buttons, tone, default, closable)
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
        cancel_text: str = "取消", tone: str = "info", danger: bool = False,
        closable: bool = True) -> bool:
    """确认框（模态）：确定返回 True，取消/关闭返回 False。

    danger=True 时确定按钮用红色（删除这类不可逆操作）。
    closable=False 时不显示标题栏的关闭按钮，只留两个操作按钮。
    """
    value = _run(master, title, message,
                 [{"text": cancel_text, "value": "cancel", "kind": "secondary"},
                  {"text": ok_text, "value": "ok",
                   "kind": "danger" if danger else "primary"}],
                 tone, "ok", closable)
    return value == "ok"


def tell(master, title: str, message: str, ok_text: str = "知道了",
         tone: str = "info", closable: bool = True) -> None:
    """提示框（模态，只有一个按钮）：用于必须让用户看到的失败/结果说明。"""
    _run(master, title, message,
         [{"text": ok_text, "value": "ok", "kind": "primary"}], tone, "ok", closable)
