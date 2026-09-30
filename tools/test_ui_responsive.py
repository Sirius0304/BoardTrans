# -*- coding: utf-8 -*-
"""设置界面**响应性**回归测试（2026-09-29 建，对应"测试时界面卡死/未响应"报障）。

每个检查都对应一个已定位的成因，且是**确定性断言**（不是主观感觉）：

  A. 空闲重绘 churn：自绘控件不得在空闲时反复重画（改前实测每秒 ~115 次）
  B. 队列命令响应延迟：空命令往返必须 < 50ms（改前实测中位 125ms）
  C. 指令突发：200 条指令洪水后，下一条命令仍要及时被处理（考验单轮预算）
  D. 心跳单轮耗时：不得出现慢轮警告（改前若 UI 线程做重活就超 120ms）
  E. 跨线程 Tcl 调用：脚本序列中必须 0 次（tkinter 非线程安全，会挂起解释器）
  F. 启动等待与线程存活：_ensure_thread 就绪 < 2s，且窗口线程存活
  G. 滚轮绑定作用域：逐控件绑定生效、**不存在进程级 bind_all**（改前有全局绑定残留风险）

用法：F:\\shunyi\\.venv\\Scripts\\python.exe tools\\test_ui_responsive.py
"""
import os
import sys
import threading
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

import tkinter as tk                                     # noqa: E402

from app import config, paths                            # noqa: E402
import app.settings_window as sw                         # noqa: E402
from app import ui_kit as ui                             # noqa: E402
from app.service import HotkeyService                    # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  —— {detail}" if detail else ""), flush=True)


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


# 跨线程检测要包住的 Tcl 触碰方法（够用即可）
GUARD_METHODS = ("winfo_width", "winfo_height", "winfo_rootx", "winfo_rooty", "winfo_reqwidth",
                 "winfo_reqheight", "winfo_screenwidth", "winfo_fpixels", "winfo_exists",
                 "winfo_manager", "winfo_children", "winfo_x", "winfo_y", "cget", "configure")


def install_cross_thread_detector(record):
    """包装 Tk 常用方法：一旦被"非归属线程"调用就记账。"""
    def make_guard(name, func):
        def wrapper(self, *a, **k):
            owner = getattr(self, "_shunyi_owner_thread", None)
            if owner is not None and threading.current_thread() is not owner:
                record.append(f"{name} 被 {threading.current_thread().name} 调用"
                              f"（窗口线程={owner.name}）")
            return func(self, *a, **k)
        return wrapper

    for name in GUARD_METHODS:
        if hasattr(tk.Misc, name):
            setattr(tk.Misc, name, make_guard(name, getattr(tk.Misc, name)))


