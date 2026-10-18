# -*- coding: utf-8 -*-
"""生成「说明文字换行宽度修复」前后对比图。

上：修复前（跟随 Tk 行数 → 每行只填 3/5 就折行）
下：修复后（按控件可用宽度断行 → 铺到右边界）
产物：docs/换行宽度修复对比.png
"""
import os
import shutil
import sys
import time

sys.path.insert(0, r"F:\shunyi")

from PIL import Image, ImageDraw, ImageFont, ImageGrab   # noqa: E402

from app import config                                   # noqa: E402
import app.settings_window as sw                         # noqa: E402
from app import ui_kit as ui                             # noqa: E402
from app.service import HotkeyService                    # noqa: E402

# 修复前截图：路径不写死（2026-10-18 脱敏 + 可移植）
# 可用环境变量 SHOWCASE_BEFORE_SRC 指定；不指定时默认从当前用户的家目录推导。
BEFORE_SRC = os.environ.get("SHOWCASE_BEFORE_SRC") or os.path.join(
    os.path.expanduser("~"), ".workbuddy", "clipboard-images",
    "clipboard-2026-10-01T09-39-01-399Z-ae9dc91f.png")
DIAG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "docs", "_diag")
os.makedirs(DIAG, exist_ok=True)
before_saved = os.path.join(DIAG, "说明框-修复前.png")
if os.path.exists(BEFORE_SRC):
    shutil.copyfile(BEFORE_SRC, before_saved)

sw.dialogs.ask = lambda *a, **k: True
sw.dialogs.tell = lambda *a, **k: None
original = dict(config.load())
service = HotkeyService(notify=lambda t, m: None)
settings = sw.SettingsWindow(service)
box = {}


def grab():
    root = settings.root
    root.attributes("-topmost", True)
    root.lift()
    root.update_idletasks()
    settings._scroll_canvas.yview_moveto(1.0)
    root.update_idletasks()

    def walk(w):
        yield w
        for c in w.winfo_children():
            yield from walk(c)

    for w in walk(root):
        if isinstance(w, ui.TextLabel) and "Win 键组合" in str(w.cget("text")):
            box["tb"] = (w.winfo_rootx() - 26, w.winfo_rooty() - 12,
                         w.winfo_width() + 52, w.winfo_height() + 26)
            box["lines"] = ui.wrap_lines(
                str(w.cget("text")), w._avail_width(), w._measure)
            box["avail"] = w._avail_width()
            box["photo"] = (w._photo.width(), w._photo.height())
            box["origin"] = (root.winfo_rootx(), root.winfo_rooty())
            return


settings.open()
time.sleep(2.5)
settings.run_in_ui(grab)
time.sleep(1.0)
ox, oy = box["origin"]
x, y, w, h = box["tb"]
full = ImageGrab.grab(bbox=(ox, oy, ox + settings.root.winfo_width(),
                            oy + settings.root.winfo_height()))
after = full.crop((x - ox, y - oy, x - ox + w, y - oy + h))
after.save(os.path.join(DIAG, "说明框-修复后.png"))
settings._queue.put(("call", lambda: settings.root.attributes("-topmost", False)))
settings.shutdown()
time.sleep(0.4)
config.update(**{k: original[k] for k in original})

print("可用宽度:", box["avail"], " 图片:", box["photo"])
for i, ln in enumerate(box["lines"]):
    print(f"  第{i + 1}行: {ln}")

before = Image.open(before_saved).convert("RGB")
after = after.convert("RGB")
Z = 2 if before.width > 300 else 3
pad, lab = 20, 32
wmax = max(before.width, after.width)
canvas = Image.new("RGB", (wmax * Z + pad * 2, (before.height + after.height) * Z
                           + pad * 3 + lab * 2), "#EDEFF2")
draw = ImageDraw.Draw(canvas)
try:
    f = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 19, index=1)
except Exception:  # noqa: BLE001
    f = ImageFont.load_default()
y = pad
for title, img in (("修复前：跟随 Tk 的行数 → 每行只填到 3/5 就折行，右侧一大块空白", before),
                   ("修复后：按控件可用宽度断行 → 铺到右边界", after)):
    draw.text((pad, y), title, fill="#374151", font=f)
    y += lab
    big = img.resize((img.width * Z, img.height * Z), Image.NEAREST)
    canvas.paste(big, (pad, y))
    draw.rectangle([pad - 1, y - 1, pad + big.width, y + big.height], outline="#C9D0DA")
    y += big.height + pad
canvas.save(r"F:\shunyi\docs\换行宽度修复对比.png")
print("已保存 docs/换行宽度修复对比.png", canvas.size)
