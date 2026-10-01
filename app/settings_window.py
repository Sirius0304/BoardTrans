# -*- coding: utf-8 -*-
"""
瞬译 · 设置窗口（tkinter / ttk）

设计要点
--------
1. **线程模型**：主线程继续跑 pystray 的托盘消息循环（完全不动），
   设置窗口独占一个**常驻子线程**（tkinter 的 Tk 实例只能由创建它的线程使用）。
   托盘回调绝不直接碰 Tk 对象，只往线程安全队列里放一个 "show"，
   窗口线程用 root.after 轮询队列后自行 deiconify —— 这样就不会出现跨线程操作 GUI 的崩溃。
2. **窗口常驻不销毁**：首次创建后 withdraw() 隐藏，点「设置」只是把已有窗口显示出来，
   天然实现"已打开则置前、不重复创建"，且打开是秒开。
3. **状态一致**：窗口与托盘共用同一份 config.json；
   窗口未被编辑时会每 480 ms 自动同步配置（托盘改了档位/语言，窗口立刻跟着变）。
4. **界面语言**：全中文，字号适中，适配 Windows 高 DPI。
"""

from __future__ import annotations

import ctypes
import os
import queue
import shutil
import threading
import time

from . import config, icon, models, paths
from .logger import get_logger

log = get_logger()

# tkinter 是标准库，但部分精简版 Python 不带它。
# 这里做兜底：导入失败时程序照常运行，只是点「设置」时给出友好提示而不是崩溃。
# ui_kit（自绘圆角卡片/按钮/开关的视觉套件）同样依赖 tkinter，一起放在兜底里。
try:
    import tkinter as tk
    from tkinter import ttk
    from . import ui_kit as ui
    TK_AVAILABLE = True
    TK_IMPORT_ERROR = ""
except Exception as _exc:  # noqa: BLE001
    tk = None
    ttk = None
    ui = None
    TK_AVAILABLE = False
    TK_IMPORT_ERROR = str(_exc)

# 统一风格的对话框（替代系统 messagebox，见 app/dialogs.py）。
# 注意：本程序**不再使用 tkinter.messagebox** —— 原生弹窗是系统样式，与设置界面
# 的自绘视觉不一致（2026-09-30 统一）。
try:
    from . import dialogs
except Exception:  # noqa: BLE001
    dialogs = None

# ----------------------------------------------------------------- 外观常量

# 界面统一字体。
# 实测（2026-09-29，用像素统计对比同一句话的渲染，非主观判断）：
#   黑体 12/13/16px → 中间灰阶像素占 5.3%/5.3%/5.6%（笔画外圈一圈灰 = 看着发虚）
#   微软雅黑 12/13/16px → 0.1%/0.2%/0.1%（纯 ClearType 亚像素，无灰晕 = 锐利）
# 原因：黑体（SimHei）带内嵌点阵、在 Tk/GDI 下走灰度抗锯齿；雅黑是 Windows 的
# ClearType 调优 UI 字体。所以**界面统一字体改用雅黑**（仍然"全界面同一字体"，
# 一致性要求不变）；黑体与 SimHei 退到候选末位，仅当系统没有雅黑时才使用。
FONT_FAMILY = "Microsoft YaHei UI"
FONT_FALLBACK = "Microsoft YaHei"
FONT_FALLBACK_CN = "微软雅黑"
FONT_LAST_RESORT = "黑体"
FONT_LAST_RESORT2 = "SimHei"

# ttk 样式里引用字体时**只能写命名字体名本身**（如 "ShunYiBase"）。
# 坑（2026-09-29 实测）：写成元组 ("ShunYiBase", 10) 后 Tcl 端会变成字符串
# "ShunYiBase 10"，Tk 把它当"字体描述"解析 —— 族名取 "ShunYiBase"（系统里并不存在
# 这个字体族）→ 静默回退成宋体，于是按钮/单选/复选全长成宋体，而显式写
# font="ShunYiBold" 的「保存」按钮却是黑体。用命名字体名即可正常解析。
UI_FONT = "ShunYiBase"
UI_FONT_BOLD = "ShunYiBold"

# ----------------------------------------------------------------- 外观常量
# 统一走 ui_kit 的设计令牌（参考图配色：近白页面 + 白卡片 + 浅灰描边 + 蓝色主色）。
# 这套常量只在 tkinter 可用时才会被用到；tkinter 缺失时窗口根本不会创建，
# 所以这里给 ui 加一层兜底字面量即可，避免"字体/颜色常量缺失"引发二次报错。
COLOR_BG = ui.COLOR_PAGE if ui else "#F7F8FA"
COLOR_CARD = ui.COLOR_CARD if ui else "#FFFFFF"
COLOR_BORDER = ui.COLOR_BORDER if ui else "#E5E7EB"
COLOR_TEXT = ui.COLOR_TEXT if ui else "#1F2937"
COLOR_SUBTEXT = ui.COLOR_SUBTEXT if ui else "#6B7280"
COLOR_PRIMARY = ui.COLOR_PRIMARY if ui else "#3B82F6"
COLOR_WARN_BG = ui.COLOR_WARN_BG if ui else "#FFF7ED"
COLOR_WARN_FG = ui.COLOR_WARN_FG if ui else "#C2410C"
COLOR_OK = ui.COLOR_OK if ui else "#16A34A"
COLOR_ERR = ui.COLOR_ERR if ui else "#DC2626"
# 窗口内轻量提醒条（保存结果提示）的底色，配合上面的前景色使用
COLOR_OK_BG = ui.COLOR_OK_SOFT if ui else "#ECFDF5"
COLOR_INFO_BG = ui.COLOR_PRIMARY_SOFT if ui else "#EFF6FF"
COLOR_ERR_BG = ui.COLOR_ERR_SOFT if ui else "#FEF2F2"

# 界面上的模型卡片定义（key 对应 app/models.py 的注册表）
MODEL_CARDS = [
    {
        "key": "zh_en",
        "title": "中译英",
        "direction": "中文 → 英文",
        "size_text": "约 71 MB",
        "note": "基础模型：翻译成英文必需，翻译成日文也需要它",
    },
    {
        "key": "en_ja",
        "title": "中译日",
        "direction": "中文 → 日文",
        "size_text": "约 115 MB",
        "note": "日文方向走「中文→英文→日文」两步翻译，因此需要中译英模型一起就位",
    },
]

QUALITY_INFO = [
    ("fast", "高速度", "响应快，适合日常聊天、随手翻译"),
    ("quality", "高质量", "更准确，适合邮件、文档等正式内容"),
]

LANG_INFO = [
    ("en", "英文", "中文 → 英文（只需 1 个模型，约 71 MB）"),
    ("ja", "日文", "中文 → 日文（经英文中转，需要 2 个模型，约 186 MB）"),
]

DEFAULT_HOTKEY = "alt+j"

# tkinter 事件里的修饰键位掩码
MASK_SHIFT = 0x0001
MASK_CTRL = 0x0004
MASK_ALT = 0x0008
MASK_ALT_ALT = 0x20000     # 部分环境把 Alt 标在这一位

# 不能作为主键的按键（避免误触）
FORBIDDEN_KEYS = {
    "control_l", "control_r", "shift_l", "shift_r", "alt_l", "alt_r",
    "super_l", "super_r", "win_l", "win_r",
    "escape", "tab", "caps_lock", "num_lock",
}


def enable_high_dpi() -> None:
    """开启进程 DPI 感知，否则在高分屏上整个窗口会被系统拉伸导致发虚。"""
    try:
        # 1 = SYSTEM_DPI_AWARE（对普通窗口已足够，且不会和托盘冲突）
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def format_hotkey(raw: str) -> str:
    """把配置里的 alt+j 显示成 Alt+J 这种看得懂的形式。"""
    if not raw:
        return ""
    parts = [p.strip() for p in str(raw).split("+") if p.strip()]
    name_map = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift", "windows": "Win", "win": "Win"}
    out = []
    for part in parts:
        low = part.lower()
        if low in name_map:
            out.append(name_map[low])
        elif len(part) == 1:
            out.append(part.upper())
        else:
            out.append(part.upper() if low.startswith("f") and low[1:].isdigit() else part.capitalize())
    return "+".join(out)


def parse_tk_event(event) -> str | None:
    """把 tkinter 键盘事件解析成 keyboard 库认识的组合键字符串；不合法返回 None。"""
    keysym = (event.keysym or "").lower()
    if keysym in FORBIDDEN_KEYS:
        return None
    # 主键名映射
    key_name = keysym
    special = {
        "space": "space", "return": "enter", "kp_enter": "enter",
        "backspace": "backspace", "delete": "delete",
        "up": "up", "down": "down", "left": "left", "right": "right",
        "home": "home", "end": "end", "prior": "page up", "next": "page down",
        "insert": "insert", "minus": "-", "equal": "=", "comma": ",",
        "period": ".", "slash": "/", "semicolon": ";", "apostrophe": "'",
        "bracketleft": "[", "bracketright": "]", "backslash": "\\", "grave": "`",
    }
    key_name = special.get(keysym, key_name)
    if len(key_name) > 1 and not (key_name.startswith("f") and key_name[1:].isdigit()) and key_name not in special.values():
        # 非字母数字、且不在映射表里的按键（比如 Num_Lock 之类）直接忽略
        if key_name not in ("space", "enter", "backspace", "delete", "insert",
                            "home", "end", "up", "down", "left", "right",
                            "page up", "page down", "-", "=", ",", ".", "/",
                            ";", "'", "[", "]", "\\", "`"):
            return None

    mods = []
    state = int(event.state or 0)
    if state & MASK_CTRL:
        mods.append("ctrl")
    if (state & MASK_ALT) or (state & MASK_ALT_ALT):
        mods.append("alt")
    if state & MASK_SHIFT:
        mods.append("shift")

    if not mods:
        return None          # 纯单键：拒绝
    return "+".join(mods + [key_name])


# ----------------------------------------------------------------- 主类


