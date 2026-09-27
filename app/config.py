# -*- coding: utf-8 -*-
"""
瞬译 - 配置模块

配置保存在 %LOCALAPPDATA%\\ShunYi\\config.json，用户可以手工编辑。
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
    # 取词方式：auto（先用当前选中，没有则自动选到行首）/ selection（只用当前选中）
    "capture_mode": "auto",
    # 单次取词的最大字符数，超过则截断（防止误选整篇长文卡住）
    "max_chars": 2000,
    # 模型下载源，"official" 为官方源，也可填自定义镜像前缀
    "download_mirror": "official",
    # CPU 推理线程数，0 = 自动
    "cpu_threads": 0,
}

_lock = threading.RLock()
_cache: dict | None = None


def _merge(base: dict, override: dict) -> dict:
    """用 override 覆盖 base，返回新字典；忽略 base 中不存在的键（防止手写错键）。"""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if key in result:
            result[key] = value
    return result


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
        merged = _merge(DEFAULTS, data if isinstance(data, dict) else {})
        _cache = merged
        if not os.path.exists(path):
            save(merged)
        return copy.deepcopy(merged)


def save(cfg: dict) -> None:
    """原子写入配置：临时文件 + os.replace（Windows 上也是原子操作）。"""
    global _cache
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


def update(**kwargs) -> dict:
    """局部更新若干键并立即落盘，返回更新后的完整配置。"""
    cfg = load()
    cfg.update({k: v for k, v in kwargs.items() if k in DEFAULTS})
    save(cfg)
    return load(force=True)


def get(key: str, default=None):
    """读取单个配置项。"""
    return load().get(key, DEFAULTS.get(key, default))
