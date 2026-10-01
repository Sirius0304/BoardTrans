# -*- coding: utf-8 -*-
"""诊断：_note_box 里那行「说明：Win 键组合…」的换行为什么提前到约 5/3 宽度。

打印它的 wraplength、图片宽、影子排版结果、以及所在容器的实际宽度。
"""
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
service = HotkeyService(notify=lambda t, m: None)
settings = sw.SettingsWindow(service)


def collect():
    root = settings.root
    root.update_idletasks()
    settings._scroll_canvas.yview_moveto(1.0)
    root.update_idletasks()

    def walk(w):
        yield w
        for c in w.winfo_children():
            yield from walk(c)

    for w in walk(root):
        if not isinstance(w, ui.TextLabel):
            continue
        text = str(w.cget("text"))
        if "Win 键组合" not in text:
            continue
        font_name = str(w.cget("font"))
        spec = ui.font_spec(w, font_name)
        ruler = lambda s: int(root.tk.call("font", "measure", font_name, s))   # noqa: E731
        print("文字:", text)
        print("字体:", font_name, spec)
        print("wraplength:", w._wraplength)
        print("图片尺寸:", (w._photo.width(), w._photo.height()) if w._photo else None)
        print("req 尺寸:", (w.winfo_reqwidth(), w.winfo_reqheight()))
        print("控件实际宽:", w.winfo_width(), " 父容器宽:", w.master.winfo_width(),
              " 祖父宽:", w.master.master.winfo_width() if w.master.master else None)
        print("影子排版 _native_metrics:", w._native_metrics(spec))
        print("按 wraplength 断行:", ui.wrap_lines(text, w._wraplength, ruler))
        nm = w._native_metrics(spec)
        if nm.get("width"):
            print("按 target_w 断行:", ui.wrap_lines(text, nm["width"], ruler))
        print("窗口宽:", root.winfo_width(), " 屏幕宽:", root.winfo_screenwidth())


settings.open()
time.sleep(2.5)
settings.run_in_ui(collect)

# ---- 窗口宽度变化时是否跟着重新断行（贴合右边界）----
print("\n=== 改变窗口宽度后的表现 ===")


def probe():
    root = settings.root

    def walk(w):
        yield w
        for c in w.winfo_children():
            yield from walk(c)

    for w in walk(root):
        if isinstance(w, ui.TextLabel) and "Win 键组合" in str(w.cget("text")):
            wl = w.winfo_width()
            pw = w._photo.width() if w._photo else 0
            hh = w._photo.height() if w._photo else 0
            return wl, pw, hh, root.winfo_width()
    return None


for width in (720, 480, 600):
    settings.run_in_ui(lambda wd=width: settings.root.geometry(f"{wd}x720"))
    time.sleep(0.7)
    r = settings.run_in_ui(probe)
    if r:
        wl, pw, hh, rw = r
        print(f"  窗口 {rw:>4} → 控件宽 {wl}, 图片宽 {pw}, 高 {hh}"
              f"（图片宽/控件宽 = {pw / wl:.0%}）")

settings.shutdown()
time.sleep(0.4)
config.update(**{k: original[k] for k in original})
