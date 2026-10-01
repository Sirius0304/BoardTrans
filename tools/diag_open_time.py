# -*- coding: utf-8 -*-
"""诊断：首次打开设置窗口的耗时分布（用户报障"首次打开明显卡顿"）。

打开路径：托盘点「设置」→ open() → _ensure_thread（首次要现建 UI）→ 窗口显示。
这里给 _run 的每个阶段打点计时，并统计 text_png（文字超采样渲染）的次数与耗时。
"""
import sys
import time

sys.path.insert(0, r"F:\shunyi")

from app import config                                   # noqa: E402
import app.settings_window as sw                         # noqa: E402
from app import ui_kit as ui                             # noqa: E402
from app.service import HotkeyService                    # noqa: E402

sw.dialogs.ask = lambda *a, **k: True
sw.dialogs.tell = lambda *a, **k: None
original = dict(config.load())

STATS = {"text_png_calls": 0, "text_png_ms": 0.0, "cache_hits": 0}
_real_text_png = ui.text_png


def counting_text_png(*a, **k):
    t0 = time.perf_counter()
    key = a[0]
    hit_before = len(ui._TEXT_PNG_CACHE)
    try:
        return _real_text_png(*a, **k)
    finally:
        STATS["text_png_ms"] += (time.perf_counter() - t0) * 1000
        if len(ui._TEXT_PNG_CACHE) > hit_before:
            STATS["text_png_calls"] += 1
        else:
            STATS["cache_hits"] += 1


ui.text_png = counting_text_png

service = HotkeyService(notify=lambda t, m: None)
settings = sw.SettingsWindow(service)

PHASES = []


def timed(name, fn):
    def wrapper(*a, **k):
        t0 = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            PHASES.append((name, (time.perf_counter() - t0) * 1000))
    return wrapper


for name in ("_apply_theme", "_build_ui", "_load_from_config", "_refresh_models"):
    setattr(settings, name, timed(name, getattr(settings, name)))
for name in ("_build_models_section", "_build_language_section", "_build_quality_section",
             "_build_memory_section", "_build_hotkey_section"):
    setattr(settings, name, timed(name, getattr(settings, name)))

t_total = time.perf_counter()
settings.open()
ok = settings._ready.wait(15)
wait_ms = (time.perf_counter() - t_total) * 1000
time.sleep(1.0)
state = "n/a"
try:
    state = settings.run_in_ui(lambda: settings.root.state())
except Exception:  # noqa: BLE001
    pass
print(f"\n首次 open() 到 UI 就绪: {wait_ms:.0f} ms（ready={ok}，窗口状态={state}）")
print("\n各阶段耗时：")
for name, ms in PHASES:
    print(f"  {name:<28} {ms:8.1f} ms")
print(f"\n文字渲染 text_png: 新渲染 {STATS['text_png_calls']} 次 "
      f"共 {STATS['text_png_ms']:.1f} ms；缓存命中 {STATS['cache_hits']} 次")
print("渲染缓存统计:", ui.text_cache_stats()["text_cached"])

settings.shutdown()
time.sleep(0.4)
config.update(**{k: original[k] for k in original})
