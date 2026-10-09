# -*- coding: utf-8 -*-
"""生成"统一风格对话框"效果图：截取真实对话框（删除确认 + 恢复默认确认）到 docs/。"""
import sys
import time

sys.path.insert(0, r"F:\shunyi")

from PIL import Image, ImageDraw, ImageFont, ImageGrab        # noqa: E402

from app import config                                        # noqa: E402
import app.settings_window as sw                              # noqa: E402
from app import dialogs, ui_kit as ui                         # noqa: E402
from app.service import HotkeyService                         # noqa: E402

TARGETS = [
    ("删除模型（危险操作 → 红色主按钮）", "删除模型",
     "将删除：中译英（基础模型）\n  · F:\\shunyi\\models\\zh_en（71 MB）\n\n"
     "注意：中译英是基础模型，删除后中译英和中译日都无法翻译。",
     [{"text": "取消", "value": "cancel", "kind": "secondary"},
      {"text": "删除", "value": "ok", "kind": "danger"}], "err"),
    ("恢复默认设置（警告 → 橙色强调条）", "恢复默认设置",
     "将把快捷键恢复为 Alt+J、翻译档位恢复为「高速度」、目标语言恢复为「英文」。\n\n"
     "注意：这只会改回窗口里的值，点「保存」后才真正生效。",
     [{"text": "取消", "value": "cancel", "kind": "secondary"},
      {"text": "恢复默认", "value": "ok", "kind": "primary"}], "warn"),
]


def main():
    original = dict(config.load())
    service = HotkeyService(notify=lambda t, m: None)
    settings = sw.SettingsWindow(service)
    settings.open()
    time.sleep(2.5)

    shots = []
    for label, title, message, buttons, tone in TARGETS:
        holder = {}

        def build():
            top, result, close = dialogs._build(settings.root, title, message, buttons, tone, "ok")
            # 弹窗创建时是全透明的（避免先在左上角闪一下），定位收敛后才现身。
            # 这里绕过了 dialogs._run，所以要自己居中，否则截到的是一张全透明图。
            dialogs._center_on_parent(top, settings.root)

            def after_layout():
                holder["bbox"] = (top.winfo_rootx(), top.winfo_rooty(),
                                  top.winfo_width(), top.winfo_height())
                top.after(900, lambda: close("cancel"))

            top.after(450, after_layout)
            try:
                top.grab_set()
            except Exception:  # noqa: BLE001
                pass
            top.wait_window(top)

        import threading
        t = threading.Thread(target=lambda: settings.run_in_ui(build), daemon=True)
        t.start()
        time.sleep(1.1)                       # 等对话框布局完成并停稳
        bbox = holder.get("bbox")
        if bbox and bbox[2] > 50:
            shots.append((label, ImageGrab.grab(bbox=bbox).convert("RGB")))
        t.join(timeout=6)

    settings.shutdown()
    time.sleep(0.4)
    config.update(**{k: original[k] for k in original})

    if not shots:
        print("未截到对话框")
        return 1

    pad, label_h, cols = 20, 34, 2
    cw = max(im.width for _l, im in shots) + pad * 2
    ch = max(im.height for _l, im in shots) + pad * 2 + label_h
    rows = (len(shots) + cols - 1) // cols
    canvas = Image.new("RGB", (cw * cols + pad, ch * rows + pad), "#F7F8FA")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 20)
        title_font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 24)
    except Exception:  # noqa: BLE001
        font = title_font = ImageFont.load_default()
    draw.text((pad + 4, 6), "键译 · 弹窗统一为设置界面风格（微软雅黑 / 圆角卡片 / 同套按钮）",
              fill="#1F2937", font=title_font)
    for idx, (label, im) in enumerate(shots):
        r, c = divmod(idx, cols)
        x = pad + c * cw
        y = pad + 34 + r * ch
        draw.text((x + 4, y + 4), label, fill="#6B7280", font=font)
        canvas.paste(im, (x + pad, y + label_h))
        draw.rectangle([x + pad - 1, y + label_h - 1,
                        x + pad + im.width, y + label_h + im.height], outline="#C9D0DA")
    out = r"F:\shunyi\docs\对话框-统一风格.png"
    canvas.save(out)
    print("已保存", out, canvas.size, "；对话框数:", len(shots))
    return 0


if __name__ == "__main__":
    sys.exit(main())
