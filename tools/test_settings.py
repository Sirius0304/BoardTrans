# -*- coding: utf-8 -*-
"""
设置窗口流程测试：
1) 窗口能否创建、界面元素是否齐全
2) 「保存」是否落盘并立即生效（档位 / 快捷键）
3) 托盘改了配置，窗口能否自动同步（不会出现"显示 A 实际 B"）
4) 快捷键冲突时是否保留原键（不失去响应快捷键的能力）
5) 窗口关闭后软件仍在运行（窗口只是隐藏）

测试会临时把 messagebox 换成自动应答，避免弹窗卡住。
"""
import os
import sys
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
    """自动应答对话框：askokcancel 返回固定值，其余丢弃。"""
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


def main():
    sw.messagebox = AutoAnswer
    # 界面弹窗已统一走 app.dialogs（不再用系统 messagebox）：这里打桩自动应答
    sw.dialogs.ask = lambda *a, **k: True
    sw.dialogs.tell = lambda *a, **k: None
    original = dict(config.load())

    service = HotkeyService(notify=lambda t, m: None)
    service.register_hotkey()          # 注册全局热键（不启动完整服务，避免预加载耗时）
    settings = sw.SettingsWindow(service)
    service.settings_launcher = settings.open

    print("\n=== 1. 打开设置窗口 ===")
    settings.open()
    time.sleep(2.5)
    created = settings.root is not None
    check("窗口创建成功", created)
    if not created:
        return 1

    holder = {}

    def inspect_ui():
        holder["quality"] = settings._quality_var.get()
        holder["hotkey_text"] = settings._hotkey_entry.get()
        holder["model_keys"] = list(settings._model_ui.keys())
        holder["model_status"] = {k: v["status"].cget("text") for k, v in settings._model_ui.items()}
        holder["action_text"] = {k: v["action"].cget("text") for k, v in settings._model_ui.items()}
        holder["visible"] = settings.root.state()
        holder["title"] = settings.root.title()
        # 翻译字数上限控件：范围与步长都要从**真实控件**上读，
        # 而不是看源码字符串（源码级断言会在重构时骗人）
        holder["limit_min"] = settings._limit_spin.cget("from")
        holder["limit_max"] = settings._limit_spin.cget("to")
        holder["limit_step"] = settings._limit_spin.cget("increment")

    settings._queue.put(("call", inspect_ui))
    time.sleep(0.8)

    print("\n=== 2. 界面元素检查 ===")
    check("窗口标题正确", holder.get("title") == "键译 · 设置", holder.get("title", ""))
    check("模型卡片齐全（两个）", sorted(holder.get("model_keys", [])) == ["en_ja", "zh_en"],
          str(holder.get("model_keys")))
    check("窗口处于可见状态", holder.get("visible") == "normal", str(holder.get("visible")))
    check("翻译字数上限范围是 0~1000",
          holder.get("limit_min") == 0 and holder.get("limit_max") == 1000,
          f"from={holder.get('limit_min')} to={holder.get('limit_max')}")
    check("翻译字数上限每次只增减 1（步长非 1 会一下跳 50）",
          holder.get("limit_step") == 1, f"increment={holder.get('limit_step')}")
    print(f"       模型状态显示：{holder.get('model_status')}")
    print(f"       按钮文字：{holder.get('action_text')}")
    check("已安装模型显示为「删除」",
          all(v == "删除" for v in holder.get("action_text", {}).values()) or
          any(v == "删除" for v in holder.get("action_text", {}).values()),
          str(holder.get("action_text")))

    print("\n=== 3. 修改档位并保存 ===")
    def set_quality():
        settings._quality_var.set("quality")
        settings._mark_dirty()
        settings._on_save()
    settings._queue.put(("call", set_quality))
    time.sleep(1.5)
    check("档位已写入配置文件", config.load()["quality"] == "quality", config.load()["quality"])
    check("服务内存中的配置同步更新", service.cfg["quality"] == "quality", service.cfg["quality"])
    check("保存后未保存标记被清除", settings._dirty is False)

    print("\n=== 4. 修改快捷键并保存 ===")
    def set_hotkey():
        settings._pending_hotkey = "ctrl+alt+j"
        settings._mark_dirty()
        settings._on_save()
    settings._queue.put(("call", set_hotkey))
    time.sleep(1.5)
    check("快捷键已写入配置文件", config.load()["hotkey"] == "ctrl+alt+j", config.load()["hotkey"])
    check("热键已重新注册（handler 有效）", service._handler is not None)

    print("\n=== 5. 快捷键冲突处理（用 RegisterHotKey 真实占用）===")
    import ctypes
    from app.service import hotkey_to_win

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    mods, vk = hotkey_to_win("ctrl+alt+k")
    HOTKEY_ID = 0x7A7A
    occupied = bool(user32.RegisterHotKey(None, HOTKEY_ID, mods, vk))
    check("测试用组合键占用成功（构造冲突场景）", occupied)

    def conflict():
        settings._pending_hotkey = "ctrl+alt+k"
        settings._mark_dirty()
        settings._on_save()
    settings._queue.put(("call", conflict))
    time.sleep(1.8)
    check("冲突时保留原快捷键（未写入配置）", config.load()["hotkey"] == "ctrl+alt+j", config.load()["hotkey"])
    check("冲突后软件仍能响应快捷键", service._handler is not None)
    user32.UnregisterHotKey(None, HOTKEY_ID)

    print("\n=== 6. 托盘侧修改 → 窗口自动同步 ===")
    service.set_quality("fast")          # 模拟托盘点「高速度」
    time.sleep(1.5)
    holder.clear()
    settings._queue.put(("call", inspect_ui))
    time.sleep(0.8)
    check("窗口档位自动跟随托盘变为高速度", holder.get("quality") == "fast", holder.get("quality"))
    check("配置文件与界面一致", config.load()["quality"] == holder.get("quality"))

    print("\n=== 7. 关闭窗口（软件继续运行）===")
    def close_window():
        settings._on_window_close()
    settings._queue.put(("call", close_window))
    time.sleep(1.0)
    settings._queue.put(("call", inspect_ui))
    time.sleep(0.6)
    check("窗口已隐藏（软件仍在运行）", holder.get("visible") == "withdrawn", str(holder.get("visible")))
    check("服务仍在运行（热键仍注册）", service._handler is not None)

    print("\n=== 8. 再次打开（不重复创建）===")
    first_root = settings.root
    settings.open()
    time.sleep(1.2)
    settings._queue.put(("call", inspect_ui))
    time.sleep(0.6)
    check("复用了同一个窗口实例", settings.root is first_root)
    check("窗口重新显示", holder.get("visible") == "normal", str(holder.get("visible")))

    # 复位配置
    config.update(**{k: original[k] for k in ("hotkey", "quality", "target_lang", "enabled")})
    service.unregister_hotkey()
    settings.shutdown()
    time.sleep(0.5)

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n=== 结果：{passed}/{len(RESULTS)} 项通过 ===")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  未通过：{name} {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
