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

import base64
import ctypes
import io
import math
import os
import sys
import tkinter as tk

# --------------------------------------------------------------- DPI 感知
# 为什么要这个（2026-09-29 实测根因）：进程若不声明 DPI 感知，Windows 会把整个
# 窗口**按位图拉伸**到缩放比例 —— 在 125%/150% 的屏幕上，文字和自绘控件都会发虚。
# 必须在创建任何窗口之前调用；per-monitor-v2（Win10 1703+）> per-monitor > system。
_DPI_AWARENESS = ""


def enable_dpi_awareness() -> str:
    """让进程感知 DPI（幂等；返回实际生效的级别，便于日志与自检）。"""
    global _DPI_AWARENESS
    if _DPI_AWARENESS:
        return _DPI_AWARENESS
    if not sys.platform.startswith("win"):
        _DPI_AWARENESS = "non-windows"
        return _DPI_AWARENESS
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
    except OSError:
        _DPI_AWARENESS = "unavailable"
        return _DPI_AWARENESS
    # 1) per-monitor v2：DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            _DPI_AWARENESS = "per-monitor-v2"
            return _DPI_AWARENESS
    except Exception:  # noqa: BLE001
        pass
    # 2) per-monitor（Win8.1+）
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        if shcore.SetProcessDpiAwareness(2) == 0:
            _DPI_AWARENESS = "per-monitor"
            return _DPI_AWARENESS
    except Exception:  # noqa: BLE001
        pass
    # 3) system（Vista+，最保守）
    try:
        if user32.SetProcessDPIAware():
            _DPI_AWARENESS = "system"
            return _DPI_AWARENESS
    except Exception:  # noqa: BLE001
        pass
    _DPI_AWARENESS = "none"
    return _DPI_AWARENESS


def dpi_awareness() -> str:
    return _DPI_AWARENESS or enable_dpi_awareness()


def is_dpi_aware() -> bool:
    """当前进程是否真的被系统认定为 DPI 感知（用系统 API 复核，不只看我们自己记的）。"""
    if not sys.platform.startswith("win"):
        return True
    try:
        return bool(ctypes.WinDLL("user32").IsProcessDPIAware())
    except Exception:  # noqa: BLE001
        return False

# --------------------------------------------------------------- 设计令牌
# 参考图（Clash Verge 风格）：近白页面 + 白卡片 + 浅灰描边 + 蓝色主色 + 语义色
COLOR_PAGE = "#F7F8FA"          # 页面底色
COLOR_CARD = "#FFFFFF"          # 卡片 / 面板底
COLOR_BORDER = "#D9DEE5"        # 1px 描边（#E5E7EB 在白底上对比不足，看着发虚）
COLOR_BORDER_STRONG = "#C9D0DA"  # 输入框/按钮等需要更清楚的描边
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

# --------------------------------------------------------------- 尺寸缩放
# 像素型设计值（圆角、间距、控件高度）必须跟着 DPI 一起放大，
# 否则在 150% 屏幕上界面会显得又小又挤（字体是按 DPI 缩放的，控件却没缩）。
_SCALE = 1.0


def set_scale(scale: float) -> float:
    """设置像素缩放系数（= 实际 DPI / 96）。建窗口前调用。"""
    global _SCALE, PAD_CARD, GAP_SECTION, RADIUS_CARD, RADIUS_CTRL
    _SCALE = max(1.0, float(scale))
    PAD_CARD = S(12)
    GAP_SECTION = S(8)
    RADIUS_CARD = S(10)
    RADIUS_CTRL = S(8)
    return _SCALE


def scale() -> float:
    return _SCALE


def S(px: float) -> int:
    """把设计稿上的像素值换算成当前 DPI 下的整数像素（保证锐利、不半像素）。"""
    return max(1, int(round(px * _SCALE)))

# 语义色 → （底色, 描边, 文字色）
TONES = {
    "info": (COLOR_PRIMARY_SOFT, COLOR_PRIMARY_SOFT_BORDER, COLOR_PRIMARY_TEXT),
    "ok": (COLOR_OK_SOFT, COLOR_OK_BORDER, COLOR_OK_TEXT),
    "warn": (COLOR_WARN_BG, COLOR_WARN_BORDER, COLOR_WARN_FG),
    "err": (COLOR_ERR_SOFT, COLOR_ERR_BORDER, COLOR_ERR_TEXT),
    "plain": (COLOR_HOVER, COLOR_BORDER, COLOR_TEXT),
}


# --------------------------------------------- 超采样渲染（真正提高"渲染分辨率"）
# Tk 画布不做抗锯齿 → 圆角与斜线是硬阶梯（放大看是"毛边"）。这里的做法是：
# 用 Pillow 在 SUPERSAMPLE 倍分辨率下绘制，再 LANCZOS 降采样成画布背景图 ——
# 曲线/斜线因此获得 1px 平滑过渡（锐利而不毛），而水平/垂直的 1px 边框
# 仍然落在整像素上（不会变灰变虚）。Pillow 不可用时自动退回纯 Tk 矢量画法。
try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    HAS_PIL = True
except Exception:  # noqa: BLE001
    HAS_PIL = False

SUPERSAMPLE = 3                    # 超采样倍数（3× ≈ 每像素 9 个采样点；4× 单次耗时翻倍）
AA_MODE = ("超采样 %d× 降采样（抗锯齿）" % SUPERSAMPLE) if HAS_PIL else "矢量硬边（无 Pillow 兜底）"

# 渲染缓存：同一 (尺寸, 圆角, 底色, 描边, 阴影) 只渲染一次。
# 为什么必须缓存（2026-09-30 实测）：4× 超采样 + 降采样 + PNG 编码约 30~130ms/组件，
# 窗口一次性构建十几个组件时会拖慢 UI 线程（心跳日志里出现过 1276ms 慢轮警告）。
_RENDER_CACHE: dict = {}
_RENDER_CACHE_MAX = 96


