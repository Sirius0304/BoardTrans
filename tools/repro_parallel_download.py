# -*- coding: utf-8 -*-
"""
复现「同时下载两个模型时进度乱跳」的问题。

怀疑的根因：
    点「中译英」开始下载后，再点「中译日」——
    此时 zh_en 还没装完（is_installed=False），于是 en_ja 的下载任务
    认为「需要基础模型」为真，又发起了一次 zh_en 下载。
    两个线程同时写同一个 .argosmodel 文件，进度也都汇报给同一个 key，
    于是进度条互相覆盖、来回跳。

本脚本用假的下载函数记录「每个模型被下载了几次」，直接看证据。
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models                  # noqa: E402
import app.settings_window as sw        # noqa: E402
from app.service import HotkeyService   # noqa: E402


class AutoAnswer:
    answer = True

    @classmethod
    def askokcancel(cls, *a, **k):
        print(f"      [弹窗-确认] {str(a[0])[:40]}... -> 自动点「确定」")
        return cls.answer

    @staticmethod
    def showinfo(*a, **k):
        print(f"      [弹窗-提示] {str(a[1] if len(a) > 1 else a)[:50]}")

    @staticmethod
    def showerror(*a, **k):
        print(f"      [弹窗-错误] {str(a[1] if len(a) > 1 else a)[:50]}")

    @staticmethod
    def showwarning(*a, **k):
        pass


def main():
    sw.messagebox = AutoAnswer

    # ---- 打桩：把真实下载换成可控的假下载 ----
    download_calls = []           # 记录每个模型被下载了几次
    progress_events = []          # 记录 (key, percent) 事件流
    lock = threading.Lock()

    def fake_ensure(key, progress_cb=None, cancel_event=None):
        # 真实 ensure() 会在整段下载期间持有该模型的锁，
        # 打桩也必须模拟这个行为，否则界面的 is_downloading() 判断会失真
        with models._model_download_lock(key):
            with lock:
                download_calls.append(key)
                started.append(key)
            for i in range(1, 11):
                if cancel_event is not None and cancel_event.is_set():
                    raise models.DownloadCancelled("已取消")
                if progress_cb:
                    progress_cb("测试", i * 7 * 1024 * 1024, 70 * 1024 * 1024)
                time.sleep(0.12)
            return "/fake/path"

    real_is_installed = models.is_installed
    # 模拟「两个模型都没装」（下载过程中 is_installed 一直是 False，这正是线上真实情形）
    models.is_installed = lambda key: False
    models.ensure = fake_ensure

    service = HotkeyService(notify=lambda t, m: None)
    service.register_hotkey()
    settings = sw.SettingsWindow(service)
    settings.open()
    time.sleep(2.5)

    # 监听进度事件：包装 _on_download_progress 记录下来
    real_progress = settings._on_download_progress

    def spy_progress(key, done, total, label=""):
        with lock:
            pct = int(done * 100 / total) if total else 0
            progress_events.append((key, pct))
        return real_progress(key, done, total, label)

    settings._on_download_progress = spy_progress
    # 重新绑定队列处理用的方法（_pump 里是 self._on_download_progress，动态查找，无需重绑）

    print("=" * 72)
    print("  复现步骤：先点「中译英」开始下载，下载途中再点「中译日」")
    print("=" * 72)
    print("\n  [1] 点「中译英」的下载按钮")
    settings._queue.put(("call", lambda: settings._on_download_model("zh_en")))
    time.sleep(0.8)

    print("\n  [2] 在「中译英」还在下载时，点「中译日」的下载按钮")
    settings._queue.put(("call", lambda: settings._on_download_model("en_ja")))
    time.sleep(1.2)

    print("\n  等待下载流程结束（约 6 秒）...")
    time.sleep(6)

    print()
    print("=" * 72)
    print("  证据")
    print("=" * 72)
    print(f"  各模型被下载的次数: {download_calls}")
    cnt = {}
    for k in download_calls:
        cnt[k] = cnt.get(k, 0) + 1
    print(f"  统计: {cnt}")

    duplicated = [k for k, v in cnt.items() if v > 1]
    if duplicated:
        print(f"\n  [BUG 已复现] 这些模型被重复下载了: {duplicated}")
        print("    原因：第二个任务的 need_base 判断只看「是否已安装」，")
        print("    看不到「另一个线程正在下载中」，于是又下了一遍。")
    else:
        print("\n  [正常] 没有模型被重复下载")

    # 检查进度事件是否出现「同一个 key 的百分比往回跳」
    print(f"\n  zh_en 卡片收到的进度序列（前 40 个）:")
    zh = [p for k, p in progress_events if k == "zh_en"]
    print(f"    {zh[:40]}")
    backjumps = sum(1 for a, b in zip(zh, zh[1:]) if b < a)
    print(f"    百分比「往回跳」的次数: {backjumps}")
    if backjumps > 0:
        print("    [BUG 已复现] 进度条会往回跳，这就是用户看到的『乱跳』")

    print(f"\n  en_ja 卡片收到的进度事件数: {len([1 for k, _ in progress_events if k == 'en_ja'])}")
    if not [1 for k, _ in progress_events if k == "en_ja"]:
        print("    [BUG 已复现] 中译日卡片一条进度都没收到，会一直停在 0%")

    models.is_installed = real_is_installed
    settings.shutdown()
    time.sleep(0.5)
    service.unregister_hotkey()

    return 1 if (duplicated or backjumps) else 0


if __name__ == "__main__":
    sys.exit(main())
