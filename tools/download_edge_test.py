# -*- coding: utf-8 -*-
"""下载器边界路径测试（配合动态切片改造后的回归）。

覆盖三条路径：
  A. 取消     —— 下载中途取消 → 抛 DownloadCancelled，半成品被删除
  B. 不支持Range —— 服务器只回 200 → 自动回退单线程，最终文件正确
  C. 响应提前结束 —— 服务器少发字节 → 判为失败（不给"看似完整实则残缺"的文件）

用法：F:\\shunyi\\.venv\\Scripts\\python.exe tools\\_dl_edge_test.py
"""
import hashlib
import http.server
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, r"F:\shunyi")

from app import models  # noqa: E402

SIZE = 16 * 1024 * 1024
READ_CHUNK = 64 * 1024
RATE = 1.0 * 1024 * 1024          # 1 MB/s，便于中途取消

print("生成 %d MB 测试数据..." % (SIZE // 1048576), flush=True)
DATA = bytes((i * 37 + 11) & 0xFF for i in range(SIZE))
SRC_SHA = hashlib.sha256(DATA).hexdigest()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    mode = "range"                 # range / norange / short / flaky
    counter = 0
    counter_lock = threading.Lock()

    def log_message(self, *a):
        pass

    def _send(self, start, end, status=206, short=False, stall=0.0):
        end = min(end, len(DATA) - 1)
        length = end - start + 1
        if status == 206:
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, len(DATA)))
        else:
            self.send_response(200)
        body_len = length // 2 if short else length
        self.send_header("Content-Length", str(body_len))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Connection", "close")
        self.end_headers()
        sent = 0
        t0 = time.time()
        try:
            if stall and sent == 0:
                time.sleep(stall)          # 模拟"连上后长时间没有数据"
            while sent < body_len:
                n = min(READ_CHUNK, body_len - sent)
                self.wfile.write(DATA[start + sent:start + sent + n])
                sent += n
                drift = sent / RATE - (time.time() - t0)
                if drift > 0:
                    time.sleep(drift)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        rng = self.headers.get("Range")
        mode = Handler.mode
        with Handler.counter_lock:
            Handler.counter += 1
            idx = Handler.counter
        if mode in ("flaky", "slowstall") and rng:
            # 只让第 3 个请求卡住一次，之后的请求恢复正常
            spec = rng.split("=", 1)[1]
            first, _, last = spec.partition("-")
            stall = 4.0 if mode == "flaky" else 40.0
            self._send(int(first), int(last) if last else len(DATA) - 1,
                       status=206, stall=(stall if idx == 3 else 0.0))
            return
        if mode == "norange" or not rng:
            self._send(0, len(DATA) - 1, status=200)
            return
        spec = rng.split("=", 1)[1]
        first, _, last = spec.partition("-")
        start = int(first)
        end = int(last) if last else len(DATA) - 1
        self._send(start, end, status=206, short=(mode == "short"))

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(DATA)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_server():
    srv = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/model.bin" % srv.server_address[1]


def sha_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def case_cancel(url, dest):
    print("\n[A] 取消路径", flush=True)
    ev = threading.Event()
    box = {}

    def run():
        try:
            models._open_with_progress(url, dest, None, "取消测试", timeout=30, cancel_event=ev)
            box["result"] = "意外成功"
        except models.DownloadCancelled:
            box["result"] = "DownloadCancelled"
        except Exception as exc:  # noqa: BLE001
            box["result"] = "%s: %s" % (type(exc).__name__, exc)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(2.0)
    ev.set()
    t.join(timeout=30)
    left = os.path.exists(dest)
    ok = box.get("result") == "DownloadCancelled" and not left
    print("  结果: %s / 半成品残留: %s → %s"
          % (box.get("result"), left, "通过 OK" if ok else "失败"))
    return ok


