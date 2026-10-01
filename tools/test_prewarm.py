# -*- coding: utf-8 -*-
"""设置窗口"首次打开卡顿"的回归测试（2026-10-01 报障）。

背景：界面原本是**懒加载**的 —— 第一次点「设置」时才现启动窗口线程、创建 Tk、
构建整个界面（含 31 张文字超采样图），全部落在用户等待期里（实测 270~330ms，
而第二次打开只要 70ms）。修法是在程序启动后**后台预热**：把界面先建好（窗口隐藏），
首次打开就只剩"显示"这一步。

本用例覆盖：
  A. 预热是后台行为（不阻塞调用方）；
  B. 预热后窗口**保持隐藏**（不抢焦点、不弹到前台）；
  C. 预热幂等 + 并发安全（预热与点「设置」同时发生也只起一个窗口线程）；
  D. 预热后首次打开明显变快；
  E. TextLabel 值没变就不重渲染（刷新路径反复 configure 不该烧 CPU）。

用法：F:\\shunyi\\.venv\\Scripts\\python.exe tools\\test_prewarm.py
"""
import os
import sys
import threading
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from app import config                                   # noqa: E402
import app.settings_window as sw                         # noqa: E402
from app import ui_kit as ui                             # noqa: E402
from app.service import HotkeyService                    # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  —— {detail}" if detail else ""),
          flush=True)


def visible_ms(settings, timeout=8.0):
    """从 open() 到窗口真正显示（state=normal）的耗时。"""
    t0 = time.perf_counter()
    settings.open()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if settings.run_in_ui(lambda: settings.root.state()) == "normal":
                return (time.perf_counter() - t0) * 1000
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.004)
    return float("nan")


def main():
    sw.dialogs.ask = lambda *a, **k: True
    sw.dialogs.tell = lambda *a, **k: None
    original = dict(config.load())
    service = HotkeyService(notify=lambda t, m: None)

    print("\n=== A. 预热是后台行为（不能阻塞程序启动路径）===")
    s1 = sw.SettingsWindow(service)
    t0 = time.perf_counter()
    s1.prewarm(delay=0.0)
    call_ms = (time.perf_counter() - t0) * 1000
    check("prewarm() 立即返回（不等界面建完）", call_ms < 50, f"{call_ms:.1f} ms")
    check("预热期间界面还没就绪", not s1._ready.is_set())
    deadline = time.time() + 10
    while time.time() < deadline and not (s1._thread and s1._thread.is_alive()
                                          and s1._ready.is_set()):
        time.sleep(0.01)
    check("预热完成后窗口线程已就绪", bool(s1._thread and s1._thread.is_alive()))

    print("\n=== B. 预热后窗口必须保持隐藏 ===")
    state = s1.run_in_ui(lambda: s1.root.state())
    check("窗口仍是隐藏状态（不会自己弹出来抢焦点）", state == "withdrawn", str(state))

    print("\n=== C. 幂等 + 并发安全（预热与点「设置」可能同时发生）===")
    before = s1._thread
    for _ in range(3):
        s1.prewarm(delay=0.0)
    time.sleep(0.3)
    check("重复 prewarm 不会再起窗口线程", s1._thread is before)
    s2 = sw.SettingsWindow(service)
    errs = []

    def race():
        try:
            s2.prewarm(delay=0.0)
            s2.open()
        except Exception as exc:  # noqa: BLE001
            errs.append(repr(exc))

    ts = [threading.Thread(target=race) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=10)
    time.sleep(1.2)
    check("预热与 open 并发没有抛异常", not errs, "; ".join(errs) or "无")
    check("并发下仍然只创建了一个窗口线程",
          sum(1 for t in threading.enumerate() if t.name == "shunyi-settings") == 2,
          f"shunyi-settings 线程数={sum(1 for t in threading.enumerate() if t.name == 'shunyi-settings')}"
          f"（预期 2 = 本次两个实例各一个）")

    print("\n=== D. 预热后首次打开要快（报障的核心指标）===")
    s3 = sw.SettingsWindow(service)
    cold = visible_ms(s3, timeout=10)
    s3.close()
    time.sleep(0.4)
    cold2 = visible_ms(s3, timeout=10)
    check("无预热时首次打开明显慢于第二次（说明确实是构建开销）", cold > cold2,
          f"首次 {cold:.0f} ms vs 第二次 {cold2:.0f} ms")
    s3.shutdown()
    time.sleep(0.5)

    s4 = sw.SettingsWindow(service)
    s4.prewarm(delay=0.0)
    deadline = time.time() + 10
    while time.time() < deadline and not (s4._thread and s4._ready.is_set()):
        time.sleep(0.01)
    warm = visible_ms(s4, timeout=10)
    check("预热后首次打开 ≪ 无预热的首次", warm < max(150.0, cold * 0.6),
          f"{cold:.0f} ms → {warm:.0f} ms")
    s4.shutdown()
    time.sleep(0.4)

    print("\n=== E. TextLabel 值没变就不重渲染 ===")
    import tkinter as tk                                  # noqa: E402
    root = tk.Tk()
    root.withdraw()
    root.tk.call("font", "create", "PW12", "-family", "Microsoft YaHei UI", "-size", -12)
    ui.register_font("PW12", "Microsoft YaHei UI", 12, "normal")
    calls = {"n": 0}
    real = ui.text_png

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    ui.text_png = counting
    try:
        lbl = ui.TextLabel(root, text="状态：待机", font="PW12", fg="#6B7280", bg="#FFFFFF")
        first = calls["n"]
        photo = lbl._photo
        lbl.configure(text="状态：待机")            # 同一文案
        lbl.configure(text="状态：待机")
        check("重复 configure 同一文案不触发重渲染", calls["n"] == first,
              f"{first} → {calls['n']} 次")
        check("图片对象也没被换掉", lbl._photo is photo)
        lbl.configure(text="状态：下载中")          # 真改了
        check("文案真变了仍然会重渲染", calls["n"] == first + 1,
              f"{calls['n']} 次")
        same_fg = calls["n"]
        lbl.configure(fg="#6B7280")                 # 同色
        check("重复设同一颜色不触发重渲染", calls["n"] == same_fg)
    finally:
        ui.text_png = real
        root.destroy()

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n=== 结果：{passed}/{total} 项通过 ===")
    if passed != total:
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  ✗ {name}  {detail}")
    config.update(**{k: original[k] for k in original})
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
