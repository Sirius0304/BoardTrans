# -*- coding: utf-8 -*-
"""
键译 - 模型管理

负责离线翻译模型的下载、校验、安装（解压）与就绪检查。

模型来源：Argos Translate 官方索引里的 .argosmodel 包
（本质是 zip，内部为 标准 CTranslate2 目录 + sentencepiece.model）。
下载源 argos-net.com 实测国内可直连，约 1 MB/s。

关于"中→日直连大模型"的调研结论（2026-09 实测，记录备查）：
Helsinki-NLP 有更大的 opus-mt-tc-big-zh-ja 模型（约 400 MB），
但官方只发布 PyTorch 版；社区在 HuggingFace 上的 CTranslate2 转换版
（ooeoeo / eeyzs1 两个仓库）实测存在词表与模型不匹配的问题，
输出为乱码，不可用。因此中→日采用"经英文中转（zh→en→ja）"方案，
下载体积也更小（71+115 MB）。若日后有可靠的直连模型，
只需在下面的 PACKAGES 里加一条 kind="hf" 的记录即可。

所有下载都带：进度回调、断点续传（服务器支持 Range 时）、失败重试、多镜像切换。
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections import deque

from . import paths
from .logger import get_logger

log = get_logger()

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BoardTrans/1.0"

# ---------------------------------------------------------------------------
# 下载参数
# ---------------------------------------------------------------------------
CHUNK_SIZE = 1024 * 256                      # 单次读取的块大小
DEFAULT_THREADS = 16                         # 分块下载线程数
MULTITHREAD_MIN_SIZE = 4 * 1024 * 1024       # 小于 4 MB 的文件不值当开多线程

# 动态切片参数（2026-09-28 加入，解决"末段降速"）
# ---------------------------------------------------------------------------
# 原来按线程数静态均分区间，每线程只下自己那一段。由于单连接速度波动极大
# （实测 0.15 ~ 0.40 MB/s），必然有线程早早完工、有线程拖到最后：
# 末段活跃连接从 16 掉到 1，聚合速度从 0.81 跌到单连接的 0.2 MB/s 左右。
# 71 MB 的模型尾巴能拖将近 1 分钟，看起来就是"下载快完成了却越来越慢"。
#
# 现在改成"把文件切成小片放进队列、线程动态领取"：谁快谁多领，
# 尾巴最多只剩一片（而不是一整段区间），末段耗时被压到一个切片的量级。
# 切片不能太小——每片是一个独立的 Range 请求，走国际链路握手要花时间，
# 所以单片下限 1 MB、片数取线程数的 3 倍（握手开销占比约 10%）。
SLICE_MIN_SIZE = 1024 * 1024                 # 单片下限
SLICE_FACTOR = 3                             # 切片数 = 线程数 × 该倍数
SLICE_ATTEMPTS = 3                           # 单片最大尝试次数（含首次）
STALL_TIMEOUT = 20                           # 单片单次读取的最长等待（秒）
# 为什么要单独的"卡顿超时"：urlopen 的 timeout 默认 120 秒，
# 一条连接彻底卡死后要干等 2 分钟才轮到重试——实测真机下载就在 95.6% 处
# 遇到过这种卡顿。把单片单次等待压到 20 秒，"重试该片"能及时发生；
# 而正常连接的 256 KB 数据块间隔远小于 20 秒（0.05 MB/s 的极端慢速也才 5 秒），
# 所以不会误伤"慢但活着"的连接。
# 为什么要有片级重试（2026-09-28 实测）：切片变多意味着请求数变多
# （71 MB 从 16 个请求变成 49 个），撞上"某条连接读超时"的概率随之上升。
# 实测真机下载在 95.6% 处有一片读超时，若按"任一片失败即整体失败"处理，
# 已下好的 71 MB 会全部作废并退回单线程从头再来（多花 160 秒）。
# 现在单片失败会先回退该片进度、重下这一片（不动其它片），重试仍失败才整体失败。

# 关于下载速度的实测记录（2026-09-27，本机 20 核 / 家宽，argos-net.com）
# ---------------------------------------------------------------------------
# 现象：官方源单连接实测只有 0.15 ~ 0.40 MB/s，且波动极大；
#       响应头 cf-cache-status=DYNAMIC 说明**没走 CDN 缓存**，每次都回源；
#       Cloudflare 给的是 AMS（阿姆斯特丹）节点，对国内用户不友好。
#       旧的备用源已全部失效（GitHub releases 404、ghfast.top 403、
#       ghps.cc 只返回 4 KB 的 HTML 错误页、data.argosopentech.com 仅 0.01 MB/s）。
#
# 结论：靠"换源"解决不了（没有可用的快源），但**并发分块可以绕过单连接限速**。
#       同一时段交替 A/B 测速（消除网络波动干扰，两轮结果一致）：
#           单线程  0.22 MB/s
#           4 线程  0.36 MB/s  (+67%)
#           8 线程  0.58 MB/s  (+168%)
#           16 线程 0.81 MB/s  (+275%)  ← 采用
#       71 MB 的模型从约 5.4 分钟缩短到约 1.5 分钟。
#
# 注意：必须让每块至少 4 MB，否则线程数收益被调度开销吃掉。
#
# 末段降速与修法（2026-09-28）
# ---------------------------------------------------------------------------
# 现象：下载快到 100% 时明显变慢。原因不是磁盘或校验，而是**静态均分区间的掉队效应**：
#       每线程只下自己那一段，而单连接速度波动很大（0.15 ~ 0.40 MB/s），
#       必然有线程早早完工、有线程拖到最后——末段活跃连接从 16 掉到 1，
#       聚合速度塌到单连接水平（0.81 → 0.2 MB/s），尾巴能拖半分钟以上。
# 修法：改成"切片入队 + 线程动态领取"（见 _ranged_download 与 SLICE_* 常量），
#       尾巴最多只剩一片。本地限速服务器 A/B（64 MB，两条慢连接 0.2 MB/s）：
#           静态均分：总耗时 20.1 s，尾巴 95→100% 7.5 s
#           动态领取：总耗时  6.7 s，尾巴 95→100% 3.9 s（缩短 47%）
#       真实链路复验 zh_en（71 MB，16 线程 / 49 片）：
#           44.7 s 完成，平均 1.59 MB/s，尾巴 95→100% 5.0 s，sha256 与原缓存一致。


# ---------------------------------------------------------------------------
# 进度上报
# ---------------------------------------------------------------------------
# 进度有两个阶段，必须显式区分（2026-10-03 修复"进度跳回 0%"）：
#   PHASE_DOWNLOAD 正在收字节，此时 done/total 才有意义
#   PHASE_EXTRACT  字节已收齐，正在解压安装 —— 此时**绝不能再当百分比显示**
# 为什么以前会跳回 0%：install() 复用同一个回调发 progress_cb("正在解压", 0, 0)，
# 而界面按 total 为 0 时把百分比算成 0，于是 100% → 0% → "已下载"，
# 用户看到的就是"进度瞬间归零又跳走"。把阶段显式带上，这个歧义就没有了。
PHASE_DOWNLOAD = "download"
PHASE_EXTRACT = "extract"


def _adapt_progress(cb):
    """把外部进度回调适配成统一签名 (label, done, total, phase)。

    只接受 3 个参数的旧回调（app/console.py 的 progress_line、各测试里的
    lambda *a）要继续能用，所以这里只在包装时探测一次签名，之后按原样转发。
    传入的已经是 _Progress 实例时原样返回（ensure→download→install 共用同一个）。
    """
    if cb is None or isinstance(cb, _Progress):
        return None if cb is None else cb._notify
    try:
        params = inspect.signature(cb).parameters.values()
        has_var = any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in params)
        positional = [p for p in params
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    except (TypeError, ValueError):          # 内建/无签名对象：按"接受全部"处理
        return cb
    if has_var or len(positional) >= 4:
        return cb
    return lambda label, done, total, phase=PHASE_DOWNLOAD: cb(label, done, total)


class _Progress:
    """单个文件的进度上报器：单调不回退，且收尾一定补到 100%。

    为什么需要它：
      1) 底层为省开销按 0.2s 节流上报，最后一个数据块常常来不及上报，
         界面会停在 98% 就直接跳成"已下载"，中间看不到 100%；
      2) 分块下载里单片重试会把已计入的字节退回去，百分比会掉下去。
    单调性只在**单个文件内**保证；跨重试/换源的整段重来由界面层的水位线兜底
    （见 settings_window._on_download_progress），否则真正的重试会被误当成回退。
    """

    def __init__(self, cb):
        # 传入的已经是 _Progress（ensure 把它传给了 download / install）时，
        # **继承它的字节数与单调水位**，而不是只借用回调。
        # 否则 install() 拿到的 done/total 会退化成 0/0 —— 这正是真实下载实测
        # 抓到的那个坑：解压事件仍是 (0, 0)，只是被界面层的阶段标记挡住了而已，
        # 底层数据本身是错的，换个调用方就会暴露。
        inherited = cb if isinstance(cb, _Progress) else None
        cb = inherited._notify if inherited else cb
        self._cb = _adapt_progress(cb)
        self._notify = self._cb
        self._lock = threading.Lock()
        self._high = inherited._high if inherited else 0
        self._done = inherited._done if inherited else 0
        self._total = inherited._total if inherited else 0
        self._closed = inherited._closed if inherited else False

    def download(self, label: str, done: int, total) -> None:
        """上报下载阶段进度（内部按百分比单调化）。"""
        cb = self._cb
        if cb is None:
            return
        with self._lock:
            if self._closed:
                return
            if total:
                self._total = total
            self._done = max(done, self._done)
            if self._total:
                pct = int(self._done * 100 / self._total)
                if pct > self._high:
                    self._high = pct
            pct = self._high if self._total else 0
        cb(label, self._done, self._total, PHASE_DOWNLOAD)

    def finish(self, label: str) -> None:
        """下载收尾：保证最后一定是 100%（哪怕最后一个数据块被节流丢掉了）。"""
        cb = self._cb
        if cb is None:
            return
        with self._lock:
            self._closed = True
            self._high = 100
            self._done = self._total
        cb(label, self._done, self._total, PHASE_DOWNLOAD)

    def extract(self, label: str = "正在解压") -> None:
        """进入解压阶段：沿用已下载字节数，阶段标记为 extract。"""
        cb = self._cb
        if cb is None:
            return
        with self._lock:
            done, total = self._done, self._total
        cb(label, done, total, PHASE_EXTRACT)


class DownloadCancelled(Exception):
    """用户取消下载。"""


def _is_cancelled(cancel_event) -> bool:
    return bool(cancel_event is not None and cancel_event.is_set())


# ---------------------------------------------------------------------------
# 同一模型的并发下载保护
# ---------------------------------------------------------------------------
# 为什么必须有这层：下载是"以追加方式写同一个文件 + 支持断点续传"实现的
# （open(dest, "ab")）。如果两个线程同时下同一个模型，写入会交错，
# 文件会损坏且进度会互相覆盖。用 RLock 保证同一模型同一时刻只有一个线程在下载
# （用 RLock 是因为 ensure() 内部还会调用 download()，同一线程需要可重入）。
_download_locks: dict = {}
_download_locks_guard = threading.Lock()


def _model_download_lock(key: str):
    with _download_locks_guard:
        lock = _download_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _download_locks[key] = lock
        return lock


def is_downloading(key: str) -> bool:
    """该模型此刻是否正被某个线程下载（界面用它来判断，避免重复发起）。"""
    lock = _model_download_lock(key)
    if lock.acquire(blocking=False):     # 能立刻拿到，说明没人在下载
        lock.release()
        return False
    return True

# ------------------------- 模型注册表 -------------------------

PACKAGES: dict[str, dict] = {
    # 中文 -> 英文（基础模型，两档都必需）
    "zh_en": {
        "kind": "argos",
        "display": "中文 → 英文（基础模型）",
        "size": 74481402,
        "size_text": "约 71 MB",
        "url": "https://argos-net.com/v1/translate-zh_en-1_9.argosmodel",
        "fallback_urls": [
            "https://github.com/argosopentech/argospm-index/releases/download/v1.9/translate-zh_en-1_9.argosmodel",
            "https://ghfast.top/https://argos-net.com/v1/translate-zh_en-1_9.argosmodel",
        ],
    },
    # 英文 -> 日文（中转模型，选择日文时必需）
    "en_ja": {
        "kind": "argos",
        "display": "英文 → 日文（中转模型）",
        "size": 120470284,
        "size_text": "约 115 MB",
        "url": "https://argos-net.com/v1/translate-en_ja-1_1.argosmodel",
        "fallback_urls": [
            "https://ghfast.top/https://argos-net.com/v1/translate-en_ja-1_1.argosmodel",
        ],
    },
}

# 每个目标语言需要哪些模型
REQUIRED_BY_TARGET: dict[str, list[str]] = {
    "en": ["zh_en"],
    "ja": ["zh_en", "en_ja"],
}


def model_dir(key: str) -> str:
    """某个模型安装后的目录。"""
    return os.path.join(paths.packages_dir(), key)


def is_installed(key: str) -> bool:
    """检查模型是否已安装可用。"""
    root = model_dir(key)
    if not os.path.isdir(root):
        return False
    info = PACKAGES[key]
    if info["kind"] == "argos":
        return (
            os.path.exists(os.path.join(root, "model", "model.bin"))
            and os.path.exists(os.path.join(root, "sentencepiece.model"))
        )
    return os.path.exists(os.path.join(root, "model.bin"))


def installed_size(key: str) -> int:
    """模型占用的磁盘空间。"""
    root = model_dir(key)
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


# ------------------------- 下载 -------------------------


def _probe(url: str, timeout: int = 30):
    """探测文件总大小与是否支持 Range 分块下载。返回 (total_size, supports_range)。

    用 `Range: bytes=0-0` 试探：返回 206 说明支持分块；返回 200 说明不支持。
    有些服务器不接受这种试探，退化为 HEAD 看 Accept-Ranges 头。
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total = None
            content_range = resp.headers.get("Content-Range")      # 形如 bytes 0-0/74481402
            if content_range and "/" in content_range:
                try:
                    total = int(content_range.rsplit("/", 1)[-1])
                except ValueError:
                    total = None
            if total is None:
                cl = resp.headers.get("Content-Length")
                total = int(cl) if cl and cl.isdigit() else None
            return total, (resp.status == 206)
    except Exception:  # noqa: BLE001
        pass
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            cl = resp.headers.get("Content-Length")
            total = int(cl) if cl and cl.isdigit() else None
            accept = (resp.headers.get("Accept-Ranges") or "").lower()
            return total, ("bytes" in accept)
    except Exception:  # noqa: BLE001
        return None, False


