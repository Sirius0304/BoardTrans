# -*- coding: utf-8 -*-
"""
瞬译 - 路径管理模块

设计要点（重要）：
1. 所有运行时数据（配置、模型、日志）统一放在 %LOCALAPPDATA%\\ShunYi 下。
   原因有二：
   - 程序可能被打包成 exe 放在任意位置，写程序目录可能没权限（Program Files）；
   - 关键：sentencepiece 的 C++ 底层无法打开包含中文的路径，
     所以数据目录必须是纯英文（ASCII）路径。
   如果用户名本身是中文，%LOCALAPPDATA% 也会含中文，这里会自动降级到
   C:\\ProgramData\\ShunYi，并在日志中给出提示。
2. 程序自身的目录（代码 / exe 所在位置）与数据目录分离，卸载时删掉数据目录即可。
"""

from __future__ import annotations

import os
import sys

APP_NAME = "ShunYi"
APP_NAME_CN = "瞬译"

# 目录探测结果缓存。
# 重要（性能）：data_dir() 首次调用会做一次"写文件探针"测试，
# 在开启实时防护的 Windows 上这一下要 ~100 ms 甚至更多，
# 如果每次取路径都跑一遍，会直接拖慢每一次翻译。
# 因此这里只探测一次，之后全部走缓存。
_data_dir_cache: str | None = None
_sub_dir_cache: dict[str, str] = {}


def is_ascii(text: str) -> bool:
    """判断路径是否只含 ASCII 字符。"""
    try:
        text.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包后的 exe 中。"""
    return bool(getattr(sys, "frozen", False))


def program_dir() -> str:
    """程序所在目录（exe 目录，或源码目录的上一级）。"""
    if is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _candidate_data_dirs() -> list[str]:
    """按优先级列出候选数据目录，第一个纯 ASCII 且可写的会被选中。

    用户也可以通过环境变量 SHUNYI_DATA_DIR 强制指定数据目录，
    例如 Windows 用户名是中文时：
        setx SHUNYI_DATA_DIR "C:\\ShunYiData"
    """
    candidates = []
    custom = os.environ.get("SHUNYI_DATA_DIR")
    if custom:
        candidates.append(custom)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, APP_NAME))
    candidates.append(os.path.join(os.path.expanduser("~"), f".{APP_NAME}"))
    programdata = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    candidates.append(os.path.join(programdata, APP_NAME))
    candidates.append(os.path.join("C:\\", APP_NAME + "Data"))
    return candidates


def data_dir() -> str:
    """返回可写的数据目录（纯 ASCII 优先）。结果会被缓存，只探测一次。"""
    global _data_dir_cache
    if _data_dir_cache is not None:
        return _data_dir_cache

    fallback = None
    for path in _candidate_data_dirs():
        fallback = fallback or path
        if not is_ascii(path):
            continue
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".write_test")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            _data_dir_cache = path
            return path
        except OSError:
            continue
    # 全部失败时返回兜底目录（后续读写会抛错，日志里能看出来）
    os.makedirs(fallback, exist_ok=True)
    _data_dir_cache = fallback
    return fallback


def _subdir(name: str) -> str:
    """数据目录下的子目录（带缓存，避免重复创建）。"""
    cached = _sub_dir_cache.get(name)
    if cached is not None:
        return cached
    path = os.path.join(data_dir(), name)
    os.makedirs(path, exist_ok=True)
    _sub_dir_cache[name] = path
    return path


def models_dir() -> str:
    """下载的 .argosmodel 包缓存目录。"""
    return _subdir("packages_cache")


def packages_dir() -> str:
    """解压后的可用模型目录，每个语言对一个子目录。"""
    return _subdir("packages")


def package_path(from_code: str, to_code: str) -> str:
    """某个语言对解压后的目录。"""
    return os.path.join(packages_dir(), f"{from_code}_{to_code}")


def config_path() -> str:
    """配置文件路径。"""
    return os.path.join(data_dir(), "config.json")


def logs_dir() -> str:
    """日志目录。"""
    return _subdir("logs")


def log_path() -> str:
    """主日志文件路径。"""
    return os.path.join(logs_dir(), "shunyi.log")


def describe() -> str:
    """给用户看的路径说明。"""
    return (
        f"程序目录: {program_dir()}\n"
        f"数据目录: {data_dir()}\n"
        f"配置    : {config_path()}\n"
        f"日志    : {log_path()}"
    )
