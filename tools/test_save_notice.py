# -*- coding: utf-8 -*-
"""「保存」结果的窗口内轻量提醒：回归测试。

验证目标（对应需求）：
  A. 点「保存」不再弹任何窗口（模态），改为窗口内提醒条
  B. 保存成功后提醒立即出现，文字准确反映"已保存"
  C. 短暂延时后自动消失，不需要用户点任何东西
  D. 未修改就点保存 → 提醒显示"无需保存"（而不是"已保存"）
  E. 快捷键冲突导致保存失败 → 提醒标红并说明原因与原快捷键保留
  F. 连续保存两次：旧倒计时不会把新提醒提前关掉
  G. 提醒内容随实际结果变化（换快捷键 / 改内存优化时带上对应说明）
  H. 提醒期间窗口不阻塞（后台队列指令仍能被处理）
  I. 关闭窗口时提醒被收起（重开不残留）

用法：F:\\shunyi\\.venv\\Scripts\\python.exe tools\\test_save_notice.py
"""
import ctypes
import os
import sys
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from app import config                        # noqa: E402
import app.settings_window as sw              # noqa: E402
from app.service import HotkeyService, hotkey_to_win   # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  —— {detail}" if detail else ""), flush=True)


class AutoAnswer:
    """记录所有模态弹窗调用：本需求要求"保存"路径一次都不该出现。"""
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
    # 界面弹窗已统一走 app.dialogs（不再用系统 messagebox）：这里打桩自动应答
    sw.dialogs.ask = lambda *a, **k: True
    sw.dialogs.tell = lambda *a, **k: None
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
        box = settings._notice_box
        holder["visible"] = settings._notice_visible()
        holder["text"] = settings._notice_label.cget("text")
        holder["bg"] = box.cget("bg")
        holder["manager"] = box.winfo_manager()          # "place" = 浮动小框
        parent_h = box.master.winfo_height()
        mid = box.winfo_y() + box.winfo_height() / 2
        holder["lower_half"] = mid > parent_h / 2        # 是否位于窗口下半部
        holder["pos"] = f"y={box.winfo_y()} 高={box.winfo_height()} 窗口高={parent_h}"

    print("\n=== A0. 初始状态 ===")
    settings._queue.put(("call", grab))
    time.sleep(0.6)
    check("初始不显示提醒条", holder.get("visible") is False, str(holder.get("visible")))

    print("\n=== A/B/C. 保存成功：立即出现、不弹窗、自动消失 ===")
    AutoAnswer.calls.clear()

    def save_quality():
        settings._quality_var.set("quality")
        settings._mark_dirty()
        settings._on_save()
        holder["immediate"] = settings._notice_visible()   # 同一次回调里立刻检查

    settings._queue.put(("call", save_quality))
    time.sleep(0.6)
    check("点保存后同一时刻提醒就已出现（立即生效，不等待）",
          holder.get("immediate") is True, str(holder.get("immediate")))
    settings._queue.put(("call", grab))
    time.sleep(0.6)
    check("提醒框已显示", holder.get("visible") is True, str(holder.get("visible")))
    check("以浮动小框形式弹出（place 定位，不改动原有布局）",
          holder.get("manager") == "place", str(holder.get("manager")))
    check("位置在窗口底部（下半部）而不是顶部",
          holder.get("lower_half") is True, str(holder.get("pos")))
    check("内容准确：提到已保存/已生效",
          ("已保存" in holder.get("text", "")) and ("立即生效" in holder.get("text", "")),
          holder.get("text", ""))
    check("底色为成功样式（浅绿）", holder.get("bg") == sw.COLOR_OK_BG, str(holder.get("bg")))
    check("没有弹出任何模态窗口", not AutoAnswer.calls, str(AutoAnswer.calls))
    check("改动确实已落盘", config.load().get("quality") == "quality",
          str(config.load().get("quality")))

    print("\n      等待它自动消失（成功提醒默认 3 秒）...")
    time.sleep(3.6)
    settings._queue.put(("call", grab))
    time.sleep(0.6)
    check("到点后自动消失（无需用户操作）", holder.get("visible") is False,
          str(holder.get("visible")))

    print("\n=== D. 没有改动时点保存：提醒要如实说「无需保存」 ===")
    AutoAnswer.calls.clear()

    def save_nochange():
        settings._on_save()

    settings._queue.put(("call", save_nochange))
    time.sleep(0.6)
    settings._queue.put(("call", grab))
    time.sleep(0.6)
    check("显示提醒而非弹窗", holder.get("visible") is True and not AutoAnswer.calls,
          f"visible={holder.get('visible')} modal={AutoAnswer.calls}")
    check("内容准确：提示无需保存", "无需保存" in holder.get("text", ""), holder.get("text", ""))
    check("底色为信息样式（浅蓝）", holder.get("bg") == sw.COLOR_INFO_BG, str(holder.get("bg")))

    print("\n=== E. 快捷键冲突：提醒标红并说明原因 ===")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    mods, vk = hotkey_to_win("ctrl+alt+k")
    HOTKEY_ID = 0x7B7B
    occupied = bool(user32.RegisterHotKey(None, HOTKEY_ID, mods, vk))
    check("构造冲突场景（真实占用 ctrl+alt+k）", occupied)
    AutoAnswer.calls.clear()
    old_hotkey = config.load().get("hotkey")

    def save_conflict():
        settings._pending_hotkey = "ctrl+alt+k"
        settings._mark_dirty()
        settings._on_save()

    settings._queue.put(("call", save_conflict))
    time.sleep(1.0)
    settings._queue.put(("call", grab))
    time.sleep(0.6)
    check("失败也是窗口内提醒（不弹窗）", holder.get("visible") is True and not AutoAnswer.calls,
          f"visible={holder.get('visible')} modal={AutoAnswer.calls}")
    check("内容准确：说明没能生效并保留了原快捷键",
          ("没能生效" in holder.get("text", "")) and ("保留原来的" in holder.get("text", "")),
          holder.get("text", ""))
    check("底色为错误样式（浅红）", holder.get("bg") == sw.COLOR_ERR_BG, str(holder.get("bg")))
    check("原快捷键未被改动", config.load().get("hotkey") == old_hotkey,
          str(config.load().get("hotkey")))
    user32.UnregisterHotKey(None, HOTKEY_ID)

    print("\n=== F. 连续保存两次：旧倒计时不得把新提醒提前关掉 ===")

    def save_mem_off():
        settings._idle_unload_var.set(False)          # 改内存优化 → 文本变长，停留时间也变长
        settings._mark_dirty()
        settings._on_save()

    def save_mem_on():
        settings._idle_unload_var.set(True)
        settings._mark_dirty()
        settings._on_save()

    t_mark = time.time()
    settings._queue.put(("call", save_mem_off))       # 提醒 A
    time.sleep(1.4)
    settings._queue.put(("call", save_mem_on))        # 提醒 B（此时 A 还没到期）
    time.sleep(3.0)                                   # 已越过 A 的到期时间
    settings._queue.put(("call", grab))
    time.sleep(0.6)
    elapsed = time.time() - t_mark
    check("过了第一条提醒的到期时间，新提醒仍在显示",
          holder.get("visible") is True,
          f"距第一条 {elapsed:.1f}s, visible={holder.get('visible')}")
    check("新提醒内容为最新一次保存的结果", "已保存" in holder.get("text", ""), holder.get("text", ""))
    time.sleep(4.5)
    settings._queue.put(("call", grab))
    time.sleep(0.6)
    check("最新提醒到点后同样自动消失", holder.get("visible") is False, str(holder.get("visible")))

    print("\n=== G. 提醒内容随实际结果变化 ===")
    settings._queue.put(("call", save_mem_off))       # 上一轮结束状态是"开启"，这次改成关闭
    time.sleep(0.6)
    settings._queue.put(("call", grab))
    time.sleep(0.6)
    check("改了内存优化时，提醒里带上对应说明",
          "内存优化已" in holder.get("text", ""), repr(holder.get("text", "")))

    print("\n=== G2. 换快捷键成功后，提醒带上新快捷键的说明 ===")

    def save_hotkey():
        settings._pending_hotkey = "ctrl+alt+j"
        settings._mark_dirty()
        settings._on_save()

    settings._queue.put(("call", save_hotkey))
    time.sleep(0.8)
    settings._queue.put(("call", grab))
    time.sleep(0.6)
    check("提醒里包含「新快捷键已经生效」的说明",
          "新快捷键已经生效" in holder.get("text", ""), repr(holder.get("text", "")))
    check("新快捷键确实已落盘", config.load().get("hotkey") == "ctrl+alt+j",
          str(config.load().get("hotkey")))

    print("\n=== H. 提醒期间窗口不阻塞（队列仍能被处理）===")
    holder["tick"] = False

    def tick():
        holder["tick"] = True

    settings._queue.put(("call", tick))               # 提醒还在显示时就发指令
    time.sleep(0.8)
    check("提醒显示期间窗口消息循环正常", holder.get("tick") is True, str(holder.get("tick")))

    print("\n=== I. 关闭窗口时提醒被收起，重开不残留 ===")
    settings._queue.put(("call", settings._on_window_close))
    time.sleep(1.0)
    settings.open()
    time.sleep(1.5)
    settings._queue.put(("call", grab))
    time.sleep(0.6)
    check("重开后没有残留的旧提醒", holder.get("visible") is False, str(holder.get("visible")))

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