def _ranged_download(url: str, dest: str, total: int, prog: _Progress, label: str,
                     timeout: int, cancel_event, threads: int = DEFAULT_THREADS) -> int:
    """多线程分块下载整个文件（不续传，取消时删除半成品）。

    为什么有效：实测官方源单连接只有 0.38 MB/s，8 个并发连接能到 0.62 MB/s
    （+63%）——服务器对单连接有限速，并发能绕过一部分。

    实现要点：
      1. 先按总大小预分配文件，每个线程用**自己独立的文件句柄** seek 到目标
         位置再写，各写各的互不重叠，避免并发写冲突；
      2. 区间**动态领取**而非静态均分：整个文件先切成"线程数 × SLICE_FACTOR"个小片
         放进队列，线程下完一片再领下一片。这样快的线程自然多干、慢的少干，
         尾巴最多只剩一片（静态均分时会剩下一整段，末段只靠最慢那条连接硬拖，
         聚合速度从 0.81 掉到 0.2 MB/s 左右，看起来就是"越下越慢"）；
      3. 单片失败先重试该片（最多 SLICE_ATTEMPTS 次，进度如实回退），
         避免"一片瞬时超时把已下好的整个文件作废、退回单线程从头再来"。
         重试仍失败才整体判失败，由上层回退单线程。
    """
    if os.path.exists(dest):
        os.remove(dest)                       # 多线程模式不做区间续传，从头下

    # 预分配，保证各线程都能写到自己的位置
    with open(dest, "wb") as f:
        f.truncate(total)

    # 切片：保证每片至少 SLICE_MIN_SIZE，片数约为线程数的 SLICE_FACTOR 倍。
    # 片太大会退化成静态均分（尾巴长），太小则握手开销吃掉收益。
    step = max(SLICE_MIN_SIZE, total // (threads * SLICE_FACTOR))
    pending: deque = deque()
    cursor = 0
    while cursor < total:
        end = min(cursor + step, total) - 1
        pending.append((cursor, end))
        cursor = end + 1

    done = [0]
    lock = threading.Lock()
    errors: list[str] = []
    last_report = [0.0]

    def next_slice():
        """领一片待下载区间；返回 None 表示全部领完。"""
        with lock:
            if not pending:
                return None
            return pending.popleft()

    def fetch_slice(start: int, end: int, f, got: list):
        """下载一片并写入文件；把本片已收字节数记在 got[0] 里（供失败时回退进度）。"""
        headers = {"User-Agent": USER_AGENT, "Range": f"bytes={start}-{end}"}
        req = urllib.request.Request(url, headers=headers)
        # 用卡顿超时（而不是 urlopen 的 120 秒）——卡死的连接 20 秒没数据就放弃，
        # 交给上层重试这一片，避免"看着像卡住、其实在干等两分钟"
        with urllib.request.urlopen(req, timeout=min(timeout, STALL_TIMEOUT)) as resp:
            if resp.status != 206:
                raise IOError(f"服务器返回 {resp.status}（不支持分块）")
            f.seek(start)
            pos = start
            while pos <= end:
                if _is_cancelled(cancel_event):
                    raise DownloadCancelled("已取消下载")
                chunk = resp.read(min(CHUNK_SIZE, end - pos + 1))
                if not chunk:
                    # 响应提前结束：预分配的文件里会留一段空洞，
                    # 与其留下"看起来完整实则残缺"的文件，不如直接判失败
                    raise IOError(
                        f"分片 {start}-{end} 响应提前结束（已收 {pos - start} 字节）")
                f.write(chunk)
                pos += len(chunk)
                got[0] = pos - start
                with lock:
                    done[0] += len(chunk)
                    now = time.time()
                    if now - last_report[0] > 0.2:
                        last_report[0] = now
                        prog.download(label, done[0], total)

    def worker():
        try:
            with open(dest, "r+b") as f:      # 每线程一个句柄，各片之间互不重叠
                while True:
                    if _is_cancelled(cancel_event):
                        raise DownloadCancelled("已取消下载")
                    item = next_slice()
                    if item is None:
                        break                 # 队列空 → 活干完了
                    start, end = item
                    got = [0]                 # 本片已计入进度的字节数
                    for attempt in range(1, SLICE_ATTEMPTS + 1):
                        try:
                            fetch_slice(start, end, f, got)
                            break             # 本片下完，继续领下一片
                        except DownloadCancelled:
                            raise
                        except Exception as exc:  # noqa: BLE001
                            # 这一片重来：先把已计入的字节数退回去，进度不会虚高
                            if got[0]:
                                with lock:
                                    done[0] -= got[0]
                                got[0] = 0
                            if attempt >= SLICE_ATTEMPTS:
                                raise
                            log.warning("分片 %d-%d 第 %d 次失败（%s: %s），重试",
                                        start, end, attempt,
                                        type(exc).__name__, str(exc)[:60])
                            time.sleep(0.6 * attempt)
        except DownloadCancelled:
            with lock:
                errors.append("__cancelled__")
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(f"{type(exc).__name__}: {str(exc)[:60]}")

    workers = []
    for i in range(threads):
        t = threading.Thread(target=worker, name=f"shunyi-dl-part-{i}", daemon=True)
        workers.append(t)
        t.start()
    for t in workers:
        t.join()

    if "__cancelled__" in errors:
        # 多线程下无法可靠续传，删除半成品，避免留下"看起来完整实则残缺"的文件
        if os.path.exists(dest):
            try:
                os.remove(dest)
            except OSError:
                pass
        raise DownloadCancelled("已取消下载")
    if errors:
        if os.path.exists(dest):
            try:
                os.remove(dest)
            except OSError:
                pass
        raise IOError(f"分块下载失败：{errors[0]}")
    # 收尾：最后一个数据块可能被 0.2s 节流丢掉，这里保证一定报到 100%
    prog.finish(label)
    return total


def _single_thread_download(url: str, dest: str, prog: _Progress, label: str,
                            timeout: int, cancel_event) -> int:
    """单线程下载（支持断点续传），作为多线程不可用时的回退路径。"""
    expect = None
    existing = os.path.getsize(dest) if os.path.exists(dest) else 0

    headers = {"User-Agent": USER_AGENT}
    if existing > 0:
        headers["Range"] = f"bytes={existing}-"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # 服务器不支持续传时返回 200，需要从头开始
        if existing > 0 and resp.status != 206:
            existing = 0
        content_length = resp.headers.get("Content-Length")
        if content_length and content_length.isdigit():
            expect = existing + int(content_length)
        mode = "ab" if existing else "wb"
        got = existing
        last_report = 0.0
        with open(dest, mode) as f:
            while True:
                if _is_cancelled(cancel_event):
                    raise DownloadCancelled("已取消下载")
                chunk = resp.read(CHUNK_SIZE)
                if not chunk:
                    break
                f.write(chunk)
                f.flush()          # 及时落盘：取消或意外中断时已下载部分不丢，下次可续传
                got += len(chunk)
                now = time.time()
                if now - last_report > 0.2:
                    last_report = now
                    prog.download(label, got, expect)
    if expect and got < expect:
        raise IOError(f"下载不完整: {got}/{expect}")
    prog.finish(label)
    return got


def _open_with_progress(url: str, dest: str, progress_cb, label: str, timeout: int = 120, cancel_event=None):
    """下载单个文件到 dest，自动选择下载方式。

    - 文件够大且服务器支持 Range 时，用多线程分块下载（实测快 60% 以上）
    - 否则回退单线程（支持断点续传）
    - 多线程失败会自动回退到单线程重试一次，保证最终能下下来
    """
    if _is_cancelled(cancel_event):
        raise DownloadCancelled("已取消下载")

    # 复用 ensure() 传下来的同一个 _Progress（保留已下载字节数与单调水位）；
    # 独立调用本函数时（测试、脚本）才在这里新建一个
    prog = progress_cb if isinstance(progress_cb, _Progress) else _Progress(progress_cb)
    total, supports_range = _probe(url, timeout=min(timeout, 30))
    use_multi = bool(supports_range and total and total >= MULTITHREAD_MIN_SIZE)

    if use_multi:
        # 根据大小决定线程数：每块至少 4 MB，避免小文件开一堆线程
        threads = max(2, min(DEFAULT_THREADS, total // (4 * 1024 * 1024)))
        try:
            return _ranged_download(url, dest, total, prog, label,
                                    timeout, cancel_event, threads=threads)
        except DownloadCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            # 分块下载失败（网络抖动 / 服务器中途拒绝 Range 等）→ 退回单线程
            log.warning("分块下载失败，改为单线程重试：%s", exc)

    # 关键点（2026-10-03）：分块失败退回单线程时，单线程是从 0 重新开始的
    # （半成品已被删除）。如果这里新建一个 _Progress，进度就会从 100% 跳回 0%，
    # 正是主人报的那个现象。所以复用同一个 prog —— 它的百分比单调不回退，
    # 单线程的字节数虽然从头计，显示的百分比仍只会往上走。
    return _single_thread_download(url, dest, prog, label, timeout, cancel_event)


def download(key: str, progress_cb=None, retries: int = 3, cancel_event=None) -> str:
    """下载模型到本地缓存目录。progress_cb(label, downloaded, total)。支持中途取消。

    同一模型的下载是互斥的（见 _model_download_lock 的说明）。
    """
    with _model_download_lock(key):
        return _download_locked(key, progress_cb=progress_cb, retries=retries,
                                cancel_event=cancel_event)


def _download_locked(key: str, progress_cb=None, retries: int = 3, cancel_event=None) -> str:
    info = PACKAGES[key]
    cache = paths.models_dir()

    if info["kind"] == "argos":
        dest = os.path.join(cache, f"{key}.argosmodel")
        if os.path.exists(dest) and os.path.getsize(dest) >= info["size"] * 0.98:
            if progress_cb:
                # 包已存在（换源失败后重试、或上次下完没装完）：直接报到 100%。
                # 这里必须显式给 100%，否则界面会停在旧进度上直到"已下载"才跳。
                # 用新的 _Progress（走裸回调）：本段是独立事件，
                # 不能沿用外层那个可能已收尾(closed)的实例，否则会被静默吞掉。
                done_cb = progress_cb._notify if isinstance(progress_cb, _Progress) else progress_cb
                prog = _Progress(done_cb)
                prog.download(info["display"], info["size"], info["size"])
                prog.finish(info["display"])
            return dest
        urls = [info["url"]] + list(info.get("fallback_urls") or [])
        last_error = None
        for attempt in range(retries):
            for url in urls:
                if _is_cancelled(cancel_event):
                    raise DownloadCancelled("已取消下载")
                try:
                    _open_with_progress(url, dest, progress_cb, info["display"], cancel_event=cancel_event)
                    return dest
                except DownloadCancelled:
                    raise
                except (urllib.error.URLError, OSError, IOError) as exc:
                    last_error = exc
                    time.sleep(1.5 * (attempt + 1))
        raise IOError(f"下载失败（已尝试 {retries} 轮 / {len(urls)} 个源）：{last_error}")

    # kind == "hf"：多文件下载
    target = model_dir(key)
    os.makedirs(target, exist_ok=True)
    mirrors = list(info.get("mirrors") or [])
    for name in info["files"]:
        if _is_cancelled(cancel_event):
            raise DownloadCancelled("已取消下载")
        dest = os.path.join(target, name)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            if progress_cb:
                # 同上：已存在的文件按"这个文件已完成"上报，避免进度看起来没动
                done_cb = progress_cb._notify if isinstance(progress_cb, _Progress) else progress_cb
                prog = _Progress(done_cb)
                prog.download(f"{info['display']} / {name}", 1, 1)
                prog.finish(f"{info['display']} / {name}")
            continue
        last_error = None
        ok = False
        for attempt in range(retries):
            for mirror in mirrors:
                url = f"{mirror}/{info['repo']}/resolve/main/{name}"
                try:
                    _open_with_progress(url, dest, progress_cb, f"{info['display']} / {name}",
                                        cancel_event=cancel_event)
                    ok = True
                    break
                except DownloadCancelled:
                    raise
                except (urllib.error.URLError, OSError, IOError) as exc:
                    last_error = exc
                    time.sleep(1.5 * (attempt + 1))
            if ok:
                break
        if not ok:
            raise IOError(f"下载 {name} 失败（已尝试 {retries} 轮 / {len(mirrors)} 个镜像）：{last_error}")
    return target


# ------------------------- 安装 -------------------------


def install(key: str, progress_cb=None) -> str:
    """把下载好的包解压安装到 packages/<key>。"""
    info = PACKAGES[key]
    target = model_dir(key)

    if info["kind"] == "hf":
        # 多文件模型下载时已直接落到目标目录
        if not is_installed(key):
            raise IOError(f"模型 {key} 文件不完整，请删除 {target} 后重试")
        return target

    archive = os.path.join(paths.models_dir(), f"{key}.argosmodel")
    if not os.path.exists(archive):
        raise FileNotFoundError(f"找不到模型包: {archive}")

    if progress_cb:
        # 关键（2026-10-03 修"进度跳回 0%"）：这里以前发的是 progress_cb("正在解压", 0, 0)，
        # 界面按"done/total"算百分比，total=0 就被算成 0% —— 于是下载刚到 100%，
        # 紧接着被这条消息打成 0%，解压完再跳成"已下载"，用户看到的就是
        # "进度瞬间归零又跳走"。现在带 extract 阶段标记、沿用真实已下载字节数，
        # 界面只显示"正在解压…"而不再重置百分比。
        # ensure() 传下来的是同一个 _Progress（带着真实的 done/total），直接复用。
        prog = progress_cb if isinstance(progress_cb, _Progress) else _Progress(progress_cb)
        prog.extract("正在解压")
    tmp = target + ".installing"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)

    with zipfile.ZipFile(archive) as z:
        z.extractall(tmp)

    # 包内顶层目录名不统一（例如 translate-zh_en-1_9 / en_ja），自动探测
    entries = [e for e in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, e))]
    src = os.path.join(tmp, entries[0]) if len(entries) == 1 else tmp

    shutil.rmtree(target, ignore_errors=True)
    shutil.move(src, target)
    shutil.rmtree(tmp, ignore_errors=True)   # 清理解压临时目录
    return target


