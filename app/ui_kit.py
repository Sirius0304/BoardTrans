# -*- coding: utf-8 -*-
"""瞬译 - 设置窗口的界面套件（视觉组件）

为什么单独一个模块：Tk 原生控件是"方角 + 系统观感"，要做到参考图那种
「近白页面底 + 白卡片 + 1px 浅灰描边 + 圆角 + 蓝色主按钮 + iOS 风格开关」
的现代扁平风格，只能自己画。这里全部用**纯 Tk Canvas** 实现 ——
不依赖 Pillow（打包后同样可用），也不引入第三方库。

对外提供：
    设计令牌            PAGE/CARD/BORDER/TEXT/PRIMARY …
    draw_round_rect()   圆角矩形（smooth 多边形，纯 Tk）
    RoundedPanel        圆角面板（可当提示框 / 胶囊标签 / 提示条用）
    RoundedCard         圆角卡片容器（内容仍用 pack 布局，接口与原 _card 一致）
    RoundedButton       圆角按钮（normal/hover/pressed/disabled 四态）
    SegmentGroup        分段选择器（选中项蓝底白字，对应参考图的「规则/全局/直连」）
    ToggleSwitch        iOS 风格开关（对应参考图的「系统代理」开关）
    IconBadge           图标徽标（浅色圆角底 + 线条图标，对应参考图卡片标题左侧）
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

# --------------------------------------------------------------- 设计令牌
# 参考图（Clash Verge 风格）：近白页面 + 白卡片 + 浅灰描边 + 蓝色主色 + 语义色
COLOR_PAGE = "#F7F8FA"          # 页面底色
COLOR_CARD = "#FFFFFF"          # 卡片 / 面板底
COLOR_BORDER = "#E5E7EB"        # 1px 描边
COLOR_BORDER_STRONG = "#D8DCE3"  # 输入框等需要更清楚的描边
COLOR_TEXT = "#1F2937"          # 主文字
COLOR_SUBTEXT = "#6B7280"       # 次要文字
COLOR_MUTED = "#9CA3AF"         # 更弱的文字（禁用）
COLOR_HOVER = "#F3F4F6"         # 悬停底色（浅灰）

COLOR_PRIMARY = "#3B82F6"       # 主色（蓝）
COLOR_PRIMARY_HOVER = "#2563EB"
COLOR_PRIMARY_PRESSED = "#1D4ED8"
COLOR_PRIMARY_SOFT = "#EFF6FF"  # 主色浅底（提示框 / 图标徽标底）
COLOR_PRIMARY_SOFT_BORDER = "#BFDBFE"
COLOR_PRIMARY_TEXT = "#1D4ED8"  # 浅底上的文字色

COLOR_OK = "#16A34A"
COLOR_OK_SOFT = "#ECFDF5"
COLOR_OK_BORDER = "#BBF7D0"
COLOR_OK_TEXT = "#15803D"

COLOR_WARN_BG = "#FFF7ED"
COLOR_WARN_FG = "#C2410C"
COLOR_WARN_BORDER = "#FED7AA"

COLOR_ERR = "#DC2626"
COLOR_ERR_SOFT = "#FEF2F2"
COLOR_ERR_BORDER = "#FECACA"
COLOR_ERR_TEXT = "#B91C1C"

# 圆角与间距（紧凑版：卡片 10px、控件 8px、卡片内边距 12px）
RADIUS_CARD = 10
RADIUS_CTRL = 8
PAD_CARD = 12
GAP_SECTION = 8

# 字体（黑体，见 settings_window 的字体探测）
UI_FONT = "ShunYiBase"
UI_FONT_BOLD = "ShunYiBold"
UI_FONT_SMALL = "ShunYiSmall"
UI_FONT_MONO = "ShunYiMono"

# 语义色 → （底色, 描边, 文字色）
TONES = {
    "info": (COLOR_PRIMARY_SOFT, COLOR_PRIMARY_SOFT_BORDER, COLOR_PRIMARY_TEXT),
    "ok": (COLOR_OK_SOFT, COLOR_OK_BORDER, COLOR_OK_TEXT),
    "warn": (COLOR_WARN_BG, COLOR_WARN_BORDER, COLOR_WARN_FG),
    "err": (COLOR_ERR_SOFT, COLOR_ERR_BORDER, COLOR_ERR_TEXT),
    "plain": (COLOR_HOVER, COLOR_BORDER, COLOR_TEXT),
}


def draw_round_rect(cv: tk.Canvas, x1, y1, x2, y2, r, fill, outline="", width=1):
    """在 Canvas 上画圆角矩形。

    实现：用 smooth=True 的多边形（每个角重复一个控制点）逼近圆角 ——
    纯 Tk 即可，不需要 Pillow 生成图片，因此在打包后的 exe 里同样可用。
    """
    r = max(1, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return cv.create_polygon(pts, smooth=True, splinesteps=12, fill=fill,
                             outline=outline, width=width)


class _CanvasBox(tk.Frame):
    """内部基类：一个铺满自身的 Canvas + 一个叠加在其上的内容 Frame。

    内容 Frame 会被内缩 pad 像素（这样它的直角边不会盖住画布上的圆角，
    圆角和描边就能露出来）。子类只需重画 _redraw()。
    """

    def __init__(self, parent, fill, border="", radius=RADIUS_CTRL, pad=8,
                 page_bg=COLOR_PAGE, shadow=False, fit_width=False):
        super().__init__(parent, bg=page_bg)
        self._fill = fill
        self._border = border
        self._radius = radius
        self._pad = pad
        self._page_bg = page_bg
        self._shadow = shadow
        # fit_width=True：宽度按内容收缩（胶囊标签、输入框外壳、浮动提醒小框用）；
        # False：宽度交给 pack/place 决定（卡片、提示框、底部提示条是通栏的）。
        # 注意 Canvas 默认请求宽度是 378px，不显式指定会把"应该很窄"的元素撑开。
        self._fit_width = fit_width
        self.canvas = tk.Canvas(self, bg=page_bg, highlightthickness=0, bd=0,
                                width=(10 if fit_width else 1), height=40)
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=fill)
        self._win = self.canvas.create_window(pad, pad, window=self.inner, anchor="nw")
        self._shapes: list = []
        self.canvas.bind("<Configure>", self._on_configure)
        self.inner.bind("<Configure>", lambda _e: self._fit_height())
        # 轻量自检（每 120ms 比一次高度）：
        # 为什么需要它：Canvas 的窗口项只要完全落在画布可视区之外就不会被映射，
        # 也就不会触发 <Configure> —— 若初始高度太小（内容在下面）就会"卡住长不高"，
        # 卡片整块不可见。这里靠定时自检把高度校准到内容真实高度，
        # 顺带也能适应动态内容（下载时出现的进度条、变长的提示文字等）。
        # 每个面板只是一次整数比较，开销可忽略。
        self.after(60, self._watch)

    def _watch(self) -> None:
        try:
            if not self.winfo_exists():
                return
            self._fit_height()
            self.after(120, self._watch)
        except Exception:  # noqa: BLE001
            pass

    # ---- 几何
    def _on_configure(self, event) -> None:
        self.canvas.itemconfigure(self._win, width=max(1, event.width - 2 * self._pad))
        self._redraw(event.width, event.height)

    def _fit_height(self) -> None:
        want = self.inner.winfo_reqheight() + 2 * self._pad
        if want < 1:
            return
        if self.canvas.winfo_reqheight() != want:
            self.canvas.configure(height=want)
        if self._fit_width:
            want_w = self.inner.winfo_reqwidth() + 2 * self._pad
            if want_w > 1 and self.canvas.winfo_reqwidth() != want_w:
                self.canvas.configure(width=want_w)
        self._redraw(self.canvas.winfo_width() or self.canvas.winfo_reqwidth(), want)

    # ---- 外观
    def set_tone(self, fill: str, border: str = "") -> None:
        """换底色（提示条/胶囊标签按状态换色时用）。"""
        self._fill, self._border = fill, border
        self.inner.configure(bg=fill)
        self._redraw(self.canvas.winfo_width(), self.canvas.winfo_height())

    def _redraw(self, w: int, h: int) -> None:
        cv = self.canvas
        for item in self._shapes:
            cv.delete(item)
        self._shapes = []
        if w <= 2 or h <= 2:
            return
        if self._shadow:
            # 轻阴影：只画一层 1px 偏移的浅灰，够暗示层次又不至于像描边
            self._shapes.append(draw_round_rect(cv, 1, 2, w - 2, h - 2, self._radius, "#F0F1F4"))
        self._shapes.append(draw_round_rect(cv, 1, 1, w - 2, h - 2, self._radius,
                                            self._fill, self._border))
        for item in self._shapes:      # 画在内容之下
            cv.tag_lower(item, self._win)

    # ---- 兼容 tk 的取色习惯（测试与业务代码会读 cget("bg")）
    def cget(self, key):
        if key == "bg":
            return self._fill
        return super().cget(key)

    def configure(self, cnf=None, **kw):  # noqa: A003
        if "bg" in kw:
            self._fill = kw.pop("bg")
            self.inner.configure(bg=self._fill)
            self._redraw(self.canvas.winfo_width(), self.canvas.winfo_height())
        if kw or cnf:
            super().configure(cnf, **kw)

    config = configure


class RoundedPanel(_CanvasBox):
    """圆角面板：提示框 / 胶囊标签 / 底部提示条都用它。

    用法：panel = RoundedPanel(parent, tone="info"); tk.Label(panel.inner, ...).pack(...)
    """

    def __init__(self, parent, tone="plain", radius=RADIUS_CTRL, pad=8,
                 page_bg=COLOR_PAGE, shadow=False, fill=None, border=None, fit_width=False):
        t_fill, t_border, _fg = TONES.get(tone, TONES["plain"])
        super().__init__(parent, fill=fill or t_fill, border=border if border is not None else t_border,
                         radius=radius, pad=pad, page_bg=page_bg, shadow=shadow,
                         fit_width=fit_width)
        self.tone = tone

    def apply_tone(self, tone: str) -> None:
        fill, border, _fg = TONES.get(tone, TONES["plain"])
        self.tone = tone
        self.set_tone(fill, border)


class RoundedCard(_CanvasBox):
    """圆角卡片：Canvas 画圆角底 + 描边 + 轻阴影，内容放进 .body（照常 pack）。"""

    def __init__(self, parent, page_bg=COLOR_PAGE, fill=COLOR_CARD, radius=RADIUS_CARD,
                 pad=PAD_CARD, shadow=True):
        super().__init__(parent, fill=fill, border=COLOR_BORDER, radius=radius, pad=pad,
                         page_bg=page_bg, shadow=shadow)
        self.body = self.inner          # 业务代码把控件 pack 进 .body


class RoundedButton(tk.Canvas):
    """圆角按钮（自绘）。

    kind: "primary"（蓝底白字）/ "secondary"（白底描边）/ "ghost"（无描边）
          / "danger"（白底红字）/ "segment"（分段项，靠 set_on() 控制选中态）
    状态: normal / hover / pressed / disabled —— 全部覆盖（参考图要求）。
    """

    def __init__(self, parent, text="", command=None, kind="secondary", width=None,
                 height=28, radius=RADIUS_CTRL, padx=12, page_bg=COLOR_CARD,
                 font=UI_FONT, on=False):
        measure = tkfont.Font(family="黑体", size=10)
        w = width or (measure.measure(text) + 2 * padx)
        super().__init__(parent, width=w, height=height, bg=page_bg, highlightthickness=0, bd=0)
        self._text = text
        self._command = command
        self._kind = kind
        self._radius = radius
        self._font = font
        self._on = on
        self._hover, self._pressed, self._disabled = False, False, False
        self._shapes: list = []
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    # ---- 交互
    def _on_enter(self, _e=None):
        if not self._disabled:
            self._hover = True
            self._draw()
            self.configure(cursor="hand2")

    def _on_leave(self, _e=None):
        self._hover = self._pressed = False
        self._draw()

    def _on_press(self, _e=None):
        if not self._disabled:
            self._pressed = True
            self._draw()

    def _on_release(self, _e=None):
        if self._disabled:
            return
        was = self._pressed
        self._pressed = False
        self._draw()
        if was and self._command:
            self._command()

    def invoke(self) -> None:
        if not self._disabled and self._command:
            self._command()

    # ---- 兼容 ttk 的最小接口（业务代码与回归测试在用）
    def cget(self, key):
        if key == "text":
            return self._text
        return super().cget(key)

    def configure(self, cnf=None, **kw):  # noqa: A003
        redraw = False
        if "text" in kw:
            self._text = kw.pop("text")
            redraw = True
        if "command" in kw:
            self._command = kw.pop("command")
        if "state" in kw:
            self._set_state(kw.pop("state"))
            redraw = True
        if "width" in kw and kw["width"] is not None:
            super().configure(width=kw.pop("width"))
        if kw or cnf:
            super().configure(cnf, **kw)
        if redraw:
            self._draw()

    config = configure

    def state(self, spec=None):
        """兼容 ttk 的 state()：["disabled"] / ["!disabled"]。"""
        if spec is None:
            return ("disabled",) if self._disabled else ()
        for item in spec:
            if item == "disabled":
                self._disabled = True
            elif item == "!disabled":
                self._disabled = False
        self._draw()
        return ()

    def _set_state(self, value) -> None:
        self._disabled = str(value) in ("disabled", "Disabled", "DISABLED")

    def set_on(self, on: bool) -> None:
        """分段按钮：是否处于选中态。"""
        self._on = bool(on)
        self._draw()

    # ---- 绘制
    def _colors(self):
        if self._disabled:
            return (COLOR_HOVER, COLOR_BORDER, COLOR_MUTED)
        k = self._kind
        if k == "primary":
            fill = COLOR_PRIMARY_PRESSED if self._pressed else (
                COLOR_PRIMARY_HOVER if self._hover else COLOR_PRIMARY)
            return (fill, fill, "#FFFFFF")
        if k == "danger":
            return (COLOR_ERR_SOFT if self._hover else COLOR_CARD, COLOR_ERR_BORDER, COLOR_ERR_TEXT)
        if k == "ghost":
            return (COLOR_HOVER if self._hover else COLOR_CARD, "", COLOR_TEXT if not self._on else COLOR_PRIMARY_TEXT)
        if k == "segment":
            if self._on:
                fill = COLOR_PRIMARY_PRESSED if self._pressed else (
                    COLOR_PRIMARY_HOVER if self._hover else COLOR_PRIMARY)
                return (fill, fill, "#FFFFFF")
            return (COLOR_HOVER if self._hover else COLOR_CARD, COLOR_BORDER,
                    COLOR_TEXT if not self._pressed else COLOR_PRIMARY_TEXT)
        # secondary
        return (COLOR_HOVER if (self._hover or self._pressed) else COLOR_CARD,
                COLOR_BORDER_STRONG, COLOR_TEXT)

    def _draw(self) -> None:
        for item in self._shapes:
            self.delete(item)
        self._shapes = []
        w = int(self.winfo_reqwidth())
        h = int(self.winfo_reqheight())
        fill, border, fg = self._colors()
        self._shapes.append(draw_round_rect(self, 1, 1, w - 1, h - 1, self._radius, fill, border))
        self._shapes.append(self.create_text(w // 2, h // 2, text=self._text, fill=fg,
                                            font=self._font))


class SegmentGroup(tk.Frame):
    """分段选择器：把若干选项画成一排圆角按钮，选中项蓝底白字。

    value → 选项值由外部 StringVar 持有（与本窗口原有的 _quality_var/_lang_var 一致），
    这里只负责"按值刷新外观"，因此业务逻辑与测试都不用改。
    """

    def __init__(self, parent, options, variable, on_change, page_bg=COLOR_CARD,
                 height=30, padx=14):
        super().__init__(parent, bg=page_bg)
        self._var = variable
        self._buttons: dict[str, RoundedButton] = {}
        for value, title in options:
            btn = RoundedButton(self, text=title, kind="segment", height=height, padx=padx,
                                page_bg=page_bg,
                                command=lambda v=value: self._choose(v))
            btn.pack(side="left", padx=(0, 8))
            self._buttons[value] = btn
        self._on_change = on_change
        self._var.trace_add("write", lambda *_: self.refresh())
        self.refresh()

    def _choose(self, value: str) -> None:
        self._var.set(value)
        if self._on_change:
            self._on_change()
        self.refresh()

    def refresh(self) -> None:
        current = self._var.get()
        for value, btn in self._buttons.items():
            btn.set_on(value == current)


class ToggleSwitch(tk.Canvas):
    """iOS 风格开关：点击切换 BooleanVar，并可触发回调。"""

    def __init__(self, parent, variable, command=None, page_bg=COLOR_CARD,
                 width=42, height=24):
        super().__init__(parent, width=width, height=height, bg=page_bg,
                         highlightthickness=0, bd=0)
        self._var = variable
        self._command = command
        self._disabled = False
        # 注意：不能叫 _w —— tkinter 用 _w 保存控件的 Tcl 路径名，覆盖它会让所有
        # Tk 调用报 invalid command name（2026-09-29 踩过）
        self._box_w, self._box_h = width, height
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", lambda _e: self.configure(cursor="hand2"))
        self._var.trace_add("write", lambda *_: self._draw())
        self._draw()

    def _toggle(self, _e=None) -> None:
        if self._disabled:
            return
        self._var.set(not bool(self._var.get()))
        if self._command:
            self._command()

    def state(self, spec=None):
        if spec is None:
            return ("disabled",) if self._disabled else ()
        for item in spec:
            if item == "disabled":
                self._disabled = True
            elif item == "!disabled":
                self._disabled = False
        self._draw()
        return ()

    def _draw(self) -> None:
        self.delete("all")
        on = bool(self._var.get())
        w, h = self._box_w, self._box_h
        r = h / 2
        fill = COLOR_PRIMARY if on else "#D1D5DB"
        if self._disabled:
            fill = "#E5E7EB" if not on else "#A5C4F5"
        draw_round_rect(self, 1, 1, w - 1, h - 1, r - 1, fill)
        knob_r = r - 3
        cx = (w - r) if on else r
        self.create_oval(cx - knob_r, h / 2 - knob_r, cx + knob_r, h / 2 + knob_r,
                         fill="#FFFFFF", outline="")


class IconBadge(tk.Canvas):
    """图标徽标：浅色圆角底 + 简单线条图标（对应参考图卡片标题左侧的小图标）。

    kind: models / lang / speed / memory / hotkey / bell
    """

    def __init__(self, parent, kind, size=26, page_bg=COLOR_CARD,
                 tint=COLOR_PRIMARY_SOFT, color=COLOR_PRIMARY):
        super().__init__(parent, width=size, height=size, bg=page_bg,
                         highlightthickness=0, bd=0)
        self._size = size
        draw_round_rect(self, 0.5, 0.5, size - 0.5, size - 0.5, 7, tint)
        self._draw_icon(kind, size, color)

    def _draw_icon(self, kind: str, s: int, color: str) -> None:
        c = color
        w = 1.6
        cx = cy = s / 2
        if kind == "models":                       # 下载箭头 + 托盘
            self.create_line(cx, cy - 7, cx, cy + 2, fill=c, width=w, capstyle="round")
            self.create_line(cx - 4, cy - 1, cx, cy + 3, cx + 4, cy - 1,
                             fill=c, width=w, capstyle="round", joinstyle="round")
            self.create_line(cx - 6, cy + 6, cx + 6, cy + 6, fill=c, width=w, capstyle="round")
        elif kind == "lang":                       # 地球
            self.create_oval(cx - 7, cy - 7, cx + 7, cy + 7, outline=c, width=w)
            self.create_line(cx - 7, cy, cx + 7, cy, fill=c, width=w)
            self.create_oval(cx - 3.2, cy - 7, cx + 3.2, cy + 7, outline=c, width=w)
        elif kind == "speed":                      # 仪表盘
            self.create_arc(cx - 7, cy - 6, cx + 7, cy + 8, start=20, extent=140,
                            style="arc", outline=c, width=w)
            self.create_line(cx, cy + 2, cx + 3.5, cy - 2, fill=c, width=w, capstyle="round")
        elif kind == "memory":                     # 芯片
            self.create_rectangle(cx - 5, cy - 5, cx + 5, cy + 5, outline=c, width=w)
            for d in (-3, 0, 3):
                self.create_line(cx - 8, cy + d, cx - 5, cy + d, fill=c, width=w)
                self.create_line(cx + 5, cy + d, cx + 8, cy + d, fill=c, width=w)
        elif kind == "hotkey":                     # 键盘
            self.create_rectangle(cx - 8, cy - 5, cx + 8, cy + 5, outline=c, width=w)
            for d in (-4, 0, 4):
                self.create_line(cx + d, cy - 1.5, cx + d, cy - 1.5, fill=c, width=w + 1)
            self.create_line(cx - 4, cy + 3, cx + 4, cy + 3, fill=c, width=w)
        elif kind == "bell":                       # 铃铛（通知）
            self.create_arc(cx - 6, cy - 7, cx + 6, cy + 5, start=0, extent=180,
                            style="arc", outline=c, width=w)
            self.create_line(cx - 6, cy - 1, cx - 6, cy + 4, fill=c, width=w)
            self.create_line(cx + 6, cy - 1, cx + 6, cy + 4, fill=c, width=w)
            self.create_line(cx - 7, cy + 4, cx + 7, cy + 4, fill=c, width=w, capstyle="round")
            self.create_oval(cx - 1.6, cy + 6, cx + 1.6, cy + 9, fill=c, outline="")