def bind_photo_cleanup(widget, attr: str = "_photo") -> None:
    """控件销毁时立刻释放 PhotoImage 引用。

    为什么要这个（2026-09-30 实测）：PhotoImage 的 __del__ 会去调用 Tk 删图片。
    如果它拖到控件/解释器销毁之后才被回收，这个调用就会失败并打印
    "main thread is not in main loop"（虽然无害，但每次关窗都刷一屏噪音）。
    在 <Destroy> 时主动置空引用，回收就发生在解释器还活着的时候。
    """
    def _on_destroy(_event=None):
        try:
            setattr(widget, attr, None)
        except Exception:  # noqa: BLE001
            pass

    try:
        widget.bind("<Destroy>", _on_destroy, add="+")
    except Exception:  # noqa: BLE001
        pass


def _cached_png(key, build_image):
    """带缓存生成 PNG 字节。

    为什么缓存字节而不是 PhotoImage（2026-09-30 实测踩过）：PhotoImage 绑定在**某个 Tk
    解释器**上，模块级长期持有它跨窗口/跨线程复用会失效 —— 表现为第二个窗口构建失败、
    线程静默退出，并在 GC 时抛 "main thread is not in main loop"。PNG 字节是纯数据，
    与解释器无关，可安全复用；每张图只在需要时用**当前控件所属解释器**创建。
    """
    hit = _RENDER_CACHE.get(key)
    if hit is not None:
        return hit
    img = build_image()
    if img is None:
        return None
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    if len(_RENDER_CACHE) >= _RENDER_CACHE_MAX:
        for old_key in list(_RENDER_CACHE)[: _RENDER_CACHE_MAX // 2]:
            _RENDER_CACHE.pop(old_key, None)
    _RENDER_CACHE[key] = data
    return data


def render_stats() -> dict:
    """渲染缓存统计（自检用）。"""
    return {"cached": len(_RENDER_CACHE), "ss": SUPERSAMPLE, "pil": HAS_PIL}


def _make_photo(master, data):
    """用 master 所属的 Tk 解释器创建 PhotoImage（必须传 master，不能用默认解释器）。"""
    if not data:
        return None
    try:
        return tk.PhotoImage(master=master, data=base64.b64encode(data))
    except Exception:  # noqa: BLE001
        return None


def render_round_rect(master, w, h, radius, fill, outline="", width=1, shadow=None):
    """超采样渲染圆角矩形 → PhotoImage（不可用/失败时返回 None 走矢量兜底）。"""
    if not HAS_PIL or w < 2 or h < 2:
        return None
    key = ("rect", int(w), int(h), int(radius), fill, outline, width, shadow)
    data = _cached_png(key, lambda: _draw_round_rect_image(w, h, radius, fill, outline,
                                                           width, shadow))
    return _make_photo(master, data)


def _draw_round_rect_image(w, h, radius, fill, outline="", width=1, shadow=None):
    ss = SUPERSAMPLE
    W, H = int(w) * ss, int(h) * ss
    r = max(1, int(min(radius, (w - 1) / 2, (h - 1) / 2))) * ss
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if shadow:
        # 阴影先模糊再叠底：模糊半径按超采样等比放大，降采样后正好是细腻的柔和投影
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle([0, ss, W - 1, H - 1], radius=r, fill=shadow)
        img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(ss * 1.2)))
    if outline:
        # 1px 描边：外圈画描边色、内圈内缩 width 像素画底色 → 降采样后恰好 1px
        draw.rounded_rectangle([0, 0, W - 1, H - 1], radius=r, fill=outline)
        inset = max(1, int(round(width))) * ss
        draw.rounded_rectangle([inset, inset, W - 1 - inset, H - 1 - inset],
                               radius=max(0, r - inset), fill=fill)
    else:
        draw.rounded_rectangle([0, 0, W - 1, H - 1], radius=r, fill=fill)
    # 整数倍超采样下用 BOX（3×3 均值）等价于理论上的超采样平均，且比 LANCZOS 快得多
    return img.resize((int(w), int(h)), Image.BOX)


def render_toggle(master, w, h, track, knob, knob_border, kx1, ky1, kx2, ky2, radius):
    """超采样渲染开关（胶囊轨道 + 圆旋钮 + 旋钮 1px 描边）。"""
    if not HAS_PIL or w < 2 or h < 2:
        return None
    key = ("toggle", int(w), int(h), track, knob, knob_border,
           int(kx1), int(ky1), int(kx2), int(ky2), int(radius))
    data = _cached_png(key, lambda: _draw_toggle_image(w, h, track, knob, knob_border,
                                                       kx1, ky1, kx2, ky2, radius))
    return _make_photo(master, data)


def _draw_toggle_image(w, h, track, knob, knob_border, kx1, ky1, kx2, ky2, radius):
    ss = SUPERSAMPLE
    W, H = int(w) * ss, int(h) * ss
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r = max(1, int(radius)) * ss
    draw.rounded_rectangle([0, 0, W - 1, H - 1], radius=r, fill=track)
    box = [int(kx1) * ss, int(ky1) * ss, int(kx2) * ss, int(ky2) * ss]
    draw.ellipse(box, fill=knob, outline=knob_border, width=ss)
    return img.resize((int(w), int(h)), Image.BOX)


def _pil_line(draw, x1, y1, x2, y2, width, color, ss, round_caps=True):
    """画线并在两端补圆点（PIL 的 line 没有圆头，补两个小圆模拟）。"""
    draw.line([x1 * ss, y1 * ss, x2 * ss, y2 * ss], fill=color, width=width * ss)
    if round_caps and width > 1:
        rr = width * ss / 2
        for px, py in ((x1, y1), (x2, y2)):
            draw.ellipse([px * ss - rr, py * ss - rr, px * ss + rr, py * ss + rr], fill=color)


def render_icon_badge(master, kind, s, tint, color, radius):
    """超采样渲染图标徽标（浅色圆角底 + 图标线条），斜线/圆弧获得抗锯齿边缘。"""
    if not HAS_PIL or s < 8:
        return None
    key = ("icon", kind, int(s), tint, color, int(radius))
    data = _cached_png(key, lambda: _draw_icon_image(kind, s, tint, color, radius))
    return _make_photo(master, data)


