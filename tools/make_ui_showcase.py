# -*- coding: utf-8 -*-
"""生成组件清晰效果交付图：截取窗口 → 裁出关键组件 → 6 倍放大拼成一张对比图。"""
import sys
import time

sys.path.insert(0, r"F:\shunyi")

from PIL import Image, ImageDraw, ImageFont, ImageGrab   # noqa: E402

from app import config                                   # noqa: E402
import app.settings_window as sw                         # noqa: E402
from app import ui_kit as ui                             # noqa: E402
from app.service import HotkeyService                    # noqa: E402


class AutoAnswer:
    @staticmethod
    def askokcancel(*a, **k):
        return True

    @staticmethod
    def showinfo(*a, **k):
        return None

    @staticmethod
    def showerror(*a, **k):
        return None

    @staticmethod
    def showwarning(*a, **k):
        return None


sw.messagebox = AutoAnswer
original = dict(config.load())
service = HotkeyService(notify=lambda t, m: None)
settings = sw.SettingsWindow(service)
shot = {}


def collect():
    root = settings.root
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    root.update_idletasks()
    regions = {}

    def walk(w):
        yield w
        for c in w.winfo_children():
            yield from walk(c)

    def clip(w, cw, ch):
        return (w.winfo_rootx(), w.winfo_rooty(), cw, ch)

    for w in walk(root):
        if isinstance(w, ui.RoundedCard):
            regions.setdefault("卡片圆角 + 1px 描边（局部 6×）", clip(w, 110, 66))
        elif isinstance(w, ui.RoundedPanel) and regions.get("提示框（圆角 + 描边）") is None:
            if w.winfo_width() > 300:
                regions["提示框（圆角 + 描边）"] = clip(w, 150, 42)
        elif isinstance(w, ui.RoundedButton) and w.cget("text") == "保存":
            regions.setdefault("主按钮（蓝底白字）", clip(w, w.winfo_width(), w.winfo_height()))
        elif isinstance(w, ui.ToggleSwitch):
            regions.setdefault("开关（轨道 + 旋钮）", clip(w, w.winfo_width(), w.winfo_height()))
        elif isinstance(w, ui.IconBadge):
            regions.setdefault("图标徽标（2px 笔画）", clip(w, w.winfo_width() + 4,
                                                         w.winfo_height() + 4))
    shot["regions"] = regions
    shot["origin"] = (root.winfo_rootx(), root.winfo_rooty())
    shot["size"] = (root.winfo_width(), root.winfo_height())


settings.open()
time.sleep(2.5)
settings.run_in_ui(collect)
time.sleep(1.0)
ox, oy = shot["origin"]
img = ImageGrab.grab(bbox=(ox, oy, ox + shot["size"][0], oy + shot["size"][1]))
settings._queue.put(("call", lambda: settings.root.attributes("-topmost", False)))
settings.shutdown()
time.sleep(0.4)
config.update(**{k: original[k] for k in original})

# 裁剪 + 6 倍放大 + 拼图
tiles = []
for name, (x, y, w, h) in shot["regions"].items():
    crop = img.crop((x - ox, y - oy, x - ox + w, y - oy + h)).convert("RGB")
    crop = crop.resize((crop.width * 6, crop.height * 6), Image.NEAREST)
    tiles.append((name, crop))

cols, pad, label_h = 2, 24, 38
cw = max(c.width for _n, c in tiles) + pad * 2
ch = max(c.height for _n, c in tiles) + pad * 2 + label_h
rows_n = (len(tiles) + cols - 1) // cols
canvas = Image.new("RGB", (cw * cols + pad, ch * rows_n + pad), "#F7F8FA")
draw = ImageDraw.Draw(canvas)
try:
    # 标签必须用支持中文的字体，否则 PIL 默认点阵字体会显示成方框
    label_font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 22)
except Exception:  # noqa: BLE001
    label_font = ImageFont.load_default()
for idx, (name, crop) in enumerate(tiles[:6]):
    r, c = divmod(idx, cols)
    x = pad + c * cw
    y = pad + r * ch
    draw.text((x + 4, y + 4), name, fill="#1F2937", font=label_font)
    canvas.paste(crop, (x + pad, y + label_h))
    draw.rectangle([x + pad - 1, y + label_h - 1, x + pad + crop.width, y + label_h + crop.height],
                   outline="#C9D0DA")
canvas.save(r"F:\shunyi\docs\组件清晰效果.png")
print("已保存 docs/组件清晰效果.png", canvas.size)
print("组件：", [n for n, _c in tiles])
