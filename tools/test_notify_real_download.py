# -*- coding: utf-8 -*-
"""「通知提醒」真实端到端验证：走完整流程跑一次真实模型下载，检查提醒是否符合开关。

流程与用户实际操作完全一致：
  设置窗口 → 点「下载」→ 后台线程真实下载（联网 71 MB）→ 队列回调 → 通知
本脚本只是把"点按钮"换成直接调用窗口的下载入口（对话框自动应答）。

用法：
  F:\\shunyi\\.venv\\Scripts\\python.exe tools\\test_notify_real_download.py off
  （参数 off/on 表示期望的开关状态；脚本会先按该状态写配置，再触发下载）

注意：会真联网下载约 71 MB（约 1 分钟）；原缓存包会先备份，结束后按 sha256 自动还原。
"""
import hashlib
import os
import shutil
import sys
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from app import config, models, notify as notify_mod, paths      # noqa: E402
import app.settings_window as sw                                 # noqa: E402
from app.service import HotkeyService                            # noqa: E402

WANT = (sys.argv[1] if len(sys.argv) > 1 else "off").lower() == "on"

CAPTURED = []


def sink(title, message):
    CAPTURED.append((title, message))


class AutoAnswer:
    answer = True

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


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def main():
    sw.messagebox = AutoAnswer
    # 界面弹窗已统一走 app.dialogs（不再用系统 messagebox）：这里打桩自动应答
    sw.dialogs.ask = lambda *a, **k: True
    sw.dialogs.tell = lambda *a, **k: None
    archive = os.path.join(paths.models_dir(), "zh_en.argosmodel")
    backup = archive + ".notifytest-bak"
    pkg = models.model_dir("zh_en")
    pkg_backup = pkg + ".notifytest-bak"

    print("期望开关状态: %s" % ("开 (on)" if WANT else "关 (off)"))
    print("原缓存包    : %s (%s 字节)" % (archive, os.path.getsize(archive) if os.path.exists(archive) else "不存在"))
    print("已安装目录  : %s (%s)" % (pkg, "存在" if os.path.isdir(pkg) else "不存在"))

    # 备份原包与已安装目录，让下载必然真实发生（ensure 在"已安装"时会直接返回，不下载）
    if os.path.exists(archive):
        if os.path.exists(backup):
            os.remove(backup)
        os.rename(archive, backup)
    if os.path.isdir(pkg):
        if os.path.isdir(pkg_backup):
            shutil.rmtree(pkg_backup, ignore_errors=True)
        os.rename(pkg, pkg_backup)

    config.update(notify_enabled=WANT)
    notify_mod.set_sink(sink)
    notify_mod.reset_dedup()

    service = HotkeyService()
    settings = sw.SettingsWindow(service)
    settings.open()
    time.sleep(2.0)

    print("\n开始真实下载（约 1 分钟，请稍候）...", flush=True)
    t0 = time.time()
    settings._queue.put(("call", lambda: settings._on_download_model("zh_en")))

    deadline = time.time() + 900
    while time.time() < deadline:
        if not settings._active_downloads and "zh_en" in settings._model_status \
                and settings._model_status["zh_en"]["status"] in ("installed", "failed", "absent"):
            break
        time.sleep(1.0)
    elapsed = time.time() - t0

    state = settings._model_status.get("zh_en", {})
    installed = models.is_installed("zh_en")
    print("\n下载耗时    : %.1f s" % elapsed)
    print("模型状态    : %s（is_installed=%s）" % (state.get("status"), installed))
    print("收到的提醒  : %s" % (CAPTURED,))

    ok = True
    if state.get("status") != "installed":
        print("  [失败] 下载未成功完成（状态=%s），无法判定提醒行为" % state.get("status"))
        ok = False
    elif not installed:
        print("  [失败] 下载回调说成功，但模型并未安装")
        ok = False
    else:
        if WANT:
            if len(CAPTURED) == 1 and CAPTURED[0][0] == "模型下载完成":
                print("  [通过] 开关开：真实下载完成后收到 1 条「模型下载完成」提醒（不多弹）")
            else:
                print("  [失败] 开关开：期望恰好 1 条提醒，实际 %s" % (CAPTURED,))
                ok = False
        else:
            if not CAPTURED:
                print("  [通过] 开关关：真实下载完成后零提醒（缺陷已修复）")
            else:
                print("  [失败] 开关关：仍然弹了提醒 %s" % (CAPTURED,))
                ok = False

    # 还原：新包与备份一致就删备份，否则保留备份并说明
    try:
        restored = True
        if os.path.isdir(pkg) and os.path.isdir(pkg_backup):
            shutil.rmtree(pkg_backup, ignore_errors=True)       # 重新解压出来的目录等效，删备份
            print("\n已安装目录已由新下载的包重建，旧备份已清理")
        elif os.path.isdir(pkg_backup):
            if os.path.isdir(pkg):
                shutil.rmtree(pkg, ignore_errors=True)
            os.rename(pkg_backup, pkg)
            print("\n新目录未生成，已还原旧的已安装目录")
            restored = False
        if os.path.exists(backup) and os.path.exists(archive):
            if sha256(backup) == sha256(archive):
                os.remove(backup)
                print("缓存包已校验一致，备份已清理")
            else:
                print("注意：新下载的包与备份 sha256 不同，备份保留在 %s" % backup)
        elif os.path.exists(backup) and not os.path.exists(archive):
            os.rename(backup, archive)
            print("下载未产生文件，已还原缓存包备份")
    except Exception as exc:  # noqa: BLE001
        print("\n还原时出错：%s（备份仍在 %s / %s）" % (exc, backup, pkg_backup))

    settings.shutdown()
    print("\n结果：%s" % ("通过" if ok else "失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
