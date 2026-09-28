# -*- coding: utf-8 -*-
"""一次性对照测试：下载末段降速（静态均分 vs 动态领取）。

做法：
  1. 本地起一个支持 Range 的限速 HTTP 服务器（模拟"单连接速度波动极大"的现状）；
  2. 用【旧算法副本】和【新算法 _ranged_download】各下一次同一个 64 MB 文件；
  3. 记录进度时间线，对比 95% → 100% 的尾巴耗时，并校验 sha256 完全一致。

用法：F:\\shunyi\\.venv\\Scripts\\python.exe tools\\_dl_tail_test.py
"""
import hashlib
import http.server
import os
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, r"F:\shunyi")

from app import models  # noqa: E402

SIZE = 64 * 1024 * 1024
FAST_RATE = 2.0 * 1024 * 1024        # 正常连接：2.0 MB/s
SLOW_RATE = 0.2 * 1024 * 1024        # 慢连接：0.2 MB/s（差 10 倍）
SLOW_REQUESTS = {3, 7}               # 第 3、7 个请求按慢速（模拟两条掉队连接）
READ_CHUNK = 64 * 1024
THREADS = 16

print("生成 %d MB 测试数据..." % (SIZE // 1024 // 1024), flush=True)
DATA = bytearray()
state = 0x1234ABCD
while len(DATA) < SIZE:
    buf = bytearray(READ_CHUNK)
    for i in range(READ_CHUNK):
        state = (1103515245 * state + 12345) & 0xFFFFFFFF
        buf[i] = (state >> 16) & 0xFF
    DATA += buf
DATA = DATA[:SIZE]
SRC_SHA = hashlib.sha256(bytes(DATA)).hexdigest()
print("源文件 sha256 =", SRC_SHA[:16], "...", flush=True)


class ThrottledRangeHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    _counter = 0
    _counter_lock = threading.Lock()

    def log_message(self, *args):        # 静音
        pass

    def _pick_rate(self):
        with ThrottledRangeHandler._counter_lock:
            ThrottledRangeHandler._counter += 1
            idx = ThrottledRangeHandler._counter
        return SLOW_RATE if idx in SLOW_REQUESTS else FAST_RATE

    def _send_range(self, start, end):
        total = len(DATA)
        end = min(end, total - 1)
        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, total))
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Connection", "close")
        self.end_headers()
        rate = self._pick_rate()
        t0 = time.time()
        pos = start
        try:
            while pos <= end:
                n = min(READ_CHUNK, end - pos + 1)
                self.wfile.write(bytes(DATA[pos:pos + n]))
                pos += n
                drift = (pos - start) / rate - (time.time() - t0)
                if drift > 0:
                    time.sleep(drift)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            spec = rng.split("=", 1)[1]
            first, _, last = spec.partition("-")
            start = int(first)
            end = int(last) if last else len(DATA) - 1
            self._send_range(start, end)
        else:
            self._send_range(0, len(DATA) - 1)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(DATA)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def old_static_download(url, dest, total, progress_cb=None, threads=THREADS):
    """旧算法副本（仅用于对照，逐字复刻静态均分逻辑）。"""
    import urllib.request as ur

    if os.path.exists(dest):
        os.remove(dest)
    with open(dest, "wb") as f:
        f.truncate(total)
    per = total // threads
    done = [0]
    lock = threading.Lock()
    errors = []

    def worker(start, end):
        headers = {"User-Agent": models.USER_AGENT, "Range": "bytes=%d-%d" % (start, end)}
        try:
            req = ur.Request(url, headers=headers)
            with ur.urlopen(req, timeout=60) as resp:
                if resp.status != 206:
                    with lock:
                        errors.append("status %d" % resp.status)
                    return
                with open(dest, "r+b") as f:
                    f.seek(start)
                    pos = start
                    while pos <= end:
                        chunk = resp.read(min(models.CHUNK_SIZE, end - pos + 1))
                        if not chunk:
                            break
                        f.write(chunk)
                        pos += len(chunk)
                        with lock:
                            done[0] += len(chunk)
                            if progress_cb:
                                progress_cb("旧算法", done[0], total)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(str(exc)[:60])

    ws = []
    for i in range(threads):
        start = i * per
        end = total - 1 if i == threads - 1 else (i + 1) * per - 1
        t = threading.Thread(target=worker, args=(start, end))
        ws.append(t)
        t.start()
    for t in ws:
        t.join()
    if errors:
        raise IOError(errors[0])
    return total


