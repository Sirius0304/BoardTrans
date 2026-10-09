# -*- coding: utf-8 -*-
"""生成"文字渲染前后对比"交付图：同一设置窗口，原生 ClearType vs 灰度超采样。

用法：python tools/make_text_showcase.py
产物：docs/文字渲染对比.png
"""
import os
import sys
import time

sys.path.insert(0, r"F:\shunyi")

from PIL import Image, ImageDraw, ImageFont, ImageGrab   # noqa: E402

from app import config                                   # noqa: E402
import app.settings_window as sw                         # noqa: E402
from app import ui_kit as ui                             # noqa: E402
from app.service import HotkeyService                    # noqa: E402

sw.dialogs.ask = lambda *a, **k: True
sw.dialogs.tell = lambda *a, **k: None
original = dict(config.load())
_real_font_file = ui.font_file


def shot_window(disable_text_render: bool):
    """开一次设置窗口并截图；disable_text_render=True 时退回原生 Tk 文字。"""
    ui.font_file = (lambda family, bold=False: None) if disable_text_render else _real_font_file
    ui._TEXT_PNG_CACHE.clear()
    service = HotkeyService(notify=lambda t, m: None)
    settings = sw.SettingsWindow(service)
    holder = {}

    def collect():
        root = settings.root
        root.attributes("-topmost", True)
        root.lift()
        root.update_idletasks()
        holder["origin"] = (root.winfo_rootx(), root.winfo_rooty())
        holder["size"] = (root.winfo_width(), root.winfo_height())
        # 找几个有代表性的文字控件做特写
        marks = {}

        def walk(w):
            yield w
            for c in w.winfo_children():
                yield from walk(c)

        for w in walk(root):
            text = ""
            try:
                text = str(w.cget("text"))
            except Exception:  # noqa: BLE001
                continue
            if not text:
                continue
            if text.startswith("键译 设置") and "header" not in marks:
                marks["header"] = (w.winfo_rootx(), w.winfo_rooty(),
                                   w.winfo_width(), w.winfo_height())
            elif "当前档位实测响应" in text and "note" not in marks:
                marks["note"] = (w.winfo_rootx(), w.winfo_rooty(),
                                 w.winfo_width(), min(w.winfo_height(), 44))
            elif "模型存放位置" in text and "path" not in marks:
                marks["path"] = (w.winfo_rootx(), w.winfo_rooty(),
                                 w.winfo_width(), min(w.winfo_height(), 24))
        holder["marks"] = marks

    settings.open()
    time.sleep(2.5)
    settings.run_in_ui(collect)
    time.sleep(1.0)
    ox, oy = holder["origin"]
    img = ImageGrab.grab(bbox=(ox, oy, ox + holder["size"][0], oy + holder["size"][1]))
    settings._queue.put(("call", lambda: settings.root.attributes("-topmost", False)))
    settings.shutdown()
    time.sleep(0.5)
    return img, holder["marks"], (ox, oy)


before, marks_a, origin_a = shot_window(True)
after, marks_b, origin_b = shot_window(False)
config.update(**{k: original[k] for k in original})
ui.font_file = _real_font_file

out = r"F:\shunyi\docs\文字渲染对比.png"
_diag = os.path.join(os.path.dirname(out), "_diag")
os.makedirs(_diag, exist_ok=True)
before.save(os.path.join(_diag, "设置窗口-原生文字.png"))
after.save(os.path.join(_diag, "设置窗口-超采样文字.png"))

# 拼图：按"同一处文字、左原生右超采样"成对放大对比
ZOOM = 5
MAXW = 132                     # 特写最多截这么宽（再宽就看不清差别了）
tiles = []
for key, label in (("header", "卡片标题"), ("path", "说明文字（路径/数字/英文）"),
                   ("note", "多行说明（自动换行）")):
    for tag, marks, origin in (("原生 ClearType", marks_a, origin_a),
                               ("超采样灰度（新）", marks_b, origin_b)):
        box = marks.get(key)
        if not box:
            continue
        x, y, w, h = box
        img = before if tag.startswith("原生") else after
        crop = img.crop((x - origin[0], y - origin[1],
                         x - origin[0] + min(w, MAXW), y - origin[1] + h)).convert("RGB")
        crop = crop.resize((crop.width * ZOOM, crop.height * ZOOM), Image.NEAREST)
        tiles.append((f"{label} · {tag}", crop))

cols = 2
pad, label_h = 20, 34
cw = max(t.width for _n, t in tiles) + pad * 2
ch = max(t.height for _n, t in tiles) + pad * 2 + label_h
rows = (len(tiles) + cols - 1) // cols
canvas = Image.new("RGB", (cw * cols + pad, ch * rows + pad + 40), "#F7F8FA")
draw = ImageDraw.Draw(canvas)
try:
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 20, index=1)
except Exception:  # noqa: BLE001
    font = ImageFont.load_default()
draw.text((pad, 12), f"文字渲染对比：同一设置窗口，{ZOOM} 倍放大（左：原生 ClearType 亚像素，"
                     "笔画带彩色虚边；右：灰度超采样降采样，干净无彩边）",
          fill="#1F2937", font=font)
for idx, (name, crop) in enumerate(tiles):
    r, c = divmod(idx, cols)
    x = pad + c * cw
    y = 40 + pad + r * ch
    draw.text((x + 4, y + 2), name, fill="#374151", font=font)
    canvas.paste(crop, (x + pad, y + label_h))
    draw.rectangle([x + pad - 1, y + label_h - 1,
                    x + pad + crop.width, y + label_h + crop.height], outline="#C9D0DA")
canvas.save(out)
print("已保存", out, canvas.size)
print("特写块：", [n for n, _c in tiles])
