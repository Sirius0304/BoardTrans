# -*- coding: utf-8 -*-
"""清晰度与 DPI 适配：回归测试（文字锐利、开关状态分明、随缩放等比放大）。

为什么需要这组检查（2026-09-29 报障"文字和开关发虚"）：
  1) 进程若未声明 DPI 感知，Windows 会把窗口**位图拉伸** → 在 125%/150% 屏幕上全糊；
  2) 字体若用"磅"（10 磅 = 13.34px 小数），字形落不到整像素上也发虚；
  3) 自绘开关/圆角若用样条逼近而不是真圆弧，边角会发毛、状态对比也弱。

本用例逐条把这三点做成确定性断言。

用法：F:\\shunyi\\.venv\\Scripts\\python.exe tools\\test_ui_crisp.py
"""
import os
import sys
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from app import config                        # noqa: E402
import app.settings_window as sw              # noqa: E402
from app import ui_kit as ui                  # noqa: E402
from app.service import HotkeyService         # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  —— {detail}" if detail else ""), flush=True)


def luminance(hex_color: str) -> float:
    """粗略亮度（0~255），用于判断两种状态是否"看起来明显不同"。"""
    c = hex_color.lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return 0.299 * r + 0.587 * g + 0.114 * b


class AutoAnswer:
    @staticmethod
    def askokcancel(*a, **k):
        return True

    @staticmethod
    def showinfo(*a, **k):
        return None

    @staticmethod
    def showerror(*a, **k):
        return None

    @staticmethod
    def showwarning(*a, **k):
        return None


