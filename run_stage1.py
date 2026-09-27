# -*- coding: utf-8 -*-
"""
瞬译 · 阶段一：控制台最小可用版
================================

功能：全局快捷键（默认 Alt+J）取词 → 离线翻译（中→英，高速度档）→ 译文自动替换回原位。
运行：双击 run_stage1.bat，或在命令行执行  python run_stage1.py

本文件刻意保持"最小"：没有托盘、没有配置界面，只验证核心链路是否跑通。
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import config, models, paths                       # noqa: E402
from app.console import progress_line, safe_print           # noqa: E402
from app.service import HotkeyService                       # noqa: E402

BANNER = """
==============================================================
  瞬译 (ShunYi) · 阶段一 控制台版
  打字即翻译：中文 -> 英文，完全离线，不联网翻译
==============================================================
"""


def ensure_models() -> bool:
    """确保中→英模型就绪；缺失则联网下载（仅首次需要）。"""
    if models.is_installed("zh_en"):
        safe_print("  [OK] 中→英 模型已就绪")
        return True

    safe_print("\n  首次运行需要下载翻译模型（约 71 MB），请保持联网，下载完成后可永久离线使用。")
    safe_print(f"  模型会保存到: {models.model_dir('zh_en')}\n")
    try:
        models.ensure("zh_en", progress_cb=progress_line)
        safe_print("\n  [OK] 模型下载并安装完成")
        return True
    except Exception as exc:  # noqa: BLE001
        safe_print(f"\n  [X] 模型下载失败: {exc}")
        safe_print("  处理办法：")
        safe_print("    1) 检查网络能否打开 https://argos-net.com")
        safe_print(f"    2) 用浏览器手动下载 translate-zh_en-1_9.argosmodel，")
        safe_print(f"       放到目录 {paths.models_dir()} 后重新运行本程序")
        return False


def main() -> int:
    safe_print(BANNER)
    safe_print("  数据目录: " + paths.data_dir())

    # 阶段一固定为：中→英、高速度档（阶段二才开放切换）
    config.update(target_lang="en", quality="fast")

    if not ensure_models():
        return 1

    safe_print("\n  正在加载翻译引擎（首次约 2~5 秒）...")
    service = HotkeyService(notify=lambda title, msg: safe_print(f"\n  [{title}] {msg}"))
    service.start()
    service.preload()
    safe_print("  [OK] 引擎就绪")

    safe_print("""
--------------------------------------------------------------
  怎么用：
    1. 打开任意可输入文字的地方（记事本、微信、浏览器地址栏都行）
    2. 用中文打一句话，例如：今天天气不错
    3. 按快捷键  Alt + J
    4. 刚才那句话会原地变成英文译文

  注意：
    - 关闭这个窗口程序就退出了（阶段二会做成托盘常驻，关窗口不退出）
    - 如果没反应，请看本窗口里打印的提示信息
    - 按 Ctrl + C 退出程序
--------------------------------------------------------------
""")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        safe_print("\n  正在退出...")
        service.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
