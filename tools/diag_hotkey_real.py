# -*- coding: utf-8 -*-
"""真实现场②：把快捷键提示的 4 种动态文案逐个放到真实设置窗口里截图，
看换行到底哪里不对（以及与原生 Tk 的差异）。"""
import os
import sys
import time

sys.path.insert(0, r"F:\shunyi")

from PIL import Image, ImageDraw, ImageFont, ImageGrab   # noqa: E402

from app import config                                   # noqa: E402
import app.settings_window as sw                         # noqa: E402
from app import ui_kit as ui                             # noqa: E402
from app.service import HotkeyService                    # noqa: E402

TEXTS = [
    ("默认", "要求：至少包含一个 Ctrl / Alt / Shift，再加一个主键（例如 Alt+J）。", "#6B7280"),
    ("录制中", "现在请直接按下你想用的组合键（例如按住 Alt 再按 J）。按 Esc 取消。", "#C2410C"),
    ("等主键", "继续按一个主键（比如 J、K、F9）…", "#C2410C"),
    ("不合法", "这个组合不行：必须至少包含一个 Ctrl / Alt / Shift"
               "（不能只用单个键，否则会干扰你正常打字）。", "#DC2626"),
]

sw.dialogs.ask = lambda *a, **k: True
sw.dialogs.tell = lambda *a, **k: None
original = dict(config.load())
service = HotkeyService(notify=lambda t, m: None)
settings = sw.SettingsWindow(service)

BOX = {}


def prepare():
    root = settings.root
    root.attributes("-topmost", True)
    root.lift()
    root.update_idletasks()
    settings._scroll_canvas.yview_moveto(1.0)
    root.update_idletasks()


settings.open()
time.sleep(2.5)
settings.run_in_ui(prepare)
time.sleep(0.8)
_ox, _oy = settings.run_in_ui(lambda: (settings.root.winfo_rootx(),
                                       settings.root.winfo_rooty()))
_w, _h = settings.run_in_ui(lambda: (settings.root.winfo_width(),
                                     settings.root.winfo_height()))
ImageGrab.grab(bbox=(_ox, _oy, _ox + _w, _oy + _h)).save(
    r"F:\shunyi\docs\_diag\设置窗口-底部.png")
print("已保存 docs/_diag/设置窗口-底部.png", (_w, _h))

tiles = []
for tag, text, fg in TEXTS:
    settings.run_in_ui(
        lambda t=text, c=fg: settings._hotkey_hint.configure(text=t, fg=c))
    time.sleep(0.45)

    def grab_box():
        h = settings._hotkey_hint
        return (h.winfo_rootx(), h.winfo_rooty(), h.winfo_width(), h.winfo_height(),
                h.winfo_reqwidth(), h.winfo_reqheight(),
                (h._photo.width(), h._photo.height()) if h._photo else None)
    x, y, w, h, rw, rh, ph = settings.run_in_ui(grab_box)
    shot = ImageGrab.grab(bbox=(x - 4, y - 6, x + 500, y + h + 6))
    tiles.append((f"{tag}  控件 {w}×{h} / req {rw}×{rh} / 图片 {ph}", shot))
    print(f"[{tag}] 控件 {w}×{h}  req {rw}×{rh}  图片 {ph}")

settings._queue.put(("call", lambda: settings.root.attributes("-topmost", False)))
settings.shutdown()
time.sleep(0.4)
config.update(**{k: original[k] for k in original})

Z = 3
pad, label_h = 18, 30
w0 = max(t.width for _n, t in tiles) * Z + pad * 2
h0 = sum(t.height for _n, t in tiles) * Z + pad * (len(tiles) + 1) + label_h * len(tiles)
canvas = Image.new("RGB", (w0, h0), "#EDEFF2")
draw = ImageDraw.Draw(canvas)
try:
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 18, index=1)
except Exception:  # noqa: BLE001
    font = ImageFont.load_default()
y = pad
os.makedirs(r"F:\shunyi\docs\_diag", exist_ok=True)
for name, t in tiles:
    draw.text((pad, y), name, fill="#374151", font=font)
    y += label_h
    big = t.resize((t.width * Z, t.height * Z), Image.NEAREST)
    canvas.paste(big, (pad, y))
    draw.rectangle([pad - 1, y - 1, pad + big.width, y + big.height], outline="#C9D0DA")
    y += big.height + pad
canvas.save(r"F:\shunyi\docs\_diag\快捷键提示-四种文案.png")
print("已保存 docs/_diag/快捷键提示-四种文案.png", canvas.size)
