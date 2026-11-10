"""
上传临时文件清理配置与调度服务（v1.9.0）

职责：
  - 读取 `config` 表中的断点续传/清理配置（带默认值与硬边界）；
  - 校验管理员写入的配置值（`config_controller` 调用）；
  - 触发一次安全清理（实际文件操作在 `file_service.cleanup_temp_files`）；
  - 配置变更后刷新合并/分片上传信号量。

设计依据：`文档记录/设计文档/v1.9.0.md` §2.10、`文档记录/需求文档/v1.9.0.md` F51/F52。
安全硬约束（D31）：只清程序自产、固定命名、位于允许根内的临时文件；
不得按「目录里有文件就清空」的泛化规则执行。
"""

from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

from src.config.settings import (
    UPLOAD_CHUNK_CONCURRENCY_KEY,
    UPLOAD_CLEANUP_CLIENT_TYPES_KEY,
    UPLOAD_CLEANUP_ENABLED_KEY,
    UPLOAD_CLEANUP_INTERVAL_KEY,
    UPLOAD_CONFIG_DEFAULTS,
    UPLOAD_DEFAULT_CHUNK_CONCURRENCY,
    UPLOAD_DEFAULT_CLEANUP_INTERVAL_MINUTES,
    UPLOAD_DEFAULT_MAX_SESSIONS_PER_USER,
    UPLOAD_DEFAULT_MERGE_CONCURRENCY,
    UPLOAD_DEFAULT_RESUME_TTL_HOURS,
    UPLOAD_LEGACY_KEYS,
    UPLOAD_MAX_CHUNK_CONCURRENCY,
    UPLOAD_MAX_CLEANUP_INTERVAL_MINUTES,
    UPLOAD_MAX_MAX_SESSIONS_PER_USER,
    UPLOAD_MAX_MERGE_CONCURRENCY,
    UPLOAD_MAX_ORPHAN_TTL_HOURS,
    UPLOAD_MAX_RESUME_TTL_HOURS,
    UPLOAD_MAX_SESSIONS_PER_USER_KEY,
    UPLOAD_MERGE_CONCURRENCY_KEY,
    UPLOAD_MIN_CLEANUP_INTERVAL_MINUTES,
    UPLOAD_MIN_MAX_SESSIONS_PER_USER,
    UPLOAD_MIN_ORPHAN_TTL_HOURS,
    UPLOAD_MIN_RESUME_TTL_HOURS,
    UPLOAD_ORPHAN_TTL_KEY,
    UPLOAD_RESUME_TTL_KEY,
    UPLOAD_CLIENT_TYPES,
)
from src.services import config_service
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class UploadCleanupOptions:
    """一次清理/限流所用的配置快照（全部已过边界校验）。"""

    resume_ttl_hours: int
    cleanup_enabled: bool
    cleanup_interval_minutes: int
    cleanup_client_types: frozenset[str]  # 空集合 = 全部类型
    orphan_ttl_hours: int
    max_sessions_per_user: int
    merge_concurrency: int
    chunk_concurrency: int

    @property
    def resume_ttl_seconds(self) -> int:
        """断点续传保障时长（秒）：唯一 TTL 来源。"""
        return self.resume_ttl_hours * 3600

    @property
    def orphan_ttl_seconds(self) -> int:
        """孤儿临时文件保留期（秒）。"""
        return self.orphan_ttl_hours * 3600

    @property
    def cleanup_interval_seconds(self) -> int:
        """自动清理周期（秒）。"""
        return self.cleanup_interval_minutes * 60

    def allows_client_type(self, client_type: str) -> bool:
        """
        判断某客户端类型产生的临时文件是否允许被自动清理。

        :param client_type: app / desktop / web / unknown
        :returns: 空集合（未配置）= 全部允许；否则必须命中集合
        """
        if not self.cleanup_client_types:
            return True
        return client_type in self.cleanup_client_types


def _safe_int(value: str | None, default: int, low: int, high: int) -> int:
    """把配置字符串转成受边界约束的整数（脏值回退默认值）。"""
    try:
        parsed = int(str(value).strip()) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


async def get_options(db: aiosqlite.Connection) -> UploadCleanupOptions:
    """
    读取清理配置（带默认值与边界夹紧）。

    v1.9.1 起只读取 3 个键（期限 / 总开关 / 间隔），其余行为固定：
      - 清理全部客户端类型（不做白名单过滤）；
      - 孤儿临时文件保留期 = 临时文件期限（同一个数）；
      - 会话上限 / 合并并发 / 分片并发 = 服务端默认值（管理端不再暴露）。

    :param db: 数据库连接
    :returns: 配置快照（不可变）
    """
    values: dict[str, str] = {}
    for key, default in UPLOAD_CONFIG_DEFAULTS.items():
        values[key] = await config_service.get(db, key, default) or default

    resume_ttl = _safe_int(
        values.get(UPLOAD_RESUME_TTL_KEY), UPLOAD_DEFAULT_RESUME_TTL_HOURS,
        UPLOAD_MIN_RESUME_TTL_HOURS, UPLOAD_MAX_RESUME_TTL_HOURS,
    )
    enabled = (values.get(UPLOAD_CLEANUP_ENABLED_KEY) or "1").strip() in ("1", "true", "True")
    interval = _safe_int(
        values.get(UPLOAD_CLEANUP_INTERVAL_KEY), UPLOAD_DEFAULT_CLEANUP_INTERVAL_MINUTES,
        UPLOAD_MIN_CLEANUP_INTERVAL_MINUTES, UPLOAD_MAX_CLEANUP_INTERVAL_MINUTES,
    )
    return UploadCleanupOptions(
        resume_ttl_hours=resume_ttl,
        cleanup_enabled=enabled,
        cleanup_interval_minutes=interval,
        cleanup_client_types=frozenset(),          # 空集合 = 全部类型
        orphan_ttl_hours=resume_ttl,               # 统一期限
        max_sessions_per_user=UPLOAD_DEFAULT_MAX_SESSIONS_PER_USER,
        merge_concurrency=UPLOAD_DEFAULT_MERGE_CONCURRENCY,
        chunk_concurrency=UPLOAD_DEFAULT_CHUNK_CONCURRENCY,
    )


