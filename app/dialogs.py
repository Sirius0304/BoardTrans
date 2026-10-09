# -*- coding: utf-8 -*-
"""键译 - 统一风格的对话框（替代 tkinter.messagebox）

为什么要自己画（2026-09-30）：
  原生 messagebox 是**系统样式**（直角、系统配色、可能宋体），与设置窗口的自绘视觉
  （微软雅黑、圆角卡片、#F7F8FA 页面底、蓝色主按钮）完全割裂，同一个软件里两种观感。
  本模块用 ui_kit 的**同一套令牌与控件**重画，保证配色、字体、圆角、间距、按钮样式一致。

用法（必须在**创建窗口的那个线程**里调用，tkinter 非线程安全）：
    from app import dialogs
    ok = dialogs.ask(self.root, "删除模型", "...", tone="err", ok_text="删除")
    dialogs.tell(self.root, "正在下载中", "...", tone="info")

其它线程（例如托盘线程）要弹框，请走 SettingsWindow.run_in_ui() 回到窗口线程。
"""

from __future__ import annotations

import ctypes
import os
import tkinter as tk

from . import ui_kit as ui

# 说明文字折行宽度（像素，随 DPI 缩放）
_TEXT_WRAP = 380

# 卡片相对窗口的外边距（_build 里 card.pack 的 padx/pady），定位时要加回去
_CARD_PAD = 8

# 窗口样式相关常量（隐藏标题栏关闭按钮时用）
_GWL_STYLE = -16
_WS_SYSMENU = 0x00080000
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOZORDER, _SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x20


