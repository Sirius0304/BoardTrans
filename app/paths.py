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
_models_root_cache: str | None = None


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


def _is_usable_dir(path: str) -> bool:
    """这个目录能不能用来放模型：必须**纯 ASCII**且可写。

    为什么要卡 ASCII：sentencepiece 是 C++ 实现，打不开非 ASCII 路径。
    实测（2026-09-27）把模型放到 F:\shunyi\\models\\ 下会直接报
        RuntimeError: NOT_FOUND: "...sentencepiece.model": No such file or directory
    ——文件明明存在，但它就是读不到。所以含中文的路径一律跳过。
    """
    if not path or not is_ascii(path):
        return False
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def default_models_root() -> str:
    """默认的模型根目录：**软件所在目录**下的 models/（让模型跟着软件走）。"""
    return os.path.join(program_dir(), "models")


def _configured_models_root() -> str:
    """读配置里用户自定义的模型目录（延迟导入 config，避免循环依赖）。"""
    try:
        from . import config
        return str(config.load().get("models_root") or "")
    except Exception:  # noqa: BLE001
        return ""


def models_root() -> str:
    """模型根目录：下载缓存与解压后的模型都放在它下面。结果会缓存。

    优先级：
      1. 环境变量 SHUNYI_MODELS_DIR   —— 高级用户强制指定
      2. 配置项 models_root           —— 设置界面 / 手工编辑 config.json
      3. 软件所在目录\\models          —— **默认**，模型跟随软件
      4. 回退到程序数据目录            —— 前三个都不可用时（例如软件装在含中文的目录）

    重要：前三个候选只要不满足"纯 ASCII + 可写"就会被跳过。
    也就是说，如果软件放在含中文的路径下（如 F:\shunyi\\），
    模型会自动回退到数据目录，而不是错误地放到读不了的地方。
    """
    global _models_root_cache
    if _models_root_cache is not None:
        return _models_root_cache

    for candidate in (os.environ.get("SHUNYI_MODELS_DIR"),
                      _configured_models_root(),
                      default_models_root()):
        if _is_usable_dir(candidate):
            _models_root_cache = candidate
            return candidate

    _models_root_cache = data_dir()
    return _models_root_cache


def reset_models_root_cache() -> None:
    """配置里改了模型目录后调用，让下次重新探测（下次取路径时生效）。"""
    global _models_root_cache
    _models_root_cache = None
    _sub_dir_cache.clear()


def _subdir(name: str) -> str:
    """数据目录下的子目录（带缓存，避免重复创建）。"""
    cached = _sub_dir_cache.get(name)
    if cached is not None:
        return cached
    path = os.path.join(data_dir(), name)
    os.makedirs(path, exist_ok=True)
    _sub_dir_cache[name] = path
    return path


def _models_subdir(name: str) -> str:
    """模型根目录下的子目录（带缓存）。"""
    key = "models:" + name
    cached = _sub_dir_cache.get(key)
    if cached is not None:
        return cached
    path = os.path.join(models_root(), name)
    os.makedirs(path, exist_ok=True)
    _sub_dir_cache[key] = path
    return path


def models_dir() -> str:
    """下载的 .argosmodel 包缓存目录。"""
    return _models_subdir("cache")


def packages_dir() -> str:
    """解压后的可用模型目录，每个语言对一个子目录。"""
    return _models_subdir("packages")


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