def _validate_int(key: str, value: str, low: int, high: int) -> str:
    """校验整型配置值并返回规范化字符串（越界抛 ValueError）。"""
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} 必须是整数") from exc
    if parsed < low or parsed > high:
        raise ValueError(f"{key} 必须在 {low}~{high} 之间")
    return str(parsed)


def validate_config_value(key: str, value: str) -> str:
    """
    校验上传清理配置键的取值（供 config_controller 调用）。

    :param key: 配置键
    :param value: 客户端提交的字符串值
    :returns: 规范化后的值；非上传清理键原样返回
    :raises ValueError: 取值非法
    """
    if key not in UPLOAD_CONFIG_DEFAULTS and key not in UPLOAD_LEGACY_KEYS:
        return value
    raw = "" if value is None else str(value).strip()
    if key == UPLOAD_RESUME_TTL_KEY:
        return _validate_int(key, raw, UPLOAD_MIN_RESUME_TTL_HOURS, UPLOAD_MAX_RESUME_TTL_HOURS)
    if key == UPLOAD_CLEANUP_ENABLED_KEY:
        if raw not in ("0", "1"):
            raise ValueError(f"{key} 只能是 0 或 1")
        return raw
    if key == UPLOAD_CLEANUP_INTERVAL_KEY:
        return _validate_int(
            key, raw, UPLOAD_MIN_CLEANUP_INTERVAL_MINUTES, UPLOAD_MAX_CLEANUP_INTERVAL_MINUTES
        )
    if key == UPLOAD_ORPHAN_TTL_KEY:
        return _validate_int(key, raw, UPLOAD_MIN_ORPHAN_TTL_HOURS, UPLOAD_MAX_ORPHAN_TTL_HOURS)
    if key == UPLOAD_MAX_SESSIONS_PER_USER_KEY:
        return _validate_int(
            key, raw, UPLOAD_MIN_MAX_SESSIONS_PER_USER, UPLOAD_MAX_MAX_SESSIONS_PER_USER
        )
    if key == UPLOAD_MERGE_CONCURRENCY_KEY:
        return _validate_int(key, raw, 0, UPLOAD_MAX_MERGE_CONCURRENCY)
    if key == UPLOAD_CHUNK_CONCURRENCY_KEY:
        return _validate_int(key, raw, 1, UPLOAD_MAX_CHUNK_CONCURRENCY)
    if key == UPLOAD_CLEANUP_CLIENT_TYPES_KEY:
        items = [item.strip().lower() for item in raw.split(",") if item.strip()]
        invalid = [item for item in items if item not in UPLOAD_CLIENT_TYPES]
        if invalid:
            raise ValueError(f"{key} 含非法客户端类型: {','.join(invalid)}")
        # 去重 + 固定顺序，保证三端展示一致
        ordered = [item for item in UPLOAD_CLIENT_TYPES if item in set(items)]
        return ",".join(ordered)
    return raw


async def refresh_limits(db: aiosqlite.Connection | None = None) -> None:
    """
    刷新上传合并/分片信号量（配置写入后调用，立即生效）。

    :param db: 可选数据库连接；不传时只按当前配置刷新（需 db 才能读库）
    """
    if db is None:
        return
    options = await get_options(db)
    from src.services import file_service  # 局部导入，避免循环依赖

    file_service.refresh_upload_limits(options.merge_concurrency, options.chunk_concurrency)


async def run_cleanup(db: aiosqlite.Connection) -> dict:
    """
    执行一次安全清理（管理员「立即清理」入口）。

    v1.9.1 语义：**不受自动清理总开关限制** —— 总开关只决定定时任务是否执行，
    管理员点「立即清理」应当立刻清一次。定时任务请用 `run_auto_cleanup`。

    :param db: 数据库连接
    :returns: 清理统计字典（不含文件名/路径）
    """
    options = await get_options(db)
    from src.services import file_service  # 局部导入，避免循环依赖

    result = await file_service.cleanup_temp_files(db, options)
    result["enabled"] = True
    return result


async def run_auto_cleanup(db: aiosqlite.Connection) -> dict:
    """
    定时任务入口：自动清理总开关为关时直接跳过（不做任何删除）。

    :param db: 数据库连接
    :returns: 清理统计字典；开关关闭时 `enabled=False` 且统计全 0
    """
    options = await get_options(db)
    if not options.cleanup_enabled:
        return {
            "enabled": False, "removed_sessions": 0, "removed_dirs": 0,
            "removed_parts": 0, "skipped": 0,
        }
    return await run_cleanup(db)
