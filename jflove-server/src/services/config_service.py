import aiosqlite
from src.repositories import config_repository

# 配置内存缓存（v1.4.0）：启动/首次读取后缓存全量配置，写操作后失效，
# 保证管理员在 C 端修改配置后**立即生效、无需重启服务端**。
_cache: dict[str, str] | None = None


def invalidate_cache() -> None:
    """清空配置内存缓存（配置写入后调用，强制下次读取走 DB）。"""
    global _cache
    _cache = None


async def _load_locked(db: aiosqlite.Connection) -> dict[str, str]:
    """读取全量配置并缓存（内部方法，调用方需处理并发安全）。"""
    global _cache
    if _cache is None:
        rows = await config_repository.get_all(db)
        _cache = {r["key"]: r["value"] for r in rows}
    return _cache


async def get(db: aiosqlite.Connection, key: str, default: str | None = None) -> str | None:
    """
    读取单个配置项（带内存缓存）。

    :param db: 数据库连接
    :param key: 配置键名
    :param default: 键不存在时返回的默认值
    :returns: 配置值；不存在且未给默认值时返回 None
    """
    cache = await _load_locked(db)
    return cache.get(key, default)


async def get_all(db: aiosqlite.Connection) -> dict:
    """
    获取全部配置项（带内存缓存），键值对字典。

    :param db: 数据库连接
    :returns: {key: value} 字典
    """
    return dict(await _load_locked(db))


async def update(db: aiosqlite.Connection, key: str, value: str) -> None:
    """
    更新配置项（upsert），写后使缓存失效。

    :param db: 数据库连接
    :param key: 配置键名
    :param value: 配置值
    """
    await config_repository.set(db, key, value)
    invalidate_cache()