def case_norange(url, dest):
    print("\n[B] 服务器不支持 Range → 应回退单线程", flush=True)
    ok = False
    try:
        models._open_with_progress(url, dest, None, "回退测试", timeout=60, cancel_event=None)
        same = sha_of(dest) == SRC_SHA
        ok = same
        print("  下载完成，sha256 一致: %s → %s" % (same, "通过 OK" if same else "失败"))
    except Exception as exc:  # noqa: BLE001
        print("  异常: %s: %s → 失败" % (type(exc).__name__, exc))
    return ok


def case_short(url, dest):
    print("\n[C] 响应提前结束 → 应判失败（不给残缺文件）", flush=True)
    raised = None
    msg = ""
    try:
        models._ranged_download(url, dest, SIZE, None, "短读测试", 30, None, threads=4)
        raised = "无异常"
    except Exception as exc:  # noqa: BLE001
        raised = type(exc).__name__
        msg = str(exc)
    left = os.path.exists(dest)
    # IOError 在 Python 3 里就是 OSError 的别名，类型名显示为 OSError
    ok = raised in ("OSError", "IOError", "URLError", "IncompleteRead") and not left
    print("  抛出: %s / 半成品残留: %s → %s" % (raised, left, "通过 OK" if ok else "失败"))
    print("  异常内容: %s" % msg)
    return ok


def case_flaky(url, dest):
    print("\n[D] 单片瞬时超时 → 应自动重试该片并成功（不再整体作废）", flush=True)
    ok = False
    try:
        # timeout=2：第 1 片会因服务器卡住 4 秒而读超时，重试时恢复正常
        models._ranged_download(url, dest, SIZE, None, "重试测试", 2, None, threads=4)
        same = sha_of(dest) == SRC_SHA
        ok = same
        print("  下载完成（未整体作废），sha256 一致: %s → %s"
              % (same, "通过 OK" if same else "失败"))
    except Exception as exc:  # noqa: BLE001
        print("  异常: %s: %s → 失败" % (type(exc).__name__, exc))
    return ok


def case_slow_stall(url, dest):
    print("\n[E] 连接卡死 40 秒 → 卡顿超时应在 ~20 秒内放弃并重试（不再干等 120 秒）", flush=True)
    ok = False
    t0 = time.time()
    try:
        # timeout=120 就是应用里的默认值；若不生效则要等满 40 秒才拿到数据
        models._ranged_download(url, dest, SIZE, None, "卡顿超时测试", 120, None, threads=4)
        elapsed = time.time() - t0
        same = sha_of(dest) == SRC_SHA
        ok = same and elapsed < 32.0
        print("  耗时 %.1f s，sha256 一致: %s → %s"
              % (elapsed, same, "通过 OK" if ok else "失败"))
    except Exception as exc:  # noqa: BLE001
        print("  异常: %s: %s → 失败" % (type(exc).__name__, exc))
    return ok


def main():
    tmp = tempfile.mkdtemp(prefix="shunyi_edge_")
    dest = os.path.join(tmp, "out.bin")
    results = []
    try:
        srv, url = start_server()
        Handler.mode = "range"
        results.append(("A 取消", case_cancel(url, dest)))

        Handler.mode = "norange"
        if os.path.exists(dest):
            os.remove(dest)
        results.append(("B 回退单线程", case_norange(url, dest)))

        Handler.mode = "short"
        if os.path.exists(dest):
            os.remove(dest)
        results.append(("C 短读判失败", case_short(url, dest)))

        Handler.mode = "flaky"
        Handler.counter = 0
        if os.path.exists(dest):
            os.remove(dest)
        results.append(("D 单片重试", case_flaky(url, dest)))

        Handler.mode = "slowstall"
        Handler.counter = 0
        if os.path.exists(dest):
            os.remove(dest)
        results.append(("E 卡顿超时", case_slow_stall(url, dest)))
        srv.shutdown()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n================ 汇总 ================")
    for name, ok in results:
        print("  %-14s %s" % (name, "通过 OK" if ok else "失败"))
    print("  全部通过" if all(ok for _n, ok in results) else "  有失败项，需复查")
    return 0 if all(ok for _n, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
