# -*- coding: utf-8 -*-
"""
启动速度对比：精简前(50.1 MB) vs 精简后(34.1 MB)

测法：启动 exe 后轮询日志，直到出现"服务已启动"，
这就是"双击到能用"的实际耗时（onefile 模式下启动要先解压，所以这个数字很关键）。
"""
import os
import subprocess
import sys
import time

LOG = os.path.join(os.environ["LOCALAPPDATA"], "ShunYi", "logs", "shunyi.log")
CANDIDATES = [
    ("精简后 (34.1 MB)", r"F:\shunyi\dist\瞬译.exe"),
    ("精简前 (50.1 MB)", r"F:\瞬译_备份_20260927\瞬译_exe_精简前_50.1MB.exe"),
]
RUNS = 3


def read_log() -> str:
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def kill(name: str):
    try:
        subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True, timeout=20)
    except Exception:  # noqa: BLE001
        pass


def measure(exe: str, runs: int = RUNS):
    name = os.path.basename(exe)
    results = []
    for i in range(runs):
        # 清空日志，确保读到的是本次启动
        try:
            with open(LOG, "w", encoding="utf-8"):
                pass
        except OSError:
            pass
        t0 = time.time()
        try:
            subprocess.Popen([exe], cwd=os.path.dirname(exe))
        except Exception as exc:  # noqa: BLE001
            print(f"    启动失败: {exc}")
            return []
        ok = False
        while time.time() - t0 < 60:
            if "服务已启动" in read_log():
                ok = True
                break
            time.sleep(0.05)
        elapsed = time.time() - t0
        results.append(elapsed if ok else float("nan"))
        kill(name)
        time.sleep(1.2)
        print(f"    第 {i+1} 次: {elapsed:.2f}s {'(成功)' if ok else '(超时)'}")
    return results


def main():
    print("=" * 66)
    print("  启动速度对比（双击 -> 托盘就绪）")
    print("=" * 66)
    summary = []
    for label, exe in CANDIDATES:
        if not os.path.exists(exe):
            print(f"\n  {label}: 文件不存在，跳过 ({exe})")
            continue
        size = os.path.getsize(exe) / 1024 / 1024
        print(f"\n  {label}  实际 {size:.1f} MB")
        times = measure(exe)
        valid = [t for t in times if t == t]     # 过滤 nan
        if valid:
            avg = sum(valid) / len(valid)
            summary.append((label, size, avg, min(valid), max(valid)))
            print(f"    平均 {avg:.2f}s   最快 {min(valid):.2f}s   最慢 {max(valid):.2f}s")

    print()
    print("=" * 66)
    print(f"  {'版本':<20}{'体积':>10}{'平均启动':>12}")
    print("-" * 66)
    for label, size, avg, _mn, _mx in summary:
        print(f"  {label:<20}{size:>8.1f} MB{avg:>11.2f}s")
    if len(summary) == 2:
        d_size = summary[0][1] - summary[1][1]
        d_time = summary[0][2] - summary[1][2]
        print("-" * 66)
        print(f"  {'差异':<20}{d_size:>+8.1f} MB{d_time:>+11.2f}s")
    print("=" * 66)


if __name__ == "__main__":
    sys.exit(main())