def main():
    sw.messagebox = AutoAnswer
    # 界面弹窗已统一走 app.dialogs（不再用系统 messagebox）：这里打桩自动应答
    sw.dialogs.ask = lambda *a, **k: True
    sw.dialogs.tell = lambda *a, **k: None
    original = dict(config.load())
    log_file = paths.log_path()
    log_before = os.path.getsize(log_file) if os.path.exists(log_file) else 0

    print("\n=== A. 启动与线程存活（改前 _ensure_thread 最长等 8 秒）===")
    service = HotkeyService(notify=lambda t, m: None)
    settings = sw.SettingsWindow(service)
    t0 = time.perf_counter()
    settings.open()
    open_ms = (time.perf_counter() - t0) * 1000
    time.sleep(2.0)
    check("窗口线程存活", settings.ui_thread_alive())
    check(f"open() 未阻塞过久（实测 {open_ms:.0f} ms，阈值 < 2000ms）", open_ms < 2000,
          f"{open_ms:.0f} ms")

    cross_calls: list = []
    install_cross_thread_detector(cross_calls)

    print("\n=== B. 空闲重绘 churn（改前：每秒数百次全量重画）===")
    counter = {"redraw": 0}
    real_redraw = ui._CanvasBox._redraw

    def counting_redraw(self, w, h):
        counter["redraw"] += 1
        return real_redraw(self, w, h)

    ui._CanvasBox._redraw = counting_redraw
    try:
        time.sleep(3.0)
    finally:
        ui._CanvasBox._redraw = real_redraw
    check(f"空闲 3 秒内重绘次数 ≤ 3（实测 {counter['redraw']} 次）", counter["redraw"] <= 3,
          f"{counter['redraw']} 次")

    print("\n=== C. 队列命令往返延迟（改前：中位 125ms，等于轮询间隔）===")
    lat = []
    for _ in range(12):
        done = threading.Event()
        t1 = time.perf_counter()
        settings._queue.put(("call", done.set))
        ok = done.wait(timeout=3.0)
        lat.append((time.perf_counter() - t1) * 1000 if ok else float("inf"))
    lat_sorted = sorted(lat)
    med = lat_sorted[len(lat) // 2]
    worst = max(lat)
    check(f"命令往返中位耗时 < 50ms（实测 {med:.1f} ms）", med < 50, f"{med:.1f} ms")
    check(f"命令往返最差 < 150ms（实测 {worst:.1f} ms）", worst < 150, f"{worst:.1f} ms")

    print("\n=== D. 指令突发：200 条洪水后仍能及时响应 ===")
    for _ in range(200):
        settings._queue.put(("call", lambda: None))
    done = threading.Event()
    t1 = time.perf_counter()
    settings._queue.put(("call", done.set))
    ok = done.wait(timeout=5.0)
    burst_ms = (time.perf_counter() - t1) * 1000 if ok else float("inf")
    # 每轮预算 20 条 → 200 条约需 10 轮；若"一次排空"，这条命令只能等洪水排完
    check(f"洪峰后仍能及时响应（实测 {burst_ms:.0f} ms，阈值 < 2000ms）",
          ok and burst_ms < 2000, f"{burst_ms:.0f} ms")

    print("\n=== E. 跨线程 Tcl 调用（改前：测试脚本从主线程碰 Tk → 挂起）===")
    # 正常脚本序列：一切 Tk 操作都走 run_in_ui / 队列
    settings.run_in_ui(lambda: None)
    settings.run_in_ui(lambda: settings._title_labels[0].cget("text"))
    width = settings.run_in_ui(lambda: settings.root.winfo_width())
    check("run_in_ui 能取回窗口线程里的值", isinstance(width, int) and width > 0, str(width))
    check(f"脚本序列中跨线程 Tcl 调用为 0（实测 {len(cross_calls)} 次）", not cross_calls,
          str(cross_calls[:3]))

    print("\n=== F. 心跳单轮耗时（改前：UI 线程里做重活会超 120ms）===")
    time.sleep(1.5)
    tail = ""
    if os.path.exists(log_file):
        with open(log_file, "rb") as f:
            f.seek(max(0, log_before))
            tail = f.read().decode("utf-8", "ignore")
    slow = [ln for ln in tail.splitlines() if "单轮心跳耗时" in ln]
    check(f"无慢轮警告（阈值 120ms；实测 {len(slow)} 条）", not slow, str(slow[:2]))

    print("\n=== G. 滚轮绑定作用域（改前：bind_all 进程级绑定）===")
    holder = {}

    def probe_bindings():
        content = settings._content
        children = content.winfo_children()
        holder["child_bound"] = sum(
            1 for c in children if c.bind("<MouseWheel>")) if children else 0
        holder["has_handler"] = bool(getattr(settings, "_wheel_handler", None))
        holder["global_bind"] = settings.root.bind_all("<MouseWheel>")

    settings.run_in_ui(probe_bindings)
    check("滚轮用逐控件绑定（内容区子控件已绑）",
          holder.get("has_handler") and holder.get("child_bound", 0) > 0,
          f"子控件已绑 {holder.get('child_bound')} 个")
    check("不存在进程级 bind_all 滚轮绑定（避免影响其它窗口）",
          not holder.get("global_bind"), repr(holder.get("global_bind"))[:60])

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
