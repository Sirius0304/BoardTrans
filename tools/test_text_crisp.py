# -*- coding: utf-8 -*-
"""文字渲染清晰度：回归测试。

为什么需要这组检查（2026-10-01 报障"字发虚、边缘发彩"）：
  1) Tk 在 Windows 上走 GDI 的 ClearType 亚像素渲染 —— 放大看笔画边缘是**彩虹色**，
     字形轮廓被次像素位置扭曲，12/13px 的小字尤其毛糙发虚；
  2) 换成 Pillow 灰度超采样之后，必须保证「与原生 Label 逐像素同尺寸」，
     否则整块界面会挪位（字变小/卡片变矮/图标错位）；
  3) Pillow 或字体文件缺失时必须能安全退回原生渲染（功能不能坏）；
  4) 换来换去不能把界面搞卡（渲染耗时、缓存上限）。

用法：F:\\shunyi\\.venv\\Scripts\\python.exe tools\\test_text_crisp.py
"""
import io
import os
import sys
import time
import tkinter as tk

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from PIL import Image, ImageGrab                      # noqa: E402

from app import ui_kit as ui                          # noqa: E402

RESULTS = []
FAMILY = "Microsoft YaHei UI"


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  —— {detail}" if detail else ""),
          flush=True)


def colorful_ratio(img) -> float:
    """明显偏色的像素占比。

    为什么阈值取 40：界面文字色本身带蓝调（#1F2937 的 R/B 差 24、#6B7280 差 21），
    阈值太低会把正常的墨点全算成"彩色"。ClearType 的亚像素彩边则是 R/B 大幅分离
    （通常 60 以上），用 40 能干净地区分两者。
    """
    raw = img.convert("RGB").tobytes()
    total = len(raw) // 3
    hit = 0
    for i in range(0, total * 3, 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        if max(r, g, b) - min(r, g, b) > 40:
            hit += 1
    return hit / max(1, total)


def ink_ratio(img) -> float:
    """墨点（明显暗于背景）占比。"""
    gray = img.convert("L").tobytes()
    return sum(1 for v in gray if v < 200) / max(1, len(gray))


def midtone_ratio(img) -> float:
    """中间灰阶占比：有抗锯齿才会有（纯硬边的话基本是 0）。"""
    gray = img.convert("L").tobytes()
    dark = min(gray)
    ink = sum(1 for v in gray if v < 200)
    mid = sum(1 for v in gray if dark + 12 < v < 244)
    return mid / max(1, ink)


SAMPLE = "已下载 12.3 / 71.0 MB，正在校验（通知提醒·日文方向）"
LONG = ("这是一段较长的说明文字，用来验证 wraplength 自动换行后图片高度是否与原生 "
        "Label 一致，行距是否均匀；含英文单词 transition 和数字 12345。")


def main():
    root = tk.Tk()
    root.title("文字渲染清晰度自检")
    root.configure(bg="#FFFFFF")
    root.geometry("+90+90")
    root.attributes("-topmost", True)
    root.tk.call("tk", "scaling", 96 / 72)
    ui.set_scale(1.0)
    fonts = {}
    for name, pt, weight in (("ShunYiBase", 10, "normal"), ("ShunYiBold", 10, "bold"),
                             ("ShunYiTitle", 12, "bold"), ("ShunYiSmall", 9, "normal")):
        px = -max(9, int(round(pt * 96 / 72)))
        try:
            root.tk.call("font", "delete", name)
        except Exception:  # noqa: BLE001
            pass
        root.tk.call("font", "create", name, "-family", FAMILY, "-size", px,
                     "-weight", weight)
        ui.register_font(name, FAMILY, abs(px), weight)
        fonts[name] = abs(px)

    print("\n=== A. 字体文件解析（Pillow 只能按文件渲染，得先把族名翻成文件）===")
    got = ui.font_file(FAMILY, False)
    check("正体字体文件可解析", bool(got and os.path.exists(got[0])),
          str(got[0]).split("\\")[-1] if got else "None")
    if got:
        from PIL import ImageFont
        face = ImageFont.truetype(got[0], 24, index=got[1]).getname()
        check("ttc 内的 face 索引正确（族名对得上）", face[0] == FAMILY, str(face))
    got_b = ui.font_file(FAMILY, True)
    check("粗体解析到的是另一个文件（msyhbd）", bool(got_b and "bd" in got_b[0].lower()),
          str(got_b[0]).split("\\")[-1] if got_b else "None")
    check("不存在的族名返回 None（不会乱指一个文件）",
          ui.font_file("NoSuchFont-XYZ", False) is None)
    check("解析结果有缓存（同参数返回同一对象）",
          ui.font_file(FAMILY, False) is ui.font_file(FAMILY, False))

    print("\n=== B. 尺寸对齐（换了渲染方式也不能挪位）===")
    spec = ui.font_spec(root, "ShunYiBase")
    ruler = lambda s: int(root.tk.call("font", "measure", "ShunYiBase", s))    # noqa: E731
    data, w, h = ui.text_png(SAMPLE, FAMILY, spec["px"], "normal", "#1F2937",
                             linespace=spec["linespace"], ascent=spec["ascent"],
                             measure=ruler)
    check("能渲染出 PNG 字节", bool(data) and len(data) > 100, f"{len(data)} bytes")
    check("高度 = Tk 的行高 linespace", h == spec["linespace"],
          f"图片 {h} vs linespace {spec['linespace']}")
    tk_w = int(root.tk.call("font", "measure", "ShunYiBase", SAMPLE))
    check("宽度与 Tk 的 font measure 一致（±1px）", abs(w - tk_w) <= 1,
          f"图片 {w} vs measure {tk_w}")

    pairs = []
    for font_name in ("ShunYiTitle", "ShunYiBase", "ShunYiSmall"):
        nat = tk.Label(root, text=SAMPLE, font=font_name, bg="#FFFFFF", anchor="w")
        tl = ui.TextLabel(root, text=SAMPLE, font=font_name, bg="#FFFFFF", anchor="w")
        pairs.append((font_name, SAMPLE, nat, tl, "单行"))
    for font_name in ("ShunYiBase", "ShunYiSmall"):
        for wrap in (200, 330):
            nat = tk.Label(root, text=LONG, font=font_name, wraplength=wrap,
                           justify="left", anchor="w", bg="#FFFFFF")
            tl = ui.TextLabel(root, text=LONG, font=font_name, wraplength=wrap,
                              justify="left", anchor="w", bg="#FFFFFF")
            pairs.append((font_name, f"多行 wrap={wrap}", nat, tl, "多行"))
    root.update_idletasks()
    single_bad, multi_bad = [], []
    n_single = sum(1 for p in pairs if p[4] == "单行")
    for font_name, tag, nat, tl, kind in pairs:
        a = (nat.winfo_reqwidth(), nat.winfo_reqheight())
        b = (tl.winfo_reqwidth(), tl.winfo_reqheight())
        if kind == "单行":
            if a != b:
                single_bad.append(f"{font_name}: {a}→{b}")
            continue
        # 多行：目标是"把可用宽度用足"，不再与原生 Tk 的行数/尺寸对齐 ——
        # Tk 的断行更保守（会多排 1 行、每行只填 3/5），跟随它就会右侧留一大块空白。
        wrap = int(tag.rsplit("=", 1)[-1])
        span = max(1, int(ui.font_spec(tl, font_name)["linespace"]))
        nat_lines = max(1, (a[1] - 6) // span)
        lines = ui.wrap_lines(LONG, wrap, tl._measure)
        widest = max(tl._measure(ln) for ln in lines)
        if len(lines) > nat_lines:
            multi_bad.append(f"{font_name}/{tag}: 行数 {len(lines)} > 原生 {nat_lines}")
        # 避头点允许一个标点的悬挂量（见 ui_kit._punct_slack），其余不得超宽
        if widest > wrap + ui._punct_slack(wrap):
            multi_bad.append(f"{font_name}/{tag}: 最长行 {widest} 超上限 {wrap}")
    check(f"单行文字与原生 Label 尺寸逐像素一致（{n_single} 组）", not single_bad,
          "；".join(single_bad) or "一致")
    check("多行文字每行不超宽、行数不多于原生（宽度用得更足）",
          not multi_bad, "；".join(multi_bad) or "通过")

    print("\n=== C. 渲染质量（核心：干净、锐利、有平滑过渡）===")
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    flat = Image.new("RGB", img.size, (255, 255, 255))
    flat.paste(img, (0, 0), img)
    colors = colorful_ratio(flat)
    check("无彩边（彩色像素 < 0.5%，ClearType 约 9%）", colors < 0.005,
          f"{colors * 100:.2f}%")
    check("墨量充足（不是空白图）", ink_ratio(flat) > 0.05,
          f"{ink_ratio(flat) * 100:.1f}% 像素是墨点")
    mid = midtone_ratio(flat)
    check("边缘有灰度过渡（抗锯齿，不是硬阶梯）", 0.4 < mid < 2.2,
          f"中间灰阶/墨点 = {mid:.2f}")
    darkest = min(flat.convert("L").tobytes())
    check("笔画到位（最深像素接近设定文字色 #1F2937）", darkest <= 80, f"最小灰度={darkest}")

    print("\n=== D. TextLabel 的接口兼容（业务代码与既有测试都在用）===")
    dyn = ui.TextLabel(root, text="状态：待机", font="ShunYiSmall", fg="#6B7280",
                       bg="#FFFFFF", anchor="w")
    check("cget('text') 返回原文（而不是空）", dyn.cget("text") == "状态：待机",
          dyn.cget("text"))
    check("cget('font') 返回命名字体名", dyn.cget("font") == "ShunYiSmall",
          str(dyn.cget("font")))
    before = dyn._photo
    dyn.configure(text="状态：正在下载 42%")
    check("configure(text=) 触发了重新渲染", dyn._photo is not before and
          dyn._photo is not None)
    check("更新后 cget('text') 是新内容", dyn.cget("text") == "状态：正在下载 42%",
          dyn.cget("text"))
    p2 = dyn._photo
    dyn.configure(fg="#DC2626")
    check("改 fg 也会重渲染（状态换色要跟着变）", dyn._photo is not p2)
    empty = ui.TextLabel(root, text="", font="ShunYiSmall", bg="#FFFFFF")
    check("空文本不挂图片（不显示破图）",
          empty.cget("image") == "" and empty.cget("text") == "")

    print("\n=== E. 兜底：Pillow / 字体不可用时退回原生渲染 ===")
    ui.HAS_PIL = False
    try:
        fb = ui.TextLabel(root, text="回退测试 Abc", font="ShunYiSmall", fg="#1F2937",
                          bg="#FFFFFF")
        check("无 Pillow 时回退到原生 Tk 文字（功能不坏）",
              fb.cget("image") == "" and
              tk.Label.cget(fb, "text") == "回退测试 Abc",
              f"image={fb.cget('image')!r}")
    finally:
        ui.HAS_PIL = True
    real_font_file = ui.font_file
    ui.font_file = lambda family, bold=False: None
    try:
        ui._TEXT_PNG_CACHE.clear()
        fb2 = ui.TextLabel(root, text="回退测试 Abc", font="ShunYiSmall", fg="#1F2937",
                           bg="#FFFFFF")
        check("字体文件找不到时也回退到原生文字",
              fb2.cget("image") == "" and tk.Label.cget(fb2, "text") == "回退测试 Abc")
    finally:
        ui.font_file = real_font_file
        ui._TEXT_PNG_CACHE.clear()

    print("\n=== F. 性能与缓存（界面不能被拖慢）===")
    ui._TEXT_PNG_CACHE.clear()
    t0 = time.perf_counter()
    ui.text_png(SAMPLE, FAMILY, 12, "normal", "#1F2937", linespace=17, ascent=13)
    first = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    for _ in range(50):
        ui.text_png(SAMPLE, FAMILY, 12, "normal", "#1F2937", linespace=17, ascent=13)
    cached = (time.perf_counter() - t0) / 50 * 1000
    check("单行渲染耗时可接受（< 15ms）", first < 15, f"{first:.2f} ms")
    check("缓存命中极快（< 0.3ms）", cached < 0.3, f"{cached:.4f} ms")
    for i in range(ui._TEXT_PNG_CACHE_MAX + 60):
        ui.text_png(f"缓存压力测试 {i}", FAMILY, 12, "normal", "#1F2937",
                    linespace=17, ascent=13)
    check("缓存有上限（不会无限涨）", len(ui._TEXT_PNG_CACHE) <= ui._TEXT_PNG_CACHE_MAX,
          f"{len(ui._TEXT_PNG_CACHE)} ≤ {ui._TEXT_PNG_CACHE_MAX}")

    print("\n=== G. 实屏对比：ClearType vs 超采样（截屏统计彩色像素）===")
    native = tk.Label(root, text=SAMPLE, font="ShunYiBase", fg="#1F2937", bg="#FFFFFF",
                      anchor="w", padx=1, pady=1)
    native.pack(anchor="w", padx=10, pady=(10, 2))
    shot_holder = ui.TextLabel(root, text=SAMPLE, font="ShunYiBase", fg="#1F2937",
                               bg="#FFFFFF", anchor="w")
    shot_holder.pack(anchor="w", padx=10, pady=(2, 10))
    root.update_idletasks()
    time.sleep(1.0)
    root.lift()
    root.update()
    time.sleep(0.6)
    root.update()
    x0, y0 = root.winfo_rootx(), root.winfo_rooty()
    a_box = (native.winfo_rootx() - x0, native.winfo_rooty() - y0,
             native.winfo_width(), native.winfo_height())
    b_box = (shot_holder.winfo_rootx() - x0, shot_holder.winfo_rooty() - y0,
             shot_holder.winfo_width(), shot_holder.winfo_height())
    full = ImageGrab.grab(bbox=(x0, y0, x0 + root.winfo_width(), y0 + root.winfo_height()))
    crop_a = full.crop((a_box[0], a_box[1], a_box[0] + a_box[2], a_box[1] + a_box[3]))
    crop_b = full.crop((b_box[0], b_box[1], b_box[0] + b_box[2], b_box[1] + b_box[3]))
    ca, cb = colorful_ratio(crop_a), colorful_ratio(crop_b)
    check("原生 ClearType 确实带彩边（对照组有效）", ca > 0.01, f"{ca * 100:.2f}%")
    check("超采样渲染的彩边显著更少（不到原生的 1/3）", cb < ca / 3,
          f"原生 {ca * 100:.2f}% → 超采样 {cb * 100:.2f}%")

    print("\n=== H. 换行禁则（标点不能落在行首）===")
    cases = [
        ("这个组合不行：必须至少包含一个 Ctrl / Alt / Shift"
         "（不能只用单个键，否则会干扰你正常打字）。", 277),
        ("这是一段较长的说明文字，用来验证换行是否正常（含括号、逗号，以及句号。）"
         "后面还接着一些内容。", 200),
        ("模型下载完成/失败、启动提示等），只记日志。默认开启；改动立即生效，"
         "不需要点「保存」。", 220),
    ]
    bad = []
    for text, limit in cases:
        lines = ui.wrap_lines(text, limit, ruler)
        for line in lines[1:]:                   # 第 2 行起不能以标点开头
            if line[:1] in ui._NO_LINE_START:
                bad.append(f"「{line[:10]}…」以「{line[:1]}」开头")
    check(f"{len(cases)} 段文本换行后，第 2 行起都没有以标点开头", not bad,
          "；".join(bad) or "无")

    lines = ui.wrap_lines("必须至少包含一个 Ctrl / Alt / Shift，再加一个主键。", 190, ruler)
    split_words = [w for w in ("Ctrl", "Alt", "Shift")
                   if not any(w in ln for ln in lines)]
    check("英文单词不会被拆到两行（Ctrl / Alt / Shift 各自完整）",
          not split_words, str(split_words) or "无")

    lines = ui.wrap_lines("快捷键要求：至少包含一个 Ctrl / Alt / Shift。", 10 ** 6, ruler)
    check("放得下时不换行（只有 1 行）", len(lines) == 1, f"{len(lines)} 行")

    print("\n=== I. 换行跟随控件实际宽度（始终贴右边界）===")
    box = tk.Frame(root, bg="#FFFFFF", width=420, height=90)
    box.pack_propagate(False)          # 固定宽度，用来模拟不同的可用宽度
    box.pack()
    lbl = ui.TextLabel(box, text=LONG, font="ShunYiBase", fg="#1F2937", bg="#FFFFFF",
                       anchor="w", justify="left", wraplength=200)
    lbl.pack(fill="x")

    def settle():
        root.update_idletasks()
        time.sleep(0.35)
        root.update()
        return lbl._photo.width() if lbl._photo else 0

    w1 = settle()
    check("按控件实际宽度出图（不再被 wraplength 卡住）", w1 > 220,
          f"控件 {lbl.winfo_width()} → 图片 {w1}（wraplength 只有 200）")
    box.configure(width=260)
    w2 = settle()
    check("控件变窄 → 重新断行（图片跟着变窄）", w2 < w1 and w2 <= 260, f"{w1} → {w2}")
    box.configure(width=560)
    w3 = settle()
    check("控件变宽 → 重新断行（图片跟着变宽）", w3 > w2, f"{w2} → {w3}")
    avail = lbl._avail_width()
    widest = max(lbl._measure(ln) for ln in ui.wrap_lines(LONG, avail, lbl._measure))
    check("最长行填到可用宽度的 85% 以上（不提前折行）", widest >= avail * 0.85,
          f"最长行 {widest} / 可用 {avail} = {widest / max(1, avail):.0%}")
    lbl.destroy()
    box.destroy()
    root.destroy()

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n=== 结果：{passed}/{total} 项通过 ===")
    if passed != total:
        print("未通过：")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  ✗ {name}  {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
