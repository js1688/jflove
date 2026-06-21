"""
日志工具模块

提供统一的日志记录器，INFO 级别写入 app.log，ERROR 级别写入 error.log。
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from src.config.settings import LOG_DIR


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的日志记录器。

    :param name: 模块名称（通常传入 __name__）
    :returns: 配置好的 Logger 实例
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # INFO 级别 → app.log（滚动，最大 5MB，保留 3 个备份）
    app_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "app.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(fmt)

    # ERROR 级别 → error.log
    err_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "error.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    err_handler.setLevel(logging.ERROR)
    err_handler.setFormatter(fmt)

    # 控制台输出（开发调试用）
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(fmt)

    logger.addHandler(app_handler)
    logger.addHandler(err_handler)
    logger.addHandler(console_handler)
    return logger
