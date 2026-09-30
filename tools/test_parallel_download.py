# -*- coding: utf-8 -*-
"""
并行下载与进度独立性测试。

覆盖三个场景：
  场景 1：中译英正在下载时，点中译日 —— 应该**真正并行**下载（互不干扰），
          而不是重复下载基础模型（原来的 bug 就是这样乱跳的）
  场景 2：两个模型都没装时点中译日 —— 应该依次下载 zh_en + en_ja，各一次，不重复
  场景 3：同一个模型连点两次下载 —— 第二次应被忽略

断言：
  - 任何模型都不会被下载两次（防重复）
  - 每个卡片的进度序列单调递增（不回跳）
  - 两个卡片的进度各自独立（各自都能收到正确的百分比）
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models                  # noqa: E402
import app.settings_window as sw        # noqa: E402
from app.service import HotkeyService   # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"    [{'通过' if ok else '失败'}] {name}" + (f"  —— {detail}" if detail else ""))


class AutoAnswer:
    answer = True

    @classmethod
    def askokcancel(cls, *a, **k):
        return cls.answer

    @staticmethod
    def showinfo(*a, **k):
        pass

    @staticmethod
    def showerror(*a, **k):
        pass

    @staticmethod
    def showwarning(*a, **k):
        pass


def backjumps(seq):
    return sum(1 for a, b in zip(seq, seq[1:]) if b < a)


def main():
    sw.messagebox = AutoAnswer
    # 界面弹窗已统一走 app.dialogs（不再用系统 messagebox）：这里打桩自动应答
    sw.dialogs.ask = lambda *a, **k: True
    sw.dialogs.tell = lambda *a, **k: None

    state = {"downloads": [], "events": [], "installed": set()}
    lock = threading.Lock()

    def fake_ensure(key, progress_cb=None, cancel_event=None):
        # 真实 ensure() 全程持有该模型的锁（下载 + 安装都包在里面），
        # 打桩必须模拟这个行为，否则界面的 is_downloading() 判断会失真
        with models._model_download_lock(key):
            with lock:
                state["downloads"].append(key)
            steps = 20                      # 约 3 秒，留出"下载途中"的操作窗口
            for i in range(1, steps + 1):
                if cancel_event is not None and cancel_event.is_set():
                    raise models.DownloadCancelled("已取消")
                if progress_cb:
                    progress_cb("测试", i * 3.5 * 1024 * 1024, 70 * 1024 * 1024)
                time.sleep(0.15)
            with lock:
                state["installed"].add(key)      # 模拟下载 + 安装完成
            return "/fake/path"

    real_is_installed = models.is_installed
    models.is_installed = lambda key: key in state["installed"]
    models.ensure = fake_ensure

    service = HotkeyService(notify=lambda t, m: None)
    service.register_hotkey()
    settings = sw.SettingsWindow(service)
    settings.open()
    time.sleep(2.5)

    real_progress = settings._on_download_progress

    def spy(key, done, total, label=""):
        with lock:
            pct = int(done * 100 / total) if total else 0
            state["events"].append((key, pct))
        return real_progress(key, done, total, label)

    settings._on_download_progress = spy

    def reset():
        with lock:
            state["downloads"] = []
            state["events"] = []
        settings._active_downloads.clear()
        settings._download_threads.clear()
        settings._cancel_events.clear()

    def click(key):
        settings._queue.put(("call", lambda k=key: settings._on_download_model(k)))

    # ================= 场景 1：真正的并行下载 =================
    print("\n=== 场景 1：中译英下载途中点中译日（应并行，且不重复下基础模型）===")
    reset()
    click("zh_en")
    time.sleep(1.0)                      # 此时 zh_en 仍在下载中（要 3 秒）
    click("en_ja")
    time.sleep(6.5)                      # 等两个下载都跑完

    d = list(state["downloads"])
    ev = dict()
    for k, p in state["events"]:
        ev.setdefault(k, []).append(p)
    print(f"      下载调用序列: {d}")
    print(f"      zh_en 进度: {ev.get('zh_en')}")
    print(f"      en_ja 进度: {ev.get('en_ja')}")

    check("zh_en 只被下载一次", d.count("zh_en") == 1, f"次数={d.count('zh_en')}")
    check("en_ja 只被下载一次", d.count("en_ja") == 1, f"次数={d.count('en_ja')}")
    check("两个模型都真正启动了（并行）", "zh_en" in d and "en_ja" in d, str(d))
    check("zh_en 进度不回跳", backjumps(ev.get("zh_en", [])) == 0,
          f"回跳 {backjumps(ev.get('zh_en', []))} 次")
    check("en_ja 进度不回跳", backjumps(ev.get("en_ja", [])) == 0,
          f"回跳 {backjumps(ev.get('en_ja', []))} 次")
    check("en_ja 收到了自己的进度（未被 zh_en 干扰）", len(ev.get("en_ja", [])) > 0,
          f"{len(ev.get('en_ja', []))} 条")
    check("两个卡片的进度都在 0~100 之间",
          all(0 <= p <= 100 for v in ev.values() for p in v))

    # ================= 场景 2：依赖模型一起下载 =================
    print("\n=== 场景 2：两个都没装时点中译日（应依次下 zh_en + en_ja，各一次）===")
    time.sleep(1.5)
    reset()
    with lock:
        state["installed"].clear()       # 模拟"两个模型都没装"
    click("en_ja")
    time.sleep(8.5)

    d2 = list(state["downloads"])
    print(f"      下载调用序列: {d2}")
    check("zh_en 被下载一次（作为基础模型）", d2.count("zh_en") == 1, f"次数={d2.count('zh_en')}")
    check("en_ja 被下载一次", d2.count("en_ja") == 1, f"次数={d2.count('en_ja')}")
    check("顺序正确：先基础模型再日文模型", d2 == ["zh_en", "en_ja"], str(d2))

    # ================= 场景 3：连点两次同一模型 =================
    print("\n=== 场景 3：同一模型连点两次下载（第二次应被忽略）===")
    time.sleep(1.5)
    reset()
    with lock:
        state["installed"].clear()
    click("zh_en")
    time.sleep(0.5)
    click("zh_en")                       # 下载途中再点
    time.sleep(5.0)

    d3 = list(state["downloads"])
    print(f"      下载调用序列: {d3}")
    check("zh_en 仍然只被下载一次", d3.count("zh_en") == 1, f"次数={d3.count('zh_en')}")

    models.is_installed = real_is_installed
    settings.shutdown()
    time.sleep(0.5)
    service.unregister_hotkey()

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n=== 结果：{passed}/{len(RESULTS)} 项通过 ===")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  未通过：{name} {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
