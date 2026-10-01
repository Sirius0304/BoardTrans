# -*- coding: utf-8 -*-
"""验证：预热之后，首次点「设置」的延迟是否降到与第二次一致。"""
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
service = HotkeyService(notify=lambda t, m: None)


def measure_to_visible(settings, label):
    t0 = time.perf_counter()
    settings.open()
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if settings.run_in_ui(lambda: settings.root.state()) == "normal":
                return (time.perf_counter() - t0) * 1000
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.004)
    return float("nan")


# ---- A. 不预热（现状）：首次打开要现建界面 ----
a = sw.SettingsWindow(service)
first_cold = measure_to_visible(a, "无预热首次")
a.close()
time.sleep(0.4)
second_cold = measure_to_visible(a, "无预热第二次")
a.shutdown()
time.sleep(0.6)

# ---- B. 预热：启动时后台先建好 ----
b = sw.SettingsWindow(service)
t0 = time.perf_counter()
b.prewarm(delay=0.2)
# 等预热真正完成（模拟"用户过几秒才点设置"）
deadline = time.time() + 10
while time.time() < deadline:
    if b._thread is not None and b._thread.is_alive() and b._ready.is_set():
        break
    time.sleep(0.01)
warm_ms = (time.perf_counter() - t0) * 1000
first_warm = measure_to_visible(b, "预热后首次")

print(f"\n无预热：首次 {first_cold:.0f} ms，第二次 {second_cold:.0f} ms")
print(f"有预热：预热耗时 {warm_ms:.0f} ms（发生在启动后台，用户无感）"
      f"，预热后首次打开 {first_warm:.0f} ms")
print(f"→ 首次打开提速 {first_cold - first_warm:.0f} ms"
      f"（{first_cold / max(1.0, first_warm):.1f}×）")
b.shutdown()
time.sleep(0.4)
config.update(**{k: original[k] for k in original})
