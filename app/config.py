# -*- coding: utf-8 -*-
"""
键译 - 配置模块

配置保存在 %LOCALAPPDATA%\\BoardTrans\\config.json，用户可以手工编辑。
切换语言 / 档位时程序会把配置原子写回磁盘（先写临时文件再替换），
因此即使直接关机、程序被强杀，也不会出现"配置文件半截损坏"的情况。
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time

from . import paths

# 默认配置：所有键都带注释性说明，方便用户手工修改
DEFAULTS: dict = {
    # 触发翻译的快捷键。写法参考 keyboard 库：alt+j / ctrl+shift+t / f9
    "hotkey": "alt+j",
    # 目标语言：en（英文）/ ja（日文）
    "target_lang": "en",
    # 翻译档位：fast（高速度）/ quality（高质量）
    "quality": "fast",
    # 总开关，托盘菜单也能改
    "enabled": True,
    # 回写方式：paste（粘贴，快）/ type（逐字打字，慢但完全不碰剪贴板）
    "inject_mode": "paste",
    # 粘贴回写后是否恢复原剪贴板内容
    "restore_clipboard": True,
    # 取词方式：
    #   auto      —— 先用当前选中；没有选中就自动取「光标所在的整段」（推荐）
    #   selection —— 只用当前选中（没选中就报错，不给兜底）
    #   line      —— 只取光标到本行行首（适合"只翻这一句"）
    "capture_mode": "auto",
    # 单次取词的最大字符数，超过则不作翻译（防止误选整篇长文卡住）。
    # 2026-10-12 由 2000 提高到 20000：2000 会把"一个正常的长段落 / 一个 txt 文件"
    # 直接拒掉，表现就是"TXT 文件无法翻译"。翻译引擎本身能分段处理长文本，
    # 这个上限只是为了挡住"误按 Ctrl+A 选中整篇文档"这类误操作。
    "max_chars": 20000,
    # 模型下载源，"official" 为官方源，也可填自定义镜像前缀
    "download_mirror": "official",
    # CPU 推理线程数，0 = 自动
    "cpu_threads": 0,
    # 空闲多少分钟后自动释放内存里的翻译模型（0 = 一直常驻，不释放）
    # 实测：单个模型约占 97~141 MB 内存，两个都加载约 238 MB；
    # 释放后再翻译需要重新加载，实测约多花 0.15 秒，几乎无感。默认 15 分钟。
    "idle_unload_minutes": 15,
    # 模型存放位置。留空 = 自动（优先用「软件所在目录\models」，不可用时回退到数据目录）。
    # 也可填绝对路径自定义，例如 "D:\\BoardTransModels"。
    # 注意：路径不能含中文 —— sentencepiece 打不开非 ASCII 路径，填了会被自动忽略并回退。
    "models_root": "",
    # 「通知提醒」总开关：控制所有面向用户的提醒（托盘气泡通知）。
    # 关掉后：翻译完成、取词失败、快捷键冲突、模型下载完成/失败、启动提示等
    # 一律不再提示（只写日志）；开启时按各自场景正常提示。
    # 实现位置：app/notify.py（唯一入口，开关只在那里判定一次）。
    # 说明（2026-09-28）：旧键 notify_download_done 已废弃——它当时的语义是
    # "下载完成用弹窗还是用气泡"，不是"是否提醒"，是"关掉开关仍被提醒"这一
    # 缺陷的来源。旧键会被自动忽略（_merge 不认识的键不做处理）。
    "notify_enabled": True,
    # 「用户是否已经选中文字」的探测窗口（毫秒）。2026-10-16 新增（提速）。
    # 按快捷键后程序要先判断"你有没有选中文字"：默认 150ms 内没等到剪贴板变化
    # 就判定没选中、直接开始自动取段（整段选取），因此按键后几乎立刻开始选字。
    # 真正有选区时复制通常 10~60ms 完成，150ms 足够；
    # 个别超慢应用（超大选区/慢剪贴板）可调大到 600~1200，
    # 调到 ≥600 会自动恢复"两次尝试 + 重试等待"的保守策略（代价是常态变慢）。
    "selection_probe_ms": 150,
    # 日志里是否记录**用户原文/译文片段**（默认关，隐私优先，2026-10-18 新增）。
    # 日志文件在 %LOCALAPPDATA%\BoardTrans\logs，本来就不出本机；但用户排障时
    # 常把整份日志贴给别人或上传工单，里面带原文就等于把敲过的字一起给了出去。
    # 关着的时候日志只记「多少字」，排查问题一般够用；确实要看内容再临时打开。
    "log_user_text": False,
}

# 旧版本的 max_chars 默认值。仅用于"默认值升级"（见 _migrate_old_defaults）：
# 磁盘上存着这个值，说明用户从没改过它，可以安全地升到新默认值。
OLD_MAX_CHARS_DEFAULT = 2000


_lock = threading.RLock()
_cache: dict | None = None
# 磁盘上配置文件的"指纹"（mtime_ns, 大小），用于判断别的进程/手工编辑是否改过它
_stat_stamp: tuple | None = None


def _merge(base: dict, override: dict) -> dict:
    """用 override 覆盖 base，返回新字典；忽略 base 中不存在的键（防止手写错键）。"""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if key in result:
            result[key] = value
    return result


def _migrate_old_defaults(data: dict, merged: dict) -> bool:
    """把"旧版本默认值"平滑升级成"新版本默认值"。返回是否需要回写磁盘。

    为什么需要它（2026-10-12）：
    `_merge` 是"磁盘值优先"——用户配置里一旦存下某个键，**新版本的默认值就永远
    盖不过它**。max_chars 从 2000 提到 20000 时，老用户配置里存着 2000，
    于是修复对他们完全不生效（照旧被"内容太长，已跳过"挡掉）。

    规则很保守：**只有当磁盘值恰好等于旧默认值**（说明用户从没自己改过）
    才升级；用户改过的值一律不动。
    """
    changed = False
    # 注意：这里只能看"磁盘上的原始值"，**不能**再拿 merged 去比对新默认值 ——
    # merged 的 max_chars 恰恰来自磁盘（= 旧默认值 2000），加那条判断会让迁移
    # 永远不触发，等于没写。（第一版就是这么写错的，被回归测试抓出来了。）
    if data.get("max_chars") == OLD_MAX_CHARS_DEFAULT:
        merged["max_chars"] = DEFAULTS["max_chars"]
        changed = True
    return changed


def load(force: bool = False) -> dict:
    """读取配置（带内存缓存）。文件不存在时写入一份默认配置。"""
    global _cache
    with _lock:
        if _cache is not None and not force:
            return copy.deepcopy(_cache)
        path = paths.config_path()
        data = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                # 配置损坏时不要崩，退回默认值并备份坏文件
                try:
                    os.replace(path, path + f".broken-{int(time.time())}")
                except OSError:
                    pass
                data = {}
        raw = data if isinstance(data, dict) else {}
        merged = _merge(DEFAULTS, raw)
        # 说明：老配置里可能残留已删除功能的旧键 —— _merge 只认 DEFAULTS
        # 里有的键，残留键会被静默丢弃，无需迁移。
        if _migrate_old_defaults(raw, merged):
            # 磁盘值只是"旧默认值"，就地升级并回写，让新默认立刻生效
            _cache = merged
            save(merged)
            return copy.deepcopy(merged)
        _cache = merged
        if not os.path.exists(path):
            save(merged)
        return copy.deepcopy(merged)


def save(cfg: dict) -> None:
    """原子写入配置：临时文件 + os.replace（Windows 上也是原子操作）。"""
    global _cache, _stat_stamp
    path = paths.config_path()
    tmp = path + ".tmp"
    payload = _merge(DEFAULTS, cfg)
    with _lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)   # 原子替换，断电也不会写坏原文件
        _cache = payload
        # 记下自己写出来的文件指纹，避免紧接着被 load_if_changed() 当成"外部改动"再读一次盘
        try:
            st = os.stat(path)
            _stat_stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            _stat_stamp = None


def load_if_changed() -> dict:
    """配置文件在磁盘上被改过就重新读盘，否则直接用内存缓存。

    为什么要这个：程序可能同时跑着多个实例，用户也可能按文档手工编辑 config.json。
    内存缓存只在 save() 时更新，别的进程（或记事本）改了文件本进程是感知不到的——
    实测后果：在 A 实例把「通知提醒」关掉后，B 实例照旧提醒；手工改配置同样"不生效"。
    用 mtime + 文件大小做指纹，命中缓存时不额外读文件，开销可忽略。
    """
    global _stat_stamp
    path = paths.config_path()
    try:
        st = os.stat(path)
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None            # 文件被删了：也让 load(force=True) 走一遍（会重建默认配置）
    if stamp != _stat_stamp:
        _stat_stamp = stamp
        return load(force=True)
    return load()


def update(**kwargs) -> dict:
    """局部更新若干键并立即落盘，返回更新后的完整配置。"""
    cfg = load()
    cfg.update({k: v for k, v in kwargs.items() if k in DEFAULTS})
    save(cfg)
    return load(force=True)


def get(key: str, default=None):
    """读取单个配置项。"""
    return load().get(key, DEFAULTS.get(key, default))
