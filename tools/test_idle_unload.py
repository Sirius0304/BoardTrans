# -*- coding: utf-8 -*-
"""
空闲释放功能测试：
1) 空闲超时后，模型是否真的从内存里放掉
2) 放掉之后再翻译，能否自动重新加载（功能不中断）
3) 关闭该选项（idle_unload_minutes=0）时，模型是否一直常驻
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, models              # noqa: E402
from app.service import HotkeyService       # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  —— {detail}" if detail else ""))


def main():
    original = dict(config.load())
    print("=" * 70)
    print("  空闲释放测试（把空闲阈值临时设为 0.05 分钟 = 3 秒，检查周期 1 秒）")
    print("=" * 70)

    config.update(idle_unload_minutes=0.05)
    service = HotkeyService(notify=lambda t, m: None)
    service._idle_check_interval = 1          # 测试用：加快检查频率
    service.settings_launcher = None
    service.start()                           # 必须 start()：空闲监视线程在这里启动

    engine = service._get_engine()
    print("\n  1) 等待后台预加载完成")
    t0 = time.time()
    while time.time() - t0 < 20 and not engine.loaded_keys():
        time.sleep(0.3)
    loaded = engine.loaded_keys()
    print(f"     用时 {time.time()-t0:.1f} 秒")
    check("模型已加载进内存", bool(loaded), str(loaded))

    print("\n  2) 等待空闲释放（约 5 秒）")
    t0 = time.time()
    released = False
    while time.time() - t0 < 8:
        if not engine.loaded_keys():
            released = True
            break
        time.sleep(0.5)
    print(f"     用时 {time.time()-t0:.1f} 秒")
    check("空闲超时后模型已从内存释放", released, f"剩余 {engine.loaded_keys()}")

    print("\n  3) 释放后再翻译（应自动重新加载，功能不中断）")
    t0 = time.time()
    out = engine.translate("你好，今天天气不错。", "en", "fast")
    cost = time.time() - t0
    print(f"     结果: {out}   ({cost*1000:.0f} ms)")
    check("释放后仍能正常翻译", "nice day" in out.lower(), out)
    check("重新加载确实有额外耗时（首次翻译变慢）", cost > 0.08, f"{cost*1000:.0f} ms（纯翻译约 20~35 ms）")
    check("翻译后模型又回到了内存里", bool(engine.loaded_keys()), str(engine.loaded_keys()))

    print("\n  4) 关闭该选项后不应再释放")
    config.update(idle_unload_minutes=0)
    time.sleep(3.5)
    check("设置为 0 时模型保持常驻", bool(engine.loaded_keys()), str(engine.loaded_keys()))

    service.stop()
    config.update(**{k: original[k] for k in ("hotkey", "target_lang", "quality",
                                             "enabled", "idle_unload_minutes")})

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n=== 结果：{passed}/{len(RESULTS)} 项通过 ===")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  未通过：{name} {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
