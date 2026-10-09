# -*- coding: utf-8 -*-
"""复现"状态胶囊文字被截断"的探针。

按真实时序驱动模型状态胶囊的文字变化，逐帧量三个量：
  - label 需要的宽度（文字实际多宽）
  - chip 的 canvas 实际宽度（容器给了多宽）
  - 是否截断（需要宽 > 容器宽）

退出码 0 = 无截断；1 = 有截断（复现成功）。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import ui_kit as ui        # noqa: E402
import tkinter as tk                # noqa: E402

ui.enable_dpi_awareness()

root = tk.Tk()
root.title("胶囊截断探针")
root.geometry("560x160+60+60")
root.attributes("-topmost", True)   # 不置顶会被其他窗口挡住，截图像素取证就抓错内容
root.lift()

card = tk.Frame(root, bg=ui.COLOR_CARD)
card.pack(fill="x")
left = tk.Frame(card, bg=ui.COLOR_CARD)
left.pack(side="left", fill="x", expand=True)

# 完全按 _status_chip 的构造方式
chip = ui.RoundedPanel(left, tone="plain", radius=999, pad=2, page_bg=ui.COLOR_CARD,
                       fit_width=True)
chip.label = ui.TextLabel(chip.inner, text="状态：未下载", bg=ui.COLOR_HOVER,
                          fg="#6B7280", font="BoardTransSmall", anchor="w")
chip.label.pack(padx=8, pady=1)
chip.pack(anchor="w", pady=(4, 0))


def chip_configure(**kw):
    fg = kw.pop("fg", None)
    text_val = kw.pop("text", None)
    if text_val is not None:
        chip.label.configure(text=text_val)
    if fg is not None:
        chip.label.configure(fg=fg)
        chip.set_tone(ui.COLOR_HOVER, "")


chip.configure = chip_configure

root.update()
time.sleep(0.3)
root.update()

PRIMARY = "#3B82F6"

# 完全按真实时序：未下载 → 点下载(fg变) → 进度文字逐帧变长(fg不变) → 解压 → 完成
SEQUENCE = [
    ("未下载",        "状态：未下载",                                     None),
    ("点击下载",      "状态：正在下载 0%",                                 PRIMARY),
    ("进度 9%",       "状态：正在下载 9%　（6.4 / 71.0 MB）",              None),
    ("进度 42%",      "状态：正在下载 42%　（30.0 / 71.0 MB）",            None),
    ("进度 100%",     "状态：正在下载 100%　（71.0 / 71.0 MB）　· 1.59 MB/s · 约剩 0 秒", None),
    ("正在解压",      "状态：下载完成，正在解压…",                          None),
    ("完成",          "状态：已下载（占用 82 MB）",                        "#16A34A"),
]

bad = []
shots = []
try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

for name, text, fg in SEQUENCE:
    kw = {"text": text}
    if fg:
        kw["fg"] = fg
    chip_configure(**kw)
    root.update_idletasks()
    root.update()
    time.sleep(0.35)          # 跨过 _watch 的 250ms 自检周期
    root.update()

    need = chip.label.winfo_reqwidth()
    have = chip.canvas.winfo_width()
    clip = need > have
    print(f"  {name:8s} 需要宽 {need:4d}px  容器宽 {have:4d}px  "
          f"{'<<< 截断' if clip else 'OK'}")
    if clip:
        bad.append((name, need, have))
    # 每帧截胶囊区域的像素图（视觉证据：文字到底显没显示完整）
    if ImageGrab is not None:
        try:
            x = chip.winfo_rootx()
            y = chip.winfo_rooty()
            w_ = chip.winfo_width()
            h_ = chip.winfo_height()
            shots.append((name, ImageGrab.grab((x - 2, y - 2, x + w_ + 2, y + h_ + 2))))
        except Exception:
            pass

if shots and ImageGrab is not None:
    try:
        pad = 8
        total_w = max(im.width for _n, im in shots) + 2 * pad
        total_h = sum(im.height for _n, im in shots) + pad * (len(shots) + 1)
        canvas_img = ImageGrab.grab((0, 0, 1, 1))  # 借一个模式
        from PIL import Image
        board = Image.new("RGB", (total_w, total_h), "#F7F8FA")
        yy = pad
        for _n, im in shots:
            board.paste(im, (pad, yy))
            yy += im.height + pad
        out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "docs", "胶囊状态渲染验证.png")
        board.save(out)
        print(f"  截图已存: {out}")
    except Exception as exc:  # noqa: BLE001
        print(f"  截图失败: {exc}")

print()
if bad:
    print(f"复现成功：{len(bad)} 个状态被截断 -> {[(n, f'{nd}/{hv}') for n, nd, hv in bad]}")
    sys.exit(1)
print("无截断")
sys.exit(0)