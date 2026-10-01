# -*- coding: utf-8 -*-
"""验证「恢复默认设置」确认框：内容 / 居中位置 / 层级 / 按钮响应。

产物：docs/_diag/恢复默认-确认框.png
"""
import os
import sys
import time

sys.path.insert(0, r"F:\shunyi")

from PIL import Image, ImageDraw, ImageFont, ImageGrab   # noqa: E402

from app import config, dialogs                          # noqa: E402
import app.settings_window as sw                         # noqa: E402
from app import ui_kit as ui                             # noqa: E402
from app.service import HotkeyService                    # noqa: E402

sw.dialogs.ask = lambda *a, **k: True
sw.dialogs.tell = lambda *a, **k: None
original = dict(config.load())
service = HotkeyService(notify=lambda t, m: None)
settings = sw.SettingsWindow(service)
BOX = {}


def show():
    root = settings.root
    root.attributes("-topmost", False)      # 别把弹窗压在下面（真实使用时 <280ms 也会取消置顶）
    root.lift()
    root.update_idletasks()
    top, result, _close = dialogs._build(
        root, "恢复默认设置", "",
        [{"text": "取消", "value": "cancel", "kind": "secondary"},
         {"text": "恢复默认", "value": "ok", "kind": "primary"}],
        "warn", "ok", frameless=True)
    dialogs._center_on_parent(top, root)
    try:
        top.attributes("-topmost", True)     # 让弹窗浮在设置窗口之上（截图用）
    except Exception:  # noqa: BLE001
        pass
    top.update_idletasks()
    top.update()
    time.sleep(0.4)
    top.update()
    BOX["top"] = top
    # 复核窗口样式：WS_SYSMENU 位应当已被清掉（= 关闭按钮消失）
    try:
        import ctypes                                    # noqa: E402
        hwnd = int(top.frame(), 16)
        style = ctypes.windll.user32.GetWindowLongW(hwnd, -16)
        BOX["style"] = hex(style)
        BOX["sysmenu"] = bool(style & 0x00080000)
        BOX["caption"] = bool(style & 0x00C00000)
    except Exception as exc:  # noqa: BLE001
        BOX["style"] = f"读取失败: {exc}"
    BOX["geo"] = (top.winfo_rootx(), top.winfo_rooty(),
                  top.winfo_reqwidth(), top.winfo_reqheight())
    BOX["parent"] = (root.winfo_rootx(), root.winfo_rooty(),
                     root.winfo_width(), root.winfo_height())
    BOX["transient"] = top.transient()
    BOX["buttons"] = [w.cget("text") for w in _all_buttons(top)]
    BOX["texts"] = _all_texts(top)


def _all_buttons(w):
    out = []
    for c in w.winfo_children():
        if isinstance(c, ui.RoundedButton):
            out.append(c)
        out.extend(_all_buttons(c))
    return out


def _all_texts(w):
    out = []
    for c in w.winfo_children():
        if isinstance(c, ui.TextLabel):
            out.append(str(c.cget("text")))
        out.extend(_all_texts(c))
    return out


settings.open()
time.sleep(2.5)
settings.run_in_ui(show)
time.sleep(1.0)
px, py, pw, ph = BOX["parent"]
wx, wy, ww, wh = BOX["geo"]
cx, cy = px + pw // 2, py + ph // 2
dx = wx + ww // 2
dy = wy + wh // 2
print(f"设置窗口: {pw}×{ph} @({px},{py})   中心=({cx},{cy})")
print(f"弹窗    : {ww}×{wh} @({wx},{wy})   中心=({dx},{dy})")
print(f"偏移    : Δx={dx - cx:+d}  Δy={dy - cy:+d}  （居中时都应为 0，误差 ≤1 属取整）")
print("弹窗里的文字控件:", BOX["texts"])
print("按钮:", BOX["buttons"])
print("窗口样式:", BOX.get("style"), " WS_SYSMENU(关闭按钮)=", BOX.get("sysmenu"),
      " WS_CAPTION(标题栏)=", BOX.get("caption"))

full = ImageGrab.grab(bbox=(px - 20, py - 20, px + pw + 20, py + ph + 20))
os.makedirs(r"F:\shunyi\docs\_diag", exist_ok=True)
# 弹窗相对整图的坐标
rel = (wx - px + 20, wy - py + 20)
crop = full.crop((rel[0] - 24, rel[1] - 24,
                  rel[0] + ww + 24, rel[1] + wh + 24))
crop.resize((crop.width * 3, crop.height * 3), Image.NEAREST).save(
    r"F:\shunyi\docs\_diag\恢复默认-确认框.png")
canvas = Image.new("RGB", (full.width, full.height), "#FFFFFF")
canvas.paste(full, (0, 0))
d = ImageDraw.Draw(canvas)
d.rectangle([rel[0], rel[1], rel[0] + ww, rel[1] + wh], outline="#E24B4A", width=2)
try:
    f = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 16, index=1)
except Exception:  # noqa: BLE001
    f = ImageFont.load_default()
d.text((rel[0], max(2, rel[1] - 20)), "弹窗（应居于窗口正中）", fill="#A32D2D", font=f)
canvas.save(r"F:\shunyi\docs\_diag\恢复默认-整窗.png")

settings._queue.put(("call", lambda: BOX["top"].destroy()))
time.sleep(0.4)
settings._queue.put(("call", lambda: settings.root.attributes("-topmost", False)))
settings.shutdown()
time.sleep(0.4)
config.update(**{k: original[k] for k in original})
