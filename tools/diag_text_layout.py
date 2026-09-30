# -*- coding: utf-8 -*-
"""精确对比：同一个设置窗口，在"原生文字"与"超采样文字"两种模式下，
每个文字控件的请求尺寸是否一致（= 布局是否零变化）。"""
import sys
import time

sys.path.insert(0, r"F:\shunyi")

from app import config                                   # noqa: E402
import app.settings_window as sw                         # noqa: E402
from app import ui_kit as ui                             # noqa: E402
from app.service import HotkeyService                    # noqa: E402

sw.dialogs.ask = lambda *a, **k: True
sw.dialogs.tell = lambda *a, **k: None
original = dict(config.load())
_real = ui.font_file


def collect_all(disable):
    ui.font_file = (lambda family, bold=False: None) if disable else _real
    ui._TEXT_PNG_CACHE.clear()
    service = HotkeyService(notify=lambda t, m: None)
    settings = sw.SettingsWindow(service)
    out = []

    def walk(w, path=""):
        yield w, path
        for i, c in enumerate(w.winfo_children()):
            yield from walk(c, f"{path}/{i}")

    def grab():
        root = settings.root

        def inner(w, path=""):
            yield w, path
            for i, c in enumerate(w.winfo_children()):
                yield from inner(c, f"{path}/{i}")

        for w, path in inner(root):
            try:
                text = str(w.cget("text"))
            except Exception:  # noqa: BLE001
                continue
            if not text:
                continue
            kind = type(w).__name__
            out.append((kind, text, w.winfo_reqwidth(), w.winfo_reqheight()))
        out.append(("__window__", "", root.winfo_reqwidth(), root.winfo_reqheight()))

    settings.open()
    time.sleep(2.0)
    settings.run_in_ui(grab)
    time.sleep(0.3)
    settings.shutdown()
    time.sleep(0.4)
    return out


a = collect_all(True)
b = collect_all(False)
config.update(**{k: original[k] for k in original})
ui.font_file = _real

print(f"原生模式 {len(a)} 个文字控件，超采样模式 {len(b)} 个")
same = 0
diffs = []
for (k1, t1, w1, h1), (k2, t2, w2, h2) in zip(a, b):
    if t1 == t2 and w1 == w2 and h1 == h2:
        same += 1
    else:
        diffs.append((t1, (w1, h1), (w2, h2)))
print(f"尺寸完全一致: {same} / {len(a)}")
if diffs:
    print("存在差异的控件：")
    for t, x, y in diffs:
        print(f"  {t[:34]!r}  原生 {x} → 超采样 {y}  (Δ{w2 if False else y[0] - x[0]:+d},"
              f"{y[1] - x[1]:+d})")
else:
    print("★ 布局零变化")
