# -*- coding: utf-8 -*-
"""统一风格对话框：回归测试（对应"恢复默认设置弹两次 + 弹窗样式不一致"报障）。

检查项：
  A. 源码级：应用内不再使用系统 tkinter.messagebox 弹窗
  B. 「恢复默认设置」只弹**一次**确认框，不再弹第二次结果提示
  C. 对话框样式与设置界面同源：字体族/配色/圆角/语义色/按钮类型逐项核对
  D. 间距与尺寸过 ui.S()（随 DPI 缩放），按钮为自绘 RoundedButton
  E. 按钮回调能正确回传结果（确定/取消）
  F. 删除模型**之后**不再弹任何窗（源码级 + 行为级双重验证，删除前确认框保留）
  G. 弹窗不闪烁：创建即透明、定位收敛才现身，首帧可见就在正中央且此后不跳动

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
from app import dialogs, models, ui_kit as ui           # noqa: E402
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

    print("\n=== B. 恢复默认设置：不弹确认框，点了就直接生效 ===")
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
        check(f"恢复默认不再弹任何确认框（实测 {len(calls['ask'])} 次）",
              len(calls["ask"]) == 0, str([c[0] for c in calls["ask"]]))
        check(f"也不弹结果提示（实测 {len(calls['tell'])} 次）",
              len(calls["tell"]) == 0, str([c[0] for c in calls["tell"]]))
        holder = {}

        def read_state():
            holder["hotkey"] = settings._pending_hotkey
            holder["quality"] = settings._pending_quality
            holder["dirty"] = settings._dirty
            cfg = config.load()
            holder["cfg"] = (cfg.get("hotkey"), cfg.get("quality"), cfg.get("target_lang"),
                             cfg.get("idle_unload_minutes"), cfg.get("notify_enabled"))

        settings.run_in_ui(read_state)
        check("界面值已恢复默认（快捷键 Alt+J / 档位 fast）",
              holder.get("hotkey") == "alt+j" and holder.get("quality") == "fast",
              f"{holder.get('hotkey')} / {holder.get('quality')}")
        check("**配置已立刻落盘生效**（不需要用户再点「保存」）",
              holder.get("cfg") == ("alt+j", "fast", "en", 15, True), str(holder.get("cfg")))
        check("恢复后不留「未保存」标记（因为已经生效了）",
              holder.get("dirty") is False, f"dirty={holder.get('dirty')}")

        # 关键：去掉确认框后，"其他设置项的保存与应用"必须照常工作
        def change_then_save():
            settings._quality_var.set("quality")
            settings._mark_dirty()
            settings._on_save()

        settings.run_in_ui(change_then_save)
        time.sleep(0.6)
        check("恢复默认之后，其他设置项仍能正常保存生效（改档位 → 保存）",
              config.load().get("quality") == "quality",
              f"quality={config.load().get('quality')}")
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
        # 弹窗现在是"定位收敛才现身"（alpha=0 → 1），走一遍真实居中流程再取样
        dialogs._center_on_parent(top, settings.root)
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
    # 2026-10-05 样式改版：有说明文字时不再渲染卡片内标题（与窗口标题栏重复），
    # 只留灰色说明 —— 所以一个带 message 的弹窗只有 1 个文字控件。
    check(f"对话框文字控件齐全（{len(labels)} 个，仅说明文字）", len(labels) == 1, str(len(labels)))
    bad_font = [lb for lb in labels
                if lb["font"].split()[0] not in (ui.UI_FONT, ui.UI_FONT_BOLD)]
    check("文字字体与设置界面同源（BoardTransBase / BoardTransBold）", not bad_font, str(bad_font[:2]))
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
    # 2026-10-05 样式改版：左侧语义色竖线随加粗标题一起移除（主人要求）。
    # 锁死"没有强调条"—— 防止将来有人把竖线加回来而不告知。
    check("确认框已无左侧强调条（样式改版后不再渲染）",
          built.get("accent") is None, str(built.get("accent")))
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
        "删除模型": ("ask", "err"),
        "放弃修改": ("ask", "warn"),
    }
    # 注：「恢复默认设置」2026-10-01 起**不再弹确认框**（点了直接生效），故不在此表
    # 注：「开始下载模型」确认框 2026-10-01 起移除（点「下载」直接开始下载），故不在此表
    # 注：删除模型**之后**的结果弹窗（删除完成 / 已删除但有影响 / 删除未完成 /
    #     无法删除 / 删除失败）2026-10-01 起全部移除，结果只走卡片状态反馈，故不在此表
    problems = []
    for title, kind, tone in tone_map:
        if title in expected:
            want_kind, want_tone = expected[title]
            if (kind, tone) != (want_kind, want_tone):
                problems.append(f"{title}: 实测({kind},{tone}) 期望({want_kind},{want_tone})")
    check(f"关键弹窗的类型与语义色正确（核对 {len(tone_map)} 处）", not problems, str(problems))
    check(f"设置窗口内的弹窗调用点 ≥ 4 处（实测 {len(tone_map)}）", len(tone_map) >= 4,
          str(len(tone_map)))

    print("\n=== D1. 弹窗防闪烁：必须全程撤隐（源码级）===")
    # 这条是 2026-10-03 两次返工换来的：窗口合成由 dwm.exe（独立进程）完成，
    # 事后设 attributes("-alpha", 0) 挡不住那一帧（实测照样闪），唯一可靠的是
    # **创建即 withdraw + 定好尺寸位置后再 deiconify**。删掉任何一句都会闪回去。
    dlg_src = io.open(os.path.join(PROJECT, "app", "dialogs.py"), encoding="utf-8").read()
    _build_body = re.search(r"def _build\(.*?\n    return top, result, _close", dlg_src, re.S)
    _bs = _build_body.group(0) if _build_body else ""
    check("_build 里创建 Toplevel 后立刻 withdraw（防左上角闪烁的关键）",
          "tk.Toplevel(master)" in _bs and "top.withdraw()" in _bs,
          f"_build 方法体 {len(_bs)} 字符")
    _center_body = re.search(r"def _center_on_parent\(.*?\ndef ", dlg_src, re.S)
    _cs = _center_body.group(0) if _center_body else ""
    # 只看真正的代码行（去掉文档字符串），否则会匹配到注释里写的示例文字
    _code = "\n".join(ln for ln in _cs.splitlines()
                      if not ln.strip().startswith("#"))
    _code_nodoc = re.sub(r'"""[\s\S]*?"""', "", _code)
    check("_center_on_parent 先写 geometry 再 deiconify（一次写对，不二次挪动）",
          _code_nodoc.find("top.geometry(") != -1
          and _code_nodoc.find("top.geometry(") < _code_nodoc.find("top.deiconify()"),
          "geometry 与 deiconify 的先后顺序不对")
    check("定位逻辑不再依赖 alpha（alpha 挡不住 DWM 合成，留着是误导）",
          'attributes("-alpha"' not in _code_nodoc)
    check("正常路径只写一次 geometry（尺寸位置一次写死，不二次挪动）",
          _code_nodoc.count("top.geometry(f\"{w}x{h}") == 1,
          f"实测 {_code_nodoc.count('top.geometry(f\"{w}x{h}')} 处正常路径 geometry 调用")

    print("\n=== D2. 删除模型：完成后不再弹任何窗 ===")
    gone_titles = ["删除完成", "已删除，但有影响", "删除未完成", "无法删除", "删除失败"]
    # 只在「弹窗标题」这个位置上核对：卡片状态文案（如「状态：删除未完成（…）」）
    # 和代码注释里出现这些字样是正常的，不算弹窗，所以不能直接搜源码子串。
    dialog_titles = [title for title, _kind, _tone in tone_map]
    popped = [t for t in gone_titles if t in dialog_titles]
    check("删除后的结果弹窗标题已从弹窗调用点移除", not popped, str(popped))
    # 删除方法体内不应再有任何 dialogs.tell（确认框 dialogs.ask 要保留）
    body = re.search(r"def _on_delete_model\(self.*?\n    def ", sw_src, re.S)
    body_src = body.group(0) if body else ""
    check("_on_delete_model 里已无 dialogs.tell 调用",
          body_src != "" and "dialogs.tell(" not in body_src,
          "方法体长度 %d 字符" % len(body_src))
    check("删除功能仍然保留（models.remove + 卸载引擎 + 结果刷新）",
          "models.remove(key)" in body_src and "unload_models()" in body_src
          and "_refresh_models()" in body_src)
    check("删除前的确认框仍然保留（误删防护没被一起去掉）",
          "dialogs.ask(" in body_src and "删除模型" in body_src)

    # 行为验证：真点一次「删除」，确认全程零弹窗、且删除流程照常执行。
    # 用打桩替换 models.remove（不动真实模型），只看界面这一层的调用与弹窗。
    print("\n=== D3. 行为验证：点一次删除，全程零弹窗 ===")
    real_remove = models.remove
    real_leftover = models.leftover_files
    real_ask, real_tell = dialogs.ask, dialogs.tell
    seen = {"tell": [], "ask": [], "remove": []}
    try:
        dialogs.tell = lambda *a, **k: seen["tell"].append(a[1] if len(a) > 1 else "")
        dialogs.ask = lambda *a, **k: (seen["ask"].append(a[1] if len(a) > 1 else ""), True)[1]
        models.remove = lambda key: (seen["remove"].append(key),
                                     {"freed": 71 * 1024 * 1024,
                                      "removed": ["fake"], "failed": []})[1]
        models.leftover_files = lambda key, deep=True: []
        settings._queue.put(("call", lambda: settings._on_delete_model("en_ja")))
        time.sleep(1.5)
    finally:
        models.remove = real_remove
        models.leftover_files = real_leftover
        dialogs.ask, dialogs.tell = real_ask, real_tell
    check("点「删除」确实走了删除流程（models.remove 被调用）",
          seen["remove"] == ["en_ja"], str(seen["remove"]))
    check("删除完成后**不弹任何结果提示**（dialogs.tell 调用 0 次）",
          seen["tell"] == [], str(seen["tell"]))
    check("删除前只弹 1 次确认框（误删防护仍在）",
          seen["ask"] == ["删除模型"], str(seen["ask"]))

    print("\n=== E. 无边框极简确认框能力（dialogs frameless，通用能力）===")
    # 注：「恢复默认设置」2026-10-01 起改成**点了直接生效、不弹框**，
    # 所以这里改为直接验证 dialogs 的 frameless 能力本身（其他极简确认场景可用）。

    def probe_restore_dialog():
        root = settings.root
        top, _r, _c = dialogs._build(
            root, "恢复默认设置", "",
            [{"text": "取消", "value": "cancel", "kind": "secondary"},
             {"text": "恢复默认", "value": "ok", "kind": "primary"}],
            "warn", "ok", frameless=True)
        dialogs._center_on_parent(top, root, frameless=True)
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
        dialogs._center_on_parent(top, root)   # 映射后才能读到真实窗口样式
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
        # 必须走定位（= _run 的真实流程）：窗口在此之前一直是撤隐的，
        # 不 deiconify 就收不到键盘事件
        dialogs._center_on_parent(top, root, frameless=True)
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

    print("\n=== F. 弹窗不闪烁：撤隐定位，首帧可见即居中且此后不动 ===")

    def probe_timeline():
        """每 4ms 采一次 (是否映射, 客户区原点, 尺寸)，还原"用户肉眼看到的时间线"。

        复现报障的关键：Toplevel 一创建就被 Windows 映射在屏幕左上角（+0+0）、
        尺寸只有 26×56，卡片撑开后才被挪到中间 —— 中间那一瞬被画出来就是
        "左上角闪一下再跳过来"。窗口合成由 dwm.exe 完成，事后设 alpha 挡不住，
        唯一可靠的办法是**全程撤隐**（见 app/dialogs.py 的说明）。
        """
        root = settings.root
        cx = root.winfo_rootx() + root.winfo_width() // 2
        cy = root.winfo_rooty() + root.winfo_height() // 2
        samples = []
        top, result, close = dialogs._build(
            root, "删除模型", "确定要删除「中译英」模型吗？\n将释放约 82 MB 磁盘空间。",
            [{"text": "取消", "value": "cancel", "kind": "secondary"},
             {"text": "删除", "value": "ok", "kind": "danger"}], "err", "ok")
        # 刚创建、内容还没建完时必须仍是"未映射"——一旦映射就可能被 DWM 画出来
        mapped_at_build = bool(top.winfo_ismapped())

        def sampler():
            try:
                samples.append((1 if top.winfo_ismapped() else 0,
                                top.winfo_rootx(), top.winfo_rooty(),
                                top.winfo_width(), top.winfo_height()))
            except Exception:  # noqa: BLE001
                pass
            if top.winfo_exists():
                top.after(4, sampler)

        dialogs._center_on_parent(top, root)      # 与 _run 完全一致
        try:
            top.grab_set()
        except Exception:  # noqa: BLE001
            pass
        top.after(4, sampler)
        top.after(600, lambda: close("cancel"))
        top.wait_window(top)
        return {"mapped_at_build": mapped_at_build, "samples": samples,
                "center": (cx, cy)}

    tl = settings.run_in_ui(probe_timeline, timeout=20) or {}
    check("弹窗创建后处于**撤隐**状态（从未在左上角被映射出来）",
          tl.get("mapped_at_build") is False, f"已映射={tl.get('mapped_at_build')}")
    _vis = [s for s in tl.get("samples", []) if s[0]]
    check("_center_on_parent 返回后窗口已可见（弹窗不会一直藏着）",
          len(_vis) > 0, f"可见 {len(_vis)} 帧")
    if _vis:
        _a, _rx, _ry, _w, _h = _vis[0]
        _cx, _cy = _rx + _w // 2, _ry + _h // 2
        _tcx, _tcy = tl.get("center", (0, 0))
        check("**第一帧可见时就已经在设置界面正中央**（误差 ≤3px）",
              abs(_cx - _tcx) <= 3 and abs(_cy - _tcy) <= 3,
              f"可见首帧中心 ({_cx},{_cy}) vs 父窗口中心 ({_tcx},{_tcy})")
        _moves = {(s[1], s[2]) for s in _vis[1:]}
        _resizes = {(s[3], s[4]) for s in _vis[1:]}
        check("可见之后位置不再变化（没有二次跳动）",
              _moves <= {(_rx, _ry)}, f"可见期内出现过的位置：{sorted(_moves)[:3]}")
        check("可见之后尺寸不再变化（卡片不会在眼前再撑开一次）",
              _resizes <= {(_w, _h)},
              f"可见期内出现过的尺寸：{sorted(_resizes)[:3]}")
        check("露面时尺寸就是终态（不是先 26×56 小框再长大）",
              _w > 200 and _h > 100, f"{_w}x{_h}")
    else:
        check("**第一帧可见时就已经在设置界面正中央**（误差 ≤3px）", False, "始终没有可见帧")
        check("可见之后位置不再变化（没有二次跳动）", False, "始终没有可见帧")

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
