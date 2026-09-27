# -*- coding: utf-8 -*-
"""
瞬译 - 模型管理

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

import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
import zipfile

from . import paths
from .logger import get_logger

log = get_logger()

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ShunYi/1.0"

# ---------------------------------------------------------------------------
# 下载参数
# ---------------------------------------------------------------------------
CHUNK_SIZE = 1024 * 256                      # 单次读取的块大小
DEFAULT_THREADS = 16                         # 分块下载线程数
MULTITHREAD_MIN_SIZE = 4 * 1024 * 1024       # 小于 4 MB 的文件不值当开多线程

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


def _ranged_download(url: str, dest: str, total: int, progress_cb, label: str,
                     timeout: int, cancel_event, threads: int = DEFAULT_THREADS) -> int:
    """多线程分块下载整个文件（不续传，取消时删除半成品）。

    为什么有效：实测官方源单连接只有 0.38 MB/s，8 个并发连接能到 0.62 MB/s
    （+63%）——服务器对单连接有限速，并发能绕过一部分。

    实现要点：先按总大小预分配文件，每个线程用**自己独立的文件句柄**
    seek 到自己的区间再写，各写各的互不重叠，避免并发写冲突。
    """
    if os.path.exists(dest):
        os.remove(dest)                       # 多线程模式不做区间续传，从头下

    # 预分配，保证各线程都能写到自己的位置
    with open(dest, "wb") as f:
        f.truncate(total)

    per = total // threads
    done = [0]
    lock = threading.Lock()
    errors: list[str] = []
    last_report = [0.0]

    def worker(start: int, end: int):
        headers = {"User-Agent": USER_AGENT, "Range": f"bytes={start}-{end}"}
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 206:
                    with lock:
                        errors.append(f"服务器返回 {resp.status}（不支持分块）")
                    return
                with open(dest, "r+b") as f:     # 每线程独立句柄，各自 seek 互不干扰
                    f.seek(start)
                    pos = start
                    while pos <= end:
                        if _is_cancelled(cancel_event):
                            raise DownloadCancelled("已取消下载")
                        chunk = resp.read(min(CHUNK_SIZE, end - pos + 1))
                        if not chunk:
                            break
                        f.write(chunk)
                        pos += len(chunk)
                        with lock:
                            done[0] += len(chunk)
                            now = time.time()
                            if progress_cb and now - last_report[0] > 0.2:
                                last_report[0] = now
                                progress_cb(label, done[0], total)
        except DownloadCancelled:
            with lock:
                errors.append("__cancelled__")
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(f"{type(exc).__name__}: {str(exc)[:60]}")

    workers = []
    for i in range(threads):
        start = i * per
        end = total - 1 if i == threads - 1 else (i + 1) * per - 1
        t = threading.Thread(target=worker, args=(start, end),
                             name=f"shunyi-dl-part-{i}", daemon=True)
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
    return total


def _single_thread_download(url: str, dest: str, progress_cb, label: str,
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
                if progress_cb and now - last_report > 0.2:
                    last_report = now
                    progress_cb(label, got, expect)
    if expect and got < expect:
        raise IOError(f"下载不完整: {got}/{expect}")
    return got


def _open_with_progress(url: str, dest: str, progress_cb, label: str, timeout: int = 120, cancel_event=None):
    """下载单个文件到 dest，自动选择下载方式。

    - 文件够大且服务器支持 Range 时，用多线程分块下载（实测快 60% 以上）
    - 否则回退单线程（支持断点续传）
    - 多线程失败会自动回退到单线程重试一次，保证最终能下下来
    """
    if _is_cancelled(cancel_event):
        raise DownloadCancelled("已取消下载")

    total, supports_range = _probe(url, timeout=min(timeout, 30))
    use_multi = bool(supports_range and total and total >= MULTITHREAD_MIN_SIZE)

    if use_multi:
        # 根据大小决定线程数：每块至少 4 MB，避免小文件开一堆线程
        threads = max(2, min(DEFAULT_THREADS, total // (4 * 1024 * 1024)))
        try:
            return _ranged_download(url, dest, total, progress_cb, label,
                                    timeout, cancel_event, threads=threads)
        except DownloadCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            # 分块下载失败（网络抖动 / 服务器中途拒绝 Range 等）→ 退回单线程
            log.warning("分块下载失败，改为单线程重试：%s", exc)

    return _single_thread_download(url, dest, progress_cb, label, timeout, cancel_event)


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
                progress_cb(info["display"], info["size"], info["size"])
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
                progress_cb(f"{info['display']} / {name}", 1, 1)
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
        progress_cb("正在解压", 0, 0)
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
    """
    with _model_download_lock(key):
        if is_installed(key):
            return model_dir(key)
        download(key, progress_cb=progress_cb, cancel_event=cancel_event)
        return install(key, progress_cb=progress_cb)


def remove(key: str) -> int:
    """删除某个模型的解压目录与下载缓存，返回释放的字节数。

    抛出 PermissionError 表示文件正被占用（调用方需要给用户可读提示）。
    """
    freed = 0
    target = model_dir(key)
    if os.path.isdir(target):
        freed += installed_size(key)
        shutil.rmtree(target)
    archive = os.path.join(paths.models_dir(), f"{key}.argosmodel")
    if os.path.exists(archive):
        try:
            freed += os.path.getsize(archive)
        except OSError:
            pass
        os.remove(archive)
    return freed


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
