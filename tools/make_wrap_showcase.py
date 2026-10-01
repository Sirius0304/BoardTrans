# -*- coding: utf-8 -*-
"""生成「快捷键提示换行修复」前后对比图。

左：修复前的纯像素贪心断行（标点"（"被挤到下一行开头）
右：修复后加了中文避头点（标点跟着上一行走）
产物：docs/换行修复对比.png
"""
import sys

sys.path.insert(0, r"F:\shunyi")

from PIL import Image, ImageDraw, ImageFont   # noqa: E402

from app import ui_kit as ui                  # noqa: E402

TEXT = ("这个组合不行：必须至少包含一个 Ctrl / Alt / Shift"
        "（不能只用单个键，否则会干扰你正常打字）。")
LIMIT = 277          # 与真实窗口里该 Label 的可用宽度一致
PX = 12              # ShunYiSmall = 9pt @96dpi = 12px
SS = 3
FONT_FILE = r"C:\Windows\Fonts\msyh.ttc"
FG = (220, 38, 38)   # COLOR_ERR（这条提示是错误态）


def old_wrap(text, limit, measure):
    """修复前的实现：纯像素贪心，标点与普通字一视同仁。"""
    lines, cur = [], ""
    for token in ui._split_tokens(text):
        if not cur:
            cur = token
        elif measure(cur + token) <= limit:
            cur += token
        else:
            lines.append(cur.rstrip())
            cur = token.lstrip() if token.strip() else ""
    lines.append(cur.rstrip())
    return lines


font = ImageFont.truetype(FONT_FILE, PX * SS, index=1)
measure = lambda s: int(font.getlength(s) / SS)          # noqa: E731

groups = [("修复前（纯像素贪心）", old_wrap(TEXT, LIMIT, measure)),
          ("修复后（加中文避头点）", ui.wrap_lines(TEXT, LIMIT, measure))]

tiles = []
for title, lines in groups:
    w = max(measure(ln) for ln in lines) + 4
    h = (font.getmetrics()[0] + font.getmetrics()[1]) * len(lines) // SS + 8
    img = Image.new("RGBA", (w * SS, h * SS), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    lh = font.getmetrics()[0] + font.getmetrics()[1]
    for i, ln in enumerate(lines):
        draw.text((2 * SS, (4 + i * lh // SS) * SS), ln, font=font, fill=FG + (255,))
    tiles.append((title, img.resize((w, h), Image.BOX), lines))

Z = 3
pad, label_h, gap = 20, 34, 16
w0 = max(t.width for _t, t, _l in tiles) * Z + pad * 2 + 40
need = 0
for _t, _img, _lines in tiles:
    need += label_h + _img.height * Z + 10 + 26 * len(_lines) + gap
h0 = need + pad * 2
canvas = Image.new("RGB", (w0, h0), "#F7F8FA")
draw = ImageDraw.Draw(canvas)
try:
    lab = ImageFont.truetype(FONT_FILE, 20, index=1)
except Exception:  # noqa: BLE001
    lab = ImageFont.load_default()

y = pad
for title, img, lines in tiles:
    draw.text((pad, y), title, fill="#374151", font=lab)
    y += label_h
    big = img.resize((img.width * Z, img.height * Z), Image.NEAREST)
    canvas.paste(big, (pad, y))
    draw.rectangle([pad - 1, y - 1, pad + big.width, y + big.height],
                   outline="#C9D0DA")
    y += big.height + 10
    for i, ln in enumerate(lines):
        tag = f"第 {i + 1} 行 →  {ln[:26]}{'…' if len(ln) > 26 else ''}"
        draw.text((pad, y), tag, fill="#6B7280", font=lab)
        y += 26
    y += gap - 10

canvas.save(r"F:\shunyi\docs\换行修复对比.png")
print("已保存 docs/换行修复对比.png", canvas.size)
for title, _img, lines in tiles:
    print(f"{title}: {len(lines)} 行")
    for ln in lines:
        print("   |", ln)
