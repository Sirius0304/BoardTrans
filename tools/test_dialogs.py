# -*- coding: utf-8 -*-
"""统一风格对话框：回归测试（对应"恢复默认设置弹两次 + 弹窗样式不一致"报障）。

检查项：
  A. 源码级：应用内不再使用系统 tkinter.messagebox 弹窗
  B. 「恢复默认设置」只弹**一次**确认框，不再弹第二次结果提示
  C. 对话框样式与设置界面同源：字体族/配色/圆角/语义色/按钮类型逐项核对
  D. 间距与尺寸过 ui.S()（随 DPI 缩放），按钮为自绘 RoundedButton
  E. 按钮回调能正确回传结果（确定/取消）

用法：F:\\shunyi\\.venv\\Scripts\\python.exe tools\\test_dialogs.py
"""
import io
import os
import re
import sys
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from app import config                                   # noqa: E402
import app.settings_window as sw                         # noqa: E402
from app import dialogs, ui_kit as ui                    # noqa: E402
from app.service import HotkeyService                    # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  —— {detail}" if detail else ""), flush=True)


def main():
    print("\n=== A. 源码级：应用内不再用系统 messagebox 弹窗 ===")
    bad = []
    for name in sorted(os.listdir(os.path.join(PROJECT, "app"))):
        if not name.endswith(".py"):
            continue
        path = os.path.join(PROJECT, "app", name)
        src = io.open(path, encoding="utf-8").read()
        for m in re.finditer(r"messagebox\.(showinfo|showwarning|showerror|askokcancel|askyesno)",
                             src):
            bad.append(f"{name}:{m.group(0)}")
    check("app/ 下没有 messagebox 弹窗调用（统一走 app.dialogs）", not bad, str(bad[:4]))

    sw_src = io.open(os.path.join(PROJECT, "app", "settings_window.py"), encoding="utf-8").read()
    check("设置窗口已接入 dialogs", "from . import dialogs" in sw_src
          and "dialogs.ask(" in sw_src and "dialogs.tell(" in sw_src)
    check("已删除「已恢复默认值」第二次提示", "已恢复默认值" not in sw_src,
          "源码中不应再出现该提示标题")

    print("\n=== B. 恢复默认设置：只弹一次确认框 ===")
    original = dict(config.load())
    service = HotkeyService(notify=lambda t, m: None)
    settings = sw.SettingsWindow(service)
    settings.open()
    time.sleep(2.5)
    if settings.root is None:
        check("设置窗口创建成功", False, "窗口未创建")
        return 1

    calls = {"ask": [], "tell": []}
    real_ask, real_tell = dialogs.ask, dialogs.tell
    try:
        dialogs.ask = lambda *a, **k: (calls["ask"].append((a[1] if len(a) > 1 else "", k)), True)[1]
        dialogs.tell = lambda *a, **k: calls["tell"].append((a[1] if len(a) > 1 else "", k))

        def make_dirty_first():
            # 先把档位改成「高质量」，制造"偏离默认"的状态（否则恢复默认后按实际值判定
            # 不会标脏 —— 这是 2026-09-29 定下的行为，不是缺陷）
            settings._quality_var.set("quality")
            settings._mark_dirty()

        settings.run_in_ui(make_dirty_first)
        time.sleep(0.4)
        settings.run_in_ui(lambda: settings._on_restore_defaults())
        time.sleep(0.5)
        check(f"恢复默认设置只弹 1 次确认框（实测 {len(calls['ask'])} 次）",
              len(calls["ask"]) == 1, str([c[0] for c in calls["ask"]]))
        check(f"恢复默认设置不再弹第二次提示（实测 {len(calls['tell'])} 次）",
              len(calls["tell"]) == 0, str([c[0] for c in calls["tell"]]))
        holder = {}

        def read_state():
            holder["hotkey"] = settings._pending_hotkey
            holder["quality"] = settings._pending_quality
            holder["dirty"] = settings._dirty

        settings.run_in_ui(read_state)
        check("恢复默认确实生效（快捷键 Alt+J / 档位 fast）",
              holder.get("hotkey") == "alt+j" and holder.get("quality") == "fast",
              f"{holder.get('hotkey')} / {holder.get('quality')}")
        check("恢复默认后状态回到默认（不再标脏，无需第二次提示）",
              holder.get("dirty") is False, f"dirty={holder.get('dirty')}")
    finally:
        dialogs.ask, dialogs.tell = real_ask, real_tell
        settings.run_in_ui(lambda: settings._load_from_config(force=True))

    print("\n=== C. 对话框样式与设置界面同源 ===")
    built = {}

    def build_dialog():
        """按真实模态流程构建：布局 + 首帧绘制都发生后再取样，最后自动关闭。"""
        top, result, close = dialogs._build(
            settings.root, "删除模型", "这里是一段说明文字，用来核对字体与配色是否与设置界面一致。",
            [{"text": "取消", "value": "cancel", "kind": "secondary"},
             {"text": "删除", "value": "ok", "kind": "danger"}], "err", "ok")
        info = {"bg": top.cget("bg"), "title": top.title(),
                "labels": [], "buttons": [], "result_before": result["value"]}

        def walk(w):
            yield w
            for c in w.winfo_children():
                yield from walk(c)

        def sample():
            # 到这一步 Tk 已完成几何管理与首帧绘制（卡片圆角、尺寸都收敛了）
            for w in walk(top):
                if isinstance(w, ui.RoundedButton):
                    info["buttons"].append({"text": w.cget("text"), "kind": w._kind,
                                            "height": w.winfo_reqheight()})
                elif w.winfo_class() == "Label":
                    info["labels"].append({"text": w.cget("text"),
                                           "font": str(w.cget("font")),
                                           "fg": w.cget("fg"), "bg": w.cget("bg")})
                elif isinstance(w, ui.RoundedCard):
                    cv = w.canvas
                    info["card"] = {
                        "has_rounded_item": any(cv.type(i) in ("image", "arc")
                                               for i in cv.find_all()),
                        "pad": w._pad,
                        "size": (cv.winfo_width(), cv.winfo_height()),
                    }
                elif w.winfo_class() == "Frame" and w.cget("bg") in (
                        ui.COLOR_WARN_FG, ui.COLOR_ERR_TEXT, ui.COLOR_PRIMARY_TEXT):
                    info["accent"] = w.cget("bg")
            info["size"] = (top.winfo_width(), top.winfo_height())
            info["mapped"] = bool(top.winfo_ismapped())
            top.after(60, lambda: close("ok"))

        top.after(500, sample)          # 留足时间让圆角卡片完成高度自适应与首帧绘制
        try:
            top.grab_set()
        except Exception:  # noqa: BLE001
            pass
        top.wait_window(top)            # 真实模态：等事件循环把界面画好再自动关闭
        info["result_after"] = result["value"]
        try:
            top.destroy()
        except Exception:  # noqa: BLE001
            pass
        built.update(info)

    settings.run_in_ui(build_dialog)
    time.sleep(0.5)

    check("对话框窗口底色 = 页面色（与设置界面一致）",
          built.get("bg") == ui.COLOR_PAGE, f"{built.get('bg')} vs {ui.COLOR_PAGE}")
    labels = built.get("labels", [])
    check(f"对话框文字控件齐全（{len(labels)} 个）", len(labels) >= 2, str(len(labels)))
    bad_font = [lb for lb in labels
                if lb["font"].split()[0] not in (ui.UI_FONT, ui.UI_FONT_BOLD)]
    check("文字字体与设置界面同源（ShunYiBase / ShunYiBold）", not bad_font, str(bad_font[:2]))
    bad_bg = [lb for lb in labels if lb["bg"] != ui.COLOR_CARD]
    check("文字底为卡片白（#FFFFFF）", not bad_bg, str(bad_bg[:2]))
    check("存在圆角卡片（超采样图片或圆弧绘制）", bool(built.get("card", {}).get("has_rounded_item")),
          str(built.get("card")))
    check("卡片内边距过 ui.S()（随 DPI 缩放）", built.get("card", {}).get("pad") == ui.S(16),
          f"{built.get('card', {}).get('pad')} vs {ui.S(16)}")
    btns = built.get("buttons", [])
    check(f"按钮是自绘 RoundedButton（{len(btns)} 个）", len(btns) == 2, str(btns))
    kinds = sorted(b["kind"] for b in btns)
    check("按钮样式分别为 danger + secondary（语义清晰）",
          kinds == ["danger", "secondary"], str(kinds))
    check("删除类确认框左侧强调条用错误色（TONES.err）",
          built.get("accent") == ui.TONES["err"][2],
          f"{built.get('accent')} vs {ui.TONES['err'][2]}")
    card_size = built.get("card", {}).get("size", (0, 0))
    check("圆角卡片高度自适应成功（按内容撑开，尺寸合理）",
          card_size[0] > 150 and card_size[1] > 80,
          f"卡片 {card_size}，窗口 {built.get('size')}，已映射={built.get('mapped')}")
    check("按钮回调能回传结果（close(ok) → ok）",
          built.get("result_before") is None and built.get("result_after") == "ok",
          f"{built.get('result_before')} → {built.get('result_after')}")

    print("\n=== D. 各调用点的语义色与文案 ===")
    tone_map = []
    for m in re.finditer(r"dialogs\.(ask|tell)\(\s*\n?\s*self\.root,\s*\"([^\"]+)\"", sw_src):
        head = sw_src[m.start():m.start() + 600]
        tone = re.search(r'tone="(\w+)"', head)
        tone_map.append((m.group(2), m.group(1), tone.group(1) if tone else "info"))
    expected = {
        "删除模型": ("ask", "err"), "删除完成": ("tell", "ok"), "无法删除": ("tell", "err"),
        "删除失败": ("tell", "err"), "恢复默认设置": ("ask", "warn"),
        "放弃修改": ("ask", "warn"), "开始下载模型": ("ask", "info"),
    }
    problems = []
    for title, kind, tone in tone_map:
        if title in expected:
            want_kind, want_tone = expected[title]
            if (kind, tone) != (want_kind, want_tone):
                problems.append(f"{title}: 实测({kind},{tone}) 期望({want_kind},{want_tone})")
    check(f"关键弹窗的类型与语义色正确（核对 {len(tone_map)} 处）", not problems, str(problems))
    check(f"设置窗口内的弹窗调用点 ≥ 10 处（实测 {len(tone_map)}）", len(tone_map) >= 10,
          str(len(tone_map)))

    print("\n=== E. 「恢复默认设置」确认框：居中 + 无说明文字 + 两个按钮 ===")

    def probe_restore_dialog():
        root = settings.root
        top, _r, _c = dialogs._build(
            root, "恢复默认设置", "",
            [{"text": "取消", "value": "cancel", "kind": "secondary"},
             {"text": "恢复默认", "value": "ok", "kind": "primary"}],
            "warn", "ok", frameless=True)
        dialogs._center_on_parent(top, root)
        # 真实使用时由 wait_window 的事件循环完成收敛；这里手动跑几轮
        for _ in range(8):
            top.update_idletasks()
            top.update()
            time.sleep(0.08)
        texts, buttons = [], []

        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, ui.TextLabel):
                    texts.append(str(c.cget("text")))
                if isinstance(c, ui.RoundedButton):
                    buttons.append(str(c.cget("text")))
                walk(c)

        walk(top)
        style = 0
        if os.name == "nt":
            try:
                import ctypes                            # noqa: E402
                style = ctypes.windll.user32.GetWindowLongW(int(top.frame(), 16), -16)
            except Exception:  # noqa: BLE001
                style = 0
        off = (top.winfo_rootx() + top.winfo_reqwidth() // 2
               - (root.winfo_rootx() + root.winfo_width() // 2),
               top.winfo_rooty() + top.winfo_reqheight() // 2
               - (root.winfo_rooty() + root.winfo_height() // 2))
        out = {"texts": texts, "buttons": buttons, "off": off, "style": hex(style),
               "has_close": bool(style & 0x00080000),
               "has_caption": bool(style & 0x00C00000),
               "req": (top.winfo_reqwidth(), top.winfo_reqheight())}
        top.destroy()
        return out

    got = settings.run_in_ui(probe_restore_dialog, timeout=10) or {}
    check("弹窗里只剩标题一处文字（说明文字已移除）",
          got.get("texts") == ["恢复默认设置"], str(got.get("texts")))
    check("只有「恢复默认」与「取消」两个按钮",
          set(got.get("buttons") or []) == {"取消", "恢复默认"}, str(got.get("buttons")))
    off = got.get("off") or (999, 999)
    check("弹窗居于设置窗口正中（客户区中心对齐，误差 ≤2px）",
          abs(off[0]) <= 2 and abs(off[1]) <= 2, f"偏移 Δx={off[0]} Δy={off[1]}")
    check("整个标题栏都已去掉（WS_CAPTION=False，弹窗就是那张圆角卡片）",
          not got.get("has_caption"), f"样式 {got.get('style')}")
    check("自然也没有关闭按钮（WS_SYSMENU=False）", not got.get("has_close"),
          f"样式 {got.get('style')}")

    def probe_normal_dialog():
        """默认参数（closable=True）的弹窗应当仍有正常的标题栏与关闭按钮。"""
        root = settings.root
        top, _r, _c = dialogs._build(
            root, "删除模型", "要删吗？",
            [{"text": "取消", "value": "cancel", "kind": "secondary"},
             {"text": "删除", "value": "ok", "kind": "danger"}], "err", "ok")
        top.update_idletasks()
        top.update()
        style = 0
        if os.name == "nt":
            try:
                import ctypes                            # noqa: E402
                style = ctypes.windll.user32.GetWindowLongW(int(top.frame(), 16), -16)
            except Exception:  # noqa: BLE001
                style = 0
        top.destroy()
        return bool(style & 0x00080000), bool(style & 0x00C00000)

    normal_close, normal_caption = settings.run_in_ui(probe_normal_dialog,
                                                      timeout=10) or (False, False)
    check("其他确认框（默认参数）仍带关闭按钮，没被一起改掉", bool(normal_close),
          "closable 默认 True")
    check("其他确认框仍然保留标题栏（没被一起改成无边框）", bool(normal_caption),
          "frameless 默认 False")

    def probe_keys():
        """无边框不该影响键盘：Esc 仍应取消（模态抓取 + 焦点强制都在）。"""
        root = settings.root
        top, r, _c = dialogs._build(
            root, "键盘测试", "",
            [{"text": "取消", "value": "cancel", "kind": "secondary"},
             {"text": "确定", "value": "ok", "kind": "primary"}],
            "info", "ok", frameless=True)
        try:
            top.grab_set()
        except Exception:  # noqa: BLE001
            pass
        top.focus_force()
        top.update_idletasks()
        top.update()
        top.event_generate("<Escape>")
        top.update()
        return r.get("value")

    check("无边框后 Esc 仍能取消（键盘行为没被破坏）",
          settings.run_in_ui(probe_keys, timeout=10) == "cancel", "模拟按下 Esc")

    settings.shutdown()
    time.sleep(0.5)
    config.update(**{k: original[k] for k in original})

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n=== 结果：{passed}/{len(RESULTS)} 项通过 ===")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  未通过：{name}  {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
