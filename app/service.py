# -*- coding: utf-8 -*-
"""
瞬译 - 全局热键服务

职责：监听全局快捷键 -> 取词 -> 翻译 -> 回写，并把状态汇报给界面（托盘/控制台）。

设计要点：
1. 键盘钩子的回调里绝不做耗时工作（否则会拖慢系统输入），
   回调只把任务丢进队列，真正的处理在后台工作线程里串行执行。
2. 任何异常都被捕获写日志并提示用户，绝不让钩子线程崩掉（需求硬要求）。
3. 引擎懒加载：只有真正翻译时才把模型读进内存，启动更快。
4. 目标语言所需模型缺失时，自动在后台下载，并汇报进度。
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable

import keyboard

from . import config, models, paths
from .capture import capture_selected_text
from .console import safe_print
from .engine import TranslationEngine
from .injector import inject_text
from .logger import get_logger

log = get_logger()

NotifyFn = Callable[[str, str], None]

# ---------------- 快捷键冲突探测 ----------------

_VK_SPECIAL = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09,
    "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "page up": 0x21, "page down": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "escape": 0x1B, "esc": 0x1B,
}
_MOD_FLAGS = {
    "ctrl": 0x0002, "control": 0x0002, "alt": 0x0001, "shift": 0x0004,
    "windows": 0x0008, "win": 0x0008, "super": 0x0008,
}


def hotkey_to_win(hotkey: str) -> tuple[int, int | None]:
    """把 "ctrl+alt+j" 解析为 Windows 的（修饰键标志, 虚拟键码）。

    字母和数字直接用 ASCII 码换算（虚拟键码 VK_A..VK_Z = 0x41..0x5A、VK_0..VK_9 = 0x30..0x39），
    其余符号才借助系统的 VkKeyScanW —— 注意必须显式声明参数类型，
    否则 ctypes 会按 ANSI 传指针，函数拿到错误字符串后返回 -1。
    """
    import ctypes
    import string

    mods = 0
    vk = None
    for part in str(hotkey or "").lower().split("+"):
        part = part.strip()
        if not part:
            continue
        if part in _MOD_FLAGS:
            mods |= _MOD_FLAGS[part]
        elif part in _VK_SPECIAL:
            vk = _VK_SPECIAL[part]
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x6F + int(part[1:])          # F1 = 0x70
        elif len(part) == 1:
            ch = part
            if ch in string.ascii_lowercase:
                vk = ord(ch.upper())
            elif ch in string.digits:
                vk = ord(ch)
            else:
                user32 = ctypes.windll.user32
                user32.VkKeyScanW.argtypes = [ctypes.c_wchar]
                user32.VkKeyScanW.restype = ctypes.c_short
                result = user32.VkKeyScanW(ch)
                if result != -1:
                    vk = result & 0xFF
    return mods, vk


def probe_hotkey_conflict(hotkey: str) -> tuple[bool, str]:
    """探测该组合键是否已被系统或其他程序注册为**全局热键**。

    做法：用 Win32 的 RegisterHotKey 试着注册一次，如果失败且错误码是
    ERROR_HOTKEY_ALREADY_REGISTERED(1409)，说明确实被别人占了。

    注意：这是尽力而为的检测。很多软件（输入法、截图工具）是靠键盘钩子实现的，
    不会出现在 RegisterHotKey 里，这类冲突检测不出来 —— 所以保存后仍要提示用户：
    "如果按了没反应就换一个组合"。
    """
    mods, vk = hotkey_to_win(hotkey)
    if vk is None:
        return False, ""

    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    # 显式声明参数类型：HWND 用 void_p（传 None 表示 NULL），
    # 否则 ctypes 可能按 32 位截断句柄，导致调用失败被误判为"冲突"
    user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    user32.RegisterHotKey.restype = ctypes.c_int
    user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.UnregisterHotKey.restype = ctypes.c_int

    hotkey_id = 0x5A5A
    MOD_NOREPEAT = 0x4000
    ctypes.set_last_error(0)
    if user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk):
        user32.UnregisterHotKey(None, hotkey_id)
        return False, ""
    if ctypes.get_last_error() == 1409:      # ERROR_HOTKEY_ALREADY_REGISTERED
        return True, "这个组合已经被系统或其他软件注册为全局快捷键了。"
    return False, ""


class HotkeyService:
    """全局翻译服务。"""

    def __init__(self, notify: NotifyFn | None = None):
        self.cfg = config.load()
        # 默认通知方式：打印到控制台（托盘模式会把它替换成托盘气泡）
        self._notify = notify or (lambda title, message: safe_print(f"[{title}] {message}"))
        self._engine: TranslationEngine | None = None
        self._engine_lock = threading.Lock()
        self._jobs: queue.Queue = queue.Queue()
        self._running = False
        self._worker: threading.Thread | None = None
        self._handler = None
        self.last_result: dict = {}
        self._busy = threading.Event()          # 防止连按触发多重翻译
        # 打开设置窗口的入口（由主程序注入；缺模型时用它把用户直接引到设置界面）
        self.settings_launcher: Callable[[], None] | None = None

    # ---------------- 生命周期 ----------------

    def start(self) -> None:
        """启动工作线程并注册全局热键。"""
        self._running = True
        self._worker = threading.Thread(target=self._worker_loop, name="shunyi-worker", daemon=True)
        self._worker.start()
        self.register_hotkey()
        log.info("服务已启动 | %s", paths.describe())
        threading.Thread(target=self._background_preload, name="shunyi-preload", daemon=True).start()

    def stop(self) -> None:
        """停止服务，注销热键。"""
        self._running = False
        self.unregister_hotkey()
        try:
            self._jobs.put_nowait(None)
        except queue.Full:
            pass
        log.info("服务已停止")

    def register_hotkey(self) -> bool:
        """按当前配置注册全局热键（重复调用会先注销旧的）。"""
        self.unregister_hotkey()
        hotkey = str(self.cfg.get("hotkey") or "alt+j")
        try:
            # suppress=True：拦下这个组合键，避免 Alt+J 被前台程序（如 Word 菜单）吃走
            self._handler = keyboard.add_hotkey(hotkey, self._on_hotkey, suppress=True)
            log.info("热键已注册: %s", hotkey)
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("热键注册失败 %s: %s", hotkey, exc)
            self._notify("快捷键注册失败", f"「{hotkey}」无法注册，可能被其他软件占用。请改配置文件里的 hotkey。")
            self._handler = None
            return False

    def unregister_hotkey(self) -> None:
        if self._handler is not None:
            try:
                keyboard.remove_hotkey(self._handler)
            except Exception:  # noqa: BLE001
                pass
            self._handler = None

    def reload_config(self) -> None:
        """重新读取配置（托盘菜单切换语言/档位后调用），必要时重注册热键。"""
        old_hotkey = self.cfg.get("hotkey")
        self.cfg = config.load(force=True)
        if self.cfg.get("hotkey") != old_hotkey:
            self.register_hotkey()
        log.info("配置已刷新: 目标=%s 档位=%s 开关=%s",
                 self.cfg["target_lang"], self.cfg["quality"], self.cfg["enabled"])

    # ---------------- 热键回调 ----------------

    def _on_hotkey(self) -> None:
        """键盘钩子回调：只做最小工作，立刻返回。"""
        try:
            if self._busy.is_set():
                return                    # 上一次还没处理完，忽略这次按键
            self._jobs.put_nowait("translate")
        except Exception:  # noqa: BLE001
            log.error("热键回调异常（已忽略）")

    def _worker_loop(self) -> None:
        while self._running:
            try:
                job = self._jobs.get(timeout=0.4)
            except queue.Empty:
                continue
            if job is None:
                break
            self._busy.set()
            try:
                self._handle_translate()
            except Exception:  # noqa: BLE001
                log.exception("翻译流程异常")
                self._notify("翻译出错", "详情见日志文件（托盘菜单可打开）")
            finally:
                self._busy.clear()

    # ---------------- 主流程 ----------------

    def _handle_translate(self) -> None:
        cfg = config.load(force=True)
        self.cfg = cfg

        if not cfg.get("enabled", True):
            self._notify("翻译已关闭", "在托盘菜单里重新开启")
            return

        target = cfg.get("target_lang", "en")
        quality = cfg.get("quality", "fast")

        # 1) 取词
        text, how = capture_selected_text(cfg.get("capture_mode", "auto"), int(cfg.get("max_chars", 2000)))
        if not text:
            log.info("取词失败: %s", how)
            self._notify("没有取到文字", how)
            return
        log.info("取词成功(%s): %s", how, text[:60])

        # 2) 模型就绪检查（缺失则引导用户到设置窗口下载，而不是报错或静默失败）
        missing = models.missing_for_target(target)
        if missing:
            names = "、".join(models.PACKAGES[k]["display"] for k in missing)
            log.info("模型未就绪: %s", missing)
            self._notify("模型还没下载", f"缺少：{names}。已为你打开设置窗口，点「下载」即可。")
            self._open_settings()
            return

        # 3) 翻译
        engine = self._get_engine()
        t0 = time.time()
        result = engine.translate(text, target, quality)
        cost = time.time() - t0
        if not result:
            self._notify("翻译结果为空", "请重试或换个说法")
            return

        # 4) 回写
        way = inject_text(result, cfg.get("inject_mode", "paste"), bool(cfg.get("restore_clipboard", True)))

        self.last_result = {
            "source": text, "target": result, "lang": target,
            "quality": quality, "seconds": round(cost, 3), "route": engine.last_stats.get("route", ""),
        }
        log.info("完成 %.2fs %s -> %s | %s | %s", cost, text[:30], result[:30], way, engine.last_stats)
        self._notify(
            f"已翻译成{'英文' if target == 'en' else '日文'}（{cost:.2f}s）",
            result[:80],
        )

    # ---------------- 引擎与模型 ----------------

    def _get_engine(self) -> TranslationEngine:
        with self._engine_lock:
            if self._engine is None:
                threads = int(config.load().get("cpu_threads", 0) or 0)
                self._engine = TranslationEngine(threads=threads)
            return self._engine

    def preload(self) -> None:
        """提前把当前目标语言的模型读进内存（切换语言后调用）。"""
        target = config.load().get("target_lang", "en")
        if models.missing_for_target(target):
            return
        try:
            engine = self._get_engine()
            engine.load_model("zh_en")
            if target == "ja":
                engine.load_model("en_ja")
            log.info("模型预加载完成: %s", target)
        except Exception:  # noqa: BLE001
            log.exception("模型预加载失败")

    def _background_preload(self) -> None:
        time.sleep(0.5)
        self.preload()

    def _open_settings(self) -> None:
        """打开设置窗口（由主程序注入入口；未注入时给出文字指引）。"""
        launcher = self.settings_launcher
        if launcher is None:
            return
        try:
            launcher()
        except Exception:  # noqa: BLE001
            log.exception("打开设置窗口失败")

    # 供托盘菜单直接调用 ----------------

    def set_target_lang(self, lang: str) -> None:
        config.update(target_lang=lang)
        self.reload_config()
        self._notify("目标语言已切换", "英文" if lang == "en" else "日文")
        threading.Thread(target=self._after_switch, args=("lang",), daemon=True).start()

    def set_quality(self, quality: str) -> None:
        config.update(quality=quality)
        self.reload_config()
        self._notify("翻译档位已切换", "高速度" if quality == "fast" else "高质量")

    def toggle_enabled(self) -> bool:
        enabled = not bool(config.load().get("enabled", True))
        config.update(enabled=enabled)
        self.reload_config()
        self._notify("翻译已开启" if enabled else "翻译已关闭", "")
        return enabled

    def set_hotkey(self, new_hotkey: str) -> tuple[bool, str]:
        """修改全局快捷键：先试注册，成功才落盘；失败自动回滚。

        返回 (是否成功, 失败原因)。保证任何情况下软件都还有快捷键可用。
        """
        new_hotkey = (new_hotkey or "").strip().lower()
        if not new_hotkey:
            return False, "快捷键不能为空。"
        if "+" not in new_hotkey:
            return False, "快捷键必须包含修饰键（Ctrl / Alt / Shift）再加一个主键。"

        old = str(config.load().get("hotkey") or "alt+j")
        if new_hotkey == old:
            return True, ""

        # 第一步：探测是否被系统/其他程序注册占用（不影响现有快捷键）
        conflict, why = probe_hotkey_conflict(new_hotkey)
        if conflict:
            log.warning("快捷键冲突 %s: %s", new_hotkey, why)
            return False, why

        # 第二步：正式切换（先注销旧的，再注册新的；失败则回滚）
        self.unregister_hotkey()
        try:
            self._handler = keyboard.add_hotkey(new_hotkey, self._on_hotkey, suppress=True)
        except Exception as exc:  # noqa: BLE001
            log.error("切换快捷键失败，回滚到旧快捷键: %s", exc)
            self._handler = None
            self.register_hotkey()          # 回滚：恢复原来的快捷键
            return False, f"注册「{new_hotkey}」失败，已自动恢复原来的快捷键。"

        # 第三步：落盘持久化
        config.update(hotkey=new_hotkey)
        self.cfg = config.load(force=True)
        log.info("快捷键已切换: %s -> %s", old, new_hotkey)
        return True, ""

    def unload_models(self) -> None:
        """卸掉已加载的模型，释放文件占用（删除模型文件前调用）。"""
        with self._engine_lock:
            if self._engine is not None:
                self._engine.unload_all()
        log.info("模型已卸载")

    def _after_switch(self, _reason: str) -> None:
        """切换目标语言后：模型齐全就预加载；缺模型就打开设置窗口引导下载。"""
        target = config.load().get("target_lang", "en")
        missing = models.missing_for_target(target)
        if missing:
            names = "、".join(models.PACKAGES[k]["display"] for k in missing)
            self._notify("还缺少翻译模型", f"{names}。已打开设置窗口，点「下载」即可。")
            self._open_settings()
        else:
            self.preload()
