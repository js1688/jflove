"""
媒体修复配置服务（v1.4.0 引入，v1.4.2 重构）

v1.4.2 架构变更：实时修复流（stream_repaired_frames / ensure_playable 的
time 分支）已整体移除，播放路径零 ffmpeg。本模块仅保留离线修复所需的
配置读取能力（repair_task_service 使用）：

  - get_concurrent_limit：离线修复队列并发上限（config 键
    media_repair_max_concurrent，管理员可调；未配置按 CPU 核数自动基线）
  - is_transcode_enabled：重编码子开关（预留：-c copy 失败降级重编码）

历史配置键 media_repair_enabled（实时修复总开关）已废弃：键保留在库中
不删，但任何代码不再读取，播放与修复行为均不受其影响。

安全约束：修复只读源文件、ffmpeg 参数列表（无 shell 拼接）、
日志不记录路径/文件名明文。
"""

import aiosqlite

from src.config.settings import (
    MEDIA_REPAIR_CONCURRENT_HARD_MAX,
    MEDIA_REPAIR_CONCURRENT_KEY,
    MEDIA_REPAIR_TRANSCODE_KEY,
    MEDIA_REPAIR_AUTO_CONCURRENT_BASE,
)
from src.services import config_service

# ── 配置读取（config 表 + 内存缓存，写后立即生效）────────────


async def is_transcode_enabled(db: aiosqlite.Connection) -> bool:
    """读取重编码子开关（config 表，默认关闭）。"""
    value = await config_service.get(db, MEDIA_REPAIR_TRANSCODE_KEY, "0")
    return value == "1"


async def get_concurrent_limit(db: aiosqlite.Connection) -> int:
    """
    读取修复并发数上限。

    管理员配置（media_repair_max_concurrent，1~硬上限）优先；未配置时使用自动基线
    （按 CPU 核数推导，见 settings.MEDIA_REPAIR_AUTO_CONCURRENT_BASE）。

    :param db: 数据库连接
    :returns: 并发数上限（≥1，≤ MEDIA_REPAIR_CONCURRENT_HARD_MAX）
    """
    raw = await config_service.get(db, MEDIA_REPAIR_CONCURRENT_KEY, default=None)
    if raw and raw.strip().isdigit():
        return max(1, min(int(raw.strip()), MEDIA_REPAIR_CONCURRENT_HARD_MAX))
    return MEDIA_REPAIR_AUTO_CONCURRENT_BASE
