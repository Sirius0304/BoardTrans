# -*- coding: utf-8 -*-
"""
键译 - 路径管理模块

设计要点（重要）：
1. 所有运行时数据（配置、模型、日志）统一放在 %LOCALAPPDATA%\\BoardTrans 下。
   原因有二：
   - 程序可能被打包成 exe 放在任意位置，写程序目录可能没权限（Program Files）；
   - 关键：sentencepiece 的 C++ 底层无法打开包含中文的路径，
     所以数据目录必须是纯英文（ASCII）路径。
   如果用户名本身是中文，%LOCALAPPDATA% 也会含中文，这里会自动降级到
   C:\\ProgramData\\BoardTrans，并在日志中给出提示。
2. 程序自身的目录（代码 / exe 所在位置）与数据目录分离，卸载时删掉数据目录即可。
"""

from __future__ import annotations

import os
import shutil
import sys

APP_NAME = "BoardTrans"
APP_NAME_CN = "键译"

# 改名前的标识（2026-10-09：瞬译/ShunYi → 键译/BoardTrans）。
# 用途只有一个：**首次启动时把老用户的配置自动搬过来**，避免换了名字就丢设置。
_LEGACY_APP_NAME = "ShunYi"
_LEGACY_ENV_NAMES = ("SHUNYI_DATA_DIR", "SHUNYI_MODELS_DIR")

# 目录探测结果缓存。
# 重要（性能）：data_dir() 首次调用会做一次"写文件探针"测试，
# 在开启实时防护的 Windows 上这一下要 ~100 ms 甚至更多，
# 如果每次取路径都跑一遍，会直接拖慢每一次翻译。
# 因此这里只探测一次，之后全部走缓存。
_data_dir_cache: str | None = None
_sub_dir_cache: dict[str, str] = {}
_models_root_cache: str | None = None
# 缓存对应的"来源值"（环境变量 + 配置项 models_root）。
# 为什么要有它（2026-09-29 修复）：models_root() 的探测结果被缓存后永不失效，
# 而全项目没有任何地方调用 reset_models_root_cache() —— 于是运行中改了配置
# （设置界面保存 / 手工编辑 config.json / 另一实例写入）都不会生效，必须重启。
# 现在把缓存和"来源值"绑定：来源变了就重新探测，没变就继续用缓存（探测要写
# 文件探针，很慢，不能每次都跑）。
_models_root_key: tuple[str, str] | None = None


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


def _env_first(*names: str) -> str:
    """按顺序取第一个非空的环境变量值。

    用于改名后的兼容：新名 BOARDTRANS_* 优先，旧名 SHUNYI_* 仍认 ——
    老用户既有的环境变量设置不会因为改名就失效。
    """
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return ""


def _candidate_data_dirs() -> list[str]:
    """按优先级列出候选数据目录，第一个纯 ASCII 且可写的会被选中。

    用户也可以通过环境变量 BOARDTRANS_DATA_DIR 强制指定数据目录，
    例如 Windows 用户名是中文时：
        setx BOARDTRANS_DATA_DIR "C:\\BoardTransData"
    （改名前的旧名 SHUNYI_DATA_DIR 仍然兼容。）
    """
    candidates = []
    custom = _env_first("BOARDTRANS_DATA_DIR", "SHUNYI_DATA_DIR")
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


def _legacy_data_dirs() -> list[str]:
    """改名前的数据目录候选（只在自动迁移时用一次）。"""
    out = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(os.path.join(local, _LEGACY_APP_NAME))
    out.append(os.path.join(os.path.expanduser("~"), f".{_LEGACY_APP_NAME}"))
    programdata = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    out.append(os.path.join(programdata, _LEGACY_APP_NAME))
    out.append(os.path.join("C:\\", _LEGACY_APP_NAME + "Data"))
    return out