def _draw_icon_image(kind, s, tint, color, radius):
    ss = SUPERSAMPLE
    S_ = int(s) * ss
    img = Image.new("RGBA", (S_, S_), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, S_ - 1, S_ - 1], radius=max(1, int(radius)) * ss, fill=tint)
    u = s / 26.0                       # 设计基准 26px
    w = max(2, int(round(2.0 * u)))    # 笔画至少 2px
    cx = cy = s / 2

    def X(v):                          # 相对中心 → 绝对坐标（设计单位）
        return round(cx + v * u, 2)

    def Y(v):
        return round(cy + v * u, 2)

    def line(x1, y1, x2, y2, lw=None):
        _pil_line(draw, x1, y1, x2, y2, lw or w, color, ss)

    if kind == "models":                          # 下载箭头 + 托盘
        line(cx, Y(-7), cx, Y(2))
        line(X(-4), Y(-1), cx, Y(3))
        line(cx, Y(3), X(4), Y(-1))
        line(X(-6), Y(6), X(6), Y(6))
    elif kind == "lang":                          # 地球
        draw.ellipse([X(-7) * ss, Y(-7) * ss, X(7) * ss, Y(7) * ss], outline=color, width=w * ss)
        line(X(-7), cy, X(7), cy)
        draw.ellipse([X(-3.2) * ss, Y(-7) * ss, X(3.2) * ss, Y(7) * ss], outline=color, width=w * ss)
    elif kind == "speed":                         # 仪表盘
        draw.arc([X(-7) * ss, Y(-6) * ss, X(7) * ss, Y(8) * ss],
                 start=20 - 140, end=20, fill=color, width=w * ss)
        line(cx, Y(2), X(3.5), Y(-2))
    elif kind == "memory":                        # 芯片
        draw.rectangle([X(-5) * ss, Y(-5) * ss, X(5) * ss, Y(5) * ss], outline=color, width=w * ss)
        for off in (-3, 0, 3):
            line(X(-8), Y(off), X(-5), Y(off))
            line(X(5), Y(off), X(8), Y(off))
    elif kind == "hotkey":                        # 键盘
        draw.rectangle([X(-8) * ss, Y(-5) * ss, X(8) * ss, Y(5) * ss], outline=color, width=w * ss)
        for off in (-4, 0, 4):
            draw.ellipse([X(off) * ss - w, Y(-1.5) * ss - w,
                          X(off) * ss + w, Y(-1.5) * ss + w], fill=color)
        line(X(-4), Y(3), X(4), Y(3))
    elif kind == "bell":                          # 铃铛
        draw.arc([X(-6) * ss, Y(-7) * ss, X(6) * ss, Y(5) * ss],
                 start=0 - 180, end=0, fill=color, width=w * ss)
        line(X(-6), Y(-1), X(-6), Y(4))
        line(X(6), Y(-1), X(6), Y(4))
        line(X(-7), Y(4), X(7), Y(4))
        draw.ellipse([X(-1.6) * ss, Y(6) * ss, X(1.6) * ss, Y(9) * ss], fill=color)
    return img.resize((int(s), int(s)), Image.BOX)


# ------------------------------- 文字渲染（灰度超采样，替代 Tk 的 ClearType 亚像素）
# 为什么这么做（2026-10-01 取证）：Tk 在 Windows 上走 GDI 的 ClearType ——
# 把字体位图放大 16 倍看，笔画边缘是**彩虹色**（R/G/B 三个子像素分家），
# 字形轮廓被"次像素位置"扭曲，12/13px 的小字尤其显得毛糙、发虚。
# 这里改用 Pillow/FreeType 在 SUPERSAMPLE 倍分辨率下渲染、再 BOX 降采样：
# 得到**灰度抗锯齿**的干净字形 —— 无彩边、笔画落在整像素上、斜线/曲线有 1px 平滑过渡。
# 实测（12px × 一行 30 字 × 3×）：单行渲染 0.85ms，PNG+PhotoImage 2.6ms，可忽略。
# Pillow 或字体文件不可用时，TextLabel 自动退回原生 Tk 渲染（功能不受影响）。

_FONTS_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
_FONT_FILE_CACHE: dict = {}
_TEXT_PNG_CACHE: dict = {}
_TEXT_PNG_CACHE_MAX = 512


def _registry_font_files() -> list:
    """从注册表列出已安装字体 [(项名, 文件路径)]；非 Windows 或读取失败返回空。"""
    out: list = []
    if not sys.platform.startswith("win"):
        return out
    try:
        import winreg
    except Exception:  # noqa: BLE001
        return out
    sub = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, sub) as key:
                idx = 0
                while True:
                    try:
                        name, value, _kind = winreg.EnumValue(key, idx)
                    except OSError:
                        break
                    idx += 1
                    if isinstance(value, str):
                        out.append((name, value))
        except OSError:
            continue
    return out


def _ttc_face_index(path: str, family: str) -> int:
    """在一个 .ttc（字体集合）里找出 family 对应的 face 序号（找不到返回 0）。"""
    wanted = family.strip().lower()
    try:
        for i in range(12):
            try:
                font = ImageFont.truetype(path, 24, index=i)
            except Exception:  # noqa: BLE001
                break
            if font.getname()[0].strip().lower() == wanted:
                return i
    except Exception:  # noqa: BLE001
        pass
    return 0


def font_file(family: str, bold: bool = False):
    """把字体**族名**解析成磁盘文件 + face 索引，返回 (路径, index) 或 None。

    为什么需要它：Pillow 只能按文件渲染，而 Tk 用的是族名（"Microsoft YaHei UI"）。
    注册表项名形如 "Microsoft YaHei & Microsoft YaHei UI (TrueType)" → msyh.ttc，
    粗体在另一个项里（msyhbd.ttc），所以要按原名匹配 + 粗细过滤 + face 探测。
    """
    key = (str(family).strip().lower(), bool(bold))
    if key in _FONT_FILE_CACHE:
        return _FONT_FILE_CACHE[key]
    result = None
    if HAS_PIL:
        wanted = key[0]
        for name, value in _registry_font_files():
            low = value.lower()
            if not low.endswith((".ttf", ".ttc", ".otf")):
                continue
            base = name.split("(")[0].strip()
            if wanted not in base.lower():
                continue
            if ("bold" in base.lower()) != bool(bold):
                continue
            path = value if ("\\" in value or "/" in value) else os.path.join(_FONTS_DIR, value)
            if not os.path.exists(path):
                continue
            result = (path, _ttc_face_index(path, family))
            break
    _FONT_FILE_CACHE[key] = result
    return result


def _tk_font_pairs(widget, font_name: str, sub: str) -> dict:
    """读 Tk 命名字体的 actual/metrics（返回 "ascent": "13" 这样的字典）。

    注意：`font actual` 返回 Tcl **列表**（Python 得到元组），而 `font metrics` 返回
    的是**字符串**（"-ascent 15 -descent 4 -linespace 19 -fixed 0"）—— 两种都要能吃。
    """
    try:
        raw = widget.tk.call("font", sub, font_name)
    except Exception:  # noqa: BLE001
        return {}
    if isinstance(raw, str):
        parts = raw.split()
    else:
        try:
            parts = [str(item) for item in raw]
        except Exception:  # noqa: BLE001
            return {}
    info = {}
    for i in range(0, len(parts) - 1, 2):
        info[parts[i].lstrip("-")] = parts[i + 1]
    return info


# 命名字体 → (family, 像素字号, weight)：由建字方（settings_window._apply_theme）
# 注册。为什么不从 Tk 反推字号：`font actual` 返回的是**四舍五入后的整数磅值**，
# 反算像素在非 100% DPI 下会差 1px（字号差 1px 就是"又发虚了"）。
_FONT_PX_REGISTRY: dict = {}


def register_font(name: str, family: str, px: int, weight: str = "normal") -> None:
    """登记命名字体的真实像素字号（建字后调用，覆盖旧值）。"""
    _FONT_PX_REGISTRY[str(name)] = (str(family), int(px), str(weight))


def font_spec(widget, font_name: str) -> dict:
    """命名字体的实际规格 {family, px, weight, ascent, descent, linespace}。"""
    metrics = _tk_font_pairs(widget, font_name, "metrics")
    spec = {
        "family": "TkDefaultFont",
        "weight": "normal",
        "px": 12,
        "ascent": 13, "descent": 4, "linespace": 17,
    }
    for field in ("ascent", "descent", "linespace"):
        try:
            spec[field] = int(metrics[field])
        except Exception:  # noqa: BLE001
            pass
    reg = _FONT_PX_REGISTRY.get(str(font_name))
    if reg:
        spec["family"], spec["px"], spec["weight"] = reg
        return spec
    actual = _tk_font_pairs(widget, font_name, "actual")
    if actual:
        spec["family"] = str(actual.get("family", spec["family"]))
        spec["weight"] = str(actual.get("weight", spec["weight"]))
        try:
            # 兜底路径：Tk 里负数字号 = 像素，正数 = 磅。磅要按**实际 DPI**折算。
            size = int(actual.get("size", -12))
            if size < 0:
                spec["px"] = abs(size)
            else:
                dpi = float(widget.winfo_fpixels("1i") or 96.0)
                spec["px"] = max(1, int(round(size * dpi / 72.0)))
        except Exception:  # noqa: BLE001
            pass
    return spec


_LATIN_WORD = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/%@+:&'\"!")


def _split_tokens(text: str) -> list:
    """切词：连续拉丁字母/数字当整词（不在词内断行），其余逐字（CJK、标点）。"""
    out, buf = [], ""
    for ch in text:
        if ch in _LATIN_WORD:
            buf += ch
        else:
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
    if buf:
        out.append(buf)
    return out


def wrap_lines(text: str, limit_px: int, measure) -> list:
    """按像素宽度贪心断行（CJK 逐字、拉丁按词）。

    `measure(s) -> 像素宽` 用**与 Tk 相同的度量函数**（TextLabel 传 `font measure`），
    这样断行点、行数与原生 Label 完全一致 —— 否则换行不同会让图片高度对不上。
    """
    lines: list = []
    for para in str(text).split("\n"):
        if limit_px <= 0:
            lines.append(para)
            continue
        cur = ""
        for token in _split_tokens(para):
            if not cur:
                cur = token
            elif measure(cur + token) <= limit_px:
                cur += token
            else:
                lines.append(cur.rstrip())
                cur = token.lstrip() if token.strip() else ""
        lines.append(cur.rstrip())
    return lines or [""]


def _pillow_measure(font, ss: int):
    """Pillow 兜底度量（没有 Tk 时用，单位统一折算成逻辑像素）。"""
    def _m(text):
        try:
            return int(math.ceil(font.getlength(text) / ss))
        except Exception:  # noqa: BLE001
            return 0
    return _m


def wrap_to_lines(text: str, limit_px: int, measure, target: int) -> list:
    """把文本断成**恰好 target 行**（每行不超过 limit_px），行宽尽量均匀。

    为什么需要它：Tk 的多行断行比"逐字贪心"更保守（Tk 实测会多排 1 行），
    若照自己的断行渲染，多行 Label 会比原生矮一整行、整块界面跟着变矮。
    这里先让 Tk 定下"该有几行"，再二分出能恰好排出该行数的行宽 —— 高度就与原来一致。
    """
    if target <= 1 or limit_px <= 0 or "\n" in str(text):
        return wrap_lines(text, limit_px, measure)
    base = wrap_lines(text, limit_px, measure)
    if len(base) >= target:
        return base
    # 行数随行宽增大而减少：找"行数仍不少于 target"的最大行宽（不越界 limit_px）
    lo, hi = 1, int(limit_px)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        try:
            count = len(wrap_lines(text, mid, measure))
        except Exception:  # noqa: BLE001
            return base
        if count >= target:
            lo = mid
        else:
            hi = mid - 1
    return wrap_lines(text, lo, measure)


def _hex_to_rgba(color: str):
    c = str(color).lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 255)
    except Exception:  # noqa: BLE001
        return (31, 41, 55, 255)


