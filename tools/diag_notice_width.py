# -*- coding: utf-8 -*-
"""看"保存成功"长提示的实际排版（加宽前后都是几行、有没有拥挤）。

产物：docs/_diag/保存提示-宽度.png
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

LONG = ("设置已保存并立即生效，无需重启软件。\n"
        "提示：新快捷键已经生效。如果按下去没反应，说明这个组合被别的软件抢占了"
        "（这类冲突无法自动检测），换一个组合重新设置即可。")

sw.dialogs.ask = lambda *a, **k: True
sw.dialogs.tell = lambda *a, **k: None
original = dict(config.load())
service = HotkeyService(notify=lambda t, m: None)
settings = sw.SettingsWindow(service)
BOX = {}


def shoot(wrap):
    root = settings.root
    root.attributes("-topmost", True)
    root.lift()
    root.update_idletasks()
    settings._show_notice(LONG, "ok")
    for _ in range(6):
        root.update_idletasks()
        root.update()
        time.sleep(0.08)
    # 注意：_show_notice 会按窗口宽度**重新计算**折行宽度，所以这里必须在它之后覆盖，
    # 才能对比"不同折行宽度下的排版"。
    settings._notice_label.configure(wraplength=ui.S(wrap))
    root.update_idletasks()
    settings._notice_box.force_redraw()      # _show_notice 里也会做这两步
    for _ in range(4):
        root.update_idletasks()
        root.update()
        time.sleep(0.06)
    lbl = settings._notice_label
    box = settings._notice_box
    BOX[wrap] = {
        "geo": (box.winfo_rootx(), box.winfo_rooty(),
                box.winfo_width(), box.winfo_height()),
        "lines": ui.wrap_lines(LONG, int(lbl.cget("wraplength")), lbl._measure),
        "wrap": int(lbl.cget("wraplength")),
    }


settings.open()
time.sleep(2.5)
tiles = []
for wrap in (430, 470, 560):
    settings.run_in_ui(lambda w=wrap: shoot(w))
    time.sleep(0.6)
    info = BOX[wrap]
    x, y, w, h = info["geo"]
    shot = ImageGrab.grab(bbox=(x - 10, y - 10, x + w + 10, y + h + 10))
    tiles.append((f"折行宽度 {info['wrap']}px → {len(info['lines'])} 行", shot))
    print(f"wrap={info['wrap']}: {len(info['lines'])} 行, 框 {w}×{h}")
    for ln in info["lines"]:
        print("    |", ln)

settings._queue.put(("call", lambda: settings.root.attributes("-topmost", False)))
settings.shutdown()
time.sleep(0.4)
config.update(**{k: original[k] for k in original})

Z = 2
pad, lab = 16, 28
W = max(t.width for _n, t in tiles) * Z + pad * 2
H = sum(t.height for _n, t in tiles) * Z + (pad + lab) * len(tiles) + pad
canvas = Image.new("RGB", (W, H), "#EDEFF2")
d = ImageDraw.Draw(canvas)
try:
    f = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 18, index=1)
except Exception:  # noqa: BLE001
    f = ImageFont.load_default()
y = pad
for name, img in tiles:
    d.text((pad, y), name, fill="#374151", font=f)
    y += lab
    big = img.resize((img.width * Z, img.height * Z), Image.NEAREST)
    canvas.paste(big, (pad, y))
    d.rectangle([pad - 1, y - 1, pad + big.width, y + big.height], outline="#C9D0DA")
    y += big.height + pad
os.makedirs(r"F:\shunyi\docs\_diag", exist_ok=True)
canvas.save(r"F:\shunyi\docs\_diag\保存提示-宽度.png")
print("已保存 docs/_diag/保存提示-宽度.png", canvas.size)
