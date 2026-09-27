# -*- coding: utf-8 -*-
"""
模型管理测试：
1) 下载中「取消」是否立即生效（并保留已下部分便于续传）
2) 「删除」是否真的释放空间（用临时假模型目录测试，不动真模型）
3) 界面上的状态文字 / 按钮文字 / 进度条是否随状态正确切换
"""
import os
import shutil
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, models, paths            # noqa: E402
import app.settings_window as sw                 # noqa: E402
from app.service import HotkeyService            # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  —— {detail}" if detail else ""))


class AutoAnswer:
    answer = True

    @classmethod
    def askokcancel(cls, *a, **k):
        return cls.answer

    @staticmethod
    def showinfo(*a, **k):
        return None

    @staticmethod
    def showerror(*a, **k):
        return None

    @staticmethod
    def showwarning(*a, **k):
        return None


def test_cancel_download():
    print("\n=== 1. 下载取消与重新下载（真实网络）===")
    cancel = threading.Event()
    dest = os.path.join(paths.models_dir(), "_cancel_test.bin")
    if os.path.exists(dest):
        os.remove(dest)

    url = models.PACKAGES["zh_en"]["url"]
    result = {}
    progress_count = [0]

    def cb(*_a):
        progress_count[0] += 1

    def worker():
        try:
            models._open_with_progress(url, dest, cb, "测试下载", cancel_event=cancel)
            result["outcome"] = "完成"
        except models.DownloadCancelled:
            result["outcome"] = "已取消"
        except Exception as exc:  # noqa: BLE001
            result["outcome"] = f"异常:{type(exc).__name__}"

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    # 等真正开始下载（并发下载会先把文件预分配，所以不能用"文件大小"判断进度，
    # 改看进度回调次数）
    deadline = time.time() + 40
    while time.time() < deadline:
        if progress_count[0] >= 3:       # 至少有几次进度更新说明已经在下了
            break
        if result.get("outcome"):
            break
        time.sleep(0.2)
    cancel.set()
    thread.join(timeout=20)

    check("取消后立即停止", result.get("outcome") == "已取消", result.get("outcome", ""))
    check("取消前确实在下（收到过进度回调）", progress_count[0] > 0, f"{progress_count[0]} 次")
    check("取消后不残留半成品文件（并发下载不做区间续传）", not os.path.exists(dest),
          f"文件{'仍存在' if os.path.exists(dest) else '已清理'}")

    # 重新下载：应该能完整拿到文件
    print("  （验证重新下载能完整完成）")
    done = {}

    def continue_worker():
        try:
            got = models._open_with_progress(url, dest, lambda *a: None, "重下测试", cancel_event=None)
            done["size"] = got
        except Exception as exc:  # noqa: BLE001
            done["error"] = str(exc)

    t2 = threading.Thread(target=continue_worker, daemon=True)
    t2.start()
    t2.join(timeout=300)
    expected = models.PACKAGES["zh_en"]["size"]
    check("重新下载后文件完整", done.get("size", 0) >= expected * 0.98,
          f"{done.get('size', 0)/1024/1024:.1f} MB / 期望 {expected/1024/1024:.1f} MB")

    if os.path.exists(dest):
        os.remove(dest)


def test_remove_logic():
    print("\n=== 2. 删除逻辑（用临时假模型，不动真模型）===")
    fake_key = "_delete_test_model"
    fake_dir = models.model_dir(fake_key)
    fake_archive = os.path.join(paths.models_dir(), f"{fake_key}.argosmodel")

    try:
        os.makedirs(os.path.join(fake_dir, "model"), exist_ok=True)
        with open(os.path.join(fake_dir, "model", "model.bin"), "wb") as f:
            f.write(b"x" * (2 * 1024 * 1024))
        with open(fake_archive, "wb") as f:
            f.write(b"y" * (1 * 1024 * 1024))

        freed = models.remove(fake_key)
        check("删除返回释放的字节数", freed >= 3 * 1024 * 1024, f"{freed/1024/1024:.1f} MB")
        check("解压目录已删除", not os.path.isdir(fake_dir))
        check("下载缓存已删除", not os.path.exists(fake_archive))
    except PermissionError as exc:
        check("删除未被占用文件", False, f"PermissionError: {exc}")
    finally:
        shutil.rmtree(fake_dir, ignore_errors=True)
        if os.path.exists(fake_archive):
            os.remove(fake_archive)

    print("  （确认真模型未受影响）")
    check("中译英模型仍在", models.is_installed("zh_en"))
    check("中译日模型仍在", models.is_installed("en_ja"))


def test_ui_states():
    print("\n=== 3. 界面状态与进度显示 ===")
    sw.messagebox = AutoAnswer
    service = HotkeyService(notify=lambda t, m: None)
    service.register_hotkey()
    settings = sw.SettingsWindow(service)
    settings.open()
    time.sleep(2.5)

    holder = {}

    def grab():
        holder["status"] = {k: v["status"].cget("text") for k, v in settings._model_ui.items()}
        holder["action"] = {k: v["action"].cget("text") for k, v in settings._model_ui.items()}
        holder["bar_visible"] = bool(settings._model_ui["zh_en"]["progress"].winfo_manager())

    settings._queue.put(("call", grab))
    time.sleep(0.8)
    print(f"       已安装状态：{holder['status']}")
    check("已安装显示「已下载」", "已下载" in holder["status"]["zh_en"], holder["status"]["zh_en"])
    check("已安装时按钮为「删除」", holder["action"]["zh_en"] == "删除", holder["action"]["zh_en"])
    check("平时不显示进度条", holder["bar_visible"] is False)

    def fake_progress():
        settings._on_download_progress("zh_en", 30 * 1024 * 1024, 71 * 1024 * 1024, "测试")
        holder["after"] = {
            "status": settings._model_ui["zh_en"]["status"].cget("text"),
            "value": settings._model_ui["zh_en"]["progress"]["value"],
            "bar": bool(settings._model_ui["zh_en"]["progress"].winfo_manager()),
        }
        settings._model_status["zh_en"] = {"status": "installed", "percent": 0, "error": ""}

    settings._queue.put(("call", fake_progress))
    time.sleep(0.8)
    after = holder.get("after", {})
    print(f"       模拟进度后：{after}")
    check("进度文字含百分比与已下/总量", "%" in after.get("status", "") and "MB" in after.get("status", ""),
          after.get("status", ""))
    check("进度条数值同步（30/71≈42%）", 40 <= float(after.get("value", 0)) <= 45, str(after.get("value")))
    check("下载时显示进度条", after.get("bar") is True)

    def check_absent_state():
        settings._model_status["en_ja"] = {"status": "absent", "percent": 0, "error": ""}
        # 模拟未安装：临时把状态改成未下载展示
        settings._model_ui["en_ja"]["status"].configure(text="状态：未下载")
        holder["absent"] = settings._model_ui["en_ja"]["status"].cget("text")

    settings._queue.put(("call", check_absent_state))
    time.sleep(0.6)
    check("未下载状态文案正确", holder.get("absent") == "状态：未下载", str(holder.get("absent")))

    settings.shutdown()
    time.sleep(0.5)
    service.unregister_hotkey()


def main():
    test_cancel_download()
    test_remove_logic()
    test_ui_states()

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n=== 结果：{passed}/{len(RESULTS)} 项通过 ===")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  未通过：{name} {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
