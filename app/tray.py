# -*- coding: utf-8 -*-
"""
瞬译 - 系统托盘

托盘菜单提供：
- 目标语言切换（英文 / 日文）
- 翻译档位切换（高速度 / 高质量）
- 翻译总开关
- 下载中日直连大模型（可选，提升日文质量）
- 打开配置文件 / 打开日志
- 退出

所有切换动作都会立即写回 config.json，重启后自动恢复。
"""

from __future__ import annotations

import os
import subprocess
import threading

import pystray
from pystray import Menu, MenuItem

from . import config, icon, paths
from .logger import get_logger

log = get_logger()

LANG_NAMES = {"en": "英文", "ja": "日文"}
QUALITY_NAMES = {"fast": "高速度", "quality": "高质量"}


class TrayApp:
    """托盘图标 + 菜单，驱动 HotkeyService。"""

    def __init__(self, service):
        self.service = service
        self.icon = pystray.Icon(
            "shunyi",
            icon.ICON_ENABLED,
            "瞬译 - 打字即翻译",
            menu=self._build_menu(),
        )
        service._notify = self.notify      # 把服务里的通知接到托盘气泡

    # ---------------- 通知 ----------------

    def notify(self, title: str, message: str) -> None:
        try:
            if message:
                self.icon.notify(message, title)
            else:
                self.icon.notify(title)
        except Exception:  # noqa: BLE001
            log.info("通知: %s %s", title, message)

    # ---------------- 菜单 ----------------

    def _build_menu(self) -> Menu:
        refresh = pystray.Menu.SEPARATOR
        return Menu(
            MenuItem(lambda _i: f"当前：{LANG_NAMES.get(config.get('target_lang'), '英文')} · "
                                f"{QUALITY_NAMES.get(config.get('quality'), '高速度')}",
                     None, enabled=False),
            MenuItem(lambda _i: f"快捷键：{config.get('hotkey')}", None, enabled=False),
            refresh,
            MenuItem(
                "目标语言",
                Menu(
                    MenuItem("英文", self._on_lang_en, checked=lambda _i: config.get("target_lang") == "en", radio=True),
                    MenuItem("日文", self._on_lang_ja, checked=lambda _i: config.get("target_lang") == "ja", radio=True),
                ),
            ),
            MenuItem(
                "翻译档位",
                Menu(
                    MenuItem("高速度", self._on_fast, checked=lambda _i: config.get("quality") == "fast", radio=True),
                    MenuItem("高质量", self._on_quality, checked=lambda _i: config.get("quality") == "quality", radio=True),
                ),
            ),
            # 注意：这里刻意不设 default=True。
            # 在 Windows 上，默认菜单项会在用户"双击托盘图标"时被触发，
            # 如果把它设成"启用翻译"，用户双击想看菜单时就会误关翻译。
            MenuItem("启用翻译", self._on_toggle, checked=lambda _i: bool(config.get("enabled"))),
            MenuItem("设置…", self._on_settings),
            refresh,
            MenuItem("打开配置文件", self._on_open_config),
            MenuItem("打开数据目录", self._on_open_folder),
            MenuItem("打开日志", self._on_open_log),
            MenuItem("使用说明", self._on_help),
            refresh,
            MenuItem("退出", self._on_quit),
        )

    # ---------------- 菜单动作 ----------------

    def _on_lang_en(self, _icon=None, _item=None):
        self.service.set_target_lang("en")
        self._refresh()

    def _on_lang_ja(self, _icon=None, _item=None):
        self.service.set_target_lang("ja")
        self._refresh()

    def _on_fast(self, _icon=None, _item=None):
        self.service.set_quality("fast")
        self._refresh()

    def _on_quality(self, _icon=None, _item=None):
        self.service.set_quality("quality")
        self._refresh()

    def _on_toggle(self, _icon=None, _item=None):
        enabled = self.service.toggle_enabled()
        self.icon.icon = icon.ICON_ENABLED if enabled else icon.ICON_DISABLED
        self._refresh()

    def _on_settings(self, _icon=None, _item=None):
        """打开设置窗口（窗口线程由 SettingsWindow 自己管理，这里只发一个请求）。"""
        self.service._open_settings()

    def _on_open_config(self, _icon=None, _item=None):
        config.load()
        self._open_path(paths.config_path())

    def _on_open_folder(self, _icon=None, _item=None):
        self._open_path(paths.data_dir())

    def _on_open_log(self, _icon=None, _item=None):
        if not os.path.exists(paths.log_path()):
            self.notify("暂无日志", "还没有产生日志文件")
            return
        self._open_path(paths.log_path())

    def _on_help(self, _icon=None, _item=None):
        hotkey = config.get("hotkey")
        self.notify(f"用法：打字后按 {hotkey}", "译文会替换你刚输入的内容。托盘菜单可切换语言与档位。")

    def _on_quit(self, _icon=None, _item=None):
        try:
            self.service.stop()
        finally:
            self.icon.stop()

    @staticmethod
    def _open_path(path: str) -> None:
        """用系统默认程序打开文件或目录（不用 shell 启动新进程时也不会误触发命令）。"""
        try:
            os.startfile(path)   # noqa: S606  Windows 专用
        except Exception:  # noqa: BLE001
            try:
                subprocess.Popen(["explorer", os.path.dirname(path)])
            except Exception:  # noqa: BLE001
                pass

    def _refresh(self) -> None:
        """刷新菜单显示（pystray 会在下次展开时重新求值，这里主要更新图标）。"""
        enabled = bool(config.get("enabled"))
        self.icon.icon = icon.ICON_ENABLED if enabled else icon.ICON_DISABLED

    # ---------------- 运行 ----------------

    def run(self) -> None:
        """阻塞运行托盘（主线程）。"""
        # 首次使用提示
        threading.Timer(2.5, self._welcome).start()
        self.icon.run()

    def _welcome(self) -> None:
        hotkey = config.get("hotkey")
        self.notify("瞬译已启动", f"打字后按 {hotkey} 即可翻译；托盘菜单可切换语言与档位。")
