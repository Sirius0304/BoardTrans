# -*- coding: utf-8 -*-
"""下载 Argos 语言包（带进度显示）。用法: python download_model.py <url> <输出文件>"""
import sys
import time
import urllib.request


def download(url: str, out_path: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    start = time.time()
    with urllib.request.urlopen(req, timeout=120) as r:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        last = -1
        with open(out_path, "wb") as f:
            while True:
                chunk = r.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total:
                    pct = int(got * 100 / total)
                    if pct != last and pct % 5 == 0:
                        last = pct
                        speed = got / 1024 / 1024 / max(time.time() - start, 0.001)
                        print(f"  {pct:3d}%  {got/1024/1024:6.1f}/{total/1024/1024:.1f} MB  {speed:.2f} MB/s", flush=True)
    print(f"完成: {out_path}  ({got/1024/1024:.1f} MB, {time.time()-start:.1f}s)")


if __name__ == "__main__":
    download(sys.argv[1], sys.argv[2])
