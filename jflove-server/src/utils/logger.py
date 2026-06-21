"""
日志工具模块

提供统一的日志记录器获取方法。
INFO 级别写入 app.log，ERROR 级别额外写入 error.log，均使用滚动文件策略。
"""

import logging
import logging.handlers
from src.config.settings import LOG_DIR
import os

_fmt = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的日志记录器。

    首次调用时自动添加控制台、app.log、error.log 三个 Handler；
    重复调用同名 logger 时直接返回已有实例，避免重复添加 Handler。

    :param name: 日志记录器名称，通常传入 __name__
    :returns: 配置完成的 Logger 实例
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    # 控制台输出
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(_fmt)
    logger.addHandler(ch)

    # INFO 及以上写入 app.log（最大 10MB，保留 5 个备份）
    fh = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, "app.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(logging.INFO)
    fh.setFormatter(_fmt)
    logger.addHandler(fh)

    # ERROR 及以上额外写入 error.log
    eh = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, "error.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    eh.setLevel(logging.ERROR)
    eh.setFormatter(_fmt)
    logger.addHandler(eh)

    return logger
