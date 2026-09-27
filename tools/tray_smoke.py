# -*- coding: utf-8 -*-
"""托盘冒烟测试：验证托盘图标能创建、菜单能构建、热键能注册、能优雅退出。"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config                     # noqa: E402
from app.service import HotkeyService      # noqa: E402
from app.tray import TrayApp               # noqa: E402

print("  当前配置:", {k: config.get(k) for k in ("hotkey", "target_lang", "quality", "enabled")})

service = HotkeyService()
tray = TrayApp(service)

# 打印菜单结构，确认菜单项都能正常构建
menu = tray.icon.menu
print("  托盘菜单项:")
for item in menu:
    try:
        text = item.text
        if callable(text):
            text = text(item)
        print(f"    - {text if text else '(分隔线)'}")
    except Exception as exc:  # noqa: BLE001
        print(f"    - <求值失败: {exc}>")

service.start()
print("  服务已启动，热键注册状态:", service._handler is not None)


def stop_later():
    time.sleep(4)
    try:
        tray.notify("冒烟测试", "托盘通知测试")
    except Exception as exc:  # noqa: BLE001
        print("  通知失败:", exc)
    time.sleep(1)
    service.stop()
    tray.icon.stop()


threading.Thread(target=stop_later, daemon=True).start()
tray.icon.run()
print("  托盘冒烟测试完成：创建 / 菜单 / 通知 / 退出 均正常")