def ensure(key: str, progress_cb=None, cancel_event=None) -> str:
    """确保模型可用：已安装直接返回，否则下载 + 安装。支持中途取消。

    整段（判断 -> 下载 -> 安装）都在同一把锁里完成，
    避免两个线程同时进入后各写一份文件。

    关键（2026-10-03）：这里创建**唯一**一个 _Progress 实例，下载与解压共用它。
    以前 install() 拿到的是裸回调，只能凭空发一条 (0, 0) 的"正在解压"，
    界面按 total=0 算百分比就成了 0% —— 这正是"进度跳回 0%"的根因。
    共用一个实例后，解压阶段能沿用真实的已下载字节数，且阶段是显式的。
    """
    with _model_download_lock(key):
        if is_installed(key):
            return model_dir(key)
        prog = _Progress(progress_cb)
        download(key, progress_cb=prog, cancel_event=cancel_event)
        return install(key, progress_cb=prog)


def _looks_like_model_package(filename: str, key: str) -> bool:
    """判断某个文件名是不是该模型的包（严格匹配，避免误删无关文件）。

    能匹配两种命名：
      - zh_en.argosmodel                      （程序自己下载时用的名字）
      - translate-zh_en-1_9.argosmodel        （Argos 官方发行包的名字）
    """
    low = filename.lower()
    if not low.endswith(".argosmodel"):
        return False
    return low == f"{key}.argosmodel" or low.startswith(f"translate-{key}-")


