# -*- coding: utf-8 -*-
"""设置窗口「目标语言」区块：回归测试（2026-09-29 新增）。

此前目标语言只能从托盘菜单改，设置窗口里既看不到也改不了（信息不对称）。
本用例验证新增的区块与整套联动：
  A. 区块存在且有两个选项，初始值与配置一致
  B. 重复点已选中的语言 → 不提示"未保存"（沿用按实际值判定）
  C. 切换语言 → 提示未保存；保存 → 写盘、服务生效、提示消失
  D. 切换后又切回 → 不提示（净变化为零）
  E. 托盘菜单改了语言 → 窗口自动同步（双向一致）
  F. 切到日文且缺模型时保存 → 提示缺模型（红色）且保存不回滚
  G. 恢复默认设置 → 语言回到「英文」

用法：F:\\shunyi\\.venv\\Scripts\\python.exe tools\\test_target_lang_ui.py
"""
import os
import sys
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from app import config, models                # noqa: E402
import app.settings_window as sw              # noqa: E402
from app.service import HotkeyService         # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  —— {detail}" if detail else ""), flush=True)


class AutoAnswer:
    answer = True
    calls: list = []

    @classmethod
    def askokcancel(cls, *a, **k):
        cls.calls.append(a[0] if a else "")
        return True

    @classmethod
    def showinfo(cls, *a, **k):
        return None

    @classmethod
    def showerror(cls, *a, **k):
        return None

    @classmethod
    def showwarning(cls, *a, **k):
        return None


def main():
    sw.messagebox = AutoAnswer
    original = dict(config.load())

    service = HotkeyService(notify=lambda t, m: None)
    service.register_hotkey()
    settings = sw.SettingsWindow(service)
    service.settings_launcher = settings.open

    settings.open()
    time.sleep(2.5)
    if settings.root is None:
        check("设置窗口创建成功", False, "窗口未创建")
        return 1
    check("设置窗口创建成功", True)

    holder = {}

    def grab():
        holder["lang"] = settings._lang_var.get()
        holder["dirty"] = settings._dirty
        holder["bar"] = bool(settings._dirty_bar.winfo_manager())
        holder["notice"] = settings._notice_visible()
        holder["notice_text"] = settings._notice_label.cget("text") if settings._notice_visible() else ""
        holder["notice_bg"] = settings._notice_box.cget("bg")
        holder["saved"] = config.load().get("target_lang")

    def act(fn):
        settings._queue.put(("call", fn))
        time.sleep(0.7)

    print("\n=== A. 区块存在且初始值正确 ===")
    act(grab)
    check("「目标语言」区块已创建（有 _lang_var）", hasattr(settings, "_lang_var"))
    check("初始值与配置一致", holder.get("lang") == original.get("target_lang"),
          f"{holder.get('lang')} vs {original.get('target_lang')}")

    print("\n=== B. 重复点已选中的语言 → 不提示未保存 ===")
    cur = settings._lang_var.get()

    def click_same():
        settings._lang_var.set(cur)
        settings._on_lang_change()

    act(click_same)
    act(grab)
    check("重复点已选中项不提示未保存", holder.get("bar") is False and holder.get("dirty") is False,
          f"bar={holder.get('bar')}")

    print("\n=== C. 切换语言 → 提示；保存 → 写盘生效 ===")
    other = "ja" if cur == "en" else "en"

    def switch():
        settings._lang_var.set(other)
        settings._on_lang_change()

    act(switch)
    act(grab)
    check("切换后提示未保存", holder.get("bar") is True, str(holder.get("bar")))
    check("提示条写明当前实际生效的目标语言", "目标语言" in settings._dirty_label.cget("text"),
          settings._dirty_label.cget("text"))

    missing_before = bool(models.missing_for_target(other))
    act(lambda: settings._on_save())
    act(grab)
    check("保存后 target_lang 已写盘", config.load().get("target_lang") == other,
          str(config.load().get("target_lang")))
    check("服务内存中的配置同步", service.cfg.get("target_lang") == other, str(service.cfg.get("target_lang")))
    check("保存后未保存提示消失", holder.get("bar") is False, str(holder.get("bar")))
    if missing_before:
        check("缺模型时给出红色提示并引导下载",
              holder.get("notice") is True and "缺少模型" in holder.get("notice_text", ""),
              holder.get("notice_text", ""))
    else:
        check("模型齐全时提示已保存并写明新语言",
              holder.get("notice") is True and ("日文" in holder.get("notice_text", "")
                                                or "英文" in holder.get("notice_text", "")),
              holder.get("notice_text", ""))

    print("\n=== D. 切换后又切回 → 不提示（净变化为零）===")
    act(switch)                                   # 切回原值（other -> cur）
    act(grab)
    check("切回原值后不提示未保存", holder.get("bar") is False and holder.get("dirty") is False,
          f"bar={holder.get('bar')}")

    print("\n=== E. 托盘菜单改语言 → 窗口自动同步 ===")
    tray_side = "ja" if config.load().get("target_lang") == "en" else "en"
    service.set_target_lang(tray_side)            # 模拟托盘点「日文/英文」
    time.sleep(1.5)                               # 等窗口自动同步（120ms 一轮）
    act(grab)
    check("窗口语言选项自动跟随托盘", holder.get("lang") == tray_side,
          f"{holder.get('lang')} vs {tray_side}")
    check("跟随后不算未保存", holder.get("dirty") is False, str(holder.get("dirty")))

    print("\n=== F. 保存时目标语言确实参与「没有改动」判定 ===")
    act(lambda: settings._on_save())              # 值与配置一致（托盘已写盘）
    act(grab)
    check("无真实改动时保存 → 提示「无需保存」",
          "无需保存" in holder.get("notice_text", ""), holder.get("notice_text", ""))

    print("\n=== G. 恢复默认设置 → 语言回到英文 ===")
    if config.load().get("target_lang") != "ja":
        service.set_target_lang("ja")
        time.sleep(1.2)

    def restore():
        settings._on_restore_defaults()

    act(restore)
    act(grab)
    check("恢复默认后语言选项为「英文」", holder.get("lang") == "en", str(holder.get("lang")))

    # 复位
    settings.shutdown()
    config.update(**{k: original[k] for k in original})
    service.unregister_hotkey()
    time.sleep(0.5)

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n=== 结果：{passed}/{len(RESULTS)} 项通过 ===")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  未通过：{name}  {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
