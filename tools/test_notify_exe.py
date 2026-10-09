# -*- coding: utf-8 -*-
"""在打包后的 exe 上验证「通知提醒」开关（用日志判定，不依赖气泡是否可见）。

原理：exe 启动 2.5 秒后会发一条「键译已启动」提醒。
  - 开关=关 → 统一入口会记一行「「通知提醒」已关闭，跳过提醒：键译已启动 ...」
  - 开关=开 → 不记这一行（气泡正常发出）
所以只要看新增日志里有没有这行，就能判定开关在打包环境里是否生效。

用法：F:\\shunyi\\.venv\\Scripts\\python.exe tools\\test_notify_exe.py
"""
import os
import subprocess
import sys
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from app import config, paths  # noqa: E402

EXE = os.path.join(PROJECT, "dist", "键译.exe")
LOG = paths.log_path()
MARK = "跳过提醒：键译已启动"


def log_size():
    return os.path.getsize(LOG) if os.path.exists(LOG) else 0


def log_since(offset):
    if not os.path.exists(LOG):
        return ""
    with open(LOG, "rb") as f:
        f.seek(offset)
        return f.read().decode("utf-8", "ignore")


def run_exe_once(seconds=8.0):
    """启动 exe，等它发出启动提醒后结束进程。"""
    proc = subprocess.Popen([EXE], cwd=os.path.dirname(EXE),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(seconds)
    subprocess.run(["taskkill", "/IM", "键译.exe", "/F"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        pass


def main():
    original = config.get("notify_enabled", True)
    print("exe      :", EXE, os.path.getsize(EXE), "字节")
    print("日志文件 :", LOG)

    results = []

    print("\n=== 1. 开关关闭 → 启动 exe，应当不打扰 ===")
    config.update(notify_enabled=False)
    mark = log_size()
    run_exe_once()
    segment = log_since(mark)
    hit = MARK in segment
    results.append(("开关关：exe 启动提醒被拦下（日志有跳过记录）", hit))
    print(f"  [{'通过' if hit else '失败'}] 新增日志里{'有' if hit else '没有'}「跳过提醒」记录")

    print("\n=== 2. 开关开启 → 启动 exe，应当正常提醒 ===")
    config.update(notify_enabled=True)
    mark = log_size()
    run_exe_once()
    segment = log_since(mark)
    hit2 = MARK in segment
    results.append(("开关开：不再跳过（气泡正常发出）", not hit2))
    print(f"  [{'通过' if not hit2 else '失败'}] 新增日志里{'有' if hit2 else '没有'}「跳过提醒」记录")

    config.update(notify_enabled=original)
    print("\n配置已还原为:", config.get("notify_enabled"))

    ok = all(v for _n, v in results)
    print("\n=== 结果：%s ===" % ("全部通过" if ok else "有失败项"))
    for name, v in results:
        print(f"  {'通过' if v else '失败'}  {name}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