def find_extra_copies(key: str) -> list[str]:
    """找出**程序管理范围之外**的同一个模型包（例如随程序一起放的离线备份）。

    背景：模型文件可能散落在多处——程序自己管理的 packages_cache 之外，
    项目/程序目录里也可能有人手工放了一份同名的 .argosmodel。
    设置里删除模型时如果不管这些副本，用户会看到"删了但文件还在"。

    只在几个明确的目录里查找（程序目录、其 models 子目录、数据目录、其 models 子目录），
    且文件名必须严格匹配模型命名规则，避免误删无关文件。
    """
    roots = [
        paths.program_dir(),
        os.path.join(paths.program_dir(), "models"),
        paths.data_dir(),
        os.path.join(paths.data_dir(), "models"),
    ]
    managed = os.path.abspath(paths.models_dir())     # 程序管理的目录由 remove 单独处理
    found: list[str] = []
    seen: set[str] = set()
    for root in roots:
        if not os.path.isdir(root) or os.path.abspath(root) == managed:
            continue
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            if not _looks_like_model_package(name, key):
                continue
            full = os.path.abspath(os.path.join(root, name))
            if full in seen or full == managed:
                continue
            if os.path.isfile(full):
                seen.add(full)
                found.append(os.path.join(root, name))
    return found


