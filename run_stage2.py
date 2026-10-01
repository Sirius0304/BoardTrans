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
from app.logger import get_logger                            # noqa: E402
from app.service import HotkeyService                        # noqa: E402
from app.settings_window import FONT_FAMILY, SettingsWindow  # noqa: E402
from app.tray import TrayApp                                 # noqa: E402


def main() -> int:
    # 0) 先声明 DPI 感知：必须在创建任何窗口（托盘/设置窗口）之前调用，
    #    否则 Windows 会把界面位图整体拉伸，文字和控件都会发虚。
    from app import ui_kit as ui                             # noqa: E402
    aware = ui.enable_dpi_awareness()

    safe_print("瞬译 正在启动...")
    safe_print("  数据目录: " + paths.data_dir())
    safe_print("  高 DPI 适配: " + aware)

    # 0.5) 单实例判定：**必须在创建托盘图标之前做**，否则第二个进程会再挂一个托盘图标
    #      （2026-09-30 报障：exe 可重复启动 → 托盘出现多个图标）。
    from app import single_instance                          # noqa: E402
    lock = single_instance.acquire()
    if not lock.is_first:
        safe_print("  检测到「瞬译」已经在运行，正在唤起已存在的窗口…")
        # ASCII 标记行：便于自动化测试/日志排查不依赖中文编码（控制台编码可能是 GBK/U8）
        safe_print("  [ShunYi] already running -> requested the existing instance "
                   "to show its settings window")
        if single_instance.signal_show():
            safe_print("  已通知正在运行的实例打开设置窗口，本进程即将退出。")
        else:
            safe_print("  未能发送唤起信号：请在系统托盘右键图标 →「设置…」。")
        # 用 os._exit 硬退出：冻结环境（PyInstaller onefile）里 sys.exit() 之后的
        # 解释器收尾阶段会挂住不返回（实测第二个 exe 打印完提示后 30 秒仍不退出），
        # 而这个分支什么都没创建（互斥体句柄已在 acquire 里关掉），无需任何清理。
        # 退出前必须显式 flush，否则管道场景下提示信息会丢。
        #
        # 注意（2026-09-30 实测验过并回退）：曾在此处结束「自己的引导父进程」以消除
        # PyInstaller onefile 父进程的滞留，结果**导致第二实例的子进程不再退出**（更糟），
        # 已回退。当前保留的状态：检测与唤起有效、第二实例不建托盘图标、不做任何写入；
        # 残留的引导父进程见 docs/常见问题与排障.md「六」里的说明。
        try:
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        os._exit(0)
    safe_print("  单实例检查：本进程为唯一实例")
    # 顺手清理「上次重复启动」留下的解包临时目录
    #（只删带本程序标记、且没被任何进程占用的，见 single_instance.cleanup_stale_runtime_dirs）
    try:
        cleaned = single_instance.cleanup_stale_runtime_dirs()
        if cleaned:
            safe_print("  已清理 %d 个陈旧的运行时临时目录" % cleaned)
    except Exception:  # noqa: BLE001
        pass

    # 1) 全局热键 + 翻译服务（原有能力，未做破坏性改动）
    #    先报告一次界面文字渲染能力：Pillow + 字体文件都可用 → 界面文字走"灰度超采样"；
    #    任一缺失 → ui.TextLabel 自动退回原生 Tk 文字渲染（功能不受影响，只是有 ClearType
    #    彩边）。打包成 exe 后尤其需要这行日志来确认冻结环境里同样生效。
    try:
        ttf = ui.font_file(FONT_FAMILY, False) if ui.HAS_PIL else None
    except Exception:  # noqa: BLE001
        ttf = None
    get_logger().info("文字渲染: %s（Pillow=%s，字体文件=%s）",
                      "灰度超采样 3×" if ttf else "原生 Tk 回退",
                      "有" if ui.HAS_PIL else "无",
                      ttf[0] if ttf else "未找到")

    service = HotkeyService()

    # 2) 设置窗口控制器：独立线程，点「设置」时才真正创建界面
    settings = SettingsWindow(service)
    # 缺模型时，服务可以直接把用户引到设置窗口去下载
    service.settings_launcher = settings.open

    # 3) 托盘（会把 service 的通知接管为托盘气泡）
    tray = TrayApp(service)

    service.start()

    # 4) 监听「重复启动」信号：别的进程要求唤起窗口时，把设置窗口显示到最前
    single_instance.listen_show(callback=settings.open)

    # 3.5) 预热设置窗口（2026-10-01 修"首次打开明显卡顿"）：
    #      界面原本是懒加载的 —— 第一次点「设置」才现建窗口线程 + Tk + 整个界面
    #      （实测 270ms，而第二次只要 70ms）。这里在启动路径跑完后，后台先把界面
    #      建好（窗口保持隐藏），首次打开就只剩"显示"这一步。
    settings.prewarm()

    safe_print("  [OK] 服务已启动，程序驻留在系统托盘（右下角小图标）")
    safe_print("      右键托盘图标 →「设置…」可管理模型、翻译档位和快捷键")
    safe_print("      再次双击本程序不会重复启动，只会把设置窗口唤到前台")

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
