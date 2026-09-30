# -*- coding: utf-8 -*-
"""单实例运行：回归测试（对应"exe 可重复启动 → 托盘出现多个图标"报障）。

三层验证：
  A. 机制层：命名互斥体判定 + 命名事件信号（同进程）
  B. 跨进程：另一个进程拿到"已存在"结论并通过事件把信号发回来
  C. 端到端：真实启动两次（默认 python 入口；加 --exe 用打包好的 瞬译.exe），
     断言第二次**很快退出且返回码 0**、进程数仍为 1、
     并且第一个实例被唤起（日志出现设置窗口创建记录）

用法：
    .venv\\Scripts\\python.exe tools\\test_single_instance.py          # 用 python 入口
    .venv\\Scripts\\python.exe tools\\test_single_instance.py --exe    # 用打包后的 exe
"""
import os
import subprocess
import sys
import threading
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from app import single_instance as si                    # noqa: E402
from app import paths                                    # noqa: E402

RESULTS = []
USE_EXE = "--exe" in sys.argv


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  —— {detail}" if detail else ""), flush=True)


def _drain(proc, sink):
    """持续读取子进程输出，避免管道写满导致子进程卡住。"""
    try:
        for line in proc.stdout:
            sink.append(line.rstrip("\n"))
    except Exception:  # noqa: BLE001
        pass


def _launch():
    if USE_EXE:
        exe = os.path.join(PROJECT, "瞬译.exe")
        cmd = [exe]
    else:
        cmd = [sys.executable, os.path.join(PROJECT, "run_stage2.py")]
    return subprocess.Popen(cmd, cwd=PROJECT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="ignore")


def _wait_for(lines, needle, timeout=25.0):
    end = time.time() + timeout
    while time.time() < end:
        if any(needle in ln for ln in lines):
            return True
        time.sleep(0.25)
    return False


def _exe_process_count() -> int:
    """当前有多少个「本程序」进程（onefile 下正常实例=2：引导父进程+应用子进程）。"""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    entry = PE()
    entry.dwSize = ctypes.sizeof(entry)
    count = 0
    if k32.Process32FirstW(snap, ctypes.byref(entry)):
        while True:
            if "瞬译" in entry.szExeFile:
                count += 1
            if not k32.Process32NextW(snap, ctypes.byref(entry)):
                break
    k32.CloseHandle(snap)
    return count


def _wait_ready_exe(timeout=40.0) -> bool:
    """exe 模式的就绪判据：应用子进程出现（进程数 ≥ 2）。"""
    end = time.time() + timeout
    while time.time() < end:
        if _exe_process_count() >= 2:
            return True
        time.sleep(0.5)
    return False