def describe_locations(key: str) -> list[tuple[str, int]]:
    """列出该模型当前在磁盘上的所有位置及占用（供界面在删除前展示给用户）。"""
    items: list[tuple[str, int]] = []
    target = model_dir(key)
    if os.path.isdir(target):
        items.append((target, installed_size(key)))
    archive = os.path.join(paths.models_dir(), f"{key}.argosmodel")
    if os.path.exists(archive):
        try:
            items.append((archive, os.path.getsize(archive)))
        except OSError:
            items.append((archive, 0))
    for extra in find_extra_copies(key):
        try:
            items.append((extra, os.path.getsize(extra)))
        except OSError:
            items.append((extra, 0))
    return items


def migrate_legacy_models() -> list[str]:
    """把旧位置（数据目录下）的模型搬到新的模型根目录，避免用户白白重新下载。

    背景：模型默认位置从「%LOCALAPPDATA%\\BoardTrans」改成了「软件目录\\models」。
    老用户升级后模型还在旧地方，直接改路径会让他们以为模型丢了、重新下 186 MB。
    这里做一次性搬迁，只在"新位置还没有内容"时才搬，不会覆盖任何东西。

    返回搬迁记录（空列表表示无需迁移或已经迁过）。
    """
    new_root = os.path.abspath(paths.models_root())
    old_root = os.path.abspath(paths.data_dir())
    # 注意：即使模型根目录没变，子目录名也可能变了（packages_cache → cache），
    # 所以这里不做提前返回，交给下面的逐项比较去判断
    _ = new_root

    pairs = [
        (os.path.join(old_root, "packages"), os.path.abspath(paths.packages_dir())),
        (os.path.join(old_root, "packages_cache"), os.path.abspath(paths.models_dir())),
        # 旧位置也可能已经在用新名字 cache（例如改名前 models_root 回退到数据目录、
        # 但子目录命名已是新版的情况），漏掉它会让 .argosmodel 缓存（186 MB）滞留旧位置
        (os.path.join(old_root, "cache"), os.path.abspath(paths.models_dir())),
    ]
    done: list[str] = []
    for old, new in pairs:
        if not os.path.isdir(old) or os.path.abspath(old) == new:
            continue
        try:
            if os.path.isdir(new) and os.listdir(new):
                continue               # 新位置已有内容，绝不动它
            os.makedirs(os.path.dirname(new), exist_ok=True)
            if os.path.exists(new):
                try:
                    os.rmdir(new)      # 目标是空目录，删掉以便直接改名
                except OSError:
                    pass
            shutil.move(old, new)
            done.append(f"{os.path.basename(old)} → {new}")
            log.info("模型已从旧位置迁移：%s → %s", old, new)
        except Exception:  # noqa: BLE001
            log.exception("迁移模型失败（跳过这一项，不影响继续使用）：%s", old)
    return done