class SettingsWindow:
    """设置窗口控制器：对外只暴露 open() / close()，内部自管子线程与界面。"""

    def __init__(self, service):
        self.service = service
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        # 保护"启动窗口线程"这一步：预热与用户点「设置」可能同时发生，
        # 不加锁会各起一个窗口线程（两个 Tk 解释器 / 两个窗口）
        self._thread_lock = threading.Lock()
        self._ready = threading.Event()

        # ---- 界面状态 ----
        self.root: tk.Tk | None = None
        self._dirty = False                     # 有未保存修改
        self._syncing = False                   # 正在程序化刷新控件（避免误判为用户修改）
        self._recording = False                 # 正在录制快捷键
        self._pending_hotkey = DEFAULT_HOTKEY   # 界面上的快捷键值
        self._pending_quality = "fast"
        self._model_ui: dict[str, dict] = {}
        self._title_labels: list = []       # 卡片标题标签（清晰度自检用：深色文字）
        self._download_threads: dict[str, threading.Thread] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        # 正在下载的模型 key 集合（含"作为依赖被带上下载"的），用于防止重复发起
        self._active_downloads: set[str] = set()
        # 每个模型最近一次进度采样 {t, done, text}，用来算瞬时速度与剩余时间
        self._dl_samples: dict[str, dict] = {}
        self._model_status: dict[str, dict] = {}
        self._last_config_snapshot: tuple | None = None

    # ---------------------------------------------------------- 对外接口

    def open(self) -> None:
        """线程安全：请求打开设置窗口（已存在则置前）。可从托盘回调直接调用。"""
        if not TK_AVAILABLE:
            self._warn_no_tkinter()
            return
        self._ensure_thread()
        self._queue.put(("show", None))

    def prewarm(self, delay: float = 0.6) -> None:
        """**提前**把界面建好（窗口保持隐藏），让首次点「设置」时瞬间弹出。

        为什么需要（2026-10-01 报障"首次打开设置界面明显卡顿"）：界面原本是**懒加载**的
        —— 第一次点「设置」时才现启动窗口线程、创建 Tk、构建整个界面（含 31 张文字超采样
        图与 20 多个自绘组件底图），这些开销全落在用户的等待期里。实测：首次 open()
        到界面就绪 **270ms**，而第二次打开只要 **70ms**（只等显示）。程序启动后在后台先
        建好，首次打开的感受就与第二次一致。

        delay：启动路径上还有热键注册、模型检查等更紧要的事，先让它们跑完再预热，
        避免抢 CPU；预热本身在独立线程里，不会拖慢托盘出现。
        """
        if not TK_AVAILABLE:
            return
        if self._thread is not None and self._thread.is_alive():
            return

        def _delayed() -> None:
            try:
                if delay > 0:
                    time.sleep(delay)
                self._ensure_thread()
            except Exception:  # noqa: BLE001
                log.exception("设置窗口预热失败")

        threading.Thread(target=_delayed, name="shunyi-prewarm", daemon=True).start()

    @staticmethod
    def _warn_no_tkinter() -> None:
        """当前 Python 没带 tkinter 时，给一个看得懂的弹窗而不是报错。"""
        text = ("设置界面需要用到 Python 自带的 tkinter 图形库，但这个 Python 环境里没有。\n\n"
                "常见原因：装的是精简版 Python（例如某些绿色版/嵌入式版）。\n\n"
                "解决办法（二选一）：\n"
                "1）到 python.org 下载官方安装包，重新安装勾选 tcl/tk 组件，再执行一次\n"
                "   .venv\\Scripts\\python.exe -m pip install -r requirements.txt\n"
                "2）暂时不改程序，直接编辑配置文件：\n"
                f"   {paths.config_path()}\n\n"
                "翻译功能不受影响，仍然可以正常使用。")
        log.error("tkinter 不可用，无法打开设置窗口: %s", TK_IMPORT_ERROR)
        try:
            ctypes.windll.user32.MessageBoxW(None, text, "瞬译 · 无法打开设置", 0x00000030)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        """线程安全：请求隐藏窗口（软件继续在托盘运行）。"""
        self._queue.put(("hide", None))

    def shutdown(self) -> None:
        """退出软件时调用：让窗口线程安全退出。"""
        self._queue.put(("quit", None))

    # ---------------------------------------------------------- 线程启动

    def _ensure_thread(self) -> None:
        """确保窗口线程已启动（并发安全：预热与 open 可能同时进来）。

        等待上限 3 秒（原 8 秒）：真出问题时也不该把调用方（托盘线程/测试）按住 8 秒
        不放 —— 那本身就是"界面卡死"的症状。超时只记日志，不影响后续调用。
        """
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._thread = threading.Thread(target=self._run, name="shunyi-settings",
                                            daemon=True)
            self._thread.start()
        # 等待放在锁外：否则预热线程持锁干等，用户点「设置」时会被挡住
        if not self._ready.wait(timeout=3):
            log.warning("设置窗口线程启动超过 3 秒仍未就绪（后面的调用会排进队列等待）")

    def _run(self) -> None:
        """窗口线程主函数：建界面 -> 常驻 mainloop。"""
        try:
            # 必须在创建任何窗口之前声明 DPI 感知（否则 Windows 位图拉伸 → 全糊）
            ui.enable_dpi_awareness()
            self.root = tk.Tk()
            # 立刻隐藏：Tk 根窗口创建后默认就是可见的，而下面还要建几十个控件、
            # 渲染二十多张自绘/文字图 —— 不先隐藏的话，屏幕上会先闪一个空白/半成品
            # 窗口出来（预热时更是全程可见）。真正的显示交给 _show() 的 deiconify。
            self.root.withdraw()
            self.root.title("瞬译 · 设置")
            self.root.configure(bg=COLOR_BG)
            self._apply_theme()
            self._build_ui()
            self._load_from_config(force=True)
            self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)
            self._refresh_models()
            self._ready.set()
            self._pump_mark_owner()          # 打上归属线程标记（供跨线程误用检测）
            self._pump()
            self.root.mainloop()
        except Exception:  # noqa: BLE001
            log.exception("设置窗口线程异常退出")
        finally:
            self._ready.set()

    def _apply_theme(self) -> None:
        """字体、DPI 缩放与 ttk 主题。"""
        root = self.root
        # 先声明 DPI 感知：否则 Windows 会把窗口位图整体拉伸，文字与自绘控件都会发虚。
        # 正常情况下启动脚本已经调用过；这里是兜底（幂等，第二次调用直接返回）。
        awareness = ui.enable_dpi_awareness()
        try:
            dpi = root.winfo_fpixels("1i") or 96.0
        except Exception:  # noqa: BLE001
            dpi = 96.0
        dpi = max(96.0, dpi)
        # 按实际 DPI 设置 tk 缩放；同时把像素型设计值（圆角/间距/控件高度）一并放大
        k = ui.set_scale(dpi / 96.0)
        try:
            root.tk.call("tk", "scaling", max(1.0, dpi / 72.0))
        except Exception:  # noqa: BLE001
            pass

        families = set()
        try:
            families = set(root.tk.call("font", "families"))
        except Exception:  # noqa: BLE001
            pass
        # 字体候选：雅黑的三个族名 → 黑体（中文族名） → SimHei（英文族名）
        family = None
        for candidate in (FONT_FAMILY, FONT_FALLBACK, FONT_FALLBACK_CN,
                          FONT_LAST_RESORT, FONT_LAST_RESORT2):
            if candidate in families:
                family = candidate
                break
        if family is None:
            family = "TkDefaultFont"
        elif family != FONT_FAMILY:
            log.warning("界面字体「%s」在本机不可用，已回退为「%s」", FONT_FAMILY, family)

        # 字号用**整数像素**（Tk 里负数表示像素）：磅值在 DPI 缩放下会算成 13.34px 这类
        # 小数，字形落不到整像素上会显得发虚；直接给像素值最锐利，也方便按 DPI 等比放大。
        for name, pt, weight in (
            ("ShunYiBase", 10, "normal"),
            ("ShunYiBold", 10, "bold"),
            ("ShunYiTitle", 12, "bold"),
            ("ShunYiSmall", 9, "normal"),
            ("ShunYiMono", 11, "normal"),
        ):
            pixels = -max(9, int(round(pt * dpi / 72.0)))
            try:
                # 先删同名旧字体再建：font create 对已存在的名字会报错并**保留旧字号**，
                # 那样在多窗口 / DPI 变化时就会用错字号（2026-09-29 实测）
                root.tk.call("font", "delete", name)
            except Exception:  # noqa: BLE001
                pass
            try:
                root.tk.call("font", "create", name, "-family", family,
                             "-size", pixels, "-weight", weight)
            except Exception:  # noqa: BLE001
                pass
            # 登记真实像素字号：ui.TextLabel 要按它去渲染文字图片。
            # 为什么不从 Tk 反推：`font actual` 返回的是四舍五入后的整数磅值，
            # 在非 100% DPI 下反算会差 1px，字号差 1px 就又发虚了。
            ui.register_font(name, family, abs(pixels), weight)

        # 统一 Tk 内置命名字体：这样"没有显式指定 font"的控件（含 tk.Entry 右键菜单、
        # 内建对话框的文本、下拉列表等）也会跟着变成黑体 + 整数像素
        for named in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                      "TkTooltipFont", "TkCaptionFont", "TkSmallCaptionFont",
                      "TkIconFont", "TkFixedFont"):
            try:
                root.tk.call("font", "configure", named, "-family", family,
                             "-size", -max(9, int(round(9 * dpi / 72.0))))
            except Exception:  # noqa: BLE001
                pass
        log.info("DPI 感知: %s；DPI %.0f（缩放 %.2f×）；字体: %s，字号按整数像素",
                 awareness, dpi, k, family)

        style = ttk.Style(root)
        # 主题选择（2026-09-29 实测结论，勿改回 vista）：
        # Windows 的 vista / xpnative 是"原生控件绘制"，**会直接忽略 ttk 的 -font 设置**，
        # 于是按钮/单选/复选/下拉全都还是系统的宋体 —— 与"界面字体统一"的目标冲突。
        # clam 是 Tk 自己的绘制，字体完全可控，因此改用 clam，并按下面的配色补回观感。
        theme_used = ""
        for candidate in ("clam", "default"):
            try:
                style.theme_use(candidate)
                theme_used = candidate
                break
            except tk.TclError:
                continue
        log.info("设置窗口 ttk 主题: %s（字体: %s）", theme_used or "默认", family)
        log.info("界面渲染模式: %s", ui.AA_MODE)

        # 先给所有 ttk 控件兜底字体；按钮/开关/分段选择器已改用 ui_kit 自绘控件，
        # 这里保留的 ttk 样式主要服务：输入框、进度条、滚动条、下拉框（防将来新增）。
        style.configure(".", font=UI_FONT, background=COLOR_BG, foreground=COLOR_TEXT)

        # 次要按钮（白底 + 浅灰描边，悬停/按下/禁用三态）
        style.configure("TButton", font=UI_FONT, padding=(10, 5),
                        background=COLOR_CARD, foreground=COLOR_TEXT,
                        bordercolor=ui.COLOR_BORDER_STRONG, lightcolor=COLOR_CARD,
                        darkcolor=COLOR_CARD, focuscolor=ui.COLOR_BORDER)
        style.map("TButton",
                  background=[("pressed", "#E8EAEE"), ("active", ui.COLOR_HOVER),
                              ("disabled", ui.COLOR_HOVER)],
                  foreground=[("disabled", ui.COLOR_MUTED)])

        # 主按钮（蓝底白字；保留给可能用 ttk 的地方，自绘版见 ui_kit.RoundedButton）
        style.configure("Big.TButton", font=UI_FONT_BOLD, padding=(14, 6),
                        background=COLOR_PRIMARY, foreground="#FFFFFF",
                        bordercolor=COLOR_PRIMARY, lightcolor=COLOR_PRIMARY,
                        darkcolor=COLOR_PRIMARY)
        style.map("Big.TButton",
                  background=[("pressed", ui.COLOR_PRIMARY_PRESSED),
                              ("active", ui.COLOR_PRIMARY_HOVER),
                              ("disabled", "#A9C4F2")],
                  foreground=[("disabled", "#F2F6FF")])

        # 输入框 / 下拉框（白底 + 浅灰描边，聚焦主色描边）
        for cls in ("TEntry", "TCombobox"):
            style.configure(cls, font=UI_FONT, fieldbackground=COLOR_CARD,
                            background=COLOR_CARD, foreground=COLOR_TEXT,
                            bordercolor=ui.COLOR_BORDER_STRONG, lightcolor=COLOR_CARD,
                            darkcolor=COLOR_CARD, arrowcolor=COLOR_SUBTEXT, padding=4)
        style.map("TCombobox",
                  fieldbackground=[("readonly", COLOR_CARD)],
                  bordercolor=[("focus", COLOR_PRIMARY)],
                  foreground=[("disabled", ui.COLOR_MUTED)])

        # 进度条：细、主色（参考图里的细进度条）
        style.configure("Thin.Horizontal.TProgressbar", thickness=6,
                        background=COLOR_PRIMARY, troughcolor="#EDF0F4",
                        bordercolor="#EDF0F4", lightcolor=COLOR_PRIMARY,
                        darkcolor=COLOR_PRIMARY)

        # 滚动条：细、无箭头（窗口较小时内容区可滚动，见 _build_scroll_area）
        style.configure("Thin.Vertical.TScrollbar", width=8, arrowsize=0,
                        background="#D6DAE0", troughcolor=COLOR_BG,
                        bordercolor=COLOR_BG, lightcolor="#D6DAE0", darkcolor="#D6DAE0",
                        relief="flat")
        style.map("Thin.Vertical.TScrollbar",
                  background=[("active", "#BFC5CE"), ("pressed", "#AAB1BC")])

        # 兼容历史样式名（卡片标题/说明在旧代码里可能引用到）
        style.configure("Card.TFrame", background=COLOR_CARD, relief="flat")
        style.configure("CardTitle.TLabel", background=COLOR_CARD, foreground=COLOR_TEXT, font="ShunYiTitle")
        style.configure("CardText.TLabel", background=COLOR_CARD, foreground=COLOR_TEXT)
        style.configure("CardSub.TLabel", background=COLOR_CARD, foreground=COLOR_SUBTEXT, font="ShunYiSmall")
        style.configure("CardOk.TLabel", background=COLOR_CARD, foreground=COLOR_OK, font="ShunYiBold")
        style.configure("CardErr.TLabel", background=COLOR_CARD, foreground=COLOR_ERR, font="ShunYiBold")
        style.configure("Hint.TLabel", background=COLOR_BG, foreground=COLOR_SUBTEXT, font="ShunYiSmall")
        style.configure("Big.TButton", font="ShunYiBold", padding=(16, 6))
        style.configure("TButton", padding=(10, 5))

        # 窗口图标（用 Pillow 画的那个，不依赖外部文件）
        try:
            from PIL import ImageTk
            self._icon_image = ImageTk.PhotoImage(icon.make_icon(64, True))
            root.iconphoto(True, self._icon_image)
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------- 界面构建

    def _build_ui(self) -> None:
        root = self.root
        root.configure(bg=COLOR_BG)

        # 窗口尺寸：按屏幕大小自适应并整体调小（紧凑），并居中显示。
        # 尺寸按 DPI 缩放系数放大，保证高分屏上物理观感一致；下限保证控件可读可用。
        try:
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            dpi = root.winfo_fpixels("1i") or 96.0
            k = max(1.0, dpi / 96.0)
            w = int(min(max(sw * 0.30, 470 * k), 540 * k))
            h = int(min(max(sh * 0.68, 480 * k), 720 * k))
            x = int(max(0, (sw - w) / 2))
            y = int(max(0, (sh - h) / 2 - 30 * k))
            root.geometry(f"{w}x{h}+{x}+{y}")
            root.minsize(int(430 * k), int(380 * k))     # 允许缩到更小，内容区自动出现滚动条
        except Exception:  # noqa: BLE001
            root.minsize(430, 380)

        outer = tk.Frame(root, bg=COLOR_BG)
        outer.pack(fill="both", expand=True, padx=0, pady=0)

        # 顶部标题（紧凑：标题 + 一行说明）
        header = tk.Frame(outer, bg=COLOR_BG)
        header.pack(fill="x", padx=14, pady=(12, 10))
        ui.TextLabel(header, text="瞬译 设置", bg=COLOR_BG, fg=COLOR_TEXT,
                 font=UI_FONT_BOLD).pack(anchor="w")
        ui.TextLabel(header, text="管理翻译模型、目标语言、档位与快捷键；点「保存」后立即生效。",
                 bg=COLOR_BG, fg=COLOR_SUBTEXT, font="ShunYiSmall").pack(anchor="w", pady=(2, 0))

        # 未保存提示条（常驻型，贴在窗口底部、按钮行上方；见 _update_dirty_bar）
        self._dirty_panel = ui.RoundedPanel(outer, tone="warn", radius=8, pad=4, page_bg=COLOR_BG)
        self._dirty_bar = self._dirty_panel            # 兼容既有引用/测试
        self._dirty_label = ui.TextLabel(self._dirty_panel.inner, text="", bg=COLOR_WARN_BG,
                                     fg=COLOR_WARN_FG, font="ShunYiSmall", anchor="w",
                                     justify="left", wraplength=ui.S(470))
        self._dirty_label.pack(fill="x", padx=8, pady=4)

        # 保存结果轻量提醒：窗口**底部**弹出的一个小框（用 place 浮在内容之上，
        # 不参与 pack 布局，所以弹出/消失都不会让上面的内容跳动）。
        # 位置与配色见 _show_notice；默认不显示。
        self._notice_box = ui.RoundedPanel(outer, tone="ok", radius=8, pad=4, page_bg=COLOR_BG,
                                           shadow=True, fit_width=True)
        self._notice_label = ui.TextLabel(self._notice_box.inner, text="", bg=COLOR_OK_BG,
                                      fg=ui.COLOR_OK_TEXT, font="ShunYiSmall", anchor="w",
                                      justify="left", wraplength=ui.S(430))
        self._notice_label.pack(fill="x", padx=8, pady=5)
        self._notice_timer = None      # after() 的句柄，用于"新提醒顶掉旧倒计时"
        self._notice_seq = 0           # 提醒序号：防止旧定时器把新提醒提前关掉

        # 内容区（可滚动；窗口小/屏幕小都能看全）
        content = self._build_scroll_area(outer)
        self._content = content

        # 各区块
        self._build_models_section(content)
        self._build_language_section(content)
        self._build_quality_section(content)
        self._build_memory_section(content)
        self._build_hotkey_section(content)
        tk.Frame(content, bg=COLOR_BG, height=4).pack(fill="x")
        self._bind_wheel_recursive()      # 内容控件建完后统一绑滚轮（见该方法注释）

        # 底部固定区（不随内容滚动）：按钮行 + 未保存提示条 + 保存结果小框
        footer = tk.Frame(outer, bg=COLOR_BG)
        footer.pack(fill="x", side="bottom", padx=14, pady=(8, 12))
        ui.RoundedButton(footer, text="恢复默认设置", kind="secondary", height=30,
                         page_bg=COLOR_BG, command=self._on_restore_defaults).pack(side="left")
        self._save_btn = ui.RoundedButton(footer, text="保存", kind="primary", height=32,
                                          padx=22, page_bg=COLOR_BG, font=UI_FONT_BOLD,
                                          command=self._on_save)
        self._save_btn.pack(side="right")
        ui.RoundedButton(footer, text="取消", kind="secondary", height=32,
                         page_bg=COLOR_BG, command=self._on_cancel).pack(side="right", padx=(0, 8))
        self._footer = footer          # 提醒小框要贴在它上方

    def _build_scroll_area(self, parent) -> tk.Frame:
        """内容区（可滚动）：窗口较小或屏幕较小时仍能看全所有设置。

        返回内容容器（各卡片 pack 到这里）。滚动条只在需要时出现。
        """
        wrap = tk.Frame(parent, bg=COLOR_BG)
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(wrap, bg=COLOR_BG, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview,
                            style="Thin.Vertical.TScrollbar")
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        content = tk.Frame(canvas, bg=COLOR_BG)
        win = canvas.create_window(0, 0, window=content, anchor="nw")

        def _on_content_configure(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfigure(win, width=event.width)
            need = content.winfo_reqheight() > event.height
            if need and not bar.winfo_manager():
                bar.pack(side="right", fill="y")
            elif not need and bar.winfo_manager():
                bar.pack_forget()

        def _on_wheel(event):
            if content.winfo_reqheight() > canvas.winfo_height():
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

        content.bind("<Configure>", _on_content_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        # 滚轮：**逐控件递归绑定**，不再用 bind_all/unbind_all。
        # 为什么改（2026-09-29）：bind_all 是**进程级**绑定，会盖住同进程里其它窗口/
        # 控件的滚轮行为；还有"进入绑上、离开解绑"的时序漏洞（指针未离开就关窗 →
        # 全局绑定残留）。递归绑定作用域清晰、无残留。内容区控件是后建的，
        # 所以 _build_ui 结束后再调一次 _bind_wheel_recursive()。
        self._wheel_handler = _on_wheel
        canvas.bind("<MouseWheel>", _on_wheel)
        self._scroll_canvas = canvas
        self._scroll_bar = bar
        return content

    def _bind_wheel_recursive(self, widget=None) -> None:
        """给内容区里的所有控件绑定滚轮（作用域清晰，不用全局绑定）。"""
        handler = getattr(self, "_wheel_handler", None)
        if handler is None:
            return
        widget = widget or getattr(self, "_content", None)
        if widget is None:
            return
        try:
            widget.bind("<MouseWheel>", handler)
        except Exception:  # noqa: BLE001
            pass
        for child in widget.winfo_children():
            self._bind_wheel_recursive(child)

    def _card(self, parent, title: str, icon: str = "") -> tk.Frame:
        """圆角卡片（自绘）：返回内容容器，调用方照常 pack 控件。"""
        card = ui.RoundedCard(parent, page_bg=COLOR_BG, shadow=True)
        card.pack(fill="x", padx=14, pady=(0, ui.GAP_SECTION))
        head = tk.Frame(card.body, bg=COLOR_CARD)
        head.pack(fill="x", pady=(0, 8))
        if icon:
            ui.IconBadge(head, icon, size=24, page_bg=COLOR_CARD).pack(side="left", padx=(0, 8))
        title_label = ui.TextLabel(head, text=title, bg=COLOR_CARD, fg=COLOR_TEXT,
                               font=UI_FONT_BOLD)
        title_label.pack(side="left")
        # 记下标题标签：清晰度自检要拿"深色文字"测灰晕（灰色说明文字的抗锯齿
        # 天然落在中间灰阶，会污染度量）
        self._title_labels.append(title_label)
        return card.body

    # -- 小组件 ------------------------------------------------------------

    def _note_box(self, parent, text: str, tone: str = "info", font: str = "ShunYiSmall",
                  wraplength: int = 470) -> ui.RoundedPanel:
        """圆角提示框（对应参考图里的浅蓝说明框）。"""
        box = ui.RoundedPanel(parent, tone=tone, radius=8, pad=4, page_bg=COLOR_CARD)
        fill, _border, fg = ui.TONES[tone]
        ui.TextLabel(box.inner, text=text, bg=fill, fg=fg, font=font, anchor="w", justify="left",
                 wraplength=ui.S(wraplength)).pack(fill="x", padx=ui.S(8), pady=ui.S(5))
        box.pack(fill="x", pady=(0, 8))
        return box

    def _status_chip(self, parent, text: str = "") -> ui.RoundedPanel:
        """模型状态胶囊：对外保留 label 的 cget/configure（业务与回归测试都用 text/fg）。"""
        chip = ui.RoundedPanel(parent, tone="plain", radius=999, pad=2, page_bg=COLOR_CARD,
                               fit_width=True)
        chip.label = ui.TextLabel(chip.inner, text=text, bg=ui.COLOR_HOVER, fg=COLOR_SUBTEXT,
                              font="ShunYiSmall", anchor="w")
        chip.label.pack(padx=8, pady=1)

        def _configure(cnf=None, **kw):
            fg = kw.pop("fg", None)
            text_val = kw.pop("text", None)
            if text_val is not None:
                chip.label.configure(text=text_val)
            if fg is not None:
                chip.label.configure(fg=fg)
                tone = "err" if fg in (COLOR_ERR, ui.COLOR_ERR_TEXT) else (
                    "warn" if fg in (COLOR_WARN_FG,) else (
                        "ok" if fg in (COLOR_OK, ui.COLOR_OK_TEXT) else (
                            "info" if fg in (COLOR_PRIMARY, ui.COLOR_PRIMARY_TEXT) else "plain")))
                fill, border, _tf = ui.TONES[tone]
                chip.label.configure(bg=fill)
                chip.set_tone(fill, border)
            if kw or cnf:
                ui.RoundedPanel.configure(chip, cnf, **kw)

        def _cget(key):
            if key == "text":
                return chip.label.cget("text")
            if key == "fg":
                return chip.label.cget("fg")
            return ui.RoundedPanel.cget(chip, key)

        chip.configure = _configure
        chip.config = _configure
        chip.cget = _cget
        return chip

    def _action_button(self, parent, text: str = "下载") -> ui.RoundedButton:
        """模型卡片右侧的动作按钮：按文字自动切换主/危险/次要配色。"""
        btn = ui.RoundedButton(parent, text=text, kind=self._kind_for_action(text), height=30,
                               padx=16, page_bg=COLOR_CARD)

        def _configure(cnf=None, **kw):
            new_text = kw.pop("text", None)
            if new_text is not None:
                btn._text = new_text
                btn._kind = self._kind_for_action(new_text)
                btn._draw()
            if kw or cnf:
                ui.RoundedButton.configure(btn, cnf, **kw)

        btn.configure = _configure
        btn.config = _configure
        return btn

    @staticmethod
    def _kind_for_action(text: str) -> str:
        if text.startswith("删除") or text.startswith("重试删除"):
            return "danger"
        if text in ("取消", "已取消"):
            return "secondary"
        return "primary"

    def _header_row(self, parent, title: str, badge: str, tone: str = "info"):
        """小节标题行：小徽标 + 标题文字（模型/语言等列表项用）。"""
        row = tk.Frame(parent, bg=COLOR_CARD)
        row.pack(fill="x")
        ui.IconBadge(row, badge, size=20, tint=ui.TONES[tone][0]).pack(side="left", padx=(0, 6))
        ui.TextLabel(row, text=title, bg=COLOR_CARD, fg=ui.TONES[tone][2],
                 font="ShunYiSmall").pack(side="left")
        return row

    def _build_models_section(self, parent) -> None:
        card = self._card(parent, "翻译模型", icon="models")
        self._note_box(card, "下载模型是本软件唯一需要联网的操作，翻译过程完全离线。", "info")

        # 显示模型实际存放位置：默认在「软件所在目录\\models」，
        # 若该路径含中文或不可写则自动回退到数据目录（sentencepiece 读不了非 ASCII 路径）
        root_dir = paths.models_root()
        is_program_dir = os.path.abspath(root_dir) == os.path.abspath(paths.default_models_root())
        where = f"模型存放位置：{root_dir}"
        if not is_program_dir:
            where += "（软件所在目录含中文或不可写，已自动回退到这里；把软件放到纯英文路径即可改用软件目录）"
        ui.TextLabel(card, text=where, bg=COLOR_CARD, fg=COLOR_SUBTEXT, font="ShunYiSmall",
                 anchor="w", justify="left", wraplength=ui.S(440)).pack(fill="x", pady=(0, 8))

        for info in MODEL_CARDS:
            key = info["key"]
            row = tk.Frame(card, bg=COLOR_CARD)
            row.pack(fill="x", pady=(0, 4))

            left = tk.Frame(row, bg=COLOR_CARD)
            left.pack(side="left", fill="x", expand=True)
            title_line = tk.Frame(left, bg=COLOR_CARD)
            title_line.pack(fill="x")
            ui.TextLabel(title_line, text=info["title"], bg=COLOR_CARD, fg=COLOR_TEXT,
                     font=UI_FONT_BOLD).pack(side="left")
            ui.TextLabel(title_line, text=info["direction"], bg=COLOR_CARD, fg=COLOR_SUBTEXT,
                     font="ShunYiSmall").pack(side="left", padx=(8, 0))
            ui.TextLabel(title_line, text=info["size_text"], bg=COLOR_CARD, fg=COLOR_SUBTEXT,
                     font="ShunYiSmall").pack(side="left", padx=(8, 0))

            status = self._status_chip(left)
            status.pack(anchor="w", pady=(4, 0))

            action = self._action_button(row)
            action.pack(side="right", padx=(10, 0))

            progress = ttk.Progressbar(card, mode="determinate", maximum=100,
                                       style="Thin.Horizontal.TProgressbar")
            note = ui.TextLabel(card, text=info["note"], bg=COLOR_CARD, fg=COLOR_SUBTEXT,
                            font="ShunYiSmall", anchor="w", justify="left", wraplength=ui.S(440))
            note.pack(fill="x", pady=(0, 10))

            self._model_ui[key] = {
                "status": status,
                "action": action,
                "progress": progress,
                "note": note,
                "row": row,
            }
            self._model_status[key] = {"status": "unknown", "percent": 0, "error": ""}

        # 「通知提醒」总开关（默认开）。关掉后所有气泡提醒都不再弹出。
        # 注意：这个开关**改动立即生效**（见 _on_notify_change），不参与「保存」批量提交，
        # 因为"它改了却还继续打扰我"对用户来说等同于开关坏了。
        self._notify_enabled_var = tk.BooleanVar(master=self.root, value=True)
        line = tk.Frame(card, bg=COLOR_CARD)
        line.pack(fill="x", pady=(2, 2))
        ui.IconBadge(line, "bell", size=20, page_bg=COLOR_CARD).pack(side="left", padx=(0, 6))
        ui.TextLabel(line, text="通知提醒", bg=COLOR_CARD, fg=COLOR_TEXT,
                 font=UI_FONT).pack(side="left")
        ui.ToggleSwitch(line, self._notify_enabled_var, command=self._on_notify_change,
                        page_bg=COLOR_CARD).pack(side="right")

    def _on_notify_change(self) -> None:
        """用户切换「通知提醒」：**立即写盘生效**，不等「保存」。

        为什么不等保存（2026-09-28 实测缺陷）：
        原来只标记"未保存"，用户取消勾选后往往直接去删除/下载模型（下载按钮就在
        同一张卡片上），而下载完成的回调会把"未保存"标记清掉并让界面同步回旧值，
        于是这次关闭凭空消失，用户看到的现象就是"开关关了也照样弹"。
        这个开关的语义是"别再打扰我"，必须一次点击就生效、且不会被静默回滚。
        """
        if self._syncing:
            return
        want = bool(self._notify_enabled_var.get())
        config.update(notify_enabled=want)
        self.service.reload_config()
        # 只把开关这一格写进"已同步快照"，避免整表重载把用户其它未保存的改动冲掉
        if self._last_config_snapshot:
            self._last_config_snapshot = tuple(self._last_config_snapshot[:-1]) + (want,)
        log.info("「通知提醒」已切换为%s并立即生效", "开启" if want else "关闭")

    def _build_language_section(self, parent) -> None:
        """目标语言（此前只能从托盘菜单改，窗口里看不到当前值——2026-09-29 补上）。"""
        card = self._card(parent, "目标语言", icon="lang")
        self._lang_var = tk.StringVar(master=self.root, value="en")
        self._lang_group = ui.SegmentGroup(card, [(v, t) for v, t, _d in LANG_INFO],
                                           self._lang_var, self._on_lang_change,
                                           page_bg=COLOR_CARD)
        self._lang_group.pack(anchor="w", pady=(0, 6))
        self._lang_desc = ui.TextLabel(card, text="", bg=COLOR_CARD, fg=COLOR_SUBTEXT,
                                   font="ShunYiSmall", anchor="w", justify="left", wraplength=ui.S(440))
        self._lang_desc.pack(fill="x", pady=(0, 6))
        self._lang_var.trace_add("write", lambda *_: self._sync_lang_desc())
        self._sync_lang_desc()
        self._note_box(card, "与托盘菜单里的「目标语言」是同一项，两边任改一处即可，显示会自动保持一致。",
                       "plain")

    def _sync_lang_desc(self) -> None:
        """更新分段选择器下方的说明文字（随选中项变化）。"""
        desc = next((d for v, _t, d in LANG_INFO if v == self._lang_var.get()), "")
        try:
            self._lang_desc.configure(text=desc)
        except Exception:  # noqa: BLE001
            pass

    def _on_lang_change(self) -> None:
        """用户点了「英文 / 日文」。"""
        if self._syncing:
            return
        self._mark_dirty()

    def _build_quality_section(self, parent) -> None:
        card = self._card(parent, "翻译档位", icon="speed")
        self._quality_var = tk.StringVar(master=self.root, value="fast")
        self._quality_group = ui.SegmentGroup(card, [(v, t) for v, t, _d in QUALITY_INFO],
                                              self._quality_var, self._on_quality_change,
                                              page_bg=COLOR_CARD)
        self._quality_group.pack(anchor="w", pady=(0, 6))
        self._speed_hint = ui.TextLabel(card, text="", bg=COLOR_CARD, fg=COLOR_SUBTEXT,
                                    font="ShunYiSmall", justify="left", anchor="w",
                                    wraplength=ui.S(440))
        self._speed_hint.pack(fill="x")

    def _build_memory_section(self, parent) -> None:
        """内存优化：空闲时是否释放模型。"""
        card = self._card(parent, "内存优化", icon="memory")
        self._idle_unload_var = tk.BooleanVar(master=self.root, value=True)

        line = tk.Frame(card, bg=COLOR_CARD)
        line.pack(fill="x", pady=(0, 6))
        ui.TextLabel(line, text="长时间不翻译时自动释放内存", bg=COLOR_CARD, fg=COLOR_TEXT,
                 font=UI_FONT).pack(side="left")
        ui.ToggleSwitch(line, self._idle_unload_var, command=self._on_memory_change,
                        page_bg=COLOR_CARD).pack(side="right")

    def _build_hotkey_section(self, parent) -> None:
        card = self._card(parent, "快捷键", icon="hotkey")
        ui.TextLabel(card, text="按一下就能把刚打的中文变成译文。", bg=COLOR_CARD, fg=COLOR_SUBTEXT,
                 font="ShunYiSmall").pack(anchor="w", pady=(0, 6))

        line = tk.Frame(card, bg=COLOR_CARD)
        line.pack(fill="x")
        # 输入框：白底 + 浅灰描边的圆角外壳（自绘面板内嵌 Entry）
        shell = ui.RoundedPanel(line, tone="plain", radius=8, pad=3, page_bg=COLOR_CARD,
                                fill=COLOR_CARD, border=ui.COLOR_BORDER_STRONG, fit_width=True)
        shell.pack(side="left")
        self._hotkey_entry = tk.Entry(shell.inner, font="ShunYiMono", justify="center", width=14,
                                      relief="flat", bd=0, bg=COLOR_CARD, fg=COLOR_TEXT,
                                      readonlybackground=COLOR_CARD,
                                      highlightthickness=0)
        self._hotkey_entry.pack(padx=6, pady=3)
        self._hotkey_entry.configure(state="readonly")
        self._record_btn = ui.RoundedButton(line, text="录制", kind="secondary", height=28,
                                            padx=14, page_bg=COLOR_CARD,
                                            command=self._toggle_record)
        self._record_btn.pack(side="left", padx=(8, 0))
        ui.RoundedButton(line, text="恢复默认", kind="secondary", height=28, padx=14,
                         page_bg=COLOR_CARD, command=self._reset_hotkey).pack(side="left", padx=(8, 0))

        self._hotkey_hint = ui.TextLabel(card, text="要求：至少包含一个 Ctrl / Alt / Shift，再加一个主键（例如 Alt+J）。",
                                     bg=COLOR_CARD, fg=COLOR_SUBTEXT, font="ShunYiSmall",
                                     anchor="w", justify="left", wraplength=ui.S(440))
        self._hotkey_hint.pack(fill="x", pady=(8, 0))
        self._note_box(card, "说明：Win 键组合无法在窗口内录制（会被系统占用），"
                             "如需使用请手工编辑配置文件写 windows+j。", "plain")

        # 录制：在输入框和窗口上都监听按键
        for widget in (self._hotkey_entry, self.root):
            widget.bind("<KeyPress>", self._on_record_key)
            widget.bind("<Escape>", self._on_record_escape)

    # ---------------------------------------------------------- 队列轮询

    # 心跳与响应性参数（实测调校，勿随意改大）
    PUMP_INTERVAL_MS = 50      # 空轮询间隔：决定队列命令的响应延迟上限
    PUMP_BUDGET = 20           # 单轮最多处理的指令数：防止一次性排空把事件循环按住
    PUMP_SLOW_MS = 120         # 单轮耗时超过它就写警告日志（把"卡"变成可观测事件）

    def run_in_ui(self, fn, timeout: float = 3.0):
        """在**窗口线程**里执行 fn 并返回结果 —— 其它线程操作 Tk 的唯一入口。

        为什么需要它（2026-09-29 实测根因之一）：tkinter 不是线程安全的，从别的线程
        直接碰控件（哪怕只是 winfo_width()）都可能让解释器挂起或崩掉 ——
        测试脚本里出现过"输出在某段静默中断、进程却没报错"，就是这种跨线程调用。
        测试与托盘代码一律走本方法；超时返回 None（不阻塞调用方）。
        """
        box: dict = {}
        done = threading.Event()

        def _wrapper() -> None:
            try:
                box["result"] = fn()
            except Exception as exc:  # noqa: BLE001
                box["error"] = exc
            finally:
                done.set()

        self._queue.put(("call", _wrapper))
        if not done.wait(timeout):
            log.warning("run_in_ui 超时（%.1fs）：窗口线程可能正忙", timeout)
            return None
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def ui_thread_alive(self) -> bool:
        """窗口线程是否活着（用于自检与排障，不碰 Tk）。"""
        return bool(self._thread and self._thread.is_alive())

    def _pump_mark_owner(self) -> None:
        """在窗口线程里给自己的控件打上"归属线程"标记。

        这样其它线程误碰 Tk 时能被检测出来（见 tools/test_ui_responsive.py 的跨线程检测）。
        """
        self.root._shunyi_owner_thread = threading.current_thread()

    def _pump(self) -> None:
        """窗口线程的心跳：处理其它线程来的指令 + 轻量同步。

        响应性设计（2026-09-29 实测后改，勿退回"120ms + 一次排空）：
          * 轮询间隔 120ms → 50ms：空命令往返延迟从实测 ~125ms 降到 ~55ms；
          * 每轮**最多处理 PUMP_BUDGET 条指令**，剩余的下轮立刻处理 ——
            否则一次性涌入大量指令（测试/脚本场景）会把事件循环按住不放，
            界面出现"未响应"；
          * 单轮耗时超过 PUMP_SLOW_MS 就写一条警告日志：把"卡"变成可观测事件，
            而不是靠用户肉眼发现。
        """
        started = time.perf_counter()
        processed = 0
        try:
            while processed < self.PUMP_BUDGET:
                try:
                    cmd, payload = self._queue.get_nowait()
                except queue.Empty:
                    break
                if cmd == "show":
                    self._show()
                elif cmd == "hide":
                    self._hide()
                elif cmd == "quit":
                    self.root.quit()
                    return
                elif cmd == "progress":
                    self._on_download_progress(*payload)
                elif cmd == "dep_start":
                    self._on_dep_start(*payload)
                elif cmd == "download_done":
                    self._on_download_done(*payload)
                elif cmd == "call":
                    # 通用入口：让别的线程请求在窗口线程里执行一个函数
                    # （tkinter 的控件只能在窗口线程里操作）
                    payload()
                processed += 1
        except Exception:  # noqa: BLE001
            log.exception("设置窗口处理指令出错")

        try:
            self._sync_from_config()
            self._update_speed_hint()
        except Exception:  # noqa: BLE001
            log.exception("设置窗口同步配置出错")

        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms > self.PUMP_SLOW_MS:
            log.warning("设置窗口单轮心跳耗时 %.0f ms（处理 %d 条指令）："
                        "若持续出现，说明 UI 线程里有耗时操作需要挪走",
                        elapsed_ms, processed)

        # 队列还有积压：立刻再来一轮（分段处理，事件循环仍有机会响应输入）
        delay = 1 if not self._queue.empty() else self.PUMP_INTERVAL_MS
        self.root.after(delay, self._pump)

    def _show(self) -> None:
        """显示窗口并置前。"""
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
            # 轻微闪烁一下，确保从托盘打开时能跳到最前面
            self.root.attributes("-topmost", True)
            self.root.after(280, lambda: self.root.attributes("-topmost", False))
        except Exception:  # noqa: BLE001
            pass
        # 刷新模型状态要读磁盘（列目录 / 算占用），放在 deiconify 之前会把"窗口出现"
        # 这一步拖慢；改成等窗口先画出来、事件循环空下来再做。
        self.root.after_idle(self._refresh_models)

    def _hide(self) -> None:
        # 收起窗口时也把提醒条收掉：否则下次打开会看到上次残留的过期提醒
        self._hide_notice()
        self.root.withdraw()

    # ---------------------------------------------------------- 配置读写

    def _load_from_config(self, force: bool = False) -> None:
        """把配置值填进界面控件（程序化刷新，不标记为"用户修改"）。"""
        cfg = config.load(force=force)
        self._last_config_snapshot = (cfg.get("hotkey"), cfg.get("quality"), cfg.get("target_lang"),
                                      int(cfg.get("idle_unload_minutes", 0) or 0) > 0,
                                      bool(cfg.get("notify_enabled", True)))
        self._syncing = True
        try:
            self._pending_hotkey = str(cfg.get("hotkey") or DEFAULT_HOTKEY)
            self._pending_quality = str(cfg.get("quality") or "fast")
            self._hotkey_entry.configure(state="normal")
            self._hotkey_entry.delete(0, "end")
            self._hotkey_entry.insert(0, format_hotkey(self._pending_hotkey))
            self._hotkey_entry.configure(state="readonly")
            self._quality_var.set(self._pending_quality)
            self._lang_var.set(str(cfg.get("target_lang") or "en"))
            self._idle_unload_var.set(int(cfg.get("idle_unload_minutes", 0) or 0) > 0)
            self._notify_enabled_var.set(bool(cfg.get("notify_enabled", True)))
            self._dirty = False
            self._update_dirty_bar()
        finally:
            self._syncing = False

    def _sync_from_config(self) -> None:
        """托盘改了配置就自动同步到界面（窗口未被编辑时）。

        这样"托盘切换档位"与"窗口显示"永远一致，不会出现显示 A 实际 B。
        """
        if self._dirty or self._recording:
            return
        # 用 load_if_changed：托盘菜单/其它实例/手工编辑改了配置时也能及时同步到界面
        cfg = config.load_if_changed()
        snapshot = (cfg.get("hotkey"), cfg.get("quality"), cfg.get("target_lang"),
                    int(cfg.get("idle_unload_minutes", 0) or 0) > 0,
                    bool(cfg.get("notify_enabled", True)))
        if snapshot == self._last_config_snapshot:
            return
        self._load_from_config(force=True)
        self._refresh_models()

    def _pending_values(self) -> tuple:
        """界面上的待保存设置（快捷键 / 档位 / 目标语言 / 内存优化的分钟数）。"""
        return (str(self._pending_hotkey or DEFAULT_HOTKEY),
                str(self._quality_var.get() or "fast"),
                str(self._lang_var.get() or "en"),
                15 if self._idle_unload_var.get() else 0)

    def _saved_values(self) -> tuple:
        """配置文件里当前真正生效的对应设置。"""
        cfg = config.load()
        return (str(cfg.get("hotkey") or DEFAULT_HOTKEY),
                str(cfg.get("quality") or "fast"),
                str(cfg.get("target_lang") or "en"),
                int(cfg.get("idle_unload_minutes", 0) or 0))

    def _values_changed(self) -> bool:
        """界面上的值是否真的和已保存的值不一致。"""
        return self._pending_values() != self._saved_values()

    def _mark_dirty(self) -> None:
        """用户动了控件后调用：**按实际值重新判定**要不要提示"未保存"。

        为什么是"重新比较"而不是"置位后不再撤销"（2026-09-28 实测缺陷）：
        有两类"看着动了、其实没改"的操作会误报未保存：
          1) 点已经选中的那个选项 —— 单选按钮的 command 照样会触发；
          2) 改到别的选项后又切回原值 —— 净变化为零。
        这两种情况下界面值和配置值完全一致，不该提示"未保存"。
        """
        if self._syncing:
            return
        self._dirty = self._values_changed()
        self._update_dirty_bar()

    def _on_memory_change(self) -> None:
        """用户勾选/取消"长时间不翻译时自动释放内存"。"""
        if self._syncing:
            return
        self._mark_dirty()

    def _on_quality_change(self) -> None:
        """用户点了「高速度 / 高质量」单选。"""
        if self._syncing:
            return
        self._pending_quality = self._quality_var.get()
        self._mark_dirty()

    def _update_dirty_bar(self) -> None:
        """显示/隐藏"有未保存修改"的提示条。

        位置：窗口**底部**（贴在「恢复默认设置 / 取消 / 保存」按钮行正上方）。
        与"保存结果小框"的区别：这条是**常驻**的（直到保存或取消），
        所以用 pack 占住版面，而不是浮在内容上，避免长时间盖住下面的卡片。
        """
        if self._dirty:
            cfg = config.load()
            live_q = {"fast": "高速度", "quality": "高质量"}.get(cfg.get("quality"), cfg.get("quality"))
            live_lang = {"en": "英文", "ja": "日文"}.get(cfg.get("target_lang"), cfg.get("target_lang"))
            text = (f"有未保存的修改。当前实际生效的是：快捷键 {format_hotkey(cfg.get('hotkey'))}"
                    f"、档位 {live_q}、目标语言 {live_lang}。点「保存」后生效。")
            self._dirty_label.configure(text=text)
            if not self._dirty_bar.winfo_manager():
                # side="bottom"：footer 先占最底部，这条随后占它上方
                self._dirty_bar.pack(fill="x", side="bottom", padx=18, pady=(0, 8))
        else:
            self._dirty_bar.pack_forget()

    # ---------------------------------------------------- 保存结果的窗口内提醒

    NOTICE_STYLE = {
        # level: (语义色名, 默认停留秒数) —— 底色/描边/字色统一取 ui_kit.TONES
        "ok": ("ok", 3.0),
        "info": ("info", 2.6),
        "err": ("err", 7.0),
    }

    def _show_notice(self, text: str, level: str = "ok", seconds: float | None = None) -> None:
        """在设置窗口**底部**弹出一个小框，自动消失（不弹窗、不阻塞、不改动布局）。

        为什么用它替代弹窗：保存是高频操作，弹模态窗口还要用户点一下"确定"，
        既打断操作也容易让人烦躁；这里只在窗口底部浮现一个小框，到点自动消失。

        关键细节：
          * 用 place 定位（贴底部按钮行上方居中），不参与 pack 布局 ——
            弹出与消失都不会让上面的内容跳动；
          * 用递增序号 + after_cancel 管理定时器 —— 连续点两次「保存」时，
            旧提醒的倒计时不能把新提醒提前关掉；
          * 文字较长（例如更换快捷键后的说明）时自动延长停留时间，保证能读完。
        """
        tone, default_secs = self.NOTICE_STYLE.get(level, self.NOTICE_STYLE["ok"])
        fill, border, fg = ui.TONES[tone]
        text = (text or "").replace("\n\n", "\n")
        if seconds is None:
            # 每多一行多停 0.9 秒，最多 7 秒
            lines = text.count("\n") + 1
            seconds = min(7.0, default_secs + 0.9 * (lines - 1))

        self._notice_seq += 1
        seq = self._notice_seq
        if self._notice_timer is not None:
            try:
                self.root.after_cancel(self._notice_timer)
            except Exception:  # noqa: BLE001
                pass
            self._notice_timer = None

        self._notice_box.set_tone(fill, border)
        self._notice_label.configure(text=text, bg=fill, fg=fg)
        # 贴在窗口底部：横向居中、纵向位于底部按钮行上方（小框底边锚定）。
        # 说明：调用本方法时"未保存提示条"必然已收起（三条保存路径都会先把
        # 未保存状态清掉），所以这里只需按按钮行高度让位，不会与它重叠。
        offset = 12
        try:
            offset = max(12, self._footer.winfo_reqheight() + 12)
        except Exception:  # noqa: BLE001
            pass
        self._notice_box.place(relx=0.5, rely=1.0, y=-offset, anchor="s")
        try:
            self._notice_box.lift()          # 保证浮在卡片内容之上
        except Exception:  # noqa: BLE001
            pass
        self._notice_timer = self.root.after(int(seconds * 1000), lambda: self._hide_notice(seq))

    def _hide_notice(self, seq: int | None = None) -> None:
        """收起提醒小框。seq 不为空时，只有"最新那条"的定时器才允许关掉它。"""
        if seq is not None and seq != self._notice_seq:
            return
        try:
            self._notice_box.place_forget()
        except Exception:  # noqa: BLE001
            pass
        self._notice_timer = None

    def _notice_visible(self) -> bool:
        """提醒小框此刻是否可见（供测试与内部判断用）。"""
        return bool(self._notice_box.winfo_manager())

    def _update_speed_hint(self) -> None:
        quality = self._quality_var.get()
        if quality == "fast":
            text = "当前档位实测响应：中译英约 0.03 秒/句，中译日约 0.09 秒/句。日常聊天建议用这一档。"
        else:
            text = "当前档位实测响应：中译英约 0.05 秒/句，中译日约 0.11 秒/句。比高速度档慢约一倍，长句更准确。"
        if self._speed_hint.cget("text") != text:
            self._speed_hint.configure(text=text)

    # ---------------------------------------------------------- 快捷键录制

    def _toggle_record(self) -> None:
        if self._recording:
            self._stop_record()
        else:
            self._start_record()

    def _start_record(self) -> None:
        self._recording = True
        self._record_btn.configure(text="取消录制")
        self._hotkey_entry.configure(state="normal")
        self._hotkey_entry.delete(0, "end")
        self._hotkey_entry.insert(0, "请按下快捷键…")
        self._hotkey_entry.configure(state="readonly")
        self._hotkey_hint.configure(text="现在请直接按下你想用的组合键（例如按住 Alt 再按 J）。按 Esc 取消。",
                                    fg=COLOR_WARN_FG)
        try:
            self.root.focus_force()
            self._hotkey_entry.focus_set()
        except Exception:  # noqa: BLE001
            pass

    def _stop_record(self) -> None:
        self._recording = False
        self._record_btn.configure(text="录制")
        self._hotkey_entry.configure(state="normal")
        self._hotkey_entry.delete(0, "end")
        self._hotkey_entry.insert(0, format_hotkey(self._pending_hotkey))
        self._hotkey_entry.configure(state="readonly")
        self._hotkey_hint.configure(text="要求：至少包含一个 Ctrl / Alt / Shift，再加一个主键（例如 Alt+J）。",
                                    fg=COLOR_SUBTEXT)

    def _on_record_escape(self, _event=None):
        if self._recording:
            self._stop_record()
            return "break"
        return None

    def _on_record_key(self, event):
        if not self._recording:
            return None
        keysym = (event.keysym or "").lower()
        if keysym == "escape":
            self._stop_record()
            return "break"
        if keysym in FORBIDDEN_KEYS:
            # 只按了修饰键：提示继续
            self._hotkey_hint.configure(text="继续按一个主键（比如 J、K、F9）…", fg=COLOR_WARN_FG)
            return "break"

        combo = parse_tk_event(event)
        if not combo:
            self._hotkey_hint.configure(
                text="这个组合不行：必须至少包含一个 Ctrl / Alt / Shift（不能只用单个键，否则会干扰你正常打字）。",
                fg=COLOR_ERR)
            return "break"

        self._pending_hotkey = combo
        self._stop_record()
        self._mark_dirty()
        return "break"

    def _reset_hotkey(self) -> None:
        self._pending_hotkey = DEFAULT_HOTKEY
        if self._recording:
            self._stop_record()
        self._hotkey_entry.configure(state="normal")
        self._hotkey_entry.delete(0, "end")
        self._hotkey_entry.insert(0, format_hotkey(self._pending_hotkey))
        self._hotkey_entry.configure(state="readonly")
        self._mark_dirty()

    # ---------------------------------------------------------- 模型管理

    def _refresh_models(self) -> None:
        """根据磁盘状态刷新两张模型卡片。"""
        for info in MODEL_CARDS:
            key = info["key"]
            ui = self._model_ui[key]
            state = self._model_status.get(key, {})
            # 正在下载（或等待依赖）的卡片不要被刷新覆盖掉进度
            if key in self._active_downloads or state.get("status") == "downloading":
                continue

            # 上次删除没删干净：如实显示，并给出"重试删除"，不要伪装成"未下载"
            if state.get("status") == "stale":
                reason = state.get("error") or "部分文件仍被占用"
                ui["status"].configure(text=f"状态：删除未完成（{reason}）", fg=COLOR_WARN_FG)
                ui["action"].configure(text="重试删除",
                                       command=lambda k=key: self._on_delete_model(k))
                ui["action"].state(["!disabled"])
                ui["progress"].pack_forget()
                continue

            # 上次下载失败：常驻显示原因，并给出"重试下载"（关闭通知时这是唯一提示）
            if state.get("status") == "failed":
                reason = state.get("error") or "未知原因"
                ui["status"].configure(
                    text=f"状态：下载失败（{reason.splitlines()[0]}）", fg=COLOR_WARN_FG)
                ui["action"].configure(text="重试下载",
                                       command=lambda k=key: self._on_download_model(k))
                ui["action"].state(["!disabled"])
                ui["progress"].pack_forget()
                continue

            installed = models.is_installed(key)
            # 只查程序管理的两处（deep=False），避免高频刷新时去扫目录
            leftover = [] if installed else models.leftover_files(key, deep=False)

            if key == "en_ja" and installed and not models.is_installed("zh_en"):
                # 日文方向还需要基础模型
                ui["status"].configure(text="已下载日文模型，但缺少「中译英」基础模型，暂时无法翻译",
                                       fg=COLOR_WARN_FG)
            elif installed:
                size_mb = models.installed_size(key) / 1024 / 1024
                ui["status"].configure(text=f"状态：已下载（占用 {size_mb:.0f} MB）", fg=COLOR_OK)
            elif leftover:
                # 文件还在但模型已不完整 —— 说明删除只完成了一半
                ui["status"].configure(
                    text=f"状态：有 {len(leftover)} 处文件残留未清理，点右侧可重试删除",
                    fg=COLOR_WARN_FG)
            else:
                ui["status"].configure(text="状态：未下载", fg=COLOR_SUBTEXT)

            can_delete = installed or bool(leftover)
            ui["action"].configure(text="删除" if can_delete else "下载",
                                   command=(lambda k=key: self._on_delete_model(k)) if can_delete
                                   else (lambda k=key: self._on_download_model(k)))
            ui["action"].state(["!disabled"])
            ui["progress"].pack_forget()

    def _on_download_model(self, key: str) -> None:
        # 本次要下载哪些模型：中译日还需要中译英作为基础模型。
        # 关键点：判断"基础模型是否就绪"不能只看 is_installed ——
        # 正在下载/正在安装的模型 is_installed 同样是 False，会导致重复下载同一个文件
        # （曾因此出现进度条来回跳、两个线程交错写坏文件的问题）。
        # 这里同时看三处：已安装、底层锁被占用、界面层登记，任何一处成立都算"在准备中"。
        zh_busy = ("zh_en" in self._active_downloads) or models.is_downloading("zh_en")
        base_ready = models.is_installed("zh_en") or zh_busy
        need_base = (key == "en_ja" and not base_ready)
        targets = ["zh_en", "en_ja"] if need_base else [key]

        # 互斥：只要清单里有任何一项正在下载，就不重复发起
        busy = [k for k in targets if k in self._active_downloads or models.is_downloading(k)]
        if busy:
            names = "、".join(self._title_of(k) for k in busy)
            if busy == ["zh_en"] and key == "en_ja":
                dialogs.tell(
                    self.root, "基础模型正在下载",
                    "「中译英」基础模型正在下载中。\n\n"
                    "等它下载完成后，再点「中译日」的下载按钮即可 —— 那时只会下载日文模型，"
                    "不会再重复下载一遍基础模型。",
                    tone="info")
            else:
                dialogs.tell(
                    self.root, "正在下载中",
                    f"「{names}」正在下载，请等它完成后再试。",
                    tone="info")
            return

        if self._download_threads.get(key) and self._download_threads[key].is_alive():
            return

        info = next(i for i in MODEL_CARDS if i["key"] == key)
        size_text = info["size_text"]
        tip = (f"即将下载：{info['title']}（{size_text}）。\n\n"
               "这是本软件唯一需要联网的操作，翻译过程本身完全离线。\n"
               "下载会自动开启并发加速（多连接同时下），速度比单连接快很多。\n"
               "中途可以随时点「取消」；取消后再点下载会重新开始（因为并发下载"
               "无法从断点接续，不过加速后整体耗时已经短很多）。")
        if need_base:
            tip += "\n\n注意：日文方向还需要「中译英」基础模型（约 71 MB），本次会一并下载。"
        if not dialogs.ask(self.root, "开始下载模型", tip, ok_text="开始下载"):
            return

        cancel = threading.Event()
        self._cancel_events[key] = cancel
        self._active_downloads.update(targets)      # 登记占用，避免其他入口重复发起
        self._model_status[key] = {"status": "downloading", "percent": 0, "error": ""}
        ui = self._model_ui[key]
        ui["status"].configure(text="状态：正在下载 0%", fg=COLOR_PRIMARY)
        ui["action"].configure(text="取消", command=lambda k=key: self._cancel_download(k))
        ui["progress"].pack(fill="x", pady=(2, 8))
        ui["progress"]["value"] = 0

        def worker():
            try:
                for k in targets:
                    if cancel.is_set():
                        raise models.DownloadCancelled()
                    if k != key:
                        # 正在下载依赖项（基础模型），让主卡片显示"准备中"而不是干等 0%
                        self._queue.put(("dep_start", (key, k)))
                    self._download_one(k, cancel)
                self._queue.put(("download_done", (key, True, "")))
            except models.DownloadCancelled:
                self._queue.put(("download_done", (key, False, "已取消下载")))
            except Exception as exc:  # noqa: BLE001
                log.exception("模型下载失败")
                self._queue.put(("download_done", (key, False, self._friendly_error(exc))))
            finally:
                # 无论成功、取消还是失败，都要释放占用登记
                for k in targets:
                    self._active_downloads.discard(k)

        thread = threading.Thread(target=worker, name=f"shunyi-dl-{key}", daemon=True)
        self._download_threads[key] = thread
        thread.start()

    @staticmethod
    def _title_of(key: str) -> str:
        for item in MODEL_CARDS:
            if item["key"] == key:
                return item["title"]
        return key

    def _on_dep_start(self, key: str, dep_key: str) -> None:
        """主卡片正在等待它的依赖模型下载完成。"""
        ui = self._model_ui.get(key)
        if not ui:
            return
        dep_title = self._title_of(dep_key)
        ui["status"].configure(
            text=f"状态：正在等待基础模型「{dep_title}」下载完成（进度见它的卡片）…",
            fg=COLOR_PRIMARY)

    def _download_one(self, key: str, cancel: threading.Event) -> None:
        def progress(label, done, total):
            self._queue.put(("progress", (key, done, total, label)))
        models.ensure(key, progress_cb=progress, cancel_event=cancel)

    def _download_rate_text(self, key: str, done: int, total: int) -> str:
        """按 1 秒窗口算瞬时速度与预计剩余时间，返回可供状态栏拼接的文本。

        为什么要有它：进度回调只给"已下载字节数"，服务端限速会让人看到百分比
        长时间不动，分不清是卡住还是在慢慢走。把速度和剩余时间显示出来，
        用户能立刻看懂当前是什么状况（配合"连接卡顿，正在重试"的提示）。
        """
        now = time.time()
        st = self._dl_samples.get(key)
        if st is None:
            st = {"t": now, "done": done, "text": ""}
        dt = now - st["t"]
        if dt >= 1.0:
            delta = done - st["done"]
            rate = delta / dt if delta > 0 else 0.0
            if rate > 0:
                text = f"　· {rate / 1024 / 1024:.2f} MB/s"
                if total:
                    remain = max(total - done, 0)
                    eta = remain / rate
                    if eta >= 60:
                        text += f" · 约剩 {int(eta // 60)} 分 {int(eta % 60)} 秒"
                    else:
                        text += f" · 约剩 {int(eta)} 秒"
            else:
                text = "　· 速度 0 MB/s（连接卡顿，正在重试）"
            st["t"], st["done"], st["text"] = now, done, text
        self._dl_samples[key] = st
        return st["text"]

    def _on_download_progress(self, key: str, done: int, total: int, label: str = "") -> None:
        ui = self._model_ui.get(key)
        if not ui:
            return
        # 防御性处理：无论从哪条路径进来，都确保进度条是显示状态
        if not ui["progress"].winfo_manager():
            ui["progress"].pack(fill="x", pady=(2, 8))
        percent = int(done * 100 / total) if total else 0
        # total 可能为 None（服务器没给 Content-Length），此时只显示已下载量
        if total:
            size_text = f"{done/1024/1024:.1f} / {total/1024/1024:.1f} MB"
        else:
            size_text = f"已下载 {done/1024/1024:.1f} MB"
        rate_text = self._download_rate_text(key, done, total)
        self._model_status[key] = {"status": "downloading", "percent": percent, "error": ""}
        ui["status"].configure(
            text=f"状态：正在下载 {percent}%　（{size_text}）{rate_text}", fg=COLOR_PRIMARY)
        if total:
            ui["progress"]["value"] = percent

    def _on_download_done(self, key: str, ok: bool, message: str) -> None:
        cancelled = "取消" in (message or "")
        if ok:
            state = "installed"
        elif cancelled:
            state = "absent"       # 用户主动取消：不算失败，卡片回到「未下载」
        else:
            # 失败：卡片常驻显示失败原因。这样即使用户关掉了「通知提醒」，
            # 也不会因为"没有弹窗"而完全不知道下载失败了、为什么失败。
            state = "failed"
        self._model_status[key] = {"status": state, "percent": 0, "error": message}
        self._active_downloads.discard(key)
        self._dl_samples.pop(key, None)      # 清掉速度采样，下次下载重新起算
        # 先刷新界面、再通知：提醒会占用窗口线程，先刷新能保证提醒消失后界面立刻是对的
        self._refresh_models()
        # 注意：这里**不能**清 _dirty。
        # 2026-09-28 实测缺陷：下载完成时清掉"未保存"标记后，_sync_from_config 会
        # 立刻把用户刚改、还没保存的控件值同步回旧值（例如刚取消勾选的「通知提醒」
        # 被静默改回已勾选），用户的修改凭空消失 → "关掉开关照样被提醒"。
        # _dirty 只应该由 _load_from_config（保存/取消时）清除。
        if ok:
            self.service.preload()      # 下载完顺手把模型读进内存，用户点保存后马上能用
            # 走统一通知入口：开关（「通知提醒」）在入口里判定，同一次事件只提醒一次
            self.service.notify("模型下载完成", "现在可以正常翻译了。")
        elif not cancelled:
            first_line = (message or "未知原因").strip().splitlines()[0]
            self.service.notify("模型下载失败", f"{first_line}（可在设置窗口点「重试下载」）")

    def _cancel_download(self, key: str) -> None:
        event = self._cancel_events.get(key)
        if event:
            event.set()
        self._model_ui[key]["status"].configure(text="状态：正在取消…", fg=COLOR_SUBTEXT)

    def _on_delete_model(self, key: str) -> None:
        info = next(i for i in MODEL_CARDS if i["key"] == key)
        cfg = config.load()
        used = (key == "zh_en" and cfg.get("target_lang") in ("en", "ja")) or \
               (key == "en_ja" and cfg.get("target_lang") == "ja")

        # 删除前把"会被清理哪些位置"完整列出来 —— 模型可能不止一处，
        # 只删程序管理的那部分会让用户觉得"删了但文件还在"
        locations = models.describe_locations(key)
        total_mb = sum(sz for _p, sz in locations) / 1024 / 1024

        lines = [f"确定要删除「{info['title']}」模型吗？", "",
                 f"将释放约 {total_mb:.0f} MB 磁盘空间。", ""]
        if locations:
            lines.append("会清理以下位置：")
            for path, size in locations:
                lines.append(f"  · {path}（{size/1024/1024:.0f} MB）")
        if key == "zh_en":
            lines.append("")
            lines.append("注意：中译英是基础模型，删除后中译英和中译日都无法翻译。")
        if used:
            lines.append("当前目标语言正在使用它，删除后按快捷键会提示模型缺失。")
        lines.append("")
        lines.append("删除后如果还需要，可以在这里重新下载。")

        if not dialogs.ask(self.root, "删除模型", "\n".join(lines), ok_text="删除",
                           tone="err", danger=True):
            return

        try:
            # 先让引擎释放模型文件，避免"文件正在使用"导致删除失败
            try:
                self.service.unload_models()
            except Exception:  # noqa: BLE001
                log.exception("卸载模型失败（继续尝试删除）")

            result = models.remove(key)
            freed = result["freed"]
            removed_count = len(result["removed"])
            failed = result["failed"]

            # 删除后再实地核实一遍，避免"界面显示删了、磁盘上还在"
            leftover = models.leftover_files(key)

            if leftover or failed:
                detail = [f"  · {p}" for p in leftover]
                detail += [f"  · {p}（{why}）" for p, why in failed]
                self._model_status[key] = {"status": "stale", "percent": 0,
                                           "error": "部分文件仍被占用"}
                dialogs.tell(
                    self.root, "删除未完成",
                    f"已清理 {removed_count} 处，释放 {freed/1024/1024:.0f} MB。\n\n"
                    "但以下文件没能删掉：\n" + "\n".join(detail[:8]) +
                    "\n\n常见原因是文件正被占用。可以退出软件后手动删除，"
                    "或在设置里再点一次「重试删除」。",
                    tone="warn")
            else:
                self._model_status.pop(key, None)
                missing = models.missing_for_target(cfg.get("target_lang", "en"))
                if missing:
                    names = "、".join(next(i["title"] for i in MODEL_CARDS if i["key"] == m)
                                      for m in missing)
                    dialogs.tell(
                        self.root, "已删除，但有影响",
                        f"删除完成：共清理 {removed_count} 处，释放 {freed/1024/1024:.0f} MB。\n\n"
                        f"当前目标语言所需的「{names}」模型已缺失，"
                        "现在按快捷键会提示模型未就绪，请重新下载后再使用。",
                        tone="warn")
                else:
                    dialogs.tell(
                        self.root, "删除完成",
                        f"删除完成：共清理 {removed_count} 处，释放 {freed/1024/1024:.0f} MB 磁盘空间。",
                        tone="ok")
        except PermissionError:
            self._model_status[key] = {"status": "stale", "percent": 0, "error": "文件被占用"}
            dialogs.tell(
                self.root, "无法删除",
                "模型文件正在被占用，删除没有执行。\n\n"
                "请先把目标语言切到另一个方向，或退出软件后手动删除该文件夹，"
                "然后再回到设置里点一次「重试删除」。",
                tone="err")
        except Exception as exc:  # noqa: BLE001
            log.exception("删除模型失败")
            self._model_status[key] = {"status": "stale", "percent": 0,
                                       "error": self._friendly_error(exc)[:20]}
            dialogs.tell(self.root, "删除失败", self._friendly_error(exc), tone="err")
        finally:
            self._refresh_models()

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        """把技术异常翻译成人话。"""
        text = str(exc)
        low = text.lower()
        if isinstance(exc, models.DownloadCancelled):
            return "已取消下载"
        if "timed out" in low or "timeout" in low:
            return "网络超时：连接下载服务器超时了。请检查网络后重试。"
        if "urlopen error" in low or "connection" in low or "getaddrinfo" in low:
            return "网络不通：无法连接到下载服务器。请检查网络或稍后重试。"
        if "no space" in low or "磁盘" in text:
            return "磁盘空间不足：请清理磁盘后重试。"
        return f"发生错误：{text[:200]}"

    # ---------------------------------------------------------- 底部按钮

    def _on_save(self) -> None:
        cfg = config.load()
        new_hotkey = self._pending_hotkey
        # 档位以界面控件当前值为准（用户可能刚点过单选按钮）
        new_quality = self._quality_var.get()
        new_lang = str(self._lang_var.get() or "en")
        # 内存优化：勾选=空闲 15 分钟释放，不勾=常驻
        new_idle_minutes = 15 if self._idle_unload_var.get() else 0
        old_idle_minutes = int(cfg.get("idle_unload_minutes", 0) or 0)

        hotkey_changed = new_hotkey != cfg.get("hotkey")
        quality_changed = new_quality != cfg.get("quality")
        lang_changed = new_lang != cfg.get("target_lang")
        memory_changed = new_idle_minutes != old_idle_minutes
        # 「通知提醒」不在保存批量里：它在勾选/取消的那一刻就已经生效落盘
        # （见 _on_notify_change），避免"改了没保存 → 依然被提醒"的错觉

        if not hotkey_changed and not quality_changed and not lang_changed and not memory_changed:
            self._dirty = False
            self._update_dirty_bar()
            self._show_notice("设置和当前生效的一致，无需保存。", "info")
            return

        # 1) 快捷键：先检测冲突 + 试注册，成功才落盘（失败则保留原键，绝不让软件失去响应快捷键的能力）
        if hotkey_changed:
            ok, reason = self.service.set_hotkey(new_hotkey)
            if not ok:
                self._pending_hotkey = cfg.get("hotkey")
                self._load_from_config(force=True)
                self._show_notice(
                    f"快捷键「{format_hotkey(new_hotkey)}」没能生效：{reason}"
                    f"已保留原来的「{format_hotkey(cfg.get('hotkey'))}」，可以换一个组合再试。",
                    "err")
                return

        if quality_changed:
            config.update(quality=new_quality)
            self.service.reload_config()

        if lang_changed:
            config.update(target_lang=new_lang)
            self.service.reload_config()

        if memory_changed:
            config.update(idle_unload_minutes=new_idle_minutes)
            self.service.reload_config()

        self._pending_quality = new_quality
        self._load_from_config(force=True)

        # 切到日文但模型还没下载：如实提醒（引导去上方卡片下载，而不是等翻译时才发现）。
        # 注意这里配置已经保存成功了，只是提醒缺模型，不算保存失败。
        if lang_changed and models.missing_for_target(new_lang):
            names = "、".join(models.PACKAGES[k]["display"]
                              for k in models.missing_for_target(new_lang))
            self._show_notice(f"目标语言已保存为{'日文' if new_lang == 'ja' else '英文'}，"
                              f"但还缺少模型：{names}。请在上方「翻译模型」卡片点「下载」。",
                              "err")
            return

        tip = "设置已保存并立即生效，无需重启软件。"
        if hotkey_changed:
            tip += ("\n提示：新快捷键已经生效。如果按下去没反应，说明这个组合被别的软件抢占了"
                    "（这类冲突无法自动检测），换一个组合重新设置即可。")
        if lang_changed:
            tip += "\n目标语言已切换为" + ("日文" if new_lang == "ja" else "英文") + "。"
        if memory_changed:
            tip += ("\n内存优化已" + ("开启：挂机超过 15 分钟会自动释放模型内存。"
                                      if new_idle_minutes else "关闭：模型会一直留在内存里，翻译始终最快。"))
        # 窗口内轻量提醒：立即出现、到点自动消失，不打断操作
        self._show_notice(tip, "ok")

    def _on_cancel(self) -> None:
        if self._dirty and not dialogs.ask(
                self.root, "放弃修改", "有未保存的修改，关闭后会丢弃，确定吗？",
                ok_text="放弃修改", tone="warn"):
            return
        self._load_from_config(force=True)
        self._hide()

    def _on_window_close(self) -> None:
        """点右上角关闭：等同「取消」（有改动先确认），窗口只是隐藏，软件继续运行。"""
        if self._dirty and not dialogs.ask(
                self.root, "放弃修改", "有未保存的修改，关闭后会丢弃，确定吗？",
                ok_text="放弃修改", tone="warn"):
            return
        self._load_from_config(force=True)
        self._hide()

    def _on_restore_defaults(self) -> None:
        """一键恢复默认：**直接生效**，不再弹二次确认（2026-10-01 按主人要求）。

        原来的流程是"弹确认框 → 只改界面上的值 → 用户还得再点一次「保存」"，既多两步，
        又容易让人以为"点了恢复默认却没变"（尤其那句"点「保存」后才真正生效"的说明
        文字我们已经删掉了）。现在点了就恢复并**立刻写盘**。

        其他设置项的保存与应用不受影响：它们仍然走「保存」按钮那条路。
        """
        self._pending_hotkey = DEFAULT_HOTKEY
        self._pending_quality = "fast"
        if self._recording:
            self._stop_record()
        self._syncing = True
        try:
            self._hotkey_entry.configure(state="normal")
            self._hotkey_entry.delete(0, "end")
            self._hotkey_entry.insert(0, format_hotkey(DEFAULT_HOTKEY))
            self._hotkey_entry.configure(state="readonly")
            self._quality_var.set("fast")
            self._lang_var.set("en")
            self._idle_unload_var.set(True)
            self._notify_enabled_var.set(True)
        finally:
            self._syncing = False
        # 一次性落盘 + 让服务重载：恢复默认本身就是"已保存"状态，不需要再点「保存」
        config.update(hotkey=DEFAULT_HOTKEY, quality="fast", target_lang="en",
                      idle_unload_minutes=15, notify_enabled=True)
        self.service.reload_config()
        if self._last_config_snapshot:
            # 免得心跳轮询把这次写入当成"别处改了配置"，又同步一遍
            self._last_config_snapshot = (DEFAULT_HOTKEY, "fast", "en", True, True)
        # 此刻界面值已与配置一致 → 这一步不会留下"未保存"标记
        # （_mark_dirty 是拿界面值和配置值**实际比较**出来的，不是粘滞标记）
        self._mark_dirty()
