# -*- coding: utf-8 -*-
"""删除确认弹窗样式改版的像素取证（2026-10-05）。

主人要求的三个验收点：
  1. 卡片内没有「删除模型」加粗标题、没有左侧红色竖线
  2. 布局无空白残留（文字不悬空、按钮行不漂移）
  3. 弹窗在父窗口中垂直居中正常

做法：真实构造删除确认弹窗（dialogs.ask 的完整路径），逐像素抓图 +
量内容框几何，三个点各给硬判据。退出码 0 = 全过。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import ui_kit as ui                    # noqa: E402
import app.settings_window as sw                # noqa: E402
from app import dialogs                        # noqa: E402
from app.service import HotkeyService          # noqa: E402

try:
    from PIL import Image, ImageGrab
except ImportError:
    print("需要 Pillow")
    sys.exit(2)

ui.enable_dpi_awareness()

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


service = HotkeyService(notify=lambda t, m: None)
win = sw.SettingsWindow(service)
win.open()
time.sleep(2.2)

MSG = ("确定要删除「中译英」模型吗？\n"
       "将释放约 153 MB 磁盘空间。\n"
       "会清理以下位置：\n"
       "  · F:/shunyi/models/packages/zh_en（82 MB）\n"
       "  · F:/shunyi/models/cache/zh_en.argosmodel（71 MB）\n"
       "注意：中译英是基础模型，删除后中译英和中译日都无法翻译。\n"
       "当前目标语言正在使用它，删除后按快捷键会提示模型缺失。\n"
       "删除后如果还需要，可以在这里重新下载。")

built = {}


def build_dialog():
    top, result, close = dialogs._build(
        win.root, "删除模型", MSG,
        [{"text": "取消", "value": "cancel", "kind": "secondary"},
         {"text": "删除", "value": "ok", "kind": "danger"}], "err", "ok")
    dialogs._center_on_parent(top, win.root)
    built["top"] = top
    built["close"] = close
    built["labels"] = []
    built["accent_frames"] = 0

    def walk(w):
        yield w
        for c in w.winfo_children():
            yield from walk(c)

    def sample():
        for w in walk(top):
            if w.winfo_class() == "Label":
                built["labels"].append({"text": w.cget("text"),
                                        "font": str(w.cget("font"))})
            elif w.winfo_class() == "Frame" and w.cget("bg") in (
                    ui.COLOR_ERR_TEXT, ui.COLOR_WARN_FG, ui.COLOR_PRIMARY_TEXT):
                built["accent_frames"] += 1
        # 内容框几何：文字控件与按钮行的边界
        card = dialogs._card_of(top)
        built["card_geom"] = (card.winfo_rootx(), card.winfo_rooty(),
                              card.winfo_width(), card.winfo_height())
        built["win_geom"] = (top.winfo_rootx(), top.winfo_rooty(),
                             top.winfo_width(), top.winfo_height())
        built["parent_geom"] = (win.root.winfo_rootx(), win.root.winfo_rooty(),
                                win.root.winfo_width(), win.root.winfo_height())
        # 找到 message 文字控件的位置
        for w in walk(top):
            if w.winfo_class() == "Label" and w.cget("text") == MSG:
                built["msg_geom"] = (w.winfo_rootx(), w.winfo_rooty(),
                                     w.winfo_width(), w.winfo_height())
        top.update_idletasks()
        shot = ImageGrab.grab((top.winfo_rootx(), top.winfo_rooty(),
                               top.winfo_rootx() + top.winfo_width(),
                               top.winfo_rooty() + top.winfo_height()))
        built["shot"] = shot
        top.after(120, lambda: close(None))

    top.after(500, sample)
    try:
        top.grab_set()
    except Exception:  # noqa: BLE001
        pass
    top.wait_window(top)


win.run_in_ui(build_dialog)
time.sleep(1.2)

# ---- 验收点 1：无标题、无竖线
labels = built.get("labels", [])
bold_titles = [lb for lb in labels
               if lb["text"] == "删除模型" and ui.UI_FONT_BOLD in lb["font"]]
check("卡片内没有「删除模型」加粗标题", not bold_titles,
      str([lb["text"][:12] for lb in labels]))
check("卡片内只有灰色说明文字（1 个文字控件）", len(labels) == 1, str(len(labels)))
check("左侧红色竖线已移除", built.get("accent_frames", 1) == 0,
      str(built.get("accent_frames")))

# ---- 验收点 2：无空白残留
# 文字左边缘应与按钮行对齐 = 卡片左缘 + 卡片内边距（16px），
# 不应再多出竖线时代的 16px（4px 线 + 12px 间距）。
mg = built.get("msg_geom")
cg = built.get("card_geom")
if mg and cg:
    left_gap = mg[0] - cg[0]
    # 卡片自身 pad=16；容许 DPI 取整 ±2px，但绝不应是 32px（竖线时代的残留）
    check(f"文字左缘与卡片内边距对齐（左距 {left_gap}px）",
          abs(left_gap - ui.S(16)) <= 2, f"实测 {left_gap}px，期望约 {ui.S(16)}px")
else:
    check("取到文字与卡片几何", False, str(built.get("msg_geom")))

# 顶部也不能残留标题的空位：文字顶 = 卡片顶 + 卡片pad
if mg and cg:
    top_gap = mg[1] - cg[1]
    check(f"文字顶缘紧贴卡片内边距（上距 {top_gap}px，无标题空位）",
          abs(top_gap - ui.S(16)) <= 2, f"实测 {top_gap}px，期望约 {ui.S(16)}px")

# ---- 验收点 3：弹窗在父窗口中垂直居中
wg = built.get("win_geom")
pg = built.get("parent_geom")
if wg and pg:
    dy = (wg[1] + wg[3] // 2) - (pg[1] + pg[3] // 2)
    dx = (wg[0] + wg[2] // 2) - (pg[0] + pg[2] // 2)
    check(f"弹窗中心与父窗口中心偏差 dx={dx} dy={dy}",
          abs(dy) <= 34 and abs(dx) <= 10,
          f"纵向偏差含标题栏补偿（≤34px 视为居中）")

# ---- 截图存档
shot = built.get("shot")
if shot is not None:
    scale = 2
    big = shot.resize((shot.width * scale, shot.height * scale), Image.NEAREST)
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "删除确认弹窗-新样式.png")
    big.save(out)
    print(f"\n  截图已存: {out}")

# ---- 验收点 4：无说明文字的弹窗（如「恢复默认设置」）仍要保留加粗标题
# 它是 message="" 的特例：标题就是唯一内容，删掉就成空白框了。
built2 = {}


def build_empty_dialog():
    top, result, close = dialogs._build(
        win.root, "恢复默认设置", "",
        [{"text": "取消", "value": "cancel", "kind": "secondary"},
         {"text": "恢复默认", "value": "ok", "kind": "danger"}], "err", "ok")
    dialogs._center_on_parent(top, win.root, frameless=True)
    built2["labels"] = []

    def walk(w):
        yield w
        for c in w.winfo_children():
            yield from walk(c)

    def sample():
        built2["labels"] = [(w.cget("text"), str(w.cget("font")))
                            for w in walk(top) if w.winfo_class() == "Label"]
        top.after(120, lambda: close(None))

    top.after(400, sample)
    try:
        top.grab_set()
    except Exception:  # noqa: BLE001
        pass
    top.wait_window(top)


win.run_in_ui(build_empty_dialog)
time.sleep(1.0)

labels2 = built2.get("labels", [])
check("无说明文字的弹窗仍保留加粗标题（未被误伤）",
      any(t == "恢复默认设置" and ui.UI_FONT_BOLD in f for t, f in labels2),
      str(labels2))

win.shutdown()

print()
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
for f in FAIL:
    print(f"  - {f}")
sys.exit(1 if FAIL else 0)