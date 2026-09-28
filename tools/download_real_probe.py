# -*- coding: utf-8 -*-
"""真实链路验证：用新算法重下 zh_en，与现有缓存做 sha256 比对，并记录末段耗时。

刻意下到临时文件名，不动现有可用缓存；测完即删。
用法：F:\\shunyi\\.venv\\Scripts\\python.exe tools\\_dl_real_probe.py
"""
import hashlib
import os
import sys
import time

sys.path.insert(0, r"F:\shunyi")

from app import models, paths  # noqa: E402

CACHE = paths.models_dir()
REAL = os.path.join(CACHE, "zh_en.argosmodel")
TMP = os.path.join(CACHE, "_tail_probe.argosmodel")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(4 * 1024 * 1024)
            if not b:
                break
            digest.update(b)
    return digest.hexdigest()


def main():
    info = models.PACKAGES["zh_en"]
    url = info["url"]

    print("缓存目录 :", CACHE)
    if os.path.exists(REAL):
        print("对照文件 : %s  %d 字节" % (REAL, os.path.getsize(REAL)))
    else:
        print("对照文件 : %s（不存在，将只验证大小）" % REAL)

    if os.path.exists(TMP):
        os.remove(TMP)

    total, supports = models._probe(url, timeout=30)
    print("探测结果 : total=%s supports_range=%s" % (total, supports))
    threads = max(2, min(models.DEFAULT_THREADS, total // (4 * 1024 * 1024)))
    step = max(models.SLICE_MIN_SIZE, total // (threads * models.SLICE_FACTOR))
    print("计划     : %d 线程，切片 %.2f MB × %d 片"
          % (threads, step / 1048576, (total + step - 1) // step))

    marks = {}
    samples = []
    t0 = time.time()
    last = [0.0, 0]

    def cb(label, got, tot):
        now = time.time()
        pct = got * 100.0 / tot
        for m in (25, 50, 75, 90, 95, 99, 100):
            if m not in marks and pct >= m:
                marks[m] = now - t0
        if now - last[0] >= 10.0:
            inst = (got - last[1]) / (now - last[0]) / 1048576
            samples.append((now - t0, pct, inst))
            print("  t=%5.0fs  %5.1f%%  瞬时 %5.2f MB/s" % (now - t0, pct, inst), flush=True)
            last[0] = now
            last[1] = got

    try:
        models._open_with_progress(url, TMP, cb, info["display"], timeout=60, cancel_event=None)
        elapsed = time.time() - t0
        print("\n下载完成 : %.1f s，平均 %.2f MB/s"
              % (elapsed, os.path.getsize(TMP) / elapsed / 1048576))
        for m in (50, 75, 90, 95, 99, 100):
            if m in marks:
                print("  到 %3d%% : %.1f s" % (m, marks[m]))
        if 95 in marks:
            print("  尾巴 95-100%%: %.1f s" % (elapsed - marks[95]))
        if 99 in marks:
            print("  尾巴 99-100%%: %.1f s" % (elapsed - marks[99]))
        got_sha = sha256(TMP)
        print("\n新文件 sha256 :", got_sha)
        if os.path.exists(REAL):
            real_sha = sha256(REAL)
            print("缓存   sha256 :", real_sha)
            print("一致性        :", "完全一致（新算法下载正确）" if got_sha == real_sha else "不一致！！")
        print("大小          : %d / 期望 %d" % (os.path.getsize(TMP), info["size"]))
    finally:
        if os.path.exists(TMP):
            os.remove(TMP)
            print("临时文件已删除")


if __name__ == "__main__":
    main()
