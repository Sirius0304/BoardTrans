# -*- coding: utf-8 -*-
"""下载 HuggingFace（含 hf-mirror 镜像）仓库中的指定文件。"""
import os
import sys
import urllib.request

MIRROR = "https://hf-mirror.com"


def download_file(repo: str, filename: str, out_dir: str):
    url = f"{MIRROR}/{repo}/resolve/main/{filename}"
    out_path = os.path.join(out_dir, filename)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as r, open(out_path, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1024 * 512)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total and got % (20 * 1024 * 1024) < 512 * 1024:
                print(f"    {filename}: {got/1024/1024:.0f}/{total/1024/1024:.0f} MB", flush=True)
    print(f"  OK {filename} ({got/1024/1024:.1f} MB)", flush=True)


if __name__ == "__main__":
    repo, out_dir = sys.argv[1], sys.argv[2]
    for name in sys.argv[3:]:
        download_file(repo, name, out_dir)
