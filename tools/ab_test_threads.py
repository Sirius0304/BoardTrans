# -*- coding: utf-8 -*-
"""
严谨的 A/B 测速：同一时段内交替测试不同线程数，消除"网络波动"这个混淆因素。

如果多线程真的有效，同一轮里多线程一定快于单线程；
如果各策略数值在同一轮内相近，说明瓶颈在网络而非连接数，多线程没用。
"""
import os
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import PACKAGES, USER_AGENT, CHUNK_SIZE   # noqa: E402

URL = PACKAGES["zh_en"]["url"]
MB = 4                       # 每次测 4 MB，控制总时长
ROUNDS = 2


def single(url, mb=MB):
    dest = os.path.join(tempfile.gettempdir(), "ab_single.bin")
    got = 0
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            t0 = time.time()
            while got < mb * 1024 * 1024:
                d = r.read(CHUNK_SIZE)
                if not d:
                    break
                f.write(d)
                got += len(d)
            el = time.time() - t0
        return got / 1024 / 1024 / el
    finally:
        if os.path.exists(dest):
            os.remove(dest)


def multi(url, threads, mb=MB):
    dest = os.path.join(tempfile.gettempdir(), "ab_multi.bin")
    size = mb * 1024 * 1024
    got = [0]
    lock = threading.Lock()

    def worker(s, e):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Range": f"bytes={s}-{e}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if r.status != 206:
                    return
                with open(dest, "r+b") as f:
                    f.seek(s)
                    pos = s
                    while pos <= e:
                        d = r.read(min(CHUNK_SIZE, e - pos + 1))
                        if not d:
                            break
                        f.write(d)
                        pos += len(d)
                        with lock:
                            got[0] += len(d)
        except Exception:  # noqa: BLE001
            pass

    with open(dest, "wb") as f:
        f.truncate(size)
    per = size // threads
    t0 = time.time()
    ths = []
    for i in range(threads):
        s = i * per
        e = size - 1 if i == threads - 1 else (i + 1) * per - 1
        t = threading.Thread(target=worker, args=(s, e), daemon=True)
        ths.append(t)
        t.start()
    for t in ths:
        t.join()
    el = time.time() - t0
    if os.path.exists(dest):
        os.remove(dest)
    return got[0] / 1024 / 1024 / el


def main():
    strategies = [("单线程", lambda: single(URL)),
                  ("4 线程", lambda: multi(URL, 4)),
                  ("8 线程", lambda: multi(URL, 8)),
                  ("16 线程", lambda: multi(URL, 16))]

    print("=" * 72)
    print(f"  同时段交替 A/B 测速（每策略每次 {MB} MB，共 {ROUNDS} 轮）")
    print("=" * 72)

    collected = {name: [] for name, _ in strategies}
    for r in range(1, ROUNDS + 1):
        print(f"\n  ---- 第 {r} 轮 ----")
        for name, fn in strategies:
            try:
                sp = fn()
                collected[name].append(sp)
                print(f"    {name:<8} {sp:6.2f} MB/s")
            except Exception as exc:  # noqa: BLE001
                print(f"    {name:<8} 失败 {type(exc).__name__}")

    print()
    print("=" * 72)
    print("  汇总（同一轮内对比才有意义）")
    print("=" * 72)
    print(f"  {'策略':<10}{'各轮速度':<32}{'平均':>8}")
    for name, _ in strategies:
        vals = collected[name]
        if vals:
            seq = "  ".join(f"{v:.2f}" for v in vals)
            print(f"  {name:<10}{seq:<32}{sum(vals)/len(vals):>7.2f}")

    base = collected["单线程"]
    if base:
        avg_single = sum(base) / len(base)
        print()
        print(f"  以单线程均值 {avg_single:.2f} MB/s 为基准：")
        for name, _ in strategies[1:]:
            vals = collected[name]
            if vals:
                avg = sum(vals) / len(vals)
                delta = (avg / avg_single - 1) * 100 if avg_single else 0
                verdict = "有效" if delta > 15 else ("基本无差别" if abs(delta) <= 15 else "反而更慢")
                print(f"    {name:<8} {avg:.2f} MB/s  ({delta:+.0f}%)  → {verdict}")
    print("=" * 72)


if __name__ == "__main__":
    sys.exit(main())