def _hide_close_button(top: tk.Toplevel) -> None:
    """去掉标题栏右上角的关闭按钮（标题栏与其余布局保持不变）。

    做法：清掉窗口样式里的 `WS_SYSMENU`（系统菜单）—— Windows 会把"×"连同
    标题栏左键菜单一起去掉，标题文字和标题栏本身都还在，所以**布局一点不变**。

    为什么不 `overrideredirect(True)`：那会把整个标题栏干掉、窗口位置/层级/外观
    全变，不符合"样式布局不变"。

    必须在窗口**映射之后**调用：撤隐状态下改非客户区样式不保证生效（实测）。
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


def _card_of(top):
    """取出弹窗里的圆角卡片（弹窗的自然尺寸得靠它量）。找不到返回 None。"""
    try:
        for child in top.winfo_children():
            if isinstance(child, ui.RoundedCard):
                return child
    except Exception:  # noqa: BLE001
        pass
    return None


def _center_on_parent(top: tk.Toplevel, master, frameless: bool = False) -> None:
    """在弹窗**仍处于撤隐状态**时把尺寸和位置一次算准，然后才让它露面。

    完整时序（2026-10-03 修"先在左上角闪一下、再跳到中间"，前后试过两版才根治）：

      1. `_build` 创建后**立刻 `withdraw()`** —— 弹窗整个成型期都不在屏幕上；
      2. 这里在撤隐状态下量准卡片的自然尺寸（`fit_now()` 直接驱动 `_fit_height`，
         不用干等 `after(60)` 的定时自检）；
      3. `geometry("WxH+X+Y")` 把**尺寸和位置一次写对**；
      4. 最后 `deiconify()` —— 用户看到的第一帧就已经在正中央，全程无闪烁、无位移。

    为什么不能"先显示再挪"（前两版失败的共同原因）：
      `tk.Toplevel` 一创建就被 Windows 映射在 `+0+0`（屏幕左上角）、尺寸只有 26×56。
      而窗口合成由 **dwm.exe（独立进程）** 完成 —— 窗口一被显示就可能已经被合成出那一帧，
      Python 层事后设 `attributes("-alpha", 0)` 来不及（实测设了照样闪）。
      **只有"从未被映射"才真正不会被画出来。**

    撤隐期能量准尺寸吗？能：卡片高度由 `_CanvasBox._fit_height` 计算，只依赖内容的
    请求尺寸，与窗口是否映射无关。实测撤隐状态下量到 418×170，加上 2×8 外边距正好
    434×186，与映射后的真实尺寸一致。

    位置换算要补偿边框：`wm geometry` 用**窗口框架**坐标、`winfo_rootx/rooty` 是
    **客户区**坐标，两者差一个边框（实测横向 8px、纵向 31px）。撤隐时量不到自己的偏移，
    就借父窗口的偏移来用（同为标准边框窗口，实测两者一致）。
    **不要拿"目标位置"去反复迭代，会来回漂移。**
    """
    try:
        # 先让内容层的几何传播跑一遍：撤隐状态下 inner 等 Frame 的请求尺寸要等
        # update_idletasks 之后才是真值（实测不跑就是 1×1）
        top.update_idletasks()
        card = _card_of(top)
        if card is not None:
            cw, ch = card.fit_now()
            w, h = cw + _CARD_PAD * 2, ch + _CARD_PAD * 2
        else:                                   # 兜底：拿不到卡片就用窗口自身请求尺寸
            w = max(top.winfo_reqwidth(), 40)
            h = max(top.winfo_reqheight(), 40)
        # 尺寸不合理（内容还没算出来）就再推一次布局，最多 3 轮
        tries = 0
        while (w < 60 or h < 40) and card is not None and tries < 3:
            top.update_idletasks()
            cw, ch = card.fit_now()
            w, h = cw + _CARD_PAD * 2, ch + _CARD_PAD * 2
            tries += 1
        if w < 60 or h < 40:                    # 实在量不准也别给一个 1×1 的窗口
            w, h = 440, 200
        try:
            px, py = master.winfo_rootx(), master.winfo_rooty()
            pw, ph = master.winfo_width(), master.winfo_height()
            if pw < 50 or ph < 50:
                raise ValueError("父窗口尺寸不可用")
        except Exception:  # noqa: BLE001
            px = py = 0
            pw, ph = top.winfo_screenwidth(), top.winfo_screenheight()
        if not frameless:
            # 只有**带标题栏**的窗口才需要扣掉边框：`wm geometry` 定位的是窗口框架
            # 左上角，而我们要对齐的是客户区中心。撤隐时量不到自己的偏移，借父窗口的
            # （同为标准边框窗口，实测一致）。无边框弹窗框架=客户区，扣了反而偏 8/31px。
            try:
                off_x = master.winfo_rootx() - master.winfo_x()
                off_y = master.winfo_rooty() - master.winfo_y()
            except Exception:  # noqa: BLE001
                off_x = off_y = 0
        else:
            off_x = off_y = 0
        want_x = px + (pw - w) // 2 - off_x
        want_y = py + (ph - h) // 2 - off_y
        # 尺寸和位置一次写对：之后不再有任何 geometry 调用，避免二次跳动
        top.geometry(f"{w}x{h}+{max(0, want_x)}+{max(0, want_y)}")
    except Exception:  # noqa: BLE001
        try:
            top.geometry("+0+0")
        except Exception:  # noqa: BLE001
            pass
    try:
        top.deiconify()                        # 唯一一次露面
        top.update_idletasks()
        if frameless:
            # 无边框窗口不会由系统自动给焦点，不给的话 Esc / Enter 都收不到。
            # deiconify 之后焦点还没落到它身上，所以要显式 lift + focus_force。
            top.lift()
            top.focus_force()
    except Exception:  # noqa: BLE001
        pass


def _build(master, title: str, message: str, buttons, tone: str, default: str,
           closable: bool = True, frameless: bool = False):
    """构建对话框并返回 (Toplevel, 结果字典, 关闭函数)。

    注意：返回时窗口仍是**撤隐**状态、且已经定好尺寸与位置，调用方必须接着调
    `_center_on_parent()` 才会真正露面（`_run` 已经这么做了）。

    tone：历史上决定左侧强调条颜色；2026-10-05 强调条随标题一起移除后，
          该参数不再影响样式（保留是为了不破坏调用方与测试的签名），
          按钮语义色由 buttons 里的 kind 直接指定。

    closable=False：去掉标题栏右上角的关闭按钮（标题栏仍在）。
    frameless=True：**连整个标题栏都不要**（`overrideredirect`），弹窗就是那张圆角卡片
                    —— 用于"一句话 + 两个按钮"的极简确认框（2026-10-01 按参考样式）。
                    `overrideredirect` 必须在窗口**映射之前**设置。
    """
    top = tk.Toplevel(master)
    top.title(title)
    # 关键（2026-10-03）：**创建后立刻撤隐**，弹窗的整个"成型期"都不在屏幕上。
    #
    # 为什么非撤隐不可：`tk.Toplevel` 一创建就被 Windows 映射在 `+0+0`（屏幕左上角）、
    # 尺寸只有 26×56，等卡片撑开、位置算准才挪到中间 —— 中间那一帧会被用户看到。
    # 第一版想用 `attributes("-alpha", 0.0)` 挡，**实测无效**：窗口合成在 dwm.exe 里进行，
    # 是独立进程，窗口一被显示就可能已经合成出那一帧，Python 层事后设 alpha 来不及。
    # 只有"从未被映射"才真正不会被画出来。
    try:
        top.withdraw()
    except Exception:  # noqa: BLE001
        pass
    if frameless:
        try:
            # overrideredirect 必须在窗口**映射之前**设置，否则不生效。
            top.overrideredirect(True)
        except Exception:  # noqa: BLE001
            pass
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
    card.pack(fill="both", expand=True, padx=ui.S(_CARD_PAD), pady=ui.S(_CARD_PAD))
    body = card.body

    # 2026-10-05 按主人要求改版：**去掉卡片内的加粗标题与左侧语义色竖线**。
    # 有说明文字时「灰字 + 按钮」就是全部内容 —— 标题与窗口标题栏重复
    # （标题栏上本来就有「删除模型」），再排一遍只是占地方。
    # 特例：message 为空时（如「恢复默认设置」的极简确认框），标题是唯一内容，仍显示。
    # tone 参数保留：它还决定按钮语义色（danger 红 / primary 蓝）。
    # 原布局的左内边距由竖线提供（4px 竖线 + 12px 间距），删掉后文字直接从
    # 卡片内边距（16px）起排，与下方按钮行天然对齐，无空白残留。
    if message:
        ui.TextLabel(body, text=message, bg=ui.COLOR_CARD, fg=ui.COLOR_SUBTEXT,
                     font=ui.UI_FONT, anchor="w", justify="left",
                     wraplength=ui.S(_TEXT_WRAP)).pack(fill="x")
    else:
        ui.TextLabel(body, text=title, bg=ui.COLOR_CARD, fg=ui.COLOR_TEXT,
                     font=ui.UI_FONT_BOLD, anchor="w", justify="left").pack(fill="x")

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


def _run(master, title, message, buttons, tone, default,
         closable: bool = True, frameless: bool = False) -> str:
    top, result, _close = _build(master, title, message, buttons, tone, default,
                                 closable, frameless)
    # 顺序要紧：先定好尺寸位置并露面，再抓模态 —— 撤隐的窗口 grab 不生效
    _center_on_parent(top, master, frameless)
    if not frameless and not closable:
        _hide_close_button(top)      # 映射之后再清 WS_SYSMENU（撤隐时改样式不保证生效）
    try:
        top.grab_set()               # 模态：必须先处理这个框
    except Exception:  # noqa: BLE001
        pass
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
        closable: bool = True, frameless: bool = False) -> bool:
    """确认框（模态）：确定返回 True，取消/关闭返回 False。

    danger=True 时确定按钮用红色（删除这类不可逆操作）。
    closable=False 时不显示标题栏的关闭按钮；frameless=True 时连标题栏都不要。
    """
    value = _run(master, title, message,
                 [{"text": cancel_text, "value": "cancel", "kind": "secondary"},
                  {"text": ok_text, "value": "ok",
                   "kind": "danger" if danger else "primary"}],
                 tone, "ok", closable, frameless)
    return value == "ok"


def tell(master, title: str, message: str, ok_text: str = "知道了",
         tone: str = "info", closable: bool = True, frameless: bool = False) -> None:
    """提示框（模态，只有一个按钮）：用于必须让用户看到的失败/结果说明。"""
    _run(master, title, message,
         [{"text": ok_text, "value": "ok", "kind": "primary"}], tone, "ok",
         closable, frameless)
