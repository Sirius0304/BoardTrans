# -*- coding: utf-8 -*-
"""文字渲染取证（第 2 步）：同屏对比「当前 Tk 渲染」与「Pillow 各种渲染」。

结论要回答：
  1) 当前 Tk 的小字有多少中间灰阶（= 抗锯齿程度）？是硬边还是平滑边？
  2) Pillow 1× / 3× 超采样 / 6× 超采样 分别是什么效果？谁更清晰？
产物：docs/_diag/文字渲染对比-原始.png（1×）+ -放大8x.png
"""
import base64
import io
import sys
import tkinter as tk

sys.path.insert(0, r"F:\shunyi")

from PIL import Image, ImageDraw, ImageFont, ImageGrab   # noqa: E402

FONT_FILE = r"C:\Windows\Fonts\msyh.ttc"
FONT_INDEX = 1          # Microsoft YaHei UI
PX = 12                 # 与项目 BoardTransSmall 一致（9pt @96dpi = 12px）
FG = (107, 114, 128)    # #6B7280 次要文字
BG = (255, 255, 255)
TEXT = "已下载 12.3 / 71.0 MB，正在校验（通知提醒·日文方向）"

# ---------------------------------------------------------------- Pillow 渲染
def pil_text(ss):
    """以 ss 倍超采样渲染一行文字，再 BOX 降采样；ss=1 即直绘。"""
    font = ImageFont.truetype(FONT_FILE, PX * ss, index=FONT_INDEX)
    ascent, descent = font.getmetrics()
    tw = int(font.getlength(TEXT))
    W = (tw + 4) * ss if ss > 1 else tw + 4
    H = (ascent + descent + 4) * ss if ss > 1 else ascent + descent + 4
    img = Image.new("RGBA", (W, H), BG + (255,))
    ImageDraw.Draw(img).text((2 * ss, 2 * ss), TEXT, font=font, fill=FG + (255,))
    if ss > 1:
        return img.resize((W // ss, H // ss), Image.BOX)
    return img


def analyze(img, label):
    tab = img.convert("L")
    hist = tab.histogram()
    ink = sum(hist[:200])                       # 深色 = 墨
    dark = min(i for i, v in enumerate(hist) if v)
    mid = sum(hist[dark + 12:244])
    rgb = img.convert("RGB").getdata()
    colored = sum(1 for r, g, b in rgb if max(r, g, b) - min(r, g, b) > 18)
    total = img.width * img.height
    print(f"  {label:<28} 最小灰度={dark:>3}  墨点={ink:>5}  "
          f"中间灰阶={mid:>5} ({mid / max(1, ink) * 100:5.1f}%)  "
          f"彩色像素={colored:>4} ({colored / total * 100:4.2f}%)")
    return {"ink": ink, "mid": mid, "ratio": mid / max(1, ink)}


shots = {}
root = tk.Tk()
root.title("文字渲染取证")
root.configure(bg="#FFFFFF")
root.geometry("640x300+60+60")
root.attributes("-topmost", True)

tk.Label(root, text=TEXT, font=("Microsoft YaHei UI", -PX), fg="#6B7280",
         bg="#FFFFFF", anchor="w").pack(anchor="w", padx=8, pady=(10, 2))
tk.Label(root, text="↑ 当前 Tk 渲染（GDI）", font=("Microsoft YaHei UI", -11),
         fg="#9CA3AF", bg="#FFFFFF", anchor="w").pack(anchor="w", padx=8)

pil_images = {}
for ss in (1, 3, 6):
    pil_images[ss] = pil_text(ss)
    buf = io.BytesIO()
    pil_images[ss].save(buf, format="PNG")
    ph = tk.PhotoImage(master=root, data=base64.b64encode(buf.getvalue()))
    holder = tk.Label(root, image=ph, bd=0, bg="#FFFFFF", anchor="w")
    holder.image = ph
    holder.pack(anchor="w", padx=8, pady=(8, 0))
    tk.Label(root, text=f"↑ Pillow {ss}× {'直绘' if ss == 1 else '超采样降采样'}",
             font=("Microsoft YaHei UI", -11), fg="#9CA3AF", bg="#FFFFFF",
             anchor="w").pack(anchor="w", padx=8)

root.update_idletasks()
root.update()
root.after(400, lambda: None)
root.update()
import time                                        # noqa: E402
time.sleep(0.8)
root.update()
x, y = root.winfo_rootx(), root.winfo_rooty()
w, h = root.winfo_width(), root.winfo_height()
shot = ImageGrab.grab(bbox=(x, y, x + w, y + h))
root.destroy()

out = r"F:\shunyi\docs"
import os                                          # noqa: E402
os.makedirs(os.path.join(out, "_diag"), exist_ok=True)
shot.save(os.path.join(out, "_diag", "文字渲染对比-原始.png"))
big = shot.resize((shot.width * 8, shot.height * 8), Image.NEAREST)
big.save(os.path.join(out, "_diag", "文字渲染对比-放大8x.png"))
print("截屏尺寸:", shot.size, "→ 放大 8× =", big.size)

print("\n=== 各类渲染的像素统计（越小越糊，越大越有抗锯齿）===")
# Tk 行：按已知位置裁出来（第一行 Label 区域）
analyze(shot.crop((8, 10, min(shot.width, 620), 40)), "Tk 当前渲染")
for ss in (1, 3, 6):
    analyze(pil_images[ss], f"Pillow {ss}×")
