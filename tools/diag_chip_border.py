# -*- coding: utf-8 -*-
"""胶囊/卡片描边被内部 Frame 遮挡的像素取证。

要回答的问题：状态胶囊（radius=999 半圆端）的**外描边**有没有被
内部的 inner Frame 盖住、导致描边不完整。

关键几何：`_CanvasBox` 把内容 Frame（inner）放在 canvas 的 (pad, pad) 处，
而canvas 上那张"圆角矩形图"（底色+描边）被 `tag_lower` 压到 inner **之下**。
inner 是**直角**矩形、底色又与胶囊同色 → 在**两端圆弧处**，
inner 会把圆角图上的描边弧线盖掉，只剩同色底色，描边看起来"断掉/不清晰"。

判据（按圆角矩形几何精确计算，不靠"跳过几个像素"的粗略假设）：
  对每个像素中心，算它到圆角矩形边界的距离，落在 [0,1) 的就是 1px 描边环。
  落在环上却不是该 tone 的描边色 → 记为"被覆盖"，并统计它变成了什么颜色。

**每个状态用各自的描边色**（info=#BFDBFE / ok=#BBF7D0 / plain=#D9DEE5）——
  第一版写死 plain 的色去比，另两个状态几百个像素全被误报。

退出码：0 = 描边完整；1 = 有被覆盖。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import ui_kit as ui                    # noqa: E402
import app.settings_window as sw                 # noqa: E402
from app.service import HotkeyService           # noqa: E402

try:
    from PIL import Image, ImageGrab
except ImportError:
    print("需要 Pillow")
    sys.exit(2)

ui.enable_dpi_awareness()


def rgb(hexstr):
    h = hexstr.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def near(a, b, tol=10):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


FILL = rgb(ui.COLOR_HOVER)
PAGE = rgb(ui.COLOR_PAGE)
CARD = rgb(sw.COLOR_CARD)

# (状态名, 文案, 前景色, tone描边色, tone 底色)
STATES = [
    ("plain 未下载", "状态：未下载", sw.COLOR_SUBTEXT,
     ui.COLOR_BORDER, ui.COLOR_HOVER),
    ("info  下载中", "状态：正在下载 42%　（30.0 / 71.0 MB）", sw.COLOR_PRIMARY,
     ui.COLOR_PRIMARY_SOFT_BORDER, ui.COLOR_PRIMARY_SOFT),
    ("ok    已下载", "状态：已下载（占用 82 MB）", sw.COLOR_OK,
     ui.COLOR_OK_BORDER, ui.COLOR_OK_SOFT),
]

service = HotkeyService(notify=lambda t, m: None)
win = sw.SettingsWindow(service)
win.open()
time.sleep(2.2)
chip = win._model_ui["zh_en"]["status"]

bad = []
shots = []
for name, text, fg, border_hex, fill_hex in STATES:
    B, F = rgb(border_hex), rgb(fill_hex)
    chip.configure(text=text, fg=fg)
    win.root.update_idletasks()
    win.root.update()
    time.sleep(0.35)
    win.root.update()

    x, y = chip.winfo_rootx(), chip.winfo_rooty()
    wpx, hpx = chip.winfo_width(), chip.winfo_height()
    im = ImageGrab.grab((x, y, x + wpx, y + hpx)).convert("RGB")
    shots.append((name, im))

    r = min(999.0, (hpx - 1) / 2.0)      # 胶囊：半圆端
    straight_bad = 0                      # 直边区被盖
    arc_bad = 0                           # 圆弧区被盖
    kinds = {}
    for j in range(hpx):
        for i in range(wpx):
            cx, cy = i + 0.5, j + 0.5
            dx, dy = min(cx, wpx - cx), min(cy, hpx - cy)
            if dx < r and dy < r:
                qx, qy = r - dx, r - dy
                dist = r - (qx * qx + qy * qy) ** 0.5
                in_arc = True
            else:
                dist = min(dx, dy)
                in_arc = False
            if not (0.0 <= dist < 1.0):
                continue
            p = im.getpixel((i, j))
            if near(p, B):
                continue
            # 判据（只信这一版，前两版试过都误报，别再改）：
            #  1px 描边经 3x BOX 降采样会与底色混合成过渡色（弧线 45 度处尤其明显），
            #  所以不能要求"像素精确等于描边纯色"。判据是**描边是否可见**：
            #  离描边色比离底色更近 -> 描边有贡献（纯描边或抗锯齿混合），正常；
            #  离底色更近 -> 这里只剩底色，描边确实被内容层盖掉了。
            # 踩过的坑：(1)"跳过两端几个像素"的粗判 -> 把圆弧当直边，28 个假问题；
            #        (2)"描边到底色连线上的 t"判 -> 负值说明方向算反，误报上百个。
            d_border = sum((a - b) ** 2 for a, b in zip(p, B)) ** 0.5
            d_fill = sum((a - b) ** 2 for a, b in zip(p, F)) ** 0.5
            if d_border < d_fill:
                continue
            if in_arc:
                arc_bad += 1
            else:
                straight_bad += 1
            kind = ("底色" if near(p, F) else
                    "页面色" if near(p, PAGE) else
                    "卡片白" if near(p, CARD) else "其他")
            kinds[kind] = kinds.get(kind, 0) + 1

    total = arc_bad + straight_bad
    print(f"  {name:14s} {wpx:3d}x{hpx:2d}px  描边环异常：直边 {straight_bad:3d} / "
          f"圆弧 {arc_bad:3d}   被覆盖成 {kinds or '无'}")
    if total:
        bad.append((name, straight_bad, arc_bad))

win.shutdown()

# 放大截图（4倍最近邻，角部能看清描边有没有被啃掉）
try:
    scale, pad = 4, 12
    W = max(im.width for _n, im in shots) * scale + pad * 2
    H = sum(im.height * scale + pad for _n, im in shots) + pad
    board = Image.new("RGB", (W, H), ui.COLOR_PAGE)
    yy = pad
    for _n, im in shots:
        big = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
        board.paste(big, (pad, yy))
        yy += big.height + pad
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "胶囊描边取证.png")
    board.save(out)
    print(f"\n  放大截图: {out}")
except Exception as exc:  # noqa: BLE001
    print(f"  截图失败: {exc}")

print()
if bad:
    print(f"描边存在被覆盖：{len(bad)} 个状态 -> "
          f"{[(n, f'直边{s}/弧{a}') for n, s, a in bad]}")
    sys.exit(1)
print("描边完整，未见遮挡")
sys.exit(0)