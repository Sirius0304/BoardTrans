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
import time
import urllib.error
import urllib.request
import zipfile

from . import paths

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ShunYi/1.0"


class DownloadCancelled(Exception):
    """用户取消下载。"""


def _is_cancelled(cancel_event) -> bool:
    return bool(cancel_event is not None and cancel_event.is_set())

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


def _open_with_progress(url: str, dest: str, progress_cb, label: str, timeout: int = 120, cancel_event=None):
    """下载单个文件到 dest，支持断点续传与取消；已完整存在则跳过。

    取消时抛出 DownloadCancelled，并保留已下载的分片（下次可续传）。
    """
    if _is_cancelled(cancel_event):
        raise DownloadCancelled("已取消下载")

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
        if content_length:
            expect = existing + int(content_length)
        mode = "ab" if existing else "wb"
        got = existing
        last_report = 0.0
        with open(dest, mode) as f:
            while True:
                if _is_cancelled(cancel_event):
                    raise DownloadCancelled("已取消下载")
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                f.flush()          # 及时落盘：取消下载或意外中断时，已下载部分不丢，下次可续传
                got += len(chunk)
                now = time.time()
                if progress_cb and now - last_report > 0.2:
                    last_report = now
                    progress_cb(label, got, expect)
    if expect and got < expect:
        raise IOError(f"下载不完整: {got}/{expect}")
    return got


def download(key: str, progress_cb=None, retries: int = 3, cancel_event=None) -> str:
    """下载模型到本地缓存目录。progress_cb(label, downloaded, total)。支持中途取消。"""
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
    """确保模型可用：已安装直接返回，否则下载 + 安装。支持中途取消。"""
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
