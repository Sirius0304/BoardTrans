# -*- coding: utf-8 -*-
"""键译 - 单实例控制（Windows 命名内核对象）

解决的问题（2026-09-30 报障）：exe 可以被重复启动，系统托盘出现多个图标。

为什么会重复：
  程序启动路径上**没有任何"只能有一个实例"的判定**，每次双击都是一次全新进程：
  各自创建自己的托盘图标（pystray 每个进程一个）、各自尝试注册同一个全局热键
  （第二个必然失败，只留一条日志）、各自可能触发下载与写配置。托盘图标因此重复。

怎么解决（两个命名内核对象，不依赖任何第三方库）：
  1) 命名互斥体（Mutex）「Local\\<app>.SingleInstance」
     —— 只用来判断"是不是已经有一个实例在跑"。WaitForSingleObject/句柄是否
     ALREADY_EXISTS 是原子操作，天然没有竞态。
  2) 命名自动重置事件（Event）「Local\\<app>.ShowSettings」
     —— 第二个实例通过 SetEvent 通知第一个实例"有人想开界面"；第一个实例起一个
     等待线程收信号后打开设置窗口。自动重置事件的信号会**保留**到被取走，
     所以即使第一个实例还没开始等待也不会丢信号。

  用 Local\\ 前缀 = 只在本登录会话内互斥：多用户/快速切换用户互不干扰，
  每个会话一个托盘图标（这正是 Windows 上正确的语义）。

所有函数在非 Windows 或调用失败时都会安全降级（返回"不是第一个实例也不报错"的
保守结果由调用方决定），不会因为互斥体拿不到就让程序起不来。
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes

from .logger import get_logger

log = get_logger()

APP_ID = "BoardTrans"
MUTEX_NAME = f"Local\\{APP_ID}.SingleInstance"
EVENT_NAME = f"Local\\{APP_ID}.ShowSettings"

ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258

_IS_WINDOWS = sys.platform.startswith("win")
if _IS_WINDOWS:
    try:
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _kernel32.CreateMutexW.restype = wintypes.HANDLE
        _kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        _kernel32.CreateEventW.restype = wintypes.HANDLE
        _kernel32.CreateEventW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL,
                                          wintypes.LPCWSTR)
        _kernel32.OpenEventW.restype = wintypes.HANDLE
        _kernel32.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
        _kernel32.SetEvent.restype = wintypes.BOOL
        _kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
        _kernel32.WaitForSingleObject.restype = wintypes.DWORD
        _kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        _kernel32.CloseHandle.restype = wintypes.BOOL
        _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    except Exception as _exc:  # noqa: BLE001
        _kernel32 = None
        log.warning("单实例控制不可用（kernel32 加载失败）：%s", _exc)
else:
    _kernel32 = None


class InstanceLock:
    """持有命名互斥体；进程存活期间必须保持引用（否则句柄被回收、互斥失效）。"""

    def __init__(self, handle, already_running: bool, name: str):
        self._handle = handle
        self.already_running = already_running
        self.name = name

    @property
    def is_first(self) -> bool:
        """本进程是否为唯一实例。"""
        return not self.already_running

    def release(self) -> None:
        if self._handle and _kernel32 is not None:
            try:
                _kernel32.CloseHandle(self._handle)
            except Exception:  # noqa: BLE001
                pass
            self._handle = None


def acquire(app_id: str = APP_ID) -> InstanceLock:
    """尝试成为唯一实例。

    返回 InstanceLock：
      * is_first=True  → 本进程是第一个实例，请继续启动服务；
      * is_first=False → 已经有实例在跑，调用方应 signal_show() 后退出。
    非 Windows 或 kernel32 不可用时按"是第一个实例"处理（不阻断正常使用）。
    """
    name = f"Local\\{app_id}.SingleInstance"
    if _kernel32 is None:
        return InstanceLock(None, False, name)
    handle = _kernel32.CreateMutexW(None, False, name)
    err = ctypes.get_last_error()
    if not handle:
        log.warning("创建单实例互斥体失败（错误码 %d），按可重复启动处理", err)
        return InstanceLock(None, False, name)
    already = err == ERROR_ALREADY_EXISTS
    if already:
        # 已经有一个实例在跑：这个句柄也关掉，交由调用方通知对方后退出
        try:
            _kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            pass
        handle = None
    return InstanceLock(handle, already, name)


def signal_show(app_id: str = APP_ID) -> bool:
    """通知"已经在跑的那个实例"打开设置窗口（被第二实例调用）。"""
    if _kernel32 is None:
        return False
    name = f"Local\\{app_id}.ShowSettings"
    handle = _kernel32.OpenEventW(EVENT_MODIFY_STATE, False, name)
    if not handle:
        # 事件不存在说明对方还没开始等待（或已退出）：直接创建并置信号，
        # 自动重置事件会把信号保留到对方来取，不会丢。
        handle = _kernel32.CreateEventW(None, False, False, name)
    if not handle:
        return False
    try:
        ok = bool(_kernel32.SetEvent(handle))
        return ok
    finally:
        try:
            _kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            pass


def listen_show(app_id: str = APP_ID, callback=None, poll_ms: int = 1000) -> threading.Thread | None:
    """起一个守护线程等待"显示设置窗口"的信号；收到就调用 callback。

    为什么用线程等待而不是窗口消息：设置窗口的 Tk 跑在独立线程、托盘是 pystray
    自己的消息循环，用命名事件可以完全绕开两套 GUI 消息机制，最稳。
    poll_ms 只作为兜底心跳（事件本身是即时唤醒，不需要轮询消耗）。
    """
    if _kernel32 is None or callback is None:
        return None
    name = f"Local\\{app_id}.ShowSettings"
    handle = _kernel32.CreateEventW(None, False, False, name)
    if not handle:
        log.warning("创建「显示设置」事件失败，重复启动将无法唤起已有窗口")
        return None

    def _worker() -> None:
        while True:
            rc = _kernel32.WaitForSingleObject(handle, poll_ms)
            if rc == WAIT_OBJECT_0:
                try:
                    callback()
                except Exception:  # noqa: BLE001
                    log.exception("处理「显示设置窗口」信号出错")

    thread = threading.Thread(target=_worker, name="shunyi-instance-signal", daemon=True)
    thread.start()
    log.info("已启动单实例信号监听线程（重复启动时会把已有窗口唤到前台）")
    return thread


# ---------------------------------------------------------------- 进程/临时目录
# 为什么需要这一段（2026-09-30 实测）：
#   本程序用 PyInstaller **onefile** 打包，双击时是"引导父进程 + 应用子进程"两层。
#   第二个实例检测到已有实例后会立刻退出（这是我们要的），但 PyInstaller 的引导父进程
#   在子进程过早退出时**会长时间滞留不退出**（实测 25 秒以上仍存活，等待 0/3/6 秒
#   再退出都无效），表现为任务管理器里多一个 键译.exe、且它占着的解包临时目录不被清理。
#   因此第二实例在退出前主动结束"自己的引导父进程"（只在父进程就是同一个可执行文件时
#   才动手，python 入口下父进程是 shell/解释器，绝不会误杀），并在**第一个实例启动时**
#   清理属于自己、且不再被占用的陈旧解包目录 —— 双向兜底，不留垃圾。

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

if _IS_WINDOWS:
    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    _kernel32.Process32FirstW.restype = wintypes.BOOL
    _kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    _kernel32.Process32NextW.restype = wintypes.BOOL
    _kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD,
                                                     wintypes.LPWSTR,
                                                     ctypes.POINTER(wintypes.DWORD))
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)


def process_table() -> list:
    """返回 [(pid, 父pid, 映像名)]；失败返回空列表。"""
    if _kernel32 is None:
        return []
    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return []
    out = []
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    ok = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
    while ok:
        out.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
        ok = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    _kernel32.CloseHandle(snapshot)
    return out


def image_path(pid: int):
    """进程映像完整路径（需要权限不足时返回 None）。"""
    if _kernel32 is None:
        return None
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        _kernel32.CloseHandle(handle)


def parent_pid(pid: int = None):
    """取指定进程的父进程 PID（默认取自己）。"""
    pid = os.getpid() if pid is None else pid
    for p, pp, _n in process_table():
        if p == pid:
            return pp
    return None


def _unused_terminate_bootloader_parent() -> bool:
    """（已停用，勿在启动路径调用）

    原本想用它消除 PyInstaller onefile 引导父进程的滞留，但 2026-09-30 实测：
    在第二实例里调用它会**导致子进程自己不再退出**（比滞留更糟），因此回退。
    保留实现仅作记录；如需重启这条思路，必须先做"调用后子进程能否正常 os._exit"的验证。
    """
    if _kernel32 is None:
        return False
    parent = parent_pid()
    if not parent:
        return False
    parent_image = image_path(parent)
    me = os.path.abspath(sys.executable) if getattr(sys, "executable", None) else ""
    if not parent_image or not me or os.path.normcase(parent_image) != os.path.normcase(me):
        return False
    handle = _kernel32.OpenProcess(PROCESS_TERMINATE, False, parent)
    if not handle:
        return False
    try:
        return bool(_kernel32.TerminateProcess(handle, 0))
    finally:
        _kernel32.CloseHandle(handle)


def cleanup_stale_runtime_dirs(marker: str = "app_icon.ico") -> int:
    """清理属于本程序、且已不被任何进程使用的解包临时目录（_MEI*）。

    三条闸门同时满足才删（避免误删别人的临时目录、也避免删掉正在运行的实例自己的目录）：
      1) 目录形如 _MEI*，且位于当前用户 TEMP 下；
      2) 目录内存在本程序独有的标记文件（打包时放进 datas，见 键译_精简.spec）；
      3) 没有任何在跑的进程从该目录里运行（逐个比进程映像路径前缀）。
    返回成功删除的目录数；任何异常都吞掉（清理失败不影响启动）。
    """
    if not _IS_WINDOWS:
        return 0
    import glob
    import shutil  # noqa: PLC0415

    temp = os.environ.get("TEMP") or os.environ.get("TMP")
    if not temp or not os.path.isdir(temp):
        return 0
    live = set()
    for p, _pp, _n in process_table():
        path = image_path(p)
        if path:
            live.add(os.path.normcase(os.path.dirname(path)))
    removed = 0
    for path in glob.glob(os.path.join(temp, "_MEI*")):
        try:
            if not os.path.isfile(os.path.join(path, marker)):
                continue
            if os.path.normcase(os.path.abspath(path)) in live:
                continue          # 正在被某个实例使用，跳过
            if os.path.normcase(getattr(sys, "_MEIPASS", "") or "") == os.path.normcase(
                    os.path.abspath(path)):
                continue          # 就是自己现在的解包目录，绝不删
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.exists(path):
                removed += 1
        except Exception:  # noqa: BLE001
            continue
    return removed
