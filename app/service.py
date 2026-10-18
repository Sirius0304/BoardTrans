# -*- coding: utf-8 -*-
"""
键译 - 全局热键服务

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

from . import capture, config, models, notify as notify_mod, paths
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
        # 所有通知统一走 app/notify.py 这个唯一入口：那里会读「通知提醒」开关
        # （关掉就一条都不发），并做同事件去重。传入的 notify 参数只表示
        # "实际怎么发"（控制台打印 / 测试收集），不参与"要不要发"的决策。
        if notify is not None:
            notify_mod.set_sink(notify)
        self._notify = notify_mod.notify
        self._engine: TranslationEngine | None = None
        self._engine_lock = threading.Lock()
        self._jobs: queue.Queue = queue.Queue()
        self._running = False
        self._worker: threading.Thread | None = None
        self._handler = None
        self.last_result: dict = {}
        self._busy = threading.Event()          # 防止连按触发多重翻译
        self._last_activity = time.time()       # 最近一次翻译的时间，用于空闲释放判定
        self._hotkey_at = 0.0                   # 最近一次按下快捷键的时刻（统计延迟用）
        self._idle_check_interval = 20          # 空闲检查周期（秒）；测试时可改小
        # 打开设置窗口的入口（由主程序注入；缺模型时用它把用户直接引到设置界面）
        self.settings_launcher: Callable[[], None] | None = None

    # ---------------- 生命周期 ----------------

    def start(self) -> None:
        """启动工作线程并注册全局热键。"""
        self._running = True
        # 模型默认位置改成「软件目录\models」后，把旧位置的模型搬过来，
        # 否则老用户会以为模型丢了、白重新下载一遍
        try:
            moved = models.migrate_legacy_models()
            if moved:
                log.info("模型位置迁移完成: %s", "；".join(moved))
                self._notify("模型位置已更新",
                             "原有模型已搬到新位置，无需重新下载。" + moved[0])
        except Exception:  # noqa: BLE001
            log.exception("迁移模型位置出错（已忽略，不影响使用）")

        self._worker = threading.Thread(target=self._worker_loop, name="shunyi-worker", daemon=True)
        self._worker.start()
        self.register_hotkey()
        log.info("服务已启动 | %s", paths.describe())
        threading.Thread(target=self._background_preload, name="shunyi-preload", daemon=True).start()
        threading.Thread(target=self._idle_watchdog, name="shunyi-idle", daemon=True).start()

    def notify(self, title: str, message: str) -> None:
        """对外暴露的通知接口（供设置窗口使用）。

        注意：这里不再直接对接托盘，而是走 app/notify.py 的统一入口，
        由入口决定"要不要发"（受「通知提醒」开关控制）与"怎么发"。
        """
        self._notify(title, message)

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
            self._hotkey_at = time.time()  # 记下按键时刻，用于统计"按键→取词/回写"耗时
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
        self._last_activity = time.time()      # 记录活跃时间，避免刚翻译完就被释放

        if not cfg.get("enabled", True):
            self._notify("翻译已关闭", "在托盘菜单里重新开启")
            return

        target = cfg.get("target_lang", "en")
        quality = cfg.get("quality", "fast")

        # 0) 模型就绪检查 —— **必须在取词之前**（2026-10-18 修「缺模型时吃掉用户输入」）。
        # 取词成功后 _post_process 会先发 Delete 删掉原文；若把这项检查排在后面，
        # 缺模型时每按一次快捷键就会删掉一段刚打的字，然后只弹一句"模型还没下载"。
        # 历史教训：当年「字数上限 = 0（不翻译）」也是因为判断得太晚而丢数据，
        # 现在的规则是：**任何"不会真的翻译"的提前返回，都必须发生在取词之前**。
        missing = models.missing_for_target(target)
        if missing:
            names = "、".join(models.PACKAGES[k]["display"] for k in missing)
            log.info("模型未就绪（未取词、未改动原文）: %s", missing)
            self._notify("模型还没下载", f"缺少：{names}。已为你打开设置窗口，点「下载」即可。")
            self._open_settings()
            return

        # 1) 模型预热：与取词并行（2026-10-16 方案②）。
        # 必须放在取词**之前** —— 取词那 0.3~0.4 秒正好用来后台读模型，
        # 取完词翻译时模型已就绪，不用再串行等加载。
        # （模型缺失时上面已返回，不会白加载一次再抛异常。）
        self._warmup_engine_async()

        # 2) 取词
        # probe_ms：判断"用户是否已选中"的窗口（默认 150ms，见 config.py）。
        # 短窗口让常态（没选区）按键后几乎立刻开始选字。
        text, how = capture_selected_text(
            cfg.get("capture_mode", "auto"),
            int(cfg.get("max_chars", 20000)),
            probe_ms=int(cfg.get("selection_probe_ms", 150) or 150))
        if not text:
            log.info("取词失败: %s（按键后 %.3fs）", how, self._since_hotkey())
            self._notify("没有取到文字", how)
            return
        log.info("取词成功(%s): %s | 按键后 %.3fs 完成取词",
                 how, text[:60], self._since_hotkey())

        # 3) 翻译（全量翻译，不做字数截断 —— 2026-10-16 已移除字数上限功能）
        engine = self._get_engine()
        t0 = time.time()
        try:
            result = engine.translate(text, target, quality)
        except Exception:  # noqa: BLE001
            # 兜底（2026-10-18）：此时原文已被 Delete 删掉，异常一走了之就是丢字。
            # 模型文件损坏、_probe_layout 认不出布局、内存不足都会走到这里。
            log.exception("翻译失败（已尝试把原文放回原处）")
            self._notify("翻译出错", "已把原文放回原处，详情见日志文件")
            self._restore_original(text, cfg)
            return
        cost = time.time() - t0
        if not result:
            # 兜底（2026-10-14）：取词成功后原文已被 Delete 删掉，
            # 若翻译为空就一走了之，用户的输入就永久丢了 —— 必须把原文贴回去。
            self._notify("翻译结果为空", "已把原文放回原处，请重试或换个说法")
            self._restore_original(text, cfg)
            return

        # 4) 回写
        ok, way = inject_text(result, cfg.get("inject_mode", "paste"),
                              bool(cfg.get("restore_clipboard", True)))
        log.info("回写完成（方式=%s, %d 字, 成功=%s）| 按键后总共 %.3fs",
                 way, len(result), ok, self._since_hotkey())
        if not ok:
            # 回写没走通（type 模式 SendInput 被吞 / 剪贴板被占用后降级也失败）：
            # 原文已删、译文没进去。把**译文**留在剪贴板并明说，给用户一条找回路径
            # （比放原文有用：原文用户还能重敲，译文重算要再等模型）。
            self._notify("回写可能失败", f"{way}。译文已留在剪贴板，可手动 Ctrl+V 粘贴。")
            capture.set_clipboard_text(result)

        self.last_result = {
            "source": text, "target": result, "lang": target,
            "quality": quality, "seconds": round(cost, 3), "route": engine.last_stats.get("route", ""),
        }
        log.info("完成 %.2fs %s -> %s | %s | %s",
                 cost, text[:30], result[:30], way, engine.last_stats)
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
            engine.ensure_loaded(target)
            self._last_activity = time.time()      # 刚预加载完，从这一刻开始算空闲
            log.info("模型预加载完成: %s", target)
        except Exception:  # noqa: BLE001
            log.exception("模型预加载失败")

    def _warmup_engine_async(self) -> None:
        """按键后立刻在后台把目标语言模型读进内存，**与取词并行**（2026-10-16 方案②）。

        为什么值得做：取词本身要 0.3~0.4 秒（150ms 探测 + 整段选取），而模型
        加载/空闲释放后的重载要 0.15~3 秒。过去这一段是串行的（先取词 → 再加载
        → 再翻译），用户感受到的就是"字选完了还不动"。
        现在把加载丢到后台线程：等取词结束时模型通常已经就绪。
        已加载（is_ready_for）时不惊动线程，零成本。
        """
        try:
            engine = self._get_engine()
            target = self.cfg.get("target_lang", "en")
            if engine.is_ready_for(target):
                return
            threading.Thread(target=self._warmup_job, args=(target,),
                             name="shunyi-warmup", daemon=True).start()
        except Exception:  # noqa: BLE001
            log.exception("启动模型预热失败（忽略，翻译时仍会自行加载）")

    def _warmup_job(self, target: str) -> None:
        try:
            t0 = time.time()
            self._get_engine().ensure_loaded(target)
            self._last_activity = time.time()
            log.info("模型预热完成（%.3fs，与取词并行）", time.time() - t0)
        except Exception:  # noqa: BLE001
            log.exception("模型预热失败（忽略，翻译时仍会自行加载）")

    def _background_preload(self) -> None:
        time.sleep(0.5)
        self.preload()

    def _idle_watchdog(self) -> None:
        """空闲一段时间后释放模型占用的内存（可在配置里关掉）。

        实测数据：单个模型加载后约占 97~141 MB 内存，中→日两个模型合计约 238 MB；
        释放后再翻译需要重新加载，实测约多花 0.15 秒。
        默认空闲 15 分钟释放，兼顾"挂机省内存"与"随手打字不卡顿"。
        把配置里的 idle_unload_minutes 设为 0 即可让它一直常驻。
        """
        while self._running:
            time.sleep(self._idle_check_interval)
            try:
                minutes = float(config.load().get("idle_unload_minutes", 0) or 0)
                if minutes <= 0:
                    continue                      # 用户选择常驻，不做任何事
                engine = self._engine
                if engine is None or not engine.loaded_keys():
                    continue                      # 内存里本来就没有模型
                idle = time.time() - self._last_activity
                if idle >= minutes * 60:
                    self.unload_models()
                    log.info("空闲 %.0f 分钟，已释放模型占用的内存（下次翻译会自动重新加载）", idle / 60)
            except Exception:  # noqa: BLE001
                log.exception("空闲释放检查出错（已忽略）")

    def _since_hotkey(self) -> float:
        """距最近一次按下快捷键过了多少秒（非热键触发时返回 0，避免日志出现天文数字）。"""
        return (time.time() - self._hotkey_at) if self._hotkey_at else 0.0

    def _restore_original(self, text: str, cfg: dict) -> None:
        """把已被取词删掉的原文贴回原位置。

        为什么要有它（2026-10-18）：「取词成功」意味着原文已经被 Delete 删掉了，
        此后**任何**不产出译文的退出路径（翻译结果为空、翻译抛异常、回写失败……）
        都必须把原文还回去，否则就是纯粹的数据丢失。
        统一走这里，避免以后新增一条失败分支又忘了还。
        """
        ok, way = inject_text(text, cfg.get("inject_mode", "paste"),
                              bool(cfg.get("restore_clipboard", True)))
        log.info("已回注原文（%d 字，方式=%s，成功=%s）", len(text), way, ok)
        if not ok:
            # 连原文都贴不回去：至少留在剪贴板里，用户还能手动 Ctrl+V。
            capture.set_clipboard_text(text)
            self._notify("原文已放入剪贴板", "自动放回失败，请手动 Ctrl+V 粘贴。")

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
