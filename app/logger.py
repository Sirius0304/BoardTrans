# -*- coding: utf-8 -*-
"""
瞬译 - 日志

日志写到数据目录的 logs/shunyi.log，按 1 MB 滚动，保留 3 份。
运行出错时先看这个文件。
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from . import paths

_logger: logging.Logger | None = None


def get_logger(name: str = "shunyi") -> logging.Logger:
    """获取全局日志对象（只初始化一次）。"""
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    try:
        file_handler = RotatingFileHandler(
            paths.log_path(), maxBytes=1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError:
        pass   # 日志目录不可写时不能影响主流程

    # 控制台输出：仅在直接运行脚本（非打包 exe、非 pythonw）时挂上
    if not paths.is_frozen() and sys.stderr is not None:
        try:
            stream = logging.StreamHandler(sys.stderr)
            stream.setFormatter(fmt)
            logger.addHandler(stream)
        except Exception:  # noqa: BLE001
            pass

    _logger = logger
    return logger


def log_exception(logger: logging.Logger, context: str) -> None:
    """记录异常堆栈（用于热键回调里兜底，避免异常被静默吞掉）。"""
    import traceback

    logger.error("%s 失败: %s", context, traceback.format_exc())
