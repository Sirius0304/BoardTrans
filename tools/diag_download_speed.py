# -*- coding: utf-8 -*-
"""
下载速度诊断：不猜，实测。

测四件事：
  1. 三个下载源的响应头（看是否走了 CDN / 缓存 / 代理）
  2. 各源建立连接的耗时（TLS 握手）
  3. 各源下载 10 MB 的实际速度
  4. f.flush() 的频率对下载速度的影响（我上次为断点续传加的"每块 flush"，可能拖慢了）

下载的临时文件测完即删。
"""
import os
import sys
import tempfile
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import PACKAGES, USER_AGENT   # noqa: E402

CHUNK = 1024 * 256          # 与 products 代码一致的块大小（256 KB）
TEST_MB = 10                # 每个源下载这么多 MB 用来测速


def head_info(url):
    """HEAD 请求，看响应头（Server / 缓存 / CDN / Content-Length）。"""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=30) as resp:
            handshake = time.time() - t0
            headers = {k.lower(): v for k, v in resp.headers.items()}
        return handshake, headers
    except Exception as exc:  # noqa: BLE001
        return None, {"error": str(exc)}


def download_chunk(url, size_mb, flush_every, dest):
    """下载指定 MB 数，每 flush_every 块 flush 一次（0 = 全程不主动 flush）。返回耗时。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    target = size_mb * 1024 * 1024
    got = 0
    flushes = 0
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
        t0 = time.time()
        block = 0
        while got < target:
            data = resp.read(CHUNK)
            if not data:
                break
            f.write(data)
            block += 1
            if flush_every and block % flush_every == 0:
                f.flush()
                flushes += 1
            got += len(data)
    elapsed = time.time() - t0
    return got, elapsed, flushes


def fmt_speed(mbs, seconds):
    return f"{mbs/1024/1024/seconds:6.2f} MB/s" if seconds > 0 else "   —   "


def main():
    url_official = PACKAGES["zh_en"]["url"]
    urls = {
        "官方源 argos-net.com": url_official,
        "GitHub 源": PACKAGES["zh_en"]["fallback_urls"][0],
        "ghfast.top 镜像": PACKAGES["zh_en"]["fallback_urls"][1],
    }

    print("=" * 76)
    print("  1. 各下载源的响应头（判断是否走了 CDN/缓存）")
    print("=" * 76)
    for name, url in urls.items():
        print(f"\n  {name}")
        print(f"    {url[:80]}")
        handshake, headers = head_info(url)
        if handshake is not None:
            print(f"    连接建立(含TLS握手): {handshake*1000:.0f} ms")
        interesting = ("server", "content-length", "x-cache", "age", "cf-cache-status",
                       "cf-ray", "via", "x-amz-cf-id", "error")
        for key in interesting:
            if key in headers:
                v = headers[key]
                print(f"    {key:<16} {v[:80]}")

    print()
    print("=" * 76)
    print("  2. 各源下载 10 MB 实测速度（测两次）")
    print("=" * 76)
    results = {}
    for name, url in urls.items():
        print(f"\n  {name}:")
        times = []
        for run in (1, 2):
            dest = os.path.join(tempfile.gettempdir(), "shunyi_speed_test.bin")
            try:
                got, elapsed, _fl = download_chunk(url, TEST_MB, 0, dest)
                speed = got / 1024 / 1024 / elapsed
                times.append(speed)
                print(f"    第 {run} 次: {elapsed:5.1f}s   {speed:6.2f} MB/s   (got {got/1024/1024:.1f} MB)")
            except Exception as exc:  # noqa: BLE001
                print(f"    第 {run} 次: 失败 - {type(exc).__name__} {str(exc)[:60]}")
            finally:
                if os.path.exists(dest):
                    os.remove(dest)
        if times:
            results[name] = sum(times) / len(times)
            print(f"    平均: {sum(times)/len(times):6.2f} MB/s")

    print()
    print("=" * 76)
    print("  3. flush 频率对速度的影响（同一官方源，10 MB）")
    print("=" * 76)
    for flush_every, label in ((1, "每 256 KB flush（当前实现）"),
                               (16, "每 4 MB flush（建议值）"),
                               (0, "全程不主动 flush（对照组）")):
        dest = os.path.join(tempfile.gettempdir(), "shunyi_flush_test.bin")
        try:
            got, elapsed, flushes = download_chunk(url_official, TEST_MB, flush_every, dest)
            speed = got / 1024 / 1024 / elapsed
            print(f"    {label:<32} {elapsed:5.1f}s  {speed:6.2f} MB/s   flush {flushes} 次")
        except Exception as exc:  # noqa: BLE001
            print(f"    {label:<32} 失败 - {type(exc).__name__}")
        finally:
            if os.path.exists(dest):
                os.remove(dest)

    print()
    print("=" * 76)
    print("  结论建议")
    print("=" * 76)
    if results:
        fastest = max(results, key=results.get)
        slowest = min(results, key=results.get)
        print(f"  最快的源: {fastest}（{results[fastest]:.2f} MB/s）")
        print(f"  最慢的源: {slowest}（{results[slowest]:.2f} MB/s）")
        if results[fastest] > results[slowest] * 1.5:
            print(f"  → 两个源速度差距 {results[fastest]/results[slowest]:.1f} 倍，"
                  "应该优先使用最快的源，或者让程序在官方源慢时自动切换。")


if __name__ == "__main__":
    sys.exit(main())
