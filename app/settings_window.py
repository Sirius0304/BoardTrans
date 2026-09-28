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
try:
    import tkinter as tk
    from tkinter import messagebox, ttk
    TK_AVAILABLE = True
    TK_IMPORT_ERROR = ""
except Exception as _exc:  # noqa: BLE001
    tk = None
    messagebox = None
    ttk = None
    TK_AVAILABLE = False
    TK_IMPORT_ERROR = str(_exc)

# ----------------------------------------------------------------- 外观常量

FONT_FAMILY = "Microsoft YaHei UI"
FONT_FALLBACK = "Microsoft YaHei"
COLOR_BG = "#F5F6F8"
COLOR_CARD = "#FFFFFF"
COLOR_TEXT = "#1F2329"
COLOR_SUBTEXT = "#6B7280"
COLOR_PRIMARY = "#2E6BE6"
COLOR_WARN_BG = "#FFF6E5"
COLOR_WARN_FG = "#9A6700"
COLOR_OK = "#1A7F37"
COLOR_ERR = "#C62828"
# 窗口内轻量提醒条（保存结果提示）的底色，配合上面的前景色使用
COLOR_OK_BG = "#E8F5EC"
COLOR_INFO_BG = "#EDF3FE"
COLOR_ERR_BG = "#FDECEC"

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
        self._ready = threading.Event()

        # ---- 界面状态 ----
        self.root: tk.Tk | None = None
        self._dirty = False                     # 有未保存修改
        self._syncing = False                   # 正在程序化刷新控件（避免误判为用户修改）
        self._recording = False                 # 正在录制快捷键
        self._pending_hotkey = DEFAULT_HOTKEY   # 界面上的快捷键值
        self._pending_quality = "fast"
        self._model_ui: dict[str, dict] = {}
        self._download_threads: dict[str, threading.Thread] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        # 正在下载的模型 key 集合（含"作为依赖被带上下载"的），用于防止重复发起
        self._active_downloads: set[str] = set()
        # 每个模型最近一次进度采样 {t, done, text}，用来算瞬时速度与剩余时间
        self._dl_samples: dict[str, dict] = {}
        self._model_status: dict[str, dict] = {}
        self._last_config_snapshot: tuple | None = None
        self._section_wrappers: list = []        # 各区块容器，用于定位提示条的插入位置

    # ---------------------------------------------------------- 对外接口

    def open(self) -> None:
        """线程安全：请求打开设置窗口（已存在则置前）。可从托盘回调直接调用。"""
        if not TK_AVAILABLE:
            self._warn_no_tkinter()
            return
        self._ensure_thread()
        self._queue.put(("show", None))

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
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="shunyi-settings", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=8)

    def _run(self) -> None:
        """窗口线程主函数：建界面 -> 常驻 mainloop。"""
        try:
            self.root = tk.Tk()
            self.root.title("瞬译 · 设置")
            self.root.configure(bg=COLOR_BG)
            self._apply_theme()
            self._build_ui()
            self._load_from_config(force=True)
            self.root.withdraw()                      # 初始隐藏，等托盘点「设置」
            self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)
            self._refresh_models()
            self._ready.set()
            self._pump()
            self.root.mainloop()
        except Exception:  # noqa: BLE001
            log.exception("设置窗口线程异常退出")
        finally:
            self._ready.set()

    def _apply_theme(self) -> None:
        """字体、DPI 缩放与 ttk 主题。"""
        root = self.root
        # 按实际 DPI 设置 tk 缩放，保证高分屏下文字与控件不发虚、不错位
        try:
            dpi = root.winfo_fpixels("1i")
            root.tk.call("tk", "scaling", max(1.0, dpi / 72.0))
        except Exception:  # noqa: BLE001
            pass

        families = set()
        try:
            families = set(root.tk.call("font", "families"))
        except Exception:  # noqa: BLE001
            pass
        family = FONT_FAMILY if FONT_FAMILY in families else (FONT_FALLBACK if FONT_FALLBACK in families else "TkDefaultFont")

        for name, size, weight in (
            ("ShunYiBase", 10, "normal"),
            ("ShunYiBold", 10, "bold"),
            ("ShunYiTitle", 12, "bold"),
            ("ShunYiSmall", 9, "normal"),
            ("ShunYiMono", 11, "normal"),
        ):
            try:
                root.tk.call("font", "create", name, "-family", family, "-size", size, "-weight", weight)
            except Exception:  # noqa: BLE001
                pass

        style = ttk.Style(root)
        try:
            style.theme_use("vista")          # Windows 原生观感
        except tk.TclError:
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
        style.configure(".", font=("ShunYiBase", 10) if family else None, background=COLOR_BG)
        style.configure("Card.TFrame", background=COLOR_CARD, relief="flat")
        style.configure("CardTitle.TLabel", background=COLOR_CARD, foreground=COLOR_TEXT, font="ShunYiTitle")
        style.configure("CardText.TLabel", background=COLOR_CARD, foreground=COLOR_TEXT)
        style.configure("CardSub.TLabel", background=COLOR_CARD, foreground=COLOR_SUBTEXT, font="ShunYiSmall")
        style.configure("CardOk.TLabel", background=COLOR_CARD, foreground=COLOR_OK, font="ShunYiBold")
        style.configure("CardErr.TLabel", background=COLOR_CARD, foreground=COLOR_ERR, font="ShunYiBold")
        style.configure("Hint.TLabel", background=COLOR_BG, foreground=COLOR_SUBTEXT, font="ShunYiSmall")
        style.configure("Warn.TFrame", background=COLOR_WARN_BG)
        style.configure("Warn.TLabel", background=COLOR_WARN_BG, foreground=COLOR_WARN_FG, font="ShunYiBold")
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
        root.minsize(640, 700)

        outer = tk.Frame(root, bg=COLOR_BG)
        outer.pack(fill="both", expand=True, padx=0, pady=0)

        # 标题
        header = tk.Frame(outer, bg=COLOR_BG)
        header.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(header, text="瞬译 设置", bg=COLOR_BG, fg=COLOR_TEXT, font="ShunYiTitle").pack(anchor="w")
        tk.Label(header, text="在这里管理翻译模型、翻译档位和快捷键；点「保存」后立即生效。",
                 bg=COLOR_BG, fg=COLOR_SUBTEXT, font="ShunYiSmall").pack(anchor="w", pady=(2, 0))

        # 未保存提示条
        self._dirty_bar = tk.Frame(outer, bg=COLOR_WARN_BG)
        self._dirty_label = tk.Label(self._dirty_bar, text="", bg=COLOR_WARN_BG, fg=COLOR_WARN_FG,
                                     font="ShunYiSmall", anchor="w", justify="left")
        self._dirty_label.pack(fill="x", padx=12, pady=6)

        # 保存结果轻量提醒：窗口**底部**弹出的一个小框（用 place 浮在内容之上，
        # 不参与 pack 布局，所以弹出/消失都不会让上面的内容跳动）。
        # 位置与配色见 _show_notice；默认不显示。
        self._notice_box = tk.Frame(outer, bg=COLOR_OK_BG,
                                    highlightbackground="#D9DDE3", highlightthickness=1)
        self._notice_label = tk.Label(self._notice_box, text="", bg=COLOR_OK_BG, fg=COLOR_OK,
                                      font="ShunYiSmall", anchor="w", justify="left",
                                      wraplength=500)
        self._notice_label.pack(fill="x", padx=12, pady=8)
        self._notice_timer = None      # after() 的句柄，用于"新提醒顶掉旧倒计时"
        self._notice_seq = 0           # 提醒序号：防止旧定时器把新提醒提前关掉

        # 三个区块
        self._build_models_section(outer)
        self._build_quality_section(outer)
        self._build_memory_section(outer)
        self._build_hotkey_section(outer)

        # 底部按钮
        footer = tk.Frame(outer, bg=COLOR_BG)
        footer.pack(fill="x", side="bottom", padx=18, pady=14)
        ttk.Button(footer, text="恢复默认设置", command=self._on_restore_defaults).pack(side="left")
        ttk.Button(footer, text="保存", style="Big.TButton", command=self._on_save).pack(side="right")
        ttk.Button(footer, text="取消", command=self._on_cancel).pack(side="right", padx=(0, 8))
        self._footer = footer          # 提醒小框要贴在它上方

    def _card(self, parent, title: str) -> ttk.Frame:
        """创建一个白色卡片容器（带内边距与标题），并把外层容器登记下来。"""
        wrapper = tk.Frame(parent, bg=COLOR_BG)
        wrapper.pack(fill="x", padx=18, pady=(0, 12))
        self._section_wrappers.append(wrapper)
        card = tk.Frame(wrapper, bg=COLOR_CARD, highlightbackground="#E3E5E9", highlightthickness=1)
        card.pack(fill="x")
        tk.Label(card, text=title, bg=COLOR_CARD, fg=COLOR_TEXT, font="ShunYiTitle").pack(anchor="w", padx=14, pady=(12, 6))
        return card

    def _build_models_section(self, parent) -> None:
        card = self._card(parent, "翻译模型")
        tk.Label(card, text="提示：下载模型是本软件唯一需要联网的操作，翻译过程完全离线。",
                 bg=COLOR_CARD, fg=COLOR_WARN_FG, font="ShunYiSmall").pack(anchor="w", padx=14, pady=(0, 8))

        # 显示模型实际存放位置：默认在「软件所在目录\models」，
        # 若该路径含中文或不可写则自动回退到数据目录（sentencepiece 读不了非 ASCII 路径）
        root = paths.models_root()
        is_program_dir = os.path.abspath(root) == os.path.abspath(paths.default_models_root())
        where = f"模型存放位置：{root}"
        if not is_program_dir:
            where += "（软件所在目录含中文或不可写，已自动回退到这里；把软件放到纯英文路径即可改用软件目录）"
        tk.Label(card, text=where, bg=COLOR_CARD, fg=COLOR_SUBTEXT, font="ShunYiSmall",
                 anchor="w", justify="left", wraplength=560).pack(fill="x", padx=14, pady=(0, 8))

        for info in MODEL_CARDS:
            key = info["key"]
            row = tk.Frame(card, bg=COLOR_CARD)
            row.pack(fill="x", padx=14, pady=(0, 6))

            left = tk.Frame(row, bg=COLOR_CARD)
            left.pack(side="left", fill="x", expand=True)
            tk.Label(left, text=f'{info["title"]}    {info["direction"]}    {info["size_text"]}',
                     bg=COLOR_CARD, fg=COLOR_TEXT, font="ShunYiBold").pack(anchor="w")
            status = tk.Label(left, text="检查中…", bg=COLOR_CARD, fg=COLOR_SUBTEXT, font="ShunYiSmall")
            status.pack(anchor="w", pady=(2, 0))

            action = ttk.Button(row, text="下载", width=8)
            action.pack(side="right", padx=(10, 0))

            progress = ttk.Progressbar(card, mode="determinate", maximum=100)
            note = tk.Label(card, text=info["note"], bg=COLOR_CARD, fg=COLOR_SUBTEXT, font="ShunYiSmall")
            note.pack(anchor="w", padx=14, pady=(0, 10))

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
        self._notify_enabled_var = tk.BooleanVar(value=True)
        line = tk.Frame(card, bg=COLOR_CARD)
        line.pack(fill="x", padx=14, pady=(10, 2))
        ttk.Checkbutton(line, text="通知提醒",
                        variable=self._notify_enabled_var,
                        command=self._on_notify_change).pack(side="left")
        tk.Label(card, text="关闭后所有气泡提醒都不再弹出（翻译完成、取词失败、快捷键冲突、"
                            "模型下载完成/失败、启动提示等），只记日志。默认开启；"
                            "改动立即生效，不需要点「保存」。",
                 bg=COLOR_CARD, fg=COLOR_SUBTEXT, font="ShunYiSmall",
                 anchor="w", justify="left", wraplength=520
                 ).pack(fill="x", padx=14, pady=(0, 12))

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
            self._last_config_snapshot = tuple(self._last_config_snapshot[:3]) + (want,)
        log.info("「通知提醒」已切换为%s并立即生效", "开启" if want else "关闭")

    def _build_quality_section(self, parent) -> None:
        card = self._card(parent, "翻译档位")
        self._quality_var = tk.StringVar(value="fast")
        for value, title, desc in QUALITY_INFO:
            line = tk.Frame(card, bg=COLOR_CARD)
            line.pack(fill="x", padx=14, pady=3)
            rb = ttk.Radiobutton(line, text=title, value=value, variable=self._quality_var,
                                 command=self._on_quality_change)
            rb.pack(side="left")
            tk.Label(line, text=f"　{desc}", bg=COLOR_CARD, fg=COLOR_SUBTEXT,
                     font="ShunYiSmall").pack(side="left")
        self._speed_hint = tk.Label(card, text="", bg=COLOR_CARD, fg=COLOR_SUBTEXT, font="ShunYiSmall",
                                    justify="left", anchor="w")
        self._speed_hint.pack(fill="x", padx=14, pady=(6, 12))

    def _build_memory_section(self, parent) -> None:
        """内存优化：空闲时是否释放模型。"""
        card = self._card(parent, "内存优化")
        self._idle_unload_var = tk.BooleanVar(value=True)

        line = tk.Frame(card, bg=COLOR_CARD)
        line.pack(fill="x", padx=14, pady=2)
        ttk.Checkbutton(
            line, text="长时间不翻译时自动释放内存",
            variable=self._idle_unload_var, command=self._on_memory_change,
        ).pack(side="left")

        tk.Label(card,
                 text="勾选：挂机 15 分钟没翻译就把模型从内存里放掉，可省约 97~238 MB；"
                      "代价是释放后下一次翻译多花约 0.15 秒（实测），基本察觉不到。",
                 bg=COLOR_CARD, fg=COLOR_SUBTEXT, font="ShunYiSmall",
                 anchor="w", justify="left", wraplength=560).pack(fill="x", padx=14, pady=(4, 2))
        tk.Label(card,
                 text="不勾选：模型一直留在内存里，翻译始终最快（适合经常随手翻译的人）。",
                 bg=COLOR_CARD, fg=COLOR_SUBTEXT, font="ShunYiSmall",
                 anchor="w", justify="left", wraplength=560).pack(fill="x", padx=14, pady=(0, 12))

    def _build_hotkey_section(self, parent) -> None:
        card = self._card(parent, "快捷键")
        tk.Label(card, text="按一下就能把刚打的中文变成译文。", bg=COLOR_CARD, fg=COLOR_SUBTEXT,
                 font="ShunYiSmall").pack(anchor="w", padx=14, pady=(0, 6))

        line = tk.Frame(card, bg=COLOR_CARD)
        line.pack(fill="x", padx=14)
        self._hotkey_entry = tk.Entry(line, font="ShunYiMono", justify="center", width=18,
                                      relief="solid", bd=1, readonlybackground="#FFFFFF")
        self._hotkey_entry.pack(side="left", ipady=4)
        self._hotkey_entry.configure(state="readonly")
        self._record_btn = ttk.Button(line, text="录制", width=8, command=self._toggle_record)
        self._record_btn.pack(side="left", padx=(8, 0))
        ttk.Button(line, text="恢复默认", width=10, command=self._reset_hotkey).pack(side="left", padx=(8, 0))

        self._hotkey_hint = tk.Label(card, text="要求：至少包含一个 Ctrl / Alt / Shift，再加一个主键（例如 Alt+J）。",
                                     bg=COLOR_CARD, fg=COLOR_SUBTEXT, font="ShunYiSmall",
                                     anchor="w", justify="left")
        self._hotkey_hint.pack(fill="x", padx=14, pady=(6, 2))
        tk.Label(card, text="说明：Win 键组合无法在窗口内录制（会被系统占用），如需使用请手工编辑配置文件写 windows+j。",
                 bg=COLOR_CARD, fg=COLOR_SUBTEXT, font="ShunYiSmall", anchor="w",
                 justify="left", wraplength=560).pack(fill="x", padx=14, pady=(0, 12))

        # 录制：在输入框和窗口上都监听按键
        for widget in (self._hotkey_entry, self.root):
            widget.bind("<KeyPress>", self._on_record_key)
            widget.bind("<Escape>", self._on_record_escape)

    # ---------------------------------------------------------- 队列轮询

    def _pump(self) -> None:
        """窗口线程的心跳：处理托盘来的指令 + 同步配置 + 刷新速度提示。"""
        try:
            while True:
                cmd, payload = self._queue.get_nowait()
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
        except queue.Empty:
            pass
        except Exception:  # noqa: BLE001
            log.exception("设置窗口处理指令出错")

        try:
            self._sync_from_config()
            self._update_speed_hint()
        except Exception:  # noqa: BLE001
            log.exception("设置窗口同步配置出错")

        self.root.after(120, self._pump)

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
        self._refresh_models()

    def _hide(self) -> None:
        # 收起窗口时也把提醒条收掉：否则下次打开会看到上次残留的过期提醒
        self._hide_notice()
        self.root.withdraw()

    # ---------------------------------------------------------- 配置读写

    def _load_from_config(self, force: bool = False) -> None:
        """把配置值填进界面控件（程序化刷新，不标记为"用户修改"）。"""
        cfg = config.load(force=force)
        self._last_config_snapshot = (cfg.get("hotkey"), cfg.get("quality"),
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
        snapshot = (cfg.get("hotkey"), cfg.get("quality"),
                    int(cfg.get("idle_unload_minutes", 0) or 0) > 0,
                    bool(cfg.get("notify_enabled", True)))
        if snapshot == self._last_config_snapshot:
            return
        self._load_from_config(force=True)
        self._refresh_models()

    def _mark_dirty(self) -> None:
        if self._syncing:
            return
        self._dirty = True
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

    def _pack_hint_bar(self, bar) -> None:
        """把一条提示条插到第一个区块之前（before 必须指向已经 pack 过的控件）。"""
        if bar.winfo_manager():
            return
        if self._section_wrappers:
            bar.pack(fill="x", padx=18, pady=(0, 10), before=self._section_wrappers[0])
        else:
            bar.pack(fill="x", padx=18, pady=(0, 10))

    def _update_dirty_bar(self) -> None:
        """显示/隐藏"有未保存修改"的提示条。"""
        if self._dirty:
            cfg = config.load()
            live_q = {"fast": "高速度", "quality": "高质量"}.get(cfg.get("quality"), cfg.get("quality"))
            text = (f"有未保存的修改。当前实际生效的是：快捷键 {format_hotkey(cfg.get('hotkey'))}"
                    f"、档位 {live_q}。点「保存」后生效。")
            self._dirty_label.configure(text=text)
            self._pack_hint_bar(self._dirty_bar)
        else:
            self._dirty_bar.pack_forget()

    # ---------------------------------------------------- 保存结果的窗口内提醒

    NOTICE_STYLE = {
        # level: (底色, 字色, 默认停留秒数)
        "ok": (COLOR_OK_BG, COLOR_OK, 3.0),
        "info": (COLOR_INFO_BG, COLOR_PRIMARY, 2.6),
        "err": (COLOR_ERR_BG, COLOR_ERR, 7.0),
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
        bg, fg, default_secs = self.NOTICE_STYLE.get(level, self.NOTICE_STYLE["ok"])
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

        self._notice_box.configure(bg=bg)
        self._notice_label.configure(text=text, bg=bg, fg=fg)
        # 贴在窗口底部：横向居中、纵向位于底部按钮行上方（小框底边锚定）
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
                messagebox.showinfo(
                    "基础模型正在下载",
                    "「中译英」基础模型正在下载中。\n\n"
                    "等它下载完成后，再点「中译日」的下载按钮即可 —— 那时只会下载日文模型，"
                    "不会再重复下载一遍基础模型。",
                    parent=self.root)
            else:
                messagebox.showinfo(
                    "正在下载中",
                    f"「{names}」正在下载，请等它完成后再试。",
                    parent=self.root)
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
        if not messagebox.askokcancel("开始下载模型", tip, parent=self.root):
            return

        cancel = threading.Event()
        self._cancel_events[key] = cancel
        self._active_downloads.update(targets)      # 登记占用，避免其他入口重复发起
        self._model_status[key] = {"status": "downloading", "percent": 0, "error": ""}
        ui = self._model_ui[key]
        ui["status"].configure(text="状态：正在下载 0%", fg=COLOR_PRIMARY)
        ui["action"].configure(text="取消", command=lambda k=key: self._cancel_download(k))
        ui["progress"].pack(fill="x", padx=14, pady=(0, 8))
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
            ui["progress"].pack(fill="x", padx=14, pady=(0, 8))
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

        if not messagebox.askokcancel("删除模型", "\n".join(lines), icon="warning", parent=self.root):
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
                messagebox.showwarning(
                    "删除未完成",
                    f"已清理 {removed_count} 处，释放 {freed/1024/1024:.0f} MB。\n\n"
                    "但以下文件没能删掉：\n" + "\n".join(detail[:8]) +
                    "\n\n常见原因是文件正被占用。可以退出软件后手动删除，"
                    "或在设置里再点一次「重试删除」。",
                    parent=self.root)
            else:
                self._model_status.pop(key, None)
                missing = models.missing_for_target(cfg.get("target_lang", "en"))
                if missing:
                    names = "、".join(next(i["title"] for i in MODEL_CARDS if i["key"] == m)
                                      for m in missing)
                    messagebox.showwarning(
                        "已删除，但有影响",
                        f"删除完成：共清理 {removed_count} 处，释放 {freed/1024/1024:.0f} MB。\n\n"
                        f"当前目标语言所需的「{names}」模型已缺失，"
                        "现在按快捷键会提示模型未就绪，请重新下载后再使用。",
                        parent=self.root)
                else:
                    messagebox.showinfo(
                        "删除完成",
                        f"删除完成：共清理 {removed_count} 处，释放 {freed/1024/1024:.0f} MB 磁盘空间。",
                        parent=self.root)
        except PermissionError:
            self._model_status[key] = {"status": "stale", "percent": 0, "error": "文件被占用"}
            messagebox.showerror(
                "无法删除",
                "模型文件正在被占用，删除没有执行。\n\n"
                "请先把目标语言切到另一个方向，或退出软件后手动删除该文件夹，"
                "然后再回到设置里点一次「重试删除」。",
                parent=self.root)
        except Exception as exc:  # noqa: BLE001
            log.exception("删除模型失败")
            self._model_status[key] = {"status": "stale", "percent": 0,
                                       "error": self._friendly_error(exc)[:20]}
            messagebox.showerror("删除失败", self._friendly_error(exc), parent=self.root)
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
        # 内存优化：勾选=空闲 15 分钟释放，不勾=常驻
        new_idle_minutes = 15 if self._idle_unload_var.get() else 0
        old_idle_minutes = int(cfg.get("idle_unload_minutes", 0) or 0)

        hotkey_changed = new_hotkey != cfg.get("hotkey")
        quality_changed = new_quality != cfg.get("quality")
        memory_changed = new_idle_minutes != old_idle_minutes
        # 「通知提醒」不在保存批量里：它在勾选/取消的那一刻就已经生效落盘
        # （见 _on_notify_change），避免"改了没保存 → 依然被提醒"的错觉

        if not hotkey_changed and not quality_changed and not memory_changed:
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

        if memory_changed:
            config.update(idle_unload_minutes=new_idle_minutes)
            self.service.reload_config()

        self._pending_quality = new_quality
        self._load_from_config(force=True)

        tip = "设置已保存并立即生效，无需重启软件。"
        if hotkey_changed:
            tip += ("\n提示：新快捷键已经生效。如果按下去没反应，说明这个组合被别的软件抢占了"
                    "（这类冲突无法自动检测），换一个组合重新设置即可。")
        if memory_changed:
            tip += ("\n内存优化已" + ("开启：挂机超过 15 分钟会自动释放模型内存。"
                                      if new_idle_minutes else "关闭：模型会一直留在内存里，翻译始终最快。"))
        # 窗口内轻量提醒：立即出现、到点自动消失，不打断操作
        self._show_notice(tip, "ok")

    def _on_cancel(self) -> None:
        if self._dirty and not messagebox.askokcancel(
                "放弃修改", "有未保存的修改，关闭后会丢弃，确定吗？", parent=self.root):
            return
        self._load_from_config(force=True)
        self._hide()

    def _on_window_close(self) -> None:
        """点右上角关闭：等同「取消」（有改动先确认），窗口只是隐藏，软件继续运行。"""
        if self._dirty and not messagebox.askokcancel(
                "放弃修改", "有未保存的修改，关闭后会丢弃，确定吗？", parent=self.root):
            return
        self._load_from_config(force=True)
        self._hide()

    def _on_restore_defaults(self) -> None:
        if not messagebox.askokcancel(
                "恢复默认设置",
                "将把快捷键恢复为 Alt+J、翻译档位恢复为「高速度」。\n\n"
                "注意：这只会改回窗口里的值，点「保存」后才真正生效。",
                icon="warning", parent=self.root):
            return
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
            self._idle_unload_var.set(True)
            self._notify_enabled_var.set(True)
        finally:
            self._syncing = False
        # 通知开关是"立即生效"型（不参与保存批量），所以恢复默认值也要立刻落盘
        config.update(notify_enabled=True)
        self.service.reload_config()
        if self._last_config_snapshot:
            self._last_config_snapshot = tuple(self._last_config_snapshot[:3]) + (True,)
        self._mark_dirty()
        messagebox.showinfo("已恢复默认值", "窗口里的设置已恢复为默认，别忘了点「保存」让它生效。",
                            parent=self.root)
