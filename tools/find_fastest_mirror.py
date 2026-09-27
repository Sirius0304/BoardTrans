# -*- coding: utf-8 -*-
"""
找更快的模型下载源：先 HEAD 探活 + 看响应头，能通的再下载 5 MB 测速。
"""
import os
import sys
import tempfile
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import USER_AGENT   # noqa: E402

MODEL = "translate-zh_en-1_9.argosmodel"
CANDIDATES = [
    ("Argos 官方 CDN", f"https://data.argosopentech.com/argospm/v1/{MODEL}"),
    ("argos-net（当前官方）", f"https://argos-net.com/v1/{MODEL}"),
    ("ghproxy.com 加速", f"https://ghproxy.com/https://argos-net.com/v1/{MODEL}"),
    ("gh-proxy.com 加速", f"https://gh-proxy.com/https://argos-net.com/v1/{MODEL}"),
    ("mirror.ghproxy.com 加速", f"https://mirror.ghproxy.com/https://argos-net.com/v1/{MODEL}"),
    ("ghps.cc 加速", f"https://ghps.cc/https://argos-net.com/v1/{MODEL}"),
]

CHUNK = 1024 * 256
TEST_MB = 5


def head_info(url):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=30) as resp:
            handshake = time.time() - t0
            headers = {k.lower(): v for k, v in resp.headers.items()}
        return handshake, headers
    except Exception as exc:  # noqa: BLE001
        return None, {"error": f"{type(exc).__name__}: {str(exc)[:60]}"}


def download_test(url, size_mb, dest):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    target = size_mb * 1024 * 1024
    got = 0
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
        t0 = time.time()
        while got < target:
            data = resp.read(CHUNK)
            if not data:
                break
            f.write(data)
            got += len(data)
    return got, time.time() - t0


def main():
    print("=" * 76)
    print("  候选下载源探测（找最快的）")
    print("=" * 76)

    usable = []
    for name, url in CANDIDATES:
        print(f"\n  {name}")
        handshake, headers = head_info(url)
        if "error" in headers:
            print(f"    [不可用] {headers['error']}")
            continue
        size = int(headers.get("content-length", 0) or 0)
        cache = headers.get("cf-cache-status") or headers.get("x-cache") or "-"
        server = headers.get("server", "-")
        node = headers.get("cf-ray", "-")
        print(f"    [可用] 连接 {handshake*1000:.0f}ms  大小 {size/1024/1024:.0f} MB  "
              f"server={server}  cache={cache}  节点={node[-4:] if len(node)>3 else node}")
        usable.append((name, url, size))

    print()
    print("=" * 76)
    print("  可用的源：下载 5 MB 实测速度")
    print("=" * 76)
    results = {}
    for name, url, size in usable:
        dest = os.path.join(tempfile.gettempdir(), "shunyi_mirror_test.bin")
        try:
            got, elapsed = download_test(url, TEST_MB, dest)
            speed = got / 1024 / 1024 / elapsed
            results[name] = speed
            print(f"    {name:<22} {elapsed:5.1f}s   {speed:6.2f} MB/s")
        except Exception as exc:  # noqa: BLE001
            print(f"    {name:<22} 失败 - {type(exc).__name__} {str(exc)[:50]}")
        finally:
            if os.path.exists(dest):
                os.remove(dest)

    print()
    print("=" * 76)
    print("  推荐")
    print("=" * 76)
    if results:
        best = max(results, key=results.get)
        print(f"    最快的源：{best}（{results[best]:.2f} MB/s）")
        for name, speed in sorted(results.items(), key=lambda x: -x[1]):
            mark = "⭐" if name == best else "  "
            print(f"    {mark} {name:<22} {speed:6.2f} MB/s   71 MB 约 {71/speed*1.0:.0f} 秒")


if __name__ == "__main__":
    sys.exit(main())
