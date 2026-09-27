# -*- coding: utf-8 -*-
"""
两个方向的验证：
  A. ghps.cc 的"17 MB/s"是真是假（HEAD 显示 0 字节，很可疑）
  B. 官方源是否支持 Range 分块下载 —— 若支持，用多线程并发下载可以显著提速，
     而且不依赖第三方镜像的稳定性
"""
import os
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import PACKAGES, USER_AGENT   # noqa: E402

OFFICIAL = PACKAGES["zh_en"]["url"]
GHPS = "https://ghps.cc/" + OFFICIAL
CHUNK = 1024 * 256


def head(url):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {k.lower(): v for k, v in resp.headers.items()}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {str(exc)[:70]}"}


def real_download_bytes(url, want_mb=2, dest=None):
    """真实下载，返回 (实际字节数, 耗时, 内容开头 32 字节)。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    got = 0
    head_bytes = b""
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
        t0 = time.time()
        while got < want_mb * 1024 * 1024:
            data = resp.read(CHUNK)
            if not data:
                break
            if not head_bytes:
                head_bytes = data[:32]
            f.write(data)
            got += len(data)
        elapsed = time.time() - t0
    return got, elapsed, head_bytes


def ranged_worker(url, dest, start, end, results, idx, stop_flag):
    """下载 [start, end] 区间并写入文件的对应位置。"""
    headers = {"User-Agent": USER_AGENT, "Range": f"bytes={start}-{end}"}
    req = urllib.request.Request(url, headers=headers)
    got = 0
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            if resp.status != 206:
                results[idx] = ("不支持Range", got)
                return
            with open(dest, "r+b") as f:
                f.seek(start)
                while got < (end - start + 1) and not stop_flag.is_set():
                    data = resp.read(CHUNK)
                    if not data:
                        break
                    f.write(data)
                    got += len(data)
        results[idx] = ("ok", got)
    except Exception as exc:  # noqa: BLE001
        results[idx] = (f"{type(exc).__name__}: {str(exc)[:40]}", got)


def ranged_download(url, dest, size, threads):
    """多线程分块下载，返回 (总耗时, 各线程结果)。"""
    with open(dest, "wb") as f:
        f.truncate(size)
    per = size // threads
    results = [None] * threads
    stop = threading.Event()
    t0 = time.time()
    ths = []
    for i in range(threads):
        start = i * per
        end = size - 1 if i == threads - 1 else (i + 1) * per - 1
        t = threading.Thread(target=ranged_worker, args=(url, dest, start, end, results, i, stop),
                             daemon=True)
        ths.append(t)
        t.start()
    for t in ths:
        t.join(timeout=180)
    elapsed = time.time() - t0
    total = sum(r[1] for r in results if r)
    return elapsed, total, results


def main():
    tmp = os.path.join(tempfile.gettempdir(), "shunyi_mirror_check.bin")

    print("=" * 76)
    print("  A. ghps.cc 到底能不能用（它的 17 MB/s 很可疑）")
    print("=" * 76)
    h = head(GHPS)
    print(f"  HEAD 响应头: {h.get('content-length', '(无 content-length)')} "
          f"server={h.get('server', '-')} type={h.get('content-type', '-')}")
    try:
        got, elapsed, head_bytes = real_download_bytes(GHPS, want_mb=2, dest=tmp)
        print(f"  实际下载 2 秒测试: 收到 {got} 字节, 耗时 {elapsed:.1f}s")
        print(f"  内容开头: {head_bytes[:24]!r}")
        if got < 1024 * 100:
            print("  [结论] 基本没下到东西 —— 那个 17 MB/s 是假的，不能用")
        else:
            print(f"  [结论] 可用，速度 {got/1024/1024/max(elapsed,0.01):.2f} MB/s")
    except Exception as exc:  # noqa: BLE001
        print(f"  [结论] 不可用: {type(exc).__name__} {str(exc)[:60]}")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    print()
    print("=" * 76)
    print("  B. 官方源是否支持 Range 分块 + 多线程并发下载")
    print("=" * 76)
    h = head(OFFICIAL)
    ar = h.get("accept-ranges", "(未声明)")
    size = int(h.get("content-length", 0) or 0)
    print(f"  accept-ranges: {ar}")
    print(f"  文件大小: {size/1024/1024:.1f} MB")
    print()

    test_size = 8 * 1024 * 1024      # 每轮下 8 MB 做对比
    print(f"  对比：单线程 vs 多线程（各下 {test_size/1024/1024:.0f} MB）")
    print()
    for threads in (1, 4, 8):
        if os.path.exists(tmp):
            os.remove(tmp)
        if threads == 1:
            try:
                got, elapsed, _hb = real_download_bytes(OFFICIAL, want_mb=8, dest=tmp)
                speed = got / 1024 / 1024 / elapsed
                print(f"    {threads} 线程: {elapsed:6.1f}s   {speed:5.2f} MB/s   "
                      f"（收到 {got/1024/1024:.1f} MB）")
            except Exception as exc:  # noqa: BLE001
                print(f"    {threads} 线程: 失败 {type(exc).__name__}")
        else:
            try:
                elapsed, total, res = ranged_download(OFFICIAL, tmp, test_size, threads)
                speed = total / 1024 / 1024 / elapsed
                statuses = {r[0] for r in res if r}
                print(f"    {threads} 线程: {elapsed:6.1f}s   {speed:5.2f} MB/s   "
                      f"（收到 {total/1024/1024:.1f} MB, 状态 {statuses}）")
            except Exception as exc:  # noqa: BLE001
                print(f"    {threads} 线程: 失败 {type(exc).__name__} {str(exc)[:40]}")
        if os.path.exists(tmp):
            os.remove(tmp)

    print()
    print("=" * 76)
    print("  说明")
    print("=" * 76)
    print("  如果多线程明显快于单线程，说明服务器支持 Range 且不限制并发 ——")
    print("  那么用多线程下载是**最稳的提速方案**：不依赖第三方镜像，源再慢也能榨出带宽。")


if __name__ == "__main__":
    sys.exit(main())