def remove(key: str, include_extra: bool = True) -> dict:
    """删除模型：解压目录 + 下载缓存 + 程序管理范围之外的同名副本。

    返回 {"freed": 释放字节数, "removed": [已删路径], "failed": [(路径, 原因)]}

    关键点：
      - 解压目录删除失败（文件被占用）会抛出 PermissionError，由调用方提示用户；
      - 范围外的副本单独 try，个别删不掉不影响其余部分，会记在 failed 里如实反馈。
    """
    freed = 0
    removed: list[str] = []
    failed: list[tuple[str, str]] = []

    target = model_dir(key)
    if os.path.isdir(target):
        freed += installed_size(key)
        shutil.rmtree(target)              # 失败会抛异常，交给调用方处理
        removed.append(target)

    archive = os.path.join(paths.models_dir(), f"{key}.argosmodel")
    if os.path.exists(archive):
        try:
            freed += os.path.getsize(archive)
        except OSError:
            pass
        os.remove(archive)
        removed.append(archive)

    if include_extra:
        for extra in find_extra_copies(key):
            try:
                size = os.path.getsize(extra)
                os.remove(extra)
                freed += size
                removed.append(extra)
            except OSError as exc:
                failed.append((extra, str(exc)))

    return {"freed": freed, "removed": removed, "failed": failed}


