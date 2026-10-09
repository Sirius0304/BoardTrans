# -*- coding: utf-8 -*-
"""
键译 · 自检工具
================

一键检查运行环境是否正常，并给出中文的修复建议。
遇到"按了快捷键没反应""程序起不来"这类问题，先运行这个工具。

运行方式：双击 checkup.bat，或命令行执行  python checkup.py
"""

from __future__ import annotations

import ctypes
import importlib
import os
import platform
import sys
import threading
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = "  [正常]"
WARN = "  [注意]"
FAIL = "  [异常]"

problems: list[str] = []
suggestions: list[str] = []


def head(title: str) -> None:
    print(f"\n{'-' * 62}\n{title}\n{'-' * 62}")


def ok(msg: str) -> None:
    print(f"{PASS} {msg}")


def warn(msg: str, advice: str = "") -> None:
    print(f"{WARN} {msg}")
    if advice:
        suggestions.append(advice)


def bad(msg: str, advice: str = "") -> None:
    print(f"{FAIL} {msg}")
    problems.append(msg)
    if advice:
        suggestions.append(advice)


# ---------------------------------------------------------------- 1 环境

def check_env() -> None:
    head("[1/8] 运行环境")
    print(f"        Python  {sys.version.split()[0]}")
    print(f"        系统    {platform.system()} {platform.release()}  {platform.machine()}")
    bits = 64 if sys.maxsize > 2 ** 32 else 32
    if bits == 64:
        ok(f"Python 为 {bits} 位")
    else:
        bad(f"Python 是 {bits} 位，翻译引擎只有 64 位版本",
            "请卸载后重新安装 64 位的 Python（官网 python.org 下载 Windows installer (64-bit)）")
    if sys.version_info < (3, 9):
        bad("Python 版本过低", "请安装 Python 3.10 或更高版本")


# ---------------------------------------------------------------- 2 依赖

def check_deps() -> None:
    head("[2/8] 依赖库")
    required = {
        "ctranslate2": "翻译推理引擎",
        "sentencepiece": "分词器",
        "keyboard": "全局快捷键",
        "pyperclip": "剪贴板读写",
        "PIL": "托盘图标绘制",
        "pystray": "系统托盘",
    }
    for mod, desc in required.items():
        try:
            m = importlib.import_module(mod)
            version = getattr(m, "__version__", "")
            ok(f"{mod:<15} {version:<10} {desc}")
        except Exception as exc:  # noqa: BLE001
            bad(f"{mod:<15} 缺失（{desc}）—— {exc}",
                "在项目目录执行： .venv\\Scripts\\pip install -r requirements.txt")

    # tkinter 是标准库，不在 requirements 里，但设置界面需要它
    try:
        import tkinter
        ok(f"{'tkinter':<15} {tkinter.TkVersion:<10} 图形设置界面")
    except Exception as exc:  # noqa: BLE001
        warn(f"tkinter 缺失（图形设置界面打不开，翻译功能不受影响）—— {exc}",
             "点托盘「设置」会弹出说明；解决方法是到 python.org 下载官方完整版 Python "
             "（安装时保持默认勾选 tcl/tk and IDLE）后重建 .venv，"
             "或者暂时直接编辑 config.json")


# ---------------------------------------------------------------- 3 目录

def check_paths() -> None:
    head("[3/8] 数据目录")
    try:
        from app import paths
        data = paths.data_dir()
        print(f"        数据目录: {data}")
        if paths.is_ascii(data):
            ok("路径为纯英文（翻译引擎要求，避免中文路径导致的崩溃）")
        else:
            bad("数据目录包含中文，翻译引擎可能无法加载模型",
                "把系统环境变量 LOCALAPPDATA 改成英文路径，或设置环境变量 SHUNYI_DATA_DIR 指向英文目录")
        probe = os.path.join(data, ".check")
        try:
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            ok("目录可读写")
        except OSError as exc:
            bad(f"目录不可写: {exc}", "请用管理员身份运行，或检查磁盘权限/剩余空间")
        print(f"        配置: {paths.config_path()}")
        print(f"        日志: {paths.log_path()}")
    except Exception as exc:  # noqa: BLE001
        bad(f"路径模块异常: {exc}")


# ---------------------------------------------------------------- 4 模型

def check_models() -> None:
    head("[4/8] 翻译模型")
    try:
        from app import models
        for key, info in models.PACKAGES.items():
            if models.is_installed(key):
                size = models.installed_size(key)
                ok(f"{info['display']}：已安装（{size/1024/1024:.0f} MB）")
            else:
                warn(f"{info['display']}：未安装（{info['size_text']}）",
                     "首次使用会自动下载；若下载失败，可手动下载模型包放到数据目录的 packages_cache 下")
    except Exception as exc:  # noqa: BLE001
        bad(f"模型检查异常: {exc}")


# ---------------------------------------------------------------- 5 配置

def check_config() -> None:
    head("[5/8] 配置文件")
    try:
        from app import config
        cfg = config.load()
        lang = {"en": "英文", "ja": "日文"}.get(cfg.get("target_lang"), cfg.get("target_lang"))
        quality = {"fast": "高速度", "quality": "高质量"}.get(cfg.get("quality"), cfg.get("quality"))
        print(f"        快捷键    {cfg.get('hotkey')}")
        print(f"        目标语言  {lang}")
        print(f"        翻译档位  {quality}")
        print(f"        翻译开关  {'开启' if cfg.get('enabled') else '关闭'}")
        print(f"        回写方式  {cfg.get('inject_mode')}")
        ok("配置文件可正常读取")
    except Exception as exc:  # noqa: BLE001
        bad(f"配置读取异常: {exc}", "删除数据目录下的 config.json 后重新运行（会恢复默认设置）")