def main():
    print("\n=== A. 进程 DPI 感知（发虚的头号原因）===")
    level = ui.enable_dpi_awareness()
    check("已声明 DPI 感知", level in ("per-monitor-v2", "per-monitor", "system"),
          f"级别={level}")
    check("系统 API 复核通过（IsProcessDPIAware）", ui.is_dpi_aware(), str(ui.is_dpi_aware()))

    sw.messagebox = AutoAnswer
    original = dict(config.load())
    service = HotkeyService(notify=lambda t, m: None)
    settings = sw.SettingsWindow(service)

    settings.open()
    time.sleep(2.5)
    if settings.root is None:
        check("设置窗口创建成功", False, "窗口未创建")
        return 1
    check("设置窗口创建成功", True)

    holder = {}

    def collect():
        root = settings.root
        dpi = root.winfo_fpixels("1i") or 96.0
        # 字体：负数 size 表示"像素"，必须是整数且与 DPI 匹配
        fonts = {}
        for name in ("ShunYiBase", "ShunYiBold", "ShunYiTitle", "ShunYiSmall", "ShunYiMono",
                     "TkDefaultFont", "TkTextFont", "TkMenuFont"):
            # 注意：必须读 font configure，不能读 font actual ——
            # actual 会把像素字号归一化成"磅"（-13 会读成 10），看起来像没生效
            size = root.tk.call("font", "configure", name, "-size")
            fonts[name] = int(size)
        # 开关：两态的轨道颜色/旋钮位置/旋钮描边
        var = settings._notify_enabled_var
        toggle = None

        def walk(w):
            yield w
            for c in w.winfo_children():
                yield from walk(c)

        for w in walk(root):
            if isinstance(w, ui.ToggleSwitch):
                toggle = w
                break
        toggle_state = {}
        if toggle is not None:
            for state in (True, False):
                var.set(state)
                toggle._draw()
                fills = [toggle.itemcget(i, "fill") for i in toggle.find_all()
                         if toggle.type(i) in ("arc", "rectangle")]
                ovals = [i for i in toggle.find_all() if toggle.type(i) == "oval"]
                knob = ovals[0] if ovals else None
                toggle_state[state] = {
                    "track": next((f for f in fills if f and f != ""), ""),
                    "knob_x": (toggle.coords(knob)[0] + toggle.coords(knob)[2]) / 2 if knob else None,
                    "knob_fill": toggle.itemcget(knob, "fill") if knob else "",
                    "knob_outline": toggle.itemcget(knob, "outline") if knob else "",
                    "arc_items": sum(1 for i in toggle.find_all() if toggle.type(i) == "arc"),
                }
            var.set(True)
        # 卡片：圆角是否为真圆弧（存在 arc 项、且没有 smooth 多边形）
        card_canvas = None
        for w in walk(root):
            if isinstance(w, ui.RoundedCard):
                card_canvas = w.canvas
                break
        card_info = {}
        if card_canvas is not None:
            card_info = {
                "arcs": sum(1 for i in card_canvas.find_all() if card_canvas.type(i) == "arc"),
                "polygons": sum(1 for i in card_canvas.find_all()
                                if card_canvas.type(i) == "polygon"),
                "width": card_canvas.winfo_width(),
                "height": card_canvas.winfo_height(),
            }
        holder.update({
            "dpi": dpi,
            "scale": ui.scale(),
            "S10": ui.S(10),
            "fonts": fonts,
            "toggle": toggle_state,
            "card": card_info,
            "win": (root.winfo_width(), root.winfo_height()),
            "screen": (root.winfo_screenwidth(), root.winfo_screenheight()),
            "aware": ui.dpi_awareness(),
        })

    settings._queue.put(("call", collect))
    time.sleep(1.2)
    data = holder
    dpi = data.get("dpi", 96.0)
    k = data.get("scale", 1.0)

    print("\n=== B. 字号用整数像素（磅值会算出小数 → 字形发虚）===")
    fonts = data.get("fonts", {})
    non_pixel = {n: s for n, s in fonts.items() if s >= 0}
    check("所有命名字体都是「像素」单位（负 size）", not non_pixel, str(non_pixel))
    expect_base = -max(9, int(round(10 * dpi / 72.0)))
    check("基准字号与 DPI 匹配（10 磅 → 整数像素）",
          fonts.get("ShunYiBase") == expect_base,
          f"实际={fonts.get('ShunYiBase')} 期望={expect_base}（DPI {dpi:.0f}）")
    expect_small = -max(9, int(round(9 * dpi / 72.0)))
    check("小字号（说明文字）同样按 DPI 换算",
          fonts.get("ShunYiSmall") == expect_small,
          f"实际={fonts.get('ShunYiSmall')} 期望={expect_small}")

    print("\n=== C. 开关：状态对比分明 ===")
    tstate = data.get("toggle", {})
    on, off = tstate.get(True, {}), tstate.get(False, {})
    check("找到开关控件", bool(on) and bool(off))
    if on and off:
        d_lum = abs(luminance(on["track"]) - luminance(off["track"]))
        check("开/关轨道颜色对比明显（亮度差 ≥ 60）", d_lum >= 60,
              f"开={on['track']} 关={off['track']} 亮度差={d_lum:.0f}")
        check("开/关轨道颜色是两种不同色", on["track"] != off["track"],
              f"{on['track']} vs {off['track']}")
        travel = abs((on["knob_x"] or 0) - (off["knob_x"] or 0))
        check("旋钮位置随状态变化（位移 ≥ 8px）", travel >= 8, f"位移={travel:.1f}px")
        check("旋钮为白色且带 1px 描边（边界清楚）",
              on["knob_fill"] == "#FFFFFF" and bool(on["knob_outline"]),
              f"填充={on['knob_fill']} 描边={on['knob_outline']}")
        check("轨道用真圆弧绘制（不是样条逼近）", on["arc_items"] >= 2,
              f"arc 项={on['arc_items']}")

    print("\n=== D. 圆角用真圆弧（边角不发毛）===")
    card = data.get("card", {})
    check("卡片画布存在", bool(card))
    if card:
        check("圆角由圆弧构成", card["arcs"] >= 4, f"arc 项={card['arcs']}")
        check("不再使用 smooth 多边形（旧的发毛画法）", card["polygons"] == 0,
              f"polygon 项={card['polygons']}")
        check("卡片有实际尺寸（内容可见、未被裁剪）",
              card["width"] > 100 and card["height"] > 40,
              f"{card['width']}x{card['height']}")

    print("\n=== E. 尺寸随 DPI 等比（高 DPI 下不缩水）===")
    check("缩放系数 = DPI/96", abs(k - max(1.0, dpi / 96.0)) < 0.01, f"k={k}")
    check("像素换算 S(10) 与缩放一致", data.get("S10") == max(1, round(10 * k)),
          f"S(10)={data.get('S10')} 期望={max(1, round(10 * k))}")
    sw_, sh_ = data.get("screen", (0, 0))
    w, h = data.get("win", (0, 0))
    expect_w = int(min(max(sw_ * 0.30, 470 * k), 540 * k))
    check("窗口宽度按屏幕与 DPI 计算", abs(w - expect_w) <= 2,
          f"实际={w} 期望={expect_w}（屏幕 {sw_}）")
    check("窗口未超过屏幕（不会跑到屏幕外）", w <= sw_ and h <= sh_,
          f"{w}x{h} vs 屏幕 {sw_}x{sh_}")

    print("\n=== G. 文字锐利度实测（软边比例 + 笔画过渡宽度）===")
    # 判据（两个互补指标）：
    #   * 软边比例：深色文字笔画外圈的中间灰阶像素占比 —— 直接对应肉眼的"糊"；
    #   * 过渡宽度：边缘跨越所需的像素数（1px 锐利 / 2~3px 发虚）。
    #   实测参考：黑体约 5% 软边（内嵌点阵 + 灰度抗锯齿）；微软雅黑约 0.1~0.2%（ClearType）。
    try:
        from PIL import ImageGrab                     # noqa: PLC0415
        shot = {}

        def _measure_targets():
            """只取**深色文字**（卡片标题）：灰色说明文字的抗锯齿像素天然落在中间灰阶区间，
            会把度量污染成假阳性。"""
            return [(w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height())
                    for w in settings._title_labels]

        def _raise_window():
            root_w = settings.root
            root_w.attributes("-topmost", True)
            root_w.lift()
            root_w.focus_force()
            root_w.update_idletasks()
            shot["targets"] = _measure_targets()
            # 窗口尺寸必须在**窗口线程**里取（tkinter 非线程安全）
            shot["win"] = (root_w.winfo_width(), root_w.winfo_height())

        def _grab_bbox():
            """只记录窗口位置与尺寸（窗口线程里做，很快）。"""
            root_w = settings.root
            x, y = root_w.winfo_rootx(), root_w.winfo_rooty()
            shot["bbox"] = (x, y, x + root_w.winfo_width(), y + root_w.winfo_height())

        def _soft_ratio(crop):
            """软边比例：笔画外圈的中间灰阶像素占比（越高越"糊"）。"""
            px = crop.load()
            total = mid = 0
            for j in range(crop.height):
                for i in range(crop.width):
                    r, g, b = px[i, j]
                    total += 1
                    if max(r, g, b) - min(r, g, b) > 24:      # ClearType 彩色像素
                        continue
                    v = (r + g + b) // 3
                    if 40 < v < 215:
                        mid += 1
            return None if total < 150 else mid / total * 100

        def _edge_width(crop):
            """笔画边缘的过渡宽度中位数（像素）。

            这才是"糊不糊"的正统度量：1px 过渡 = 锐利；2~3px 过渡 = 发虚。
            注意：黑体走内嵌点阵、过渡会是 0px（锯齿硬边），0px 不代表更清晰，
            所以本指标只用于**绝对阈值**检查，不用于字体之间的比较。
            """
            px = crop.load()
            lum = [[(px[i, j][0] + px[i, j][1] + px[i, j][2]) / 3
                    for i in range(crop.width)] for j in range(crop.height)]
            flat = [v for row in lum for v in row]
            lo_v, hi_v = min(flat), max(flat)
            if hi_v - lo_v < 120:
                return None
            lo = lo_v + 0.25 * (hi_v - lo_v)
            hi = lo_v + 0.75 * (hi_v - lo_v)
            widths = []
            for j in range(crop.height):
                row = lum[j]
                i = 1
                while i < crop.width - 1:
                    if row[i - 1] <= lo and hi <= row[i + 1]:
                        k = i
                        cnt = 0
                        while k < crop.width and row[k] < hi:
                            if row[k] > lo:
                                cnt += 1
                            k += 1
                        widths.append(cnt)
                        # 关键：保证 i 前进。曾写成 i = k，当 row[i] 本身已达 hi 时
                        # k == i → 不前进 → 死循环空转（2026-09-29 实测：测试脚本
                        # 就是这么被卡死、被外部超时杀掉的）
                        i = k if k > i else i + 1
                    else:
                        i += 1
            if not widths:
                return None
            widths.sort()
            return widths[len(widths) // 2]

        def _metrics_of(image, region_list):
            out = []
            for (wx, wy, ww, wh) in region_list:
                rx, ry = wx - shot["origin"][0], wy - shot["origin"][1]
                if ww < 30 or wh < 10:
                    continue
                # 只测**完全落在窗口可视区**内的文字：滚动到区外的控件坐标会跑到窗口之外，
                # 截出来的不是文字 → 假数据（2026-09-29 实测踩过）
                if (rx < 0 or ry < 0
                        or rx + min(ww, 300) > shot["win"][0]
                        or ry + wh > shot["win"][1]):
                    continue
                crop = image.crop((rx, ry, rx + min(ww, 300), ry + wh)).convert("RGB")
                flat = [sum(crop.getpixel((i, j))) / 3
                        for j in range(crop.height) for i in range(crop.width)]
                if not flat or max(flat) - min(flat) < 120:
                    continue                  # 对比度不足（无文字/未抓对窗口）
                out.append((_edge_width(crop), _soft_ratio(crop)))
            return out

        def _use_family(fam):
            def _apply():
                for name in ("ShunYiBase", "ShunYiBold", "ShunYiTitle", "ShunYiSmall"):
                    settings.root.tk.call("font", "configure", name, "-family", fam)
                settings.root.update_idletasks()
            return _apply

        settings._queue.put(("call", _raise_window))
        time.sleep(1.2)                    # 等窗口真的抬到最前并重绘完
        settings._queue.put(("call", _grab_bbox))
        time.sleep(0.8)
        # 截屏放在**主线程**：ImageGrab 实测 ~127ms，放窗口线程会把界面按住不放
        #（心跳的慢轮警告日志能证明）。PIL 不碰 Tk，主线程调用安全。
        img = ImageGrab.grab(bbox=shot["bbox"]) if shot.get("bbox") else None
        shot["origin"] = (shot["bbox"][0], shot["bbox"][1]) if shot.get("bbox") else (0, 0)
        m_yahei = _metrics_of(img, shot.get("targets", [])) if img else []
        if not m_yahei:
            print("      ⚠ 跳过（截屏不可用或未采到有效文字像素）")
        else:
            widths = sorted(w for w, _h in m_yahei if w is not None)
            halos = sorted(h for _w, h in m_yahei if h is not None)
            med_w = widths[len(widths) // 2] if widths else 0
            med_h = halos[len(halos) // 2] if halos else 0.0
            check(f"文字笔画过渡宽度锐利（中位数 {med_w}px，阈值 ≤ 2px）", med_w <= 2,
                  f"共 {len(m_yahei)} 处深色文字")
            # 说明：软边比例**不适合设绝对阈值** —— 粗体 ClearType 文字本身就带大量
            # 低彩度的过渡像素（实测雅黑粗体 11%，而其 13px 正文只有 0.1%）。
            # 因此它只用于"同一段文字、换字体"的相对比较（见下面的对照）。
            print(f"      参考：雅黑粗体标题软边比例 {med_h:.1f}%（仅用于相对比较）")
            # 对照实验：临时切回黑体，用**软边比例**做相对比较。
            # 不用"过渡宽度"比较：黑体走内嵌点阵、过渡 0px 属锯齿硬边，不代表更清晰。
            settings._queue.put(("call", _use_family("黑体")))
            time.sleep(1.2)
            settings._queue.put(("call", lambda: shot.update(targets2=_measure_targets())))
            settings._queue.put(("call", _grab_bbox))
            time.sleep(1.0)
            img2 = ImageGrab.grab(bbox=shot["bbox"]) if shot.get("bbox") else None
            m_hei = _metrics_of(img2, shot.get("targets2") or []) if img2 else []
            halos_hei = sorted(h for _w, h in m_hei if h is not None)
            if halos_hei:
                med_h_hei = halos_hei[len(halos_hei) // 2]
                check("对照组：雅黑的软边比例不高于黑体（更少「灰晕」）",
                      med_h <= med_h_hei, f"雅黑 {med_h:.1f}% ≤ 黑体 {med_h_hei:.1f}%")
            else:
                print("      ⚠ 对照组跳过（未采到有效像素）")
            settings._queue.put(("call", _use_family(sw.FONT_FAMILY)))
            time.sleep(0.8)
        settings._queue.put(("call", lambda: settings.root.attributes("-topmost", False)))
    except ImportError:
        print("      ⚠ 跳过（缺少 Pillow；该检查仅用于开发环境，运行时不依赖）")

    print("\n=== H. 组件像素对齐与对比度（「模糊感」的组件侧来源）===")
    audit = {}

    def _audit_components():
        """审计所有自绘组件：坐标是否整像素、描边对比度、图标笔画宽度。"""
        bad_coords = []
        icon_strokes = []
        canvases = []

        def walk(w):
            yield w
            for c in w.winfo_children():
                yield from walk(c)

        for w in walk(settings.root):
            if isinstance(w, (ui.RoundedCard, ui.RoundedPanel, ui.RoundedButton,
                              ui.ToggleSwitch, ui.IconBadge)):
                cv = w.canvas if hasattr(w, "canvas") else w
                canvases.append(cv)
                for item in cv.find_all():
                    try:
                        coords = cv.coords(item)
                    except Exception:  # noqa: BLE001
                        continue
                    if any(isinstance(v, float) and v != int(v) for v in coords):
                        bad_coords.append((type(w).__name__, cv.type(item)))
                if isinstance(w, ui.IconBadge):
                    for item in cv.find_all():
                        try:
                            if cv.type(item) == "line":
                                icon_strokes.append(int(float(cv.itemcget(item, "width"))))
                        except Exception:  # noqa: BLE001
                            pass
        audit["bad_coords"] = bad_coords
        audit["icon_min_stroke"] = min(icon_strokes) if icon_strokes else 0
        audit["n_components"] = len(canvases)

    settings.run_in_ui(_audit_components)
    check(f"自绘组件坐标全部整像素（审计 {audit.get('n_components', 0)} 个组件，"
          f"非整数坐标 {len(audit.get('bad_coords', []))} 个）",
          not audit.get("bad_coords"), str(audit.get("bad_coords", [])[:4]))

    def _lum(hex_color):
        c = hex_color.lstrip("#")
        return 0.299 * int(c[0:2], 16) + 0.587 * int(c[2:4], 16) + 0.114 * int(c[4:6], 16)

    diff_border = 255 - _lum(ui.COLOR_BORDER)
    check(f"卡片描边与白底对比足够（亮度差 {diff_border:.0f}，阈值 ≥ 25）",
          diff_border >= 25, f"描边色 {ui.COLOR_BORDER}")
    check(f"图标笔画 ≥ 2px（实测最小 {audit.get('icon_min_stroke', 0)}px）",
          audit.get("icon_min_stroke", 0) >= 2, f"{audit.get('icon_min_stroke')}px")

    settings.shutdown()
    time.sleep(0.5)

    print("\n=== F. 模拟 150% 缩放（本机 100%，靠注入 DPI 验证高缩放下同样清晰）===")
    real_fpixels = sw.tk.Tk.winfo_fpixels

    def fake_fpixels(self, spec="1i"):
        if spec == "1i":
            return 144.0          # 150% 缩放
        return real_fpixels(self, spec)

    sw.tk.Tk.winfo_fpixels = fake_fpixels
    try:
        ui.set_scale(1.0)                     # 先复位，看窗口自己按 DPI 重算
        service2 = HotkeyService(notify=lambda t, m: None)
        settings2 = sw.SettingsWindow(service2)
        settings2.open()
        time.sleep(2.5)
        holder2 = {}

        def collect2():
            root = settings2.root
            holder2["base"] = int(root.tk.call("font", "configure", "ShunYiBase", "-size"))
            holder2["small"] = int(root.tk.call("font", "configure", "ShunYiSmall", "-size"))
            holder2["scale"] = ui.scale()
            holder2["S10"] = ui.S(10)
            holder2["S46"] = ui.S(46)
            holder2["win"] = (root.winfo_width(), root.winfo_height())
            tog = None

            def walk(w):
                yield w
                for c in w.winfo_children():
                    yield from walk(c)

            for w in walk(root):
                if isinstance(w, ui.ToggleSwitch):
                    tog = w
                    break
            holder2["toggle_w"] = tog.winfo_reqwidth() if tog else 0
            holder2["sw"] = root.winfo_screenwidth()

        if settings2.root is not None:
            settings2._queue.put(("call", collect2))
            time.sleep(1.2)
            check("150% 下缩放系数 = 1.5", abs(holder2.get("scale", 0) - 1.5) < 0.02,
                  str(holder2.get("scale")))
            check("150% 下基准字号按 DPI 放大为 20px",
                  holder2.get("base") == -20, str(holder2.get("base")))
            check("150% 下小字号放大为 18px", holder2.get("small") == -18,
                  str(holder2.get("small")))
            check("150% 下设计像素等比放大（S(10)=15，S(46)=69）",
                  holder2.get("S10") == 15 and holder2.get("S46") == 69,
                  f"S(10)={holder2.get('S10')} S(46)={holder2.get('S46')}")
            expect_w150 = int(min(max(holder2.get("sw", 2560) * 0.30, 470 * 1.5), 540 * 1.5))
            check(f"150% 下窗口宽度按屏幕与 DPI 计算（期望 {expect_w150}）",
                  abs(holder2.get("win", (0, 0))[0] - expect_w150) <= 3, str(holder2.get("win")))
            check("150% 下开关控件等比放大（46×1.5=69）",
                  holder2.get("toggle_w") == 69, str(holder2.get("toggle_w")))
            settings2.shutdown()
        else:
            check("150% 下设置窗口创建成功", False, "窗口未创建")
        time.sleep(0.5)
    finally:
        sw.tk.Tk.winfo_fpixels = real_fpixels
        ui.set_scale(1.0)
        config.update(**{k2: original[k2] for k2 in original})

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n=== 结果：{passed}/{len(RESULTS)} 项通过 ===")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  未通过：{name}  {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