def leftover_files(key: str, deep: bool = True) -> list[str]:
    """删除后再检查一遍：还有哪些属于该模型的文件没被清掉。

    deep=False 时只查程序自己管理的两处（很快，供界面高频刷新时用）；
    deep=True 时连"程序目录里的额外副本"一起查（供删除后核实用）。
    """
    left: list[str] = []
    target = model_dir(key)
    if os.path.exists(target):
        left.append(target)
    archive = os.path.join(paths.models_dir(), f"{key}.argosmodel")
    if os.path.exists(archive):
        left.append(archive)
    if deep:
        left.extend(find_extra_copies(key))
    return left


def ensure_for_target(target_lang: str, progress_cb=None) -> list[str]:
    """确保目标语言所需模型就绪，返回本次实际下载的模型 key 列表。"""
    downloaded = []
    for key in REQUIRED_BY_TARGET.get(target_lang, ["zh_en"]):
        if not is_installed(key):
            ensure(key, progress_cb=progress_cb)
            downloaded.append(key)
    return downloaded


def missing_for_target(target_lang: str) -> list[str]:
    """列出目标语言缺失的模型。"""
    return [k for k in REQUIRED_BY_TARGET.get(target_lang, ["zh_en"]) if not is_installed(k)]


def describe_status() -> str:
    """给用户看的模型状态摘要。"""
    lines = []
    for key, info in PACKAGES.items():
        state = "已安装" if is_installed(key) else "未安装"
        lines.append(f"- {info['display']}（{info['size_text']}）：{state}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    print(describe_status())
    if len(sys.argv) > 1:
        def cb(label, done, total):
            pct = f"{done * 100 // total}%" if total else "..."
            print(f"  {label}: {done/1024/1024:.1f}/{total/1024/1024:.1f} MB {pct}", flush=True)
        print(ensure(sys.argv[1], progress_cb=cb))