def run_case(name, downloader, url, dest, total):
    marks = {}
    t0 = time.time()

    def cb(label, got, tot):
        elapsed = time.time() - t0
        pct = got * 100.0 / tot
        for m in (90, 95, 99, 100):
            if m not in marks and pct >= m:
                marks[m] = elapsed

    downloader(url, dest, total, cb)
    total_time = time.time() - t0
    for m in (90, 95, 99, 100):
        if m not in marks:
            # 回调有 0.2 秒节流，最后一段阈值可能没被单独报出来；
            # 用完成时刻补上（对两种算法同样处理，保证可比）
            marks[m] = total_time
    digest = hashlib.sha256()
    with open(dest, "rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            digest.update(b)
    ok = digest.hexdigest() == SRC_SHA
    print("\n=== {} ===".format(name), flush=True)
    print("  总耗时        : {:.1f} s".format(total_time))
    for m in (90, 95, 99, 100):
        t = marks.get(m)
        print("  到 {}%        : {}".format(m, ("{:.1f} s".format(t)) if t is not None else "n/a"))
    if 95 in marks and 100 in marks:
        tail = marks[100] - marks[95]
        print("  尾巴 95-100%  : {:.1f} s".format(tail))
    if 99 in marks and 100 in marks:
        print("  尾巴 99-100%  : {:.1f} s".format(marks[100] - marks[99]))
    print("  sha256 一致   : {}".format("是 OK" if ok else "否 失败"))
    return total_time, (marks.get(100, 0) - marks.get(95, 0)), ok


def main():
    srv = Server(("127.0.0.1", 0), ThrottledRangeHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/model.bin" % port
    print("限速服务器: %s（正常 %.1f MB/s，第 3/7 个请求 %.1f MB/s）"
          % (url, FAST_RATE / 1048576, SLOW_RATE / 1048576), flush=True)

    tmp = tempfile.mkdtemp(prefix="shunyi_tail_")
    dest = os.path.join(tmp, "out.bin")

    def old_runner(u, d, total, cb):
        old_static_download(u, d, total, progress_cb=cb)

    try:
        ThrottledRangeHandler._counter = 0
        old_t, old_tail, old_ok = run_case(
            "旧算法：静态均分区间（16 线程 × 4 MB）", old_runner, url, dest, SIZE)

        ThrottledRangeHandler._counter = 0
        total_size, supports = models._probe(url, timeout=30)
        step = max(models.SLICE_MIN_SIZE, total_size // (THREADS * models.SLICE_FACTOR))

        def new_runner(u, d, total, cb):
            models._open_with_progress(u, d, cb, "测试", timeout=60, cancel_event=None)

        new_t, new_tail, new_ok = run_case(
            "新算法：动态领取（%d 线程，切片 %.2f MB × %d 片）"
            % (THREADS, step / 1048576, (total_size + step - 1) // step),
            new_runner, url, dest, SIZE)

        print("\n================ 结论 ================")
        shrink = (1 - new_tail / old_tail) * 100 if old_tail > 0 else 0
        print("  尾巴 95-100%  : {:.1f} s -> {:.1f} s（缩短 {:.0f}%）".format(old_tail, new_tail, shrink))
        print("  总耗时        : {:.1f} s -> {:.1f} s".format(old_t, new_t))
        print("  sha256 校验   : 旧 {} / 新 {}".format(
            "通过" if old_ok else "失败", "通过" if new_ok else "失败"))
    finally:
        srv.shutdown()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
