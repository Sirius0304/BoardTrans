# -*- coding: utf-8 -*-
"""实测几个新发现的候选源（含 Argos 的 DigitalOcean Spaces CDN）。"""
import os
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import USER_AGENT   # noqa: E402

CANDIDATES = [
    ("DigitalOcean Spaces CDN",
     "https://argos-opentech.nyc3.digitaloceanspaces.com/translate-zh_en-1_9.argosmodel"),
    ("DO Spaces（argospm 路径）",
     "https://argos-opentech.nyc3.digitaloceanspaces.com/argospm/translate-zh_en-1_9.argosmodel"),
    ("argos-net（当前默认）",
     "https://argos-net.com/v1/translate-zh_en-1_9.argosmodel"),
]
CHUNK = 1024 * 256
TEST_MB = 5


def head(url):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=25) as r:
            hs = {k.lower(): v for k, v in r.headers.items()}
        return time.time() - t0, hs
    except Exception as exc:  # noqa: BLE001
        return None, {"error": f"{type(exc).__name__}: {str(exc)[:60]}"}


def speed_test(url, mb=TEST_MB):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    dest = os.path.join(tempfile.gettempdir(), "shunyi_src_test.bin")
    got = 0
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            t0 = time.time()
            while got < mb * 1024 * 1024:
                data = r.read(CHUNK)
                if not data:
                    break
                f.write(data)
                got += len(data)
            elapsed = time.time() - t0
        return got, elapsed
    finally:
        if os.path.exists(dest):
            os.remove(dest)


def main():
    print("=" * 76)
    print("  候选源实测")
    print("=" * 76)
    results = {}
    for name, url in CANDIDATES:
        print(f"\n  {name}")
        handshake, hs = head(url)
        if "error" in hs:
            print(f"    [不可用] {hs['error']}")
            continue
        size = int(hs.get("content-length", 0) or 0)
        print(f"    [可用] 连接 {handshake*1000:.0f}ms  大小 {size/1024/1024:.1f} MB  "
              f"server={hs.get('server','-')}  accepts-ranges={hs.get('accept-ranges','-')}")
        try:
            got, elapsed = speed_test(url)
            sp = got / 1024 / 1024 / elapsed
            results[name] = sp
            print(f"    实测 {got/1024/1024:.1f} MB / {elapsed:.1f}s  =  {sp:.2f} MB/s"
                  f"    （71 MB 约需 {71/sp:.0f} 秒）")
        except Exception as exc:  # noqa: BLE001
            print(f"    测速失败: {type(exc).__name__} {str(exc)[:50]}")

    print()
    print("=" * 76)
    if results:
        best = max(results, key=results.get)
        print(f"  最快: {best}")
        for name, sp in sorted(results.items(), key=lambda x: -x[1]):
            print(f"    {name:<28} {sp:6.2f} MB/s")
    print("=" * 76)


if __name__ == "__main__":
    sys.exit(main())
