# -*- coding: utf-8 -*-
"""模型预热（方案②）改前 / 改后 对照测量 —— 用**真模型**跑真加载。

场景设定：按键后 → 取词（实测约 0.36s）→ 翻译。模型不在内存时（空闲释放后、
或进程内首次翻译）需要加载 0.15~3 秒。差别只在"这段加载跟取词是串行还是并行"：

  改前（串行）：取词 0.36s → 加载 L 秒 → 翻译     合计 ≈ 0.36 + L
  改后（并行）：取词 0.36s 与加载 L 秒重叠 → 翻译  合计 ≈ max(0.36, L)

每个子场景都在**独立子进程**里跑（模型对象不能跨场景复用），
两种顺序各跑两次取最大值，避免"第二次磁盘缓存已热"把并行那组算得太漂亮。

用法：.venv\\Scripts\\python.exe tools\\diag_warmup.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
CAPTURE_SIM = 0.36          # 取词实测耗时（见 diag_capture_latency.py）

CHILD = r'''
import sys, time, threading
sys.path.insert(0, r"{root}")
from app.engine import TranslationEngine

phase = sys.argv[1]
sim = float(sys.argv[2])

eng = TranslationEngine()
target = "en"          # 中文 -> 英文，只需 zh_en 一个模型

# 单独量一次"纯加载"耗时，供参考
t = time.monotonic()
probe = TranslationEngine(); probe.ensure_loaded(target)
print("LOAD=%.3f" % (time.monotonic() - t), flush=True)
probe.unload_all()

if phase == "serial":
    # 改前：先取词（模拟等待），再加载
    t0 = time.monotonic()
    time.sleep(sim)
    eng.ensure_loaded(target)
    dt = time.monotonic() - t0
elif phase == "parallel":
    # 改后：加载丢到后台线程，与取词重叠（取词结束就返回）
    t0 = time.monotonic()
    th = threading.Thread(target=lambda: eng.ensure_loaded(target), daemon=True)
    th.start()
    deadline = t0 + sim
    while time.monotonic() < deadline and not eng.is_ready_for(target):
        time.sleep(0.01)
    th.join()
    dt = time.monotonic() - t0
else:
    raise SystemExit("unknown phase")

print("RESULT=%.3f" % dt, flush=True)
'''


def run_child(phase):
    out = subprocess.run([PY, "-c", CHILD.format(root=ROOT), phase, str(CAPTURE_SIM)],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", cwd=ROOT).stdout
    load = result = None
    for line in out.splitlines():
        if line.startswith("LOAD="):
            load = float(line.split("=")[1])
        elif line.startswith("RESULT="):
            result = float(line.split("=")[1])
    return load, result


def main():
    print("=" * 74)
    print("模型预热 改前/改后 对照（真模型、真加载；取词耗时按实测 %.2fs 模拟）" % CAPTURE_SIM)
    print("=" * 74)

    orders = [("serial", "parallel"), ("parallel", "serial")]
    worst = {"serial": 0.0, "parallel": 0.0}
    loads = []
    for order in orders:
        for phase in order:
            load, result = run_child(phase)
            if load:
                loads.append(load)
            worst[phase] = max(worst[phase], result or 0.0)
            print(f"  [{phase:8s}] 纯加载 {(load or 0):.3f}s | 本次「按键→模型就绪」 {(result or 0):.3f}s")

    s, p = worst["serial"], worst["parallel"]
    print("-" * 74)
    print(f"  纯加载耗时（参考）        : {max(loads):.3f}s")
    print(f"  改前（串行）按键→模型就绪 : {s:.3f}s   = 取词 {CAPTURE_SIM:.2f}s + 加载")
    print(f"  改后（并行）按键→模型就绪 : {p:.3f}s   = max(取词, 加载)")
    print(f"  翻译阶段省下              : {s - p:.3f}s")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
