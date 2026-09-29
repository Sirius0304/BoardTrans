# -*- coding: utf-8 -*-
"""「未保存」提示的判定与位置：回归测试。

对应报障的两个场景：
  1) 重复点已经选中的选项 → 不该提示"未保存"
  2) 改到别的选项、又切回原值 → 不该提示"未保存"
另外验证：只有真正改了才提示；提示显示在窗口**底部**（不是顶部）；
保存后提示消失、且此时不会与"保存结果小框"重叠。

用法：F:\\shunyi\\.venv\\Scripts\\python.exe tools\\test_dirty_hint.py
"""
import os
import sys
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from app import config                        # noqa: E402
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
        cls.calls.append(("askokcancel", a[0] if a else ""))
        return True

    @classmethod
    def showinfo(cls, *a, **k):
        cls.calls.append(("showinfo", a[0] if a else ""))

    @classmethod
    def showerror(cls, *a, **k):
        cls.calls.append(("showerror", a[0] if a else ""))

    @classmethod
    def showwarning(cls, *a, **k):
        cls.calls.append(("showwarning", a[0] if a else ""))


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
        bar = settings._dirty_bar
        outer = bar.master
        mid = bar.winfo_y() + bar.winfo_height() / 2
        holder["bar_shown"] = bool(bar.winfo_manager())
        holder["dirty"] = settings._dirty
        holder["text"] = settings._dirty_label.cget("text")
        holder["manager"] = bar.winfo_manager()
        holder["lower_half"] = mid > outer.winfo_height() / 2
        holder["pos"] = f"y={bar.winfo_y()} 高={bar.winfo_height()} 窗口高={outer.winfo_height()}"
        holder["notice_shown"] = settings._notice_visible()
        holder["saved_q"] = config.load().get("quality")

    def act(fn):
        settings._queue.put(("call", fn))
        time.sleep(0.7)

    print("\n=== A. 初始状态 ===")
    act(grab)
    check("初始不显示「未保存」提示", holder.get("bar_shown") is False, str(holder.get("bar_shown")))

    print("\n=== B. 场景1：重复点已选中的选项 → 不应提示 ===")
    saved = config.load().get("quality")
    same_val = settings._quality_var.get()

    def click_same():
        # 模拟"点了当前已选中的那个单选按钮"：按钮的 command 照样会触发
        settings._quality_var.set(same_val)
        settings._on_quality_change()

    for i in range(3):
        act(click_same)
        act(grab)
        check(f"第 {i+1} 次点已选中项后不提示未保存",
              holder.get("bar_shown") is False and holder.get("dirty") is False,
              f"bar={holder.get('bar_shown')} dirty={holder.get('dirty')}")

    print("\n=== C. 场景2：改到别处再改回原值 → 不应提示 ===")
    other = "quality" if same_val == "fast" else "fast"

    def switch_to_other():
        settings._quality_var.set(other)
        settings._on_quality_change()

    def switch_back():
        settings._quality_var.set(same_val)
        settings._on_quality_change()

    act(switch_to_other)
    act(grab)
    check("改到另一档：应当提示未保存（并且提示在）",
          holder.get("bar_shown") is True and holder.get("dirty") is True,
          f"bar={holder.get('bar_shown')}")
    check("提示文字说明有未保存修改", "有未保存的修改" in holder.get("text", ""), holder.get("text", ""))

    act(switch_back)
    act(grab)
    check("改回原值后：不再提示未保存（本次报障的核心）",
          holder.get("bar_shown") is False and holder.get("dirty") is False,
          f"bar={holder.get('bar_shown')} dirty={holder.get('dirty')}")
    check("档位值确实等于已保存的值", holder.get("saved_q") == same_val,
          str(holder.get("saved_q")))

    print("\n=== D. 内存优化来回切换同样不误报 ===")
    mem_now = bool(settings._idle_unload_var.get())

    def mem_toggle(value):
        def fn():
            settings._idle_unload_var.set(value)
            settings._on_memory_change()
        return fn

    act(mem_toggle(not mem_now))
    act(grab)
    check("改动内存优化：提示未保存", holder.get("bar_shown") is True, str(holder.get("bar_shown")))
    act(mem_toggle(mem_now))
    act(grab)
    check("切回原值：不再提示", holder.get("bar_shown") is False, str(holder.get("bar_shown")))

    print("\n=== E. 快捷键：录制成与原来相同的组合 → 不应提示 ===")

    def record_same():
        settings._pending_hotkey = config.load().get("hotkey")
        settings._mark_dirty()

    act(record_same)
    act(grab)
    check("快捷键没变化时不提示", holder.get("bar_shown") is False, str(holder.get("bar_shown")))

    print("\n=== F. 提示位置：窗口底部（下半部）===")
    act(switch_to_other)                      # 制造真实改动
    act(grab)
    check("提示条显示在窗口底部（下半部）", holder.get("lower_half") is True, str(holder.get("pos")))
    check("提示条用 pack 常驻占位（不浮在内容上）", holder.get("manager") == "pack",
          str(holder.get("manager")))

    print("\n=== G. 保存后提示消失，且不与「保存结果小框」重叠 ===")

    def save_now():
        settings._on_save()

    act(save_now)
    act(grab)
    check("保存成功后「未保存」提示消失", holder.get("bar_shown") is False, str(holder.get("bar_shown")))
    check("此时显示的是保存结果小框", holder.get("notice_shown") is True, str(holder.get("notice_shown")))
    check("保存结果小框也在窗口底部",
          settings._notice_box.winfo_y() + settings._notice_box.winfo_height() / 2
          > settings._notice_box.master.winfo_height() / 2,
          f"y={settings._notice_box.winfo_y()} 窗口高={settings._notice_box.master.winfo_height()}")

    print("\n=== H. 无真实改动时关闭窗口：不该弹「放弃修改」确认框 ===")
    AutoAnswer.calls.clear()

    def close_win():
        settings._on_window_close()

    act(close_win)
    asked = [c for c in AutoAnswer.calls if c[0] == "askokcancel"]
    check("没有未保存改动 → 不弹确认框（之前会误弹）", not asked, str(asked))

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