def _migrate_legacy_data(target: str) -> None:
    """改名后的一次性数据迁移：把旧名目录里的用户数据**复制**过来。

    触发条件（三者都满足才动）：
      1. 目标目录里还没有 config.json（否则是已有用户，绝不覆盖）；
      2. 某个旧名目录里存在 config.json（说明那边确实用过）；
      3. 两边不是同一个目录。

    为什么是复制而不是移动：旧数据留作保险，万一新目录有问题还能回退。
    为什么跳过 models/：模型动辄几百 MB，首次启动拷这么大会卡很久；
    而现在的模型默认放在「软件目录\\models」，不在数据目录里，
    真要遇到旧目录里有模型的情况，日志里会明确写出位置让用户自行处理。
    """
    if os.path.isfile(os.path.join(target, "config.json")):
        return
    for old in _legacy_data_dirs():
        if os.path.abspath(old) == os.path.abspath(target):
            continue
        if not os.path.isfile(os.path.join(old, "config.json")):
            continue
        moved = []
        try:
            for name in os.listdir(old):
                src = os.path.join(old, name)
                dst = os.path.join(target, name)
                if name == "models" or os.path.exists(dst):
                    continue
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
                moved.append(name)
        except Exception:  # noqa: BLE001
            pass
        try:
            from . import logger
            logger.get().info("已从旧数据目录迁移用户数据：%s → %s（%s）",
                              old, target, ", ".join(moved) or "无")
            if os.path.isdir(os.path.join(old, "models")):
                logger.get().warning(
                    "旧数据目录里还有模型文件夹，未随迁移复制（体积大）：%s",
                    os.path.join(old, "models"))
        except Exception:  # noqa: BLE001
            pass
        return


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
            # 改名后的一次性迁移：只在"用默认候选目录"时做（见函数内说明）
            if not _env_first("BOARDTRANS_DATA_DIR", "SHUNYI_DATA_DIR"):
                _migrate_legacy_data(path)
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
    实测（2026-09-27）把模型放到 F:\\键译\\models\\ 下会直接报
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
    """读配置里用户自定义的模型目录（延迟导入 config，避免循环依赖）。

    用 load_if_changed：手工编辑 config.json、或另一个实例改了配置，
    这里都能立刻拿到新值（内部有 mtime+大小指纹，没变时不会真读文件）。
    """
    try:
        from . import config
        return str(config.load_if_changed().get("models_root") or "")
    except Exception:  # noqa: BLE001
        return ""


def models_root() -> str:
    """模型根目录：下载缓存与解压后的模型都放在它下面。结果会缓存。

    优先级：
      1. 环境变量 BOARDTRANS_MODELS_DIR —— 高级用户强制指定（旧名 SHUNYI_MODELS_DIR 兼容）
      2. 配置项 models_root           —— 设置界面 / 手工编辑 config.json
      3. 软件所在目录\\models          —— **默认**，模型跟随软件
      4. 回退到程序数据目录            —— 前三个都不可用时（例如软件装在含中文的目录）

    重要：前三个候选只要不满足"纯 ASCII + 可写"就会被跳过。
    也就是说，如果软件放在含中文的路径下（如 F:\\键译\\），
    模型会自动回退到数据目录，而不是错误地放到读不了的地方。

    缓存策略：结果与"来源值"（环境变量 + 配置项）绑定 —— 来源没变就用缓存
    （探测要写文件探针，不能每次跑）；来源变了（含手工编辑 config.json、
    其它实例写入）就自动重新探测，无需重启、也无需谁记得调用 reset。
    """
    global _models_root_cache, _models_root_key
    env_val = _env_first("BOARDTRANS_MODELS_DIR", "SHUNYI_MODELS_DIR")
    cfg_val = _configured_models_root()
    key = (env_val, cfg_val)
    if _models_root_cache is not None and key == _models_root_key:
        return _models_root_cache

    # 来源变了（或首次调用）：清掉旧的 models:* 子目录缓存再重新探测
    for name in [k for k in _sub_dir_cache if k.startswith("models:")]:
        _sub_dir_cache.pop(name, None)

    for candidate in (env_val, cfg_val, default_models_root()):
        if _is_usable_dir(candidate):
            _models_root_cache = candidate
            _models_root_key = key
            return candidate

    _models_root_cache = data_dir()
    _models_root_key = key
    return _models_root_cache


def reset_models_root_cache() -> None:
    """强制清掉模型目录缓存（下次取路径时重新探测）。测试用；正常运行不需要。"""
    global _models_root_cache, _models_root_key
    _models_root_cache = None
    _models_root_key = None
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
