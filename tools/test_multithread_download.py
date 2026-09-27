# -*- coding: utf-8 -*-
"""
验证多线程分块下载：
  1. 实际下载完整的 zh_en（71 MB），测真实速度
  2. 校验下载文件的完整性（大小 + zip 结构）
  3. 对比单线程（用之前的实测基线）
  4. 验证进度回调正常（单调递增、最终 100%）
"""
import os
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models, paths   # noqa: E402

KEY = "zh_en"
info = models.PACKAGES[KEY]
url = info["url"]
dest = os.path.join(paths.models_dir(), f"{KEY}.argosmodel")

print("=" * 72)
print("  多线程分块下载 · 实测验证")
print("=" * 72)
print(f"  目标: {info['display']}")
print(f"  地址: {url}")
print(f"  落地: {dest}")

# 先探测
t0 = time.time()
total, supports = models._probe(url)
print(f"\n  探测结果: 大小 {total/1024/1024:.1f} MB, 支持 Range = {supports}"
      f"（耗时 {time.time()-t0:.1f}s）")

if os.path.exists(dest):
    os.remove(dest)

progress_log = []


def cb(label, done, total_bytes):
    progress_log.append((done, total_bytes))


print(f"\n  开始下载（{models.DEFAULT_THREADS} 线程）...")
t0 = time.time()
got = models._open_with_progress(url, dest, cb, info["display"], timeout=120)
elapsed = time.time() - t0
speed = got / 1024 / 1024 / elapsed

print(f"  完成: {got/1024/1024:.1f} MB / {elapsed:.1f} 秒 = {speed:.2f} MB/s")

print()
print("=" * 72)
print("  验证")
print("=" * 72)
size_ok = abs(got - info["size"]) < info["size"] * 0.02
print(f"  文件大小: {os.path.getsize(dest)/1024/1024:.1f} MB（期望 {info['size']/1024/1024:.1f} MB）"
      f"  {'[通过]' if size_ok else '[不通过]'}")

try:
    zf = zipfile.ZipFile(dest)
    bad = zf.testzip()
    names = zf.namelist()
    print(f"  zip 结构: {len(names)} 个条目，CRC 校验 {'通过' if bad is None else f'失败({bad})'}")
    print(f"  包含关键文件: "
          f"model/model.bin={'model/model.bin' in names or any('model.bin' in n for n in names)}, "
          f"sentencepiece.model={'sentencepiece.model' in names}")
except Exception as exc:  # noqa: BLE001
    print(f"  [不通过] zip 打开失败: {type(exc).__name__} {exc}")

pcts = [int(d * 100 / t) if t else 0 for d, t in progress_log]
jumps = sum(1 for a, b in zip(pcts, pcts[1:]) if b < a)
print(f"  进度回调: 共 {len(progress_log)} 次，回跳 {jumps} 次，"
      f"最后百分比 {pcts[-1] if pcts else '-'}%  {'[通过]' if jumps == 0 else '[不通过]'}")

print()
print("=" * 72)
print("  速度对比")
print("=" * 72)
print(f"  本次（{models.DEFAULT_THREADS} 线程）: {speed:5.2f} MB/s   ——  71 MB 约 {71/speed:.0f} 秒")
print(f"  之前单线程实测基线        : 0.38 MB/s   ——  71 MB 约 187 秒")
if speed > 0.38:
    print(f"  提升: {(speed/0.38-1)*100:.0f}%")
print("=" * 72)
