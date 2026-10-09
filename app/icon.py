# -*- coding: utf-8 -*-
"""
键译 - 图标

用 Pillow 现场绘制托盘图标，不依赖任何外部图片文件
（打包成单文件 exe 时最省事）。字体优先用系统自带的中文字体。
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]


def _load_font(size: int):
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)   # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def make_icon(size: int = 64, enabled: bool = True, label: str = "译") -> Image.Image:
    """绘制托盘图标：圆形底 + 中文字。enabled=False 时画成灰色表示已关闭。"""
    scale = 4
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if enabled:
        top = (46, 107, 230)      # 蓝
        bottom = (23, 66, 160)
    else:
        top = (150, 155, 165)     # 灰
        bottom = (100, 105, 115)

    # 垂直渐变
    for y in range(big):
        ratio = y / max(big - 1, 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3)) + (255,)
        draw.line([(0, y), (big, y)], fill=color)

    # 圆形遮罩
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    img.putalpha(mask)

    font = _load_font(int(big * 0.56))
    try:
        left, top_, right, bottom_ = draw.textbbox((0, 0), label, font=font)
        draw.text(
            ((big - (right - left)) / 2 - left, (big - (bottom_ - top_)) / 2 - top_),
            label,
            font=font,
            fill=(255, 255, 255, 255),
        )
    except Exception:  # noqa: BLE001
        pass

    return img.resize((size, size), Image.LANCZOS)


ICON_ENABLED = make_icon(64, True)
ICON_DISABLED = make_icon(64, False)
