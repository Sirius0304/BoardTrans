# -*- coding: utf-8 -*-
"""文字渲染取证（第 1 步）：先看系统文字平滑设置与 Tk 实际渲染出来的像素。

结论要回答两个问题：
  1) 当前设置窗口的小字，究竟有没有抗锯齿？（直接截屏统计灰度分布）
  2) 系统的 ClearType 开关是什么状态？（决定 Tk 走的是哪条渲染路径）
"""
import ctypes
import sys
from ctypes import wintypes

sys.path.insert(0, r"F:\shunyi")

from PIL import Image, ImageDraw, ImageFont, ImageGrab   # noqa: E402

SPI_GETFONTSMOOTHING = 0x004A
SPI_GETFONTSMOOTHINGTYPE = 0x200B
SPI_GETFONTSMOOTHINGCONTRAST = 0x200C
SPI_GETFONTSMOOTHINGORIENTATION = 0x2013
FE_FONTSMOOTHINGSTANDARD = 1
FE_FONTSMOOTHINGCLEARTYPE = 2


def probe_system_text():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    out = {}

    def qpi(flag, kind=wintypes.UINT):
        val = kind()
        ok = user32.SystemParametersInfoW(flag, 0, ctypes.byref(val), 0)
        return val.value if ok else None

    out["smoothing_on"] = qpi(SPI_GETFONTSMOOTHING, wintypes.BOOL)
    t = qpi(SPI_GETFONTSMOOTHINGTYPE)
    out["smoothing_type"] = {1: "standard(灰度)", 2: "cleartype(亚像素)"}.get(t, t)
    out["contrast"] = qpi(SPI_GETFONTSMOOTHINGCONTRAST)
    o = qpi(SPI_GETFONTSMOOTHINGORIENTATION)
    out["orientation"] = {0: "水平(默认)", 1: "RGB", 2: "BGR"}.get(o, o)

    # 当前进程的 DPI
    try:
        shcore = ctypes.WinDLL("shcore")
        dpi = wintypes.UINT()
        shcore.GetDpiForMonitor(ctypes.WinDLL("user32").GetDC(0), 0,
                                ctypes.byref(dpi), ctypes.byref(wintypes.UINT()))
        out["dpi"] = dpi.value
    except Exception:  # noqa: BLE001
        pass
    try:
        hdc = ctypes.WinDLL("user32").GetDC(0)
        gdi = ctypes.WinDLL("gdi32")
        out["device_px_per_inch"] = gdi.GetDeviceCaps(hdc, 88)   # LOGPIXELSX
        out["screen_w"] = gdi.GetDeviceCaps(hdc, 8)
        out["screen_h"] = gdi.GetDeviceCaps(hdc, 10)
        ctypes.WinDLL("user32").ReleaseDC(0, hdc)
    except Exception:  # noqa: BLE001
        pass
    return out


def analyze(img, label):
    """统计灰度分布：中间灰阶（抗锯齿）比例、纯黑比例、纯白比例、彩色像素比例。"""
    g = img.convert("L")
    px = list(g.getdata())
    total = len(px)
    darkest = min(px)
    mid = sum(1 for v in px if darkest + 12 < v < 244)
    ink = sum(1 for v in px if v < 200)
    pure_black = sum(1 for v in px if v <= darkest + 4)
    rgb = img.convert("RGB")
    colored = 0
    for r, gg, b in rgb.getdata():
        if max(r, gg, b) - min(r, gg, b) > 18:      # 明显偏色 = 亚像素渲染痕迹
            colored += 1
    print(f"  {label}: 最小灰度={darkest} 墨点={ink} 中间灰阶={mid} "
          f"({mid / max(1, ink) * 100:.1f}% of ink) 纯黑={pure_black} "
          f"彩色像素={colored} ({colored / total * 100:.2f}%)")


if __name__ == "__main__":
    print("=== 系统文字平滑设置 ===")
    for k, v in probe_system_text().items():
        print(f"  {k} = {v}")
