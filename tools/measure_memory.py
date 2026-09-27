# -*- coding: utf-8 -*-
"""
运行时内存测量：按启动顺序逐个加载，看每个部分的实际内存成本。
用 Windows 的 GetProcessMemoryInfo 读工作集（也就是任务管理器里显示的那个数）。
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)
# 必须显式声明：GetCurrentProcess 返回的是 64 位句柄，
# 不声明的话 ctypes 会按 32 位 int 处理，句柄被截断，调用就会失败并返回 0
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                        ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
                                        wintypes.DWORD]
_psapi.GetProcessMemoryInfo.restype = wintypes.BOOL


def working_set_mb() -> float:
    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    ok = _psapi.GetProcessMemoryInfo(_kernel32.GetCurrentProcess(),
                                     ctypes.byref(counters), counters.cb)
    if not ok:
        raise OSError(f"读取内存失败，错误码 {ctypes.get_last_error()}")
    return counters.WorkingSetSize / 1024 / 1024


rows = []
last = [working_set_mb()]


def mark(label: str):
    time.sleep(0.15)
    now = working_set_mb()
    rows.append((label, now, now - last[0]))
    last[0] = now


mark("① Python 解释器 + 标准库（刚启动）")

import pystray          # noqa: E402
from PIL import Image, ImageDraw   # noqa: E402
mark("② + pystray / Pillow（托盘与图标）")

from app import config, paths, models   # noqa: E402
mark("③ + 项目基础模块（配置/路径/模型管理）")

from app.service import HotkeyService   # noqa: E402
mark("④ + 服务模块（连带 ctranslate2 / sentencepiece）")

import app.settings_window              # noqa: E402
mark("⑤ + 设置窗口模块（连带 tkinter / tcl-tk）")

from app.engine import TranslationEngine   # noqa: E402
engine = TranslationEngine()
engine.load_model("zh_en")
mark("⑥ + 加载中译英模型（翻译必备）")

engine.load_model("en_ja")
mark("⑦ + 再加载中译英→日模型（切日文后）")

engine.unload_all()
mark("⑧ + 调用 unload_all() 卸载全部模型")

print("=" * 70)
print("  运行时内存测量（工作集）")
print("=" * 70)
print(f"{'阶段':<44}{'当前':>9}{'增量':>9}")
print("-" * 70)
for label, now, delta in rows:
    sign = "+" if delta >= 0 else ""
    print(f"{label:<44}{now:>7.1f}M{sign}{delta:>8.1f}M")
print("-" * 70)

# 命令行传入基线时做对比
if len(sys.argv) > 1 and sys.argv[1] == "--json":
    import json
    print(json.dumps([{"label": l, "now": round(n, 1), "delta": round(d, 1)} for l, n, d in rows],
                     ensure_ascii=False))