# ---------------------------------------------------------------- 6 剪贴板

def check_clipboard() -> None:
    head("[6/8] 剪贴板读写")
    try:
        import pyperclip
        backup = pyperclip.paste() or ""
        token = f"__shunyi_check_{int(time.time())}__"
        pyperclip.copy(token)
        got = pyperclip.paste()
        pyperclip.copy(backup)
        if got == token:
            ok("剪贴板可正常读写（原内容已恢复）")
        else:
            bad("剪贴板写入后读回内容不一致",
                "可能有剪贴板管理软件（如 Ditto、剪贴板增强工具）在干扰，请暂时关闭后重试")
    except Exception as exc:  # noqa: BLE001
        bad(f"剪贴板异常: {exc}", "可能有其他程序占用剪贴板，稍后重试")


# ---------------------------------------------------------------- 7 快捷键

def check_hotkey() -> None:
    head("[7/8] 全局快捷键注册")
    try:
        import keyboard
        from app import config
        hotkey = str(config.load().get("hotkey") or "alt+j")
        handler = keyboard.add_hotkey(hotkey, lambda: None, suppress=True)
        keyboard.remove_hotkey(handler)
        ok(f"快捷键「{hotkey}」可以注册")
    except Exception as exc:  # noqa: BLE001
        bad(f"快捷键注册失败: {exc}",
            "该组合键可能已被其他软件占用。请编辑 config.json 把 hotkey 改成别的，例如 ctrl+alt+j")


# ---------------------------------------------------------------- 8 键盘模拟

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
WS_CHILD = 0x40000000
WS_BORDER = 0x00800000
ES_MULTILINE = 0x0004
CW_USEDEFAULT = 0x80000000
SW_SHOW = 5
PM_REMOVE = 0x0001
ASFW_ANY = -1
WM_SETTEXT, WM_GETTEXT, WM_GETTEXTLENGTH = 0x000C, 0x000D, 0x000E
WM_DESTROY = 0x0002

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


def _proc(hwnd, msg, wp, lp):
    if msg == WM_DESTROY:
        return 0
    return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wp, lp)


def check_sendinput() -> None:
    """验证"模拟键盘输入"是否被系统允许——这是取词与回写的关键。"""
    head("[8/8] 键盘模拟能力（取词/回写的关键）")
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_longlong
    proc = WNDPROC(_proc)

    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    wc.lpfnWndProc = proc
    wc.hInstance = hinst
    wc.lpszClassName = "BoardTransCheckup"
    user32.RegisterClassW(ctypes.byref(wc))

    hwnd = user32.CreateWindowExW(0, "BoardTransCheckup", "键译自检窗口",
                                  WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                                  CW_USEDEFAULT, CW_USEDEFAULT, 420, 140, None, None, hinst, None)
    edit = user32.CreateWindowExW(0, "EDIT", "",
                                  WS_CHILD | WS_VISIBLE | WS_BORDER | ES_MULTILINE,
                                  10, 10, 390, 90, hwnd, None, hinst, None)
    if not hwnd or not edit:
        bad("无法创建测试窗口", "请重启电脑后重试；若仍失败，可能是系统策略限制")
        return

    user32.AllowSetForegroundWindow(ASFW_ANY)
    user32.ShowWindow(hwnd, SW_SHOW)
    user32.UpdateWindow(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(edit)

    def pump(seconds: float):
        msg = wintypes.MSG()
        end = time.time() + seconds
        while time.time() < end:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.01)

    pump(0.4)
    user32.SendMessageW(edit, WM_SETTEXT, 0, "")

    from app.injector import send_unicode_text
    send_unicode_text("测试OK")
    pump(0.4)

    length = user32.SendMessageW(edit, WM_GETTEXTLENGTH, 0, 0)
    buf = ctypes.create_unicode_buffer(length + 2)
    user32.SendMessageW(edit, WM_GETTEXT, length + 1, buf)
    content = buf.value

    user32.PostMessageW(hwnd, WM_DESTROY, 0, 0)
    pump(0.2)

    if content == "测试OK":
        ok("模拟键盘输入正常（取词与译文回写可用）")
    else:
        bad(f"模拟键盘输入被系统拦截（实际收到 {content!r}）",
            "常见原因：① 安全软件（360/火绒/企业管控）拦截了键盘模拟；"
            "② 目标窗口以管理员身份运行而本程序不是；"
            "③ 在远程桌面/虚拟机里运行。建议：关闭安全软件的键盘防护，"
            "或右键以管理员身份运行本程序")


# ---------------------------------------------------------------- 主流程

def main() -> int:
    print("=" * 62)
    print("  键译 自检工具")
    print("=" * 62)

    for step in (check_env, check_deps, check_paths, check_models,
                 check_config, check_clipboard, check_hotkey, check_sendinput):
        try:
            step()
        except Exception as exc:  # noqa: BLE001
            bad(f"该项检查本身出错: {exc}")

    print("\n" + "=" * 62)
    if not problems:
        print("  结论：一切正常，可以正常使用。")
    else:
        print(f"  结论：发现 {len(problems)} 个问题：")
        for p in problems:
            print(f"    - {p}")
    if suggestions:
        print("\n  建议：")
        for i, s in enumerate(dict.fromkeys(suggestions), 1):
            print(f"    {i}. {s}")
    print("=" * 62)
    return 1 if problems else 0


if __name__ == "__main__":
    code = main()
    try:
        input("\n按回车键关闭...")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(code)