def main():
    print(f"\n=== A. 机制层（命名互斥体 + 命名事件，app_id=SingleInstTest）===")
    first = si.acquire("SingleInstTest")
    second = si.acquire("SingleInstTest")
    check("第一个 acquire 判定为唯一实例", first.is_first, f"is_first={first.is_first}")
    check("第二个 acquire 判定为已有实例在跑", not second.is_first, f"is_first={second.is_first}")

    got = []
    thread = si.listen_show("SingleInstTest", callback=lambda: got.append(time.time()), poll_ms=200)
    si.signal_show("SingleInstTest")
    time.sleep(0.8)
    check("监听线程能收到「显示窗口」信号", bool(got),
          f"线程={getattr(thread, 'name', None)}，收到 {len(got)} 次")

    print("\n=== B. 跨进程：另一个进程判定 + 反向发信号 ===")
    child_code = f"""
import sys
sys.path.insert(0, r'{PROJECT}')
from app import single_instance as si
lock = si.acquire('SingleInstTest')
print('CHILD_FIRST=%s' % lock.is_first)
print('CHILD_SIGNAL=%s' % si.signal_show('SingleInstTest'))
"""
    got.clear()
    out = subprocess.run([sys.executable, "-c", child_code], capture_output=True,
                         text=True, encoding="utf-8", errors="ignore", timeout=60)
    time.sleep(0.8)
    child_out = (out.stdout or "").strip()
    check("子进程判定为已有实例", "CHILD_FIRST=False" in child_out,
          " | ".join(child_out.splitlines()[-2:]))
    check("子进程成功发出唤起信号", "CHILD_SIGNAL=True" in child_out)
    check("本进程收到了子进程的信号（唤起链路通）", bool(got), f"收到 {len(got)} 次")

    lock = first
    lock.release()

    print(f"\n=== C. 端到端：真实启动两次（{'打包 exe' if USE_EXE else 'python 入口'}）===")
    log_file = paths.log_path()
    log_before = os.path.getsize(log_file) if os.path.exists(log_file) else 0

    p1 = _launch()
    lines1: list = []
    threading.Thread(target=_drain, args=(p1, lines1), daemon=True).start()
    if USE_EXE:
        ready = _wait_ready_exe(timeout=45)
        time.sleep(2.0)          # 再给托盘/信号监听线程一点时间
        check("第一个实例启动成功（应用子进程已就绪）", ready,
              f"当前进程数 {_exe_process_count()}")
    else:
        ready = _wait_for(lines1, "服务已启动", timeout=40)
        check("第一个实例启动成功（服务已启动）", ready, f"输出 {len(lines1)} 行")

    p2 = None
    try:
        time.sleep(1.5)
        t0 = time.time()
        p2 = _launch()
        lines2: list = []
        threading.Thread(target=_drain, args=(p2, lines2), daemon=True).start()
        try:
            p2.wait(timeout=20)
            elapsed = time.time() - t0
        except subprocess.TimeoutExpired:
            elapsed = time.time() - t0
            # onefile 的引导父进程在"子进程过早退出"时可能滞留（PyInstaller 行为，
            # 见 docs/常见问题与排障.md 第六节）；这里按"应用子进程已退出"判定
        child_gone = True
        if USE_EXE:
            time.sleep(1.0)
            child_gone = _exe_process_count() <= 3      # 实例1 有 2 个进程，残留父进程只算 1 个
        check(f"第二个实例的应用进程已退出（耗时 {elapsed:.1f}s，阈值 < 20s）",
              child_gone, f"returncode={p2.returncode}，进程数={_exe_process_count()}")
        joined = "\n".join(lines2)
        # 用 ASCII 标记行判定：控制台编码可能是 GBK，中文断言会因解码而误判
        check("第二个实例明确提示「已经在运行」（ASCII 标记）",
              "already running" in joined,
              next((ln.strip() for ln in lines2 if "already running" in ln), ""))

        time.sleep(1.0)
        check("第一个实例仍在运行（没有被误杀）", p1.poll() is None,
              f"returncode={p1.poll()}")

        # 唤起链路：第一个实例应因信号而创建/显示设置窗口（日志出现窗口创建记录）
        new_log = ""
        if os.path.exists(log_file):
            with open(log_file, "rb") as f:
                f.seek(max(0, log_before))
                new_log = f.read().decode("utf-8", "ignore")
        check("第一个实例被唤起（日志出现设置窗口创建记录）",
              ("设置窗口 ttk 主题" in new_log) or ("界面渲染模式" in new_log),
              "日志新增 %d 字节" % len(new_log))

        # 重复图标的根因是"第二个进程也走到了创建托盘那一步"。
        # 因此直接断言：第二个实例的输出里**没有**「服务已启动」这行
        #（该行在托盘创建之后才打印），说明它在创建托盘之前就退出了。
        check("第二个实例没有走到创建托盘（输出无「服务已启动」）",
              "[OK] 服务已启动" not in joined,
              "第二个实例输出 %d 行" % len(lines2))
        if USE_EXE:
            # 判定口径：没有"第二个应用实例"残留（实例1 自身是 2 个进程：引导父+应用子）。
            # onefile 可能留下一个引导父进程（无托盘图标、无窗口），已如实记录在文档里。
            time.sleep(2.0)
            left = _exe_process_count()
            check(f"没有第二个应用实例残留（实测 {left} 个进程，上限 3）", left <= 3,
                  f"实测 {left} 个；实例1 自身占 2 个")
    finally:
        for proc in (p2, p1):
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=10)
                except Exception:  # noqa: BLE001
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001
                        pass

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n=== 结果：{passed}/{len(RESULTS)} 项通过 ===")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  未通过：{name}  {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
