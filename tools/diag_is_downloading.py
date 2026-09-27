# -*- coding: utf-8 -*-
"""诊断 models.is_downloading() 在多线程下是否可靠。"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models   # noqa: E402

print("=" * 60)
print("  is_downloading() 行为诊断")
print("=" * 60)

print(f"\n  起始状态: is_downloading('zh_en') = {models.is_downloading('zh_en')}  (期望 False)")

holder_ready = threading.Event()


def worker():
    with models._model_download_lock("zh_en"):
        holder_ready.set()
        time.sleep(2.0)


print("\n  启动一个线程持有 zh_en 的下载锁...")
t = threading.Thread(target=worker, daemon=True)
t.start()
holder_ready.wait(3)
time.sleep(0.3)

mid = models.is_downloading("zh_en")
print(f"  持锁期间主线程检测: {mid}  {'[正确]' if mid else '[错误！应为 True]'}")

t.join(timeout=5)
time.sleep(0.3)
after = models.is_downloading("zh_en")
print(f"  释放之后: {after}  {'[正确]' if not after else '[错误！应为 False]'}")

# 再验证：窗口线程与下载线程不同时，RLock 的可重入性会不会干扰判断
print("\n  补充验证：同一线程内重复检测（RLock 可重入的影响）")
lock = models._model_download_lock("test_reentrant")
print(f"    is_downloading('test_reentrant') = {models.is_downloading('test_reentrant')}")
with lock:
    inner = models.is_downloading("test_reentrant")
    print(f"    本线程持锁时再检测 = {inner}  "
          f"{'[注意] 同线程可重入会导致误判为「没在下载」'}")

print("\n  结论：" + ("is_downloading 工作正常" if (mid and not after) else "is_downloading 有问题，需要修"))
