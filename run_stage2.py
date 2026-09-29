# -*- coding: utf-8 -*-
"""
瞬译 · 阶段二：托盘常驻完整版（含图形化设置界面）
==================================================

在原有「托盘 + 全局热键 + 离线翻译」基础上新增：
- 托盘右键菜单「设置…」打开图形化设置窗口
- 设置窗口内管理翻译模型（下载 / 进度 / 取消 / 删除）、切换翻译档位、录制快捷键
- 保存后立即生效并持久化

原有能力（快捷键取词、离线翻译、译文回写、托盘常驻、托盘快捷切换）全部保持不变。

运行：双击 2-托盘版.bat，或 python run_stage2.py
"""

from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import config, models, paths                        # noqa: E402
from app.console import safe_print                           # noqa: E402
from app.service import HotkeyService                        # noqa: E402
from app.settings_window import SettingsWindow               # noqa: E402
from app.tray import TrayApp                                 # noqa: E402


def main() -> int:
    # 0) 先声明 DPI 感知：必须在创建任何窗口（托盘/设置窗口）之前调用，
    #    否则 Windows 会把界面位图整体拉伸，文字和控件都会发虚。
    from app import ui_kit as ui                             # noqa: E402
    aware = ui.enable_dpi_awareness()

    safe_print("瞬译 正在启动...")
    safe_print("  数据目录: " + paths.data_dir())
    safe_print("  高 DPI 适配: " + aware)

    # 1) 全局热键 + 翻译服务（原有能力，未做破坏性改动）
    service = HotkeyService()

    # 2) 设置窗口控制器：独立线程，点「设置」时才真正创建界面
    settings = SettingsWindow(service)
    # 缺模型时，服务可以直接把用户引到设置窗口去下载
    service.settings_launcher = settings.open

    # 3) 托盘（会把 service 的通知接管为托盘气泡）
    tray = TrayApp(service)

    service.start()

    safe_print("  [OK] 服务已启动，程序驻留在系统托盘（右下角小图标）")
    safe_print("      右键托盘图标 →「设置…」可管理模型、翻译档位和快捷键")

    # 首次运行、模型不全时，主动打开设置窗口引导下载（不静默失败，也不后台偷偷下载）
    missing = models.missing_for_target(config.load().get("target_lang", "en"))
    if missing:
        names = "、".join(models.PACKAGES[k]["display"] for k in missing)
        safe_print(f"  检测到缺少翻译模型：{names}")
        safe_print("  正在打开设置窗口，请在「翻译模型」区域点「下载」")
        threading.Timer(2.0, settings.open).start()

    try:
        tray.run()               # 阻塞在托盘消息循环
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        settings.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
