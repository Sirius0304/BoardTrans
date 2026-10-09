# -*- coding: utf-8 -*-
"""
打包后的引擎自检：验证 exe 环境里翻译引擎能否正常工作。
会打包成一个带控制台的小 exe，运行后直接显示结果。
"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def pause():
    """交互式运行时才等待按键，自动化测试时不阻塞。"""
    if "--no-pause" in sys.argv:
        return
    try:
        if sys.stdin and sys.stdin.isatty():
            input("\n按回车键关闭...")
    except Exception:  # noqa: BLE001
        pass


print("=" * 60)
print("  键译 引擎自检（exe 环境）")
print("=" * 60)
print("  是否打包运行 (frozen):", bool(getattr(sys, "frozen", False)))
print("  可执行文件:", sys.executable)

try:
    import ctranslate2
    print("  ctranslate2:", ctranslate2.__version__)
    import sentencepiece
    print("  sentencepiece: OK")
    import keyboard, pyperclip, pystray, PIL
    print("  keyboard/pyperclip/pystray/Pillow: OK")
except Exception:  # noqa: BLE001
    print("!! 依赖导入失败：")
    traceback.print_exc()
    pause()
    sys.exit(1)

# ---- 图形界面（设置窗口）相关 ----
print("\n  ---- 图形界面能力 ----")
try:
    import tkinter
    print("  tkinter: 可用（Tk %s）" % tkinter.TkVersion)
    _r = tkinter.Tk()
    _r.withdraw()
    print("  创建窗口: 成功")
    _r.destroy()
    from app.settings_window import SettingsWindow, TK_AVAILABLE
    print("  设置窗口模块:", "可用" if TK_AVAILABLE else "不可用")
except Exception as exc:  # noqa: BLE001
    print("  !! tkinter 不可用:", exc)
    print("  影响：托盘的「设置」菜单会弹出提示而不是打开窗口，翻译功能不受影响。")

# ---- 设置窗口在打包环境下能否真正跑起来 ----
try:
    from app.service import HotkeyService
    from app.settings_window import SettingsWindow
    import time as _time

    _service = HotkeyService(notify=lambda t, m: None)
    _svc_window = SettingsWindow(_service)
    _svc_window.open()
    _time.sleep(3.0)
    ok = _svc_window.root is not None and _svc_window.root.winfo_exists()
    print(f"  设置窗口启动测试: {'成功' if ok else '失败'}")
    if ok:
        holder = {}
        _svc_window._queue.put(("call", lambda: holder.update({
            "title": _svc_window.root.title(),
            "cards": len(_svc_window._model_ui),
        })))
        _time.sleep(1.0)
        print(f"      窗口标题: {holder.get('title')}   模型卡片数: {holder.get('cards')}")
    _svc_window.shutdown()
    _time.sleep(0.5)
except Exception:  # noqa: BLE001
    print("  !! 设置窗口启动测试失败：")
    traceback.print_exc()

try:
    from app import models, paths
    print("  数据目录:", paths.data_dir())
    print(models.describe_status())

    if not models.is_installed("zh_en"):
        print("!! 中→英模型未安装，无法自检翻译。请先运行主程序下载模型。")
        pause()
        sys.exit(2)

    from app.engine import TranslationEngine
    t0 = time.time()
    eng = TranslationEngine()
    eng.load_model("zh_en")
    print(f"  模型加载: {time.time()-t0:.2f}s")

    for text, target, quality in (("你好，今天天气不错。", "en", "fast"),
                                  ("我们明天下午三点开个会。", "en", "quality")):
        t0 = time.time()
        out = eng.translate(text, target, quality)
        print(f"  [{target}/{quality}] {text}  ->  {out}   ({(time.time()-t0)*1000:.0f} ms)")

    print("\n  结论：EXE 环境翻译引擎工作正常。")
    code = 0
except Exception:  # noqa: BLE001
    print("\n!! 自检失败：")
    traceback.print_exc()
    code = 3

pause()
sys.exit(code)
