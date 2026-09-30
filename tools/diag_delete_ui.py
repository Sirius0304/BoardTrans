# -*- coding: utf-8 -*-
"""
排查第二轮：走完整的界面删除路径（_on_delete_model），并模拟"删除时引擎被重新加载"的竞态。

场景 C：正常点删除按钮                   → 期望清理干净
场景 D：删除过程中引擎被重新加载（模拟用户同时按快捷键 / 恰好有下载完成触发预加载）
        → 怀疑会因"文件正在使用"而删除失败，但界面却显示已移除

每个场景测完都恢复备份。
"""
import os
import shutil
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models, paths           # noqa: E402
import app.settings_window as sw        # noqa: E402
from app.service import HotkeyService   # noqa: E402

KEY = "zh_en"
PKG_DIR = models.model_dir(KEY)
CACHE_FILE = os.path.join(paths.models_dir(), f"{KEY}.argosmodel")
BACKUP = r"F:\瞬译_备份_20260927\排查删除问题_zh_en"


class AutoAnswer:
    answer = True
    log = []

    @classmethod
    def askokcancel(cls, *a, **k):
        cls.log.append(("确认框", str(a[0] if a else "")))
        return True

    @staticmethod
    def showinfo(title, msg=None, **k):
        AutoAnswer.log.append(("提示", str(title)))

    @staticmethod
    def showerror(title, msg=None, **k):
        AutoAnswer.log.append(("错误", str(title)))

    @staticmethod
    def showwarning(title, msg=None, **k):
        AutoAnswer.log.append(("警告", str(title)))


def snapshot():
    return {
        "pkg_exists": os.path.isdir(PKG_DIR),
        "pkg_files": sum(len(fs) for _d, _s, fs in os.walk(PKG_DIR)) if os.path.isdir(PKG_DIR) else 0,
        "cache_exists": os.path.exists(CACHE_FILE),
        "is_installed": models.is_installed(KEY),
    }


def show(tag, s):
    print(f"  [{tag}] packages/: {'有' if s['pkg_exists'] else '无'}"
          f"({s['pkg_files']}文件)  cache: {'有' if s['cache_exists'] else '无'}"
          f"  is_installed: {s['is_installed']}")


def backup():
    if os.path.exists(BACKUP):
        shutil.rmtree(BACKUP, ignore_errors=True)
    os.makedirs(BACKUP, exist_ok=True)
    if os.path.isdir(PKG_DIR):
        shutil.copytree(PKG_DIR, os.path.join(BACKUP, "packages_" + KEY))
    if os.path.exists(CACHE_FILE):
        shutil.copy2(CACHE_FILE, os.path.join(BACKUP, f"{KEY}.argosmodel"))


def restore():
    bpkg = os.path.join(BACKUP, "packages_" + KEY)
    bfile = os.path.join(BACKUP, f"{KEY}.argosmodel")
    if os.path.isdir(PKG_DIR):
        shutil.rmtree(PKG_DIR, ignore_errors=True)
    if os.path.isdir(bpkg):
        shutil.copytree(bpkg, PKG_DIR)
    if not os.path.exists(CACHE_FILE) and os.path.exists(bfile):
        shutil.copy2(bfile, CACHE_FILE)


def main():
    sw.messagebox = AutoAnswer
    # 界面弹窗已统一走 app.dialogs（不再用系统 messagebox）：这里打桩自动应答
    sw.dialogs.ask = lambda *a, **k: True
    sw.dialogs.tell = lambda *a, **k: None
    print("=" * 74)
    print("  界面删除路径实测")
    print("=" * 74)

    backup()
    print("  删除前:")
    show("前", snapshot())

    # ---------------- 场景 C：正常点删除 ----------------
    print()
    print("=" * 74)
    print("  场景 C：正常点删除按钮（无并发）")
    print("=" * 74)
    AutoAnswer.log.clear()
    service = HotkeyService(notify=lambda t, m: None)
    settings = sw.SettingsWindow(service)
    settings.open()
    time.sleep(2.5)

    settings._queue.put(("call", lambda: settings._on_delete_model(KEY)))
    time.sleep(4)
    s = snapshot()
    print(f"  弹窗记录: {AutoAnswer.log}")
    show("删除后", s)
    c_ok = (not s["pkg_exists"]) and (not s["cache_exists"]) and (not s["is_installed"])
    print(f"  → 场景 C {'清理干净' if c_ok else '有残留！'}")

    settings.shutdown()
    time.sleep(0.5)

    print("\n  （恢复备份）")
    restore()
    time.sleep(1)

    # ---------------- 场景 D：删除时引擎被重新加载 ----------------
    print()
    print("=" * 74)
    print("  场景 D：删除的同时，引擎被重新加载（竞态）")
    print("=" * 74)
    AutoAnswer.log.clear()
    service2 = HotkeyService(notify=lambda t, m: None)
    settings2 = sw.SettingsWindow(service2)
    settings2.open()
    time.sleep(2.5)

    # 先在内存里加载模型（模拟"用户刚翻译过"）
    engine = service2._get_engine()
    engine.load_model(KEY)
    print(f"  已加载模型到内存: {engine.loaded_keys()}")

    stop = threading.Event()

    def keep_reloading():
        """模拟"用户同时按快捷键翻译"——worker 线程会不断把模型重新加载回内存。"""
        for _ in range(40):
            if stop.is_set():
                break
            try:
                engine.load_model(KEY)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.05)

    reloader = threading.Thread(target=keep_reloading, daemon=True)
    reloader.start()

    settings2._queue.put(("call", lambda: settings2._on_delete_model(KEY)))
    time.sleep(4.5)
    stop.set()
    reloader.join(timeout=3)

    s = snapshot()
    print(f"  弹窗记录: {AutoAnswer.log}")
    show("删除后", s)
    d_ok = (not s["pkg_exists"]) and (not s["cache_exists"]) and (not s["is_installed"])
    print(f"  → 场景 D {'清理干净' if d_ok else '有残留！← 问题复现'}")

    settings2.shutdown()
    time.sleep(0.5)

    print("\n  （恢复备份）")
    restore()
    time.sleep(1)
    show("恢复后", snapshot())

    print()
    print("=" * 74)
    print("  结论")
    print("=" * 74)
    if c_ok and d_ok:
        print("  两种界面路径都干净。说明问题不在删除逻辑本身，")
        print("  需要进一步排查（例如用户看到的是别的文件夹、或旧版本 exe）。")
    elif not d_ok:
        print("  **场景 D 复现了问题**：删除过程中模型被重新加载 → 文件占用 → 删除失败，")
        print("  但界面因为 is_installed() 变为 False 而显示成'已移除'，实际文件残留。")
    else:
        print("  场景 C 就有残留，问题更基础。")
    print("=" * 74)


if __name__ == "__main__":
    sys.exit(main())