def text_png(text: str, family: str, px: int, weight: str = "normal", fg: str = COLOR_TEXT,
             wraplength: int = 0, linespace: int = 0, ascent: int = 0,
             ss: int = SUPERSAMPLE, measure=None, pad_x: int = 0, pad_y: int = 0,
             target_lines: int = 0, target_w: int = 0):
    """超采样渲染一段文字 → (PNG 字节, 宽, 高)。失败返回 (None, 0, 0)。

    尺寸对齐（2026-10-01 实测 Tk 规则，勿改）：
      文字 Label 的请求尺寸 = 文字 + 2*borderwidth + 2*padx/pady
      图片 Label 的请求尺寸 = 图片 + 2*borderwidth（**padx/pady 被忽略**）
    所以图片要把 padx/pady 自己画进去（四周留白），否则换控件后界面会比原来窄/矮 2px。
    宽度用 Tk 的 `font measure` 为准、高度用 Tk 的 `linespace` 为准 —— 与原生逐像素一致。
    """
    if not HAS_PIL or not text:
        return (None, 0, 0)
    pad_x, pad_y = max(0, int(pad_x)), max(0, int(pad_y))
    key = ("txt", text, family, int(px), str(weight), fg, int(wraplength),
           int(linespace), int(ascent), int(ss), measure is not None, pad_x, pad_y,
           int(target_lines), int(target_w))
    hit = _TEXT_PNG_CACHE.get(key)
    if hit is not None:
        return hit
    result = (None, 0, 0)
    got = font_file(family, str(weight).lower() == "bold")
    if got:
        path, index = got
        try:
            font = ImageFont.truetype(path, max(1, int(px)) * ss, index=index)
            pil_a, pil_d = font.getmetrics()
            # 断行统一走「逻辑像素 + 同一个度量函数」：有 Tk 的 measure 就用它（与原生
            # Label 完全一致），否则退回 FreeType 度量。
            ruler = measure if measure is not None else _pillow_measure(font, ss)
            limit = int(wraplength) if wraplength else 0
            if target_w > 0 and (not limit or target_w < limit):
                # 多行场景：用 Tk 实际排出来的行宽当上限，断行点才会与原生一致
                limit = int(target_w)
            lines = wrap_lines(text, limit, ruler)
            if target_lines > 0 and len(lines) < target_lines:
                lines = wrap_to_lines(text, limit, ruler, int(target_lines))
            step = int(linespace) * ss if linespace else (pil_a + pil_d)
            off_y = max(0, int(ascent) * ss - pil_a) if ascent else 0
            pil_w = max(int(math.ceil(font.getlength(line))) for line in lines) or 1
            tk_w = 0
            if measure is not None:
                try:
                    tk_w = max(int(measure(line)) for line in lines)
                except Exception:  # noqa: BLE001
                    tk_w = 0
            # 宽度以 Tk 的度量为准（保证与原生 Label 对齐）；只有 Pillow 的字形明显更宽
            # 时才放宽，避免右边最后一笔被裁掉。1px 内视为舍入差 —— 字的右侧本来就是
            # advance 留白，裁不到笔画。
            pil_w_px = int(math.ceil(pil_w / ss))
            body_w = tk_w if (tk_w and pil_w_px <= tk_w + 1) else max(1, tk_w, pil_w_px)
            if target_w > 0:
                body_w = max(body_w, int(target_w))
            body_h = int(linespace) if linespace else int(
                math.ceil((off_y + pil_a + pil_d) / ss))
            n_lines = max(len(lines), int(target_lines))
            w_px = body_w + 2 * pad_x
            h_px = max(1, body_h + int(linespace) * (n_lines - 1)) + 2 * pad_y
            img = Image.new("RGBA", (w_px * ss, h_px * ss), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            rgba = _hex_to_rgba(fg)
            for i, line in enumerate(lines):
                if line:
                    draw.text((pad_x * ss, pad_y * ss + off_y + i * step), line,
                              font=font, fill=rgba)
            img = img.resize((w_px, h_px), Image.BOX)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            result = (buf.getvalue(), w_px, h_px)
        except Exception:  # noqa: BLE001
            result = (None, 0, 0)
    if len(_TEXT_PNG_CACHE) >= _TEXT_PNG_CACHE_MAX:
        for old in list(_TEXT_PNG_CACHE)[: _TEXT_PNG_CACHE_MAX // 2]:
            _TEXT_PNG_CACHE.pop(old, None)
    _TEXT_PNG_CACHE[key] = result
    return result


def render_text(master, text: str, family: str, px: int, weight: str = "normal",
                fg: str = COLOR_TEXT, wraplength: int = 0, linespace: int = 0,
                ascent: int = 0, measure=None, pad_x: int = 0, pad_y: int = 0,
                target_lines: int = 0, target_w: int = 0):
    """渲染文字并返回 (PhotoImage, 宽, 高)；不可用时 (None, 0, 0)。"""
    data, w, h = text_png(text, family, px, weight, fg, wraplength, linespace,
                          ascent, measure=measure, pad_x=pad_x, pad_y=pad_y,
                          target_lines=target_lines, target_w=target_w)
    return (_make_photo(master, data), w, h)


def text_cache_stats() -> dict:
    """文字渲染缓存统计（自检用）。"""
    return {"text_cached": len(_TEXT_PNG_CACHE), "font_files": len(_FONT_FILE_CACHE),
            "files": {f"{k[0]}{'/bold' if k[1] else ''}": (v[0] if v else None)
                      for k, v in _FONT_FILE_CACHE.items()}}


class TextLabel(tk.Label):
    """文字标签：用**灰度超采样**渲染文字，替代 Tk 的 ClearType 亚像素渲染。

    与 tk.Label 的差别只在"怎么画字"——text / font / fg / bg / anchor / justify /
    wraplength 的接口保持兼容（cget 也照旧返回原名），因此布局与既有代码、
    回归测试都不用改。Pillow 或字体文件不可用时自动退回原生 Tk 渲染。
    """

    def __init__(self, parent, text: str = "", font: str = UI_FONT, fg: str = COLOR_TEXT,
                 bg: str = COLOR_CARD, wraplength: int = 0, justify: str = "center",
                 anchor: str = "center", **kw):
        self._text = text
        self._font_name = font
        self._fg = fg
        self._wraplength = wraplength or 0
        self._justify = justify
        # 含图片的 Label 默认 padx/pady 是 0，含文字的默认是 1（Tk 的规则）——
        # 不显式补齐就会比原生文字 Label 矮 2px、窄 2px，整体布局会跟着挪位。
        kw.setdefault("padx", 1)
        kw.setdefault("pady", 1)
        super().__init__(parent, text="", font=font, fg=fg, bg=bg, anchor=anchor,
                         justify=justify, wraplength=self._wraplength, **kw)
        self._photo = None
        bind_photo_cleanup(self)
        self._refresh_text()

    # ---- 内容刷新
    def _measure(self, line: str) -> int:
        """用 Tk 自己的字体度量测宽（保证与原生 Label 的换行/宽度一致）。"""
        return int(self.tk.call("font", "measure", self._font_name, line))

    def _native_metrics(self, spec: dict) -> dict:
        """多行场景：让 Tk 自己排一次版，问出它"打算占几行、每行多宽"。

        为什么要问 Tk：Tk 的断行比逐字贪心保守（实测同一个 wraplength 会多排 1 行），
        照自己的断行渲染，多行 Label 会比原生矮一整行，整块界面跟着变矮变宽。
        拿到行数与内容宽之后，按同样的行数、同样的宽度出图 —— 布局回到原样。
        """
        if not self._wraplength:
            return {}
        try:
            probe = tk.Label(self.master, text=str(self._text), font=self._font_name,
                             wraplength=self._wraplength, justify=self._justify,
                             anchor=self.cget("anchor"), padx=self.cget("padx"),
                             pady=self.cget("pady"), bd=self.cget("borderwidth"),
                             highlightthickness=self.cget("highlightthickness"))
            req_w = int(probe.winfo_reqwidth())
            req_h = int(probe.winfo_reqheight())
            probe.destroy()
            span = max(1, int(spec.get("linespace", 17)))
            pad_x = int(self.cget("padx"))
            extra_y = 2 * (int(self.cget("pady")) + int(self.cget("borderwidth"))
                           + int(self.cget("highlightthickness") or 0))
            extra_x = 2 * (pad_x + int(self.cget("borderwidth"))
                           + int(self.cget("highlightthickness") or 0))
            return {"lines": max(1, (req_h - extra_y) // span),
                    "width": max(1, req_w - extra_x)}
        except Exception:  # noqa: BLE001
            return {}

    def _refresh_text(self) -> None:
        if not self._text:
            self._photo = None
            super().configure(image="", text="")
            return
        spec = font_spec(self, self._font_name)
        try:
            pad_x = max(0, int(self.cget("padx")))
            pad_y = max(0, int(self.cget("pady")))
        except Exception:  # noqa: BLE001
            pad_x = pad_y = 0
        native = self._native_metrics(spec)
        photo, _w, _h = render_text(self, str(self._text), spec["family"], spec["px"],
                                    spec["weight"], self._fg, self._wraplength,
                                    spec["linespace"], spec["ascent"], self._measure,
                                    pad_x, pad_y, native.get("lines", 0),
                                    native.get("width", 0))
        if photo is None:
            # 兜底：退回原生 Tk 文字（回到改造前的样子，功能不受影响）
            self._photo = None
            super().configure(image="", text=str(self._text))
            return
        self._photo = photo              # 必须持有引用，否则图片会被回收
        super().configure(image=photo, text="")

    # ---- 兼容 tk.Label 的读写习惯（业务代码与回归测试都在用）
    def cget(self, key):
        if key == "text":
            return self._text
        if key == "font":
            return self._font_name
        if key == "fg" or key == "foreground":
            return self._fg
        if key == "wraplength":
            return self._wraplength
        return super().cget(key)

    def configure(self, cnf=None, **kw):  # noqa: A003
        if cnf is not None and isinstance(cnf, dict):
            kw = {**cnf, **kw}
        need = False
        if "text" in kw:
            self._text = kw.pop("text")
            need = True
        if "font" in kw:
            self._font_name = kw.pop("font")
            need = True
        if "fg" in kw:
            self._fg = kw.pop("fg")
            need = True
        if "foreground" in kw:
            self._fg = kw.pop("foreground")
            need = True
        if "wraplength" in kw:
            self._wraplength = kw.pop("wraplength") or 0
            # 同时交给 Tk：兜底（Pillow 缺失）时会退回原生文字渲染，那时靠它换行
            kw["wraplength"] = self._wraplength
            need = True
        if "justify" in kw:
            self._justify = kw.pop("justify")
        if cnf is not None:
            super().configure(cnf, **kw)
        elif kw:
            super().configure(**kw)
        if need:
            self._refresh_text()

    config = configure


def draw_round_rect(cv: tk.Canvas, x1, y1, x2, y2, r, fill, outline="", width=1):
    """在 Canvas 上画圆角矩形（**真实圆弧**拼成，纯 Tk 兜底路径）。


    为什么不用 smooth 多边形（2026-09-29 改）：smooth=True 是样条逼近，
    圆角不是真圆弧 —— 小尺寸下边框看着发毛、四角不够齐。改成
    「4 段 arc + 2 个矩形」的精确画法，边框更锐利、四角更规整。

    返回画出来的 item 列表（调用方需要时可以删除/置底）。
    """
    # 像素对齐：所有坐标与半径取整。Tk 画布不做抗锯齿，半像素坐标会让 1px
    # 描边摊到相邻两行/列上（看起来发虚）；整数坐标才能保证边缘落在单一像素行。
    x1, y1, x2, y2 = int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))
    r = max(1, min(int(round(r)), (x2 - x1) // 2, (y2 - y1) // 2))
    items = []
    # 填充：中间两条矩形 + 四个 90° 扇形（扇形自带的两条半径线落在形状内部，看不见）
    items.append(cv.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=""))
    items.append(cv.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline=""))
    for cx, cy, start in ((x1 + r, y1 + r, 90), (x2 - r, y1 + r, 0),
                          (x2 - r, y2 - r, 270), (x1 + r, y2 - r, 180)):
        items.append(cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=start, extent=90,
                                   style="pieslice", fill=fill, outline=""))
    if outline and width:
        w = int(width)
        # 描边：四条直线 + 四段圆弧（真圆弧，1px 细边也不会发毛）
        items.append(cv.create_line(x1 + r, y1, x2 - r, y1, fill=outline, width=w))
        items.append(cv.create_line(x1 + r, y2, x2 - r, y2, fill=outline, width=w))
        items.append(cv.create_line(x1, y1 + r, x1, y2 - r, fill=outline, width=w))
        items.append(cv.create_line(x2, y1 + r, x2, y2 - r, fill=outline, width=w))
        for cx, cy, start in ((x1 + r, y1 + r, 90), (x2 - r, y1 + r, 0),
                              (x2 - r, y2 - r, 270), (x1 + r, y2 - r, 180)):
            items.append(cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=start, extent=90,
                                       style="arc", outline=outline, width=w))
    return items


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
        self._radius = S(radius)
        self._pad = S(pad)
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
        self._last_key = None          # 上次绘制时的（高, 宽, 底色, 描边）指纹
        self._stable_checks = 0        # 连续多少次自检没有变化（用于退避）
        self.canvas.bind("<Configure>", self._on_configure)
        self.inner.bind("<Configure>", lambda _e: self._fit_height())
        # 轻量自检（校准高度）：Canvas 的窗口项只要完全落在画布可视区之外就不会被映射，
        # 也就不会触发 <Configure> —— 若初始高度太小（内容在下面）就会"卡住长不高"，
        # 卡片整块不可见。所以需要定时校准；但**只有尺寸/配色真的变了才重画**，
        # 并在稳定后退避到 1 秒一次（2026-09-29 实测：改前空闲时每秒重画 ~115 次）。
        bind_photo_cleanup(self)
        self.after(60, self._watch)

    def _watch(self) -> None:
        try:
            if not self.winfo_exists():
                return
            before = self._last_key
            self._fit_height()
            if before == self._last_key:
                self._stable_checks += 1
            else:
                self._stable_checks = 0
            delay = 250 if self._stable_checks < 4 else 1000
            self.after(delay, self._watch)
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
        width = self.canvas.winfo_width() or self.canvas.winfo_reqwidth()
        key = (want, width, self._fill, self._border, self._radius)
        if key == self._last_key:
            return                  # 尺寸/配色都没变 → 不重画（省 CPU，避免闪烁）
        self._last_key = key
        if self.canvas.winfo_reqheight() != want:
            self.canvas.configure(height=want)
        if self._fit_width:
            want_w = self.inner.winfo_reqwidth() + 2 * self._pad
            if want_w > 1 and self.canvas.winfo_reqwidth() != want_w:
                self.canvas.configure(width=want_w)
        self._redraw(width, want)

    def force_redraw(self) -> None:
        """内容变化后主动重画一次（例如提示文案变长、状态换色）。"""
        self._last_key = None
        self._fit_height()

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
        # 优先用超采样渲染的背景图（曲线抗锯齿、直线仍是硬 1px）；
        # Pillow 不可用或渲染失败时退回矢量画法（效果略毛但功能一致）
        photo = render_round_rect(
            cv, w, h, self._radius, self._fill, self._border,
            shadow=("#E7EAEF" if self._shadow else None))
        if photo is not None:
            self._photo = photo                     # 必须持有引用，否则图片会被回收
            self._shapes.append(cv.create_image(0, 0, image=photo, anchor="nw"))
        else:
            if self._shadow:
                self._shapes.extend(draw_round_rect(cv, 1, 2, w - 1, h - 1, self._radius, "#E7EAEF"))
            self._shapes.extend(draw_round_rect(cv, 1, 1, w - 2, h - 2, self._radius,
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
                 pad=PAD_CARD, shadow=True, fit_width=False):
        super().__init__(parent, fill=fill, border=COLOR_BORDER, radius=radius, pad=pad,
                         page_bg=page_bg, shadow=shadow, fit_width=fit_width)
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
        height, padx = S(height), S(padx)
        super().__init__(parent, width=S(width) if width else S(60), height=height,
                         bg=page_bg, highlightthickness=0, bd=0)
        self._text = text
        self._command = command
        self._kind = kind
        self._radius = S(radius)
        # 文字宽度用**控件自己的 Tcl 解释器**量（font measure）。
        # 不能用 tkinter.font.Font(...)：它走"默认 Tk 解释器"，在设置窗口这种
        # 独立线程 + 多窗口场景下会抛 "main thread is not in main loop"
        # （2026-09-29 模拟 150% 缩放时踩到）。
        if width is None:
            try:
                need = int(self.tk.call("font", "measure", font, text)) + 2 * padx
            except Exception:  # noqa: BLE001
                need = S(72)
            super().configure(width=max(need, S(40)))
        self._font = font
        self._on = on
        self._hover, self._pressed, self._disabled = False, False, False
        self._shapes: list = []
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        bind_photo_cleanup(self)
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
        # 坐标取整数像素：1px 描边落在整像素行/列上才锐利（半像素会让线摊成两层灰）
        photo = render_round_rect(self, w, h, self._radius, fill, border)
        if photo is not None:
            self._photo = photo
            self._shapes.append(self.create_image(0, 0, image=photo, anchor="nw"))
        else:
            self._shapes.extend(draw_round_rect(self, 1, 1, w - 2, h - 2, self._radius,
                                                fill, border))
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
            btn.pack(side="left", padx=(0, S(8)))
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
    """iOS 风格开关：点击切换 BooleanVar，并可触发回调。

    清晰度（2026-09-29 改进）：真圆弧拼出的胶囊 + 白色旋钮带 1px 描边，
    且开/关两态**颜色与旋钮位置都明显不同**，一眼能看出状态。
    """

    # 开：主色；关：中性灰（比原来更深一点，避免"灰得发白"看不清边界）
    COLOR_ON = COLOR_PRIMARY
    COLOR_ON_HOVER = COLOR_PRIMARY_HOVER
    COLOR_OFF = "#C3CAD6"
    COLOR_OFF_HOVER = "#B4BCC9"
    COLOR_DISABLED_ON = "#A8C6F7"
    COLOR_DISABLED_OFF = "#DFE3E9"
    KNOB_BORDER = "#E2E5EA"

    def __init__(self, parent, variable, command=None, page_bg=COLOR_CARD,
                 width=46, height=26):
        w, h = S(width), S(height)
        if h % 2:                     # 高度取偶数：胶囊半径 = (h-2)/2 才是整数
            h += 1
        super().__init__(parent, width=w, height=h, bg=page_bg,
                         highlightthickness=0, bd=0)
        self._var = variable
        self._command = command
        self._disabled = False
        self._hover = False
        # 注意：不能叫 _w —— tkinter 用 _w 保存控件的 Tcl 路径名，覆盖它会让所有
        # Tk 调用报 invalid command name（2026-09-29 踩过）
        self._box_w, self._box_h = w, h
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self._var.trace_add("write", lambda *_: self._draw())
        bind_photo_cleanup(self)
        self._draw()

    def _on_enter(self, _e=None) -> None:
        self._hover = True
        if not self._disabled:
            self.configure(cursor="hand2")
        self._draw()

    def _on_leave(self, _e=None) -> None:
        self._hover = False
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

    def _track_color(self, on: bool) -> str:
        if self._disabled:
            return self.COLOR_DISABLED_ON if on else self.COLOR_DISABLED_OFF
        if on:
            return self.COLOR_ON_HOVER if self._hover else self.COLOR_ON
        return self.COLOR_OFF_HOVER if self._hover else self.COLOR_OFF

    def _draw(self) -> None:
        self.delete("all")
        on = bool(self._var.get())
        w, h = self._box_w, self._box_h
        inset = S(3)
        knob_d = h - 2 - 2 * inset        # 旋钮直径（整数）
        if knob_d % 2:
            knob_d -= 1
        knob_r = knob_d / 2
        cx = (w - 1 - inset - knob_r) if on else (1 + inset + knob_r)
        cy = h / 2
        # 记录本次绘制状态：两种渲染路径下自检脚本都能查到（不依赖画布 item 类型）
        track_color = self._track_color(on)
        self.track_color = track_color
        self.knob_center_x = cx
        self.knob_fill, self.knob_outline = "#FFFFFF", self.KNOB_BORDER
        # 轨道 + 旋钮一起超采样渲染（胶囊两端与圆旋钮都获得抗锯齿边缘）
        photo = render_toggle(self, w, h, track_color, "#FFFFFF", self.KNOB_BORDER,
                             int(cx - knob_r), int(cy - knob_r),
                             int(cx + knob_r), int(cy + knob_r), (h - 2) // 2)
        if photo is not None:
            self._photo = photo
            self.create_image(0, 0, image=photo, anchor="nw")
        else:
            draw_round_rect(self, 1, 1, w - 2, h - 2, (h - 2) // 2, self._track_color(on))
            self.create_oval(int(cx - knob_r), int(cy - knob_r),
                             int(cx + knob_r), int(cy + knob_r),
                             fill="#FFFFFF", outline=self.KNOB_BORDER, width=1)


class IconBadge(tk.Canvas):
    """图标徽标：浅色圆角底 + 简单线条图标（对应参考图卡片标题左侧的小图标）。

    kind: models / lang / speed / memory / hotkey / bell
    """

    def __init__(self, parent, kind, size=26, page_bg=COLOR_CARD,
                 tint=COLOR_PRIMARY_SOFT, color=COLOR_PRIMARY_HOVER):
        s = S(size)
        super().__init__(parent, width=s, height=s, bg=page_bg,
                         highlightthickness=0, bd=0)
        self._size = s
        # 记录设计笔画宽度：矢量与超采样两条路径共用，自检脚本据此断言"笔画 ≥2px"
        self.stroke_width = max(2, int(round(2.0 * (s / 26.0))))
        bind_photo_cleanup(self)
        photo = render_icon_badge(self, kind, s, tint, color, S(7))
        if photo is not None:
            self._photo = photo
            self.create_image(0, 0, image=photo, anchor="nw")
        else:
            draw_round_rect(self, 1, 1, s - 2, s - 2, S(7), tint)
            self._draw_icon(kind, s, color)

    def _draw_icon(self, kind: str, s: int, color: str) -> None:
        """图标按尺寸等比绘制（u = 单位长度），高 DPI 下自动放大不变形。"""
        c = color
        u = s / 26.0                      # 设计基准：26px
        w = max(2, int(round(2.0 * u)))   # 细线在浅底上发虚，笔画至少 2px
        cx = cy = s / 2

        def px(v):
            return int(round(v * u))       # 整数像素：细线/小图标不糊

        if kind == "models":                       # 下载箭头 + 托盘
            self.create_line(cx, cy - px(7), cx, cy + px(2),
                             fill=c, width=w, capstyle="round")
            self.create_line(cx - px(4), cy - px(1), cx, cy + px(3), cx + px(4), cy - px(1),
                             fill=c, width=w, capstyle="round", joinstyle="round")
            self.create_line(cx - px(6), cy + px(6), cx + px(6), cy + px(6),
                             fill=c, width=w, capstyle="round")
        elif kind == "lang":                       # 地球
            self.create_oval(cx - px(7), cy - px(7), cx + px(7), cy + px(7), outline=c, width=w)
            self.create_line(cx - px(7), cy, cx + px(7), cy, fill=c, width=w)
            self.create_oval(cx - px(3.2), cy - px(7), cx + px(3.2), cy + px(7),
                             outline=c, width=w)
        elif kind == "speed":                      # 仪表盘
            self.create_arc(cx - px(7), cy - px(6), cx + px(7), cy + px(8), start=20, extent=140,
                            style="arc", outline=c, width=w)
            self.create_line(cx, cy + px(2), cx + px(3.5), cy - px(2),
                             fill=c, width=w, capstyle="round")
        elif kind == "memory":                     # 芯片
            self.create_rectangle(cx - px(5), cy - px(5), cx + px(5), cy + px(5),
                                  outline=c, width=w)
            for d in (-3, 0, 3):
                self.create_line(cx - px(8), cy + px(d), cx - px(5), cy + px(d), fill=c, width=w)
                self.create_line(cx + px(5), cy + px(d), cx + px(8), cy + px(d), fill=c, width=w)
        elif kind == "hotkey":                     # 键盘
            self.create_rectangle(cx - px(8), cy - px(5), cx + px(8), cy + px(5),
                                  outline=c, width=w)
            for d in (-4, 0, 4):
                self.create_line(cx + px(d), cy - px(1.5), cx + px(d), cy - px(1.5),
                                 fill=c, width=w + 1)
            self.create_line(cx - px(4), cy + px(3), cx + px(4), cy + px(3), fill=c, width=w)
        elif kind == "bell":                       # 铃铛（通知）
            self.create_arc(cx - px(6), cy - px(7), cx + px(6), cy + px(5), start=0, extent=180,
                            style="arc", outline=c, width=w)
            self.create_line(cx - px(6), cy - px(1), cx - px(6), cy + px(4), fill=c, width=w)
            self.create_line(cx + px(6), cy - px(1), cx + px(6), cy + px(4), fill=c, width=w)
            self.create_line(cx - px(7), cy + px(4), cx + px(7), cy + px(4),
                             fill=c, width=w, capstyle="round")
            self.create_oval(cx - px(1.6), cy + px(6), cx + px(1.6), cy + px(9),
                             fill=c, outline="")
