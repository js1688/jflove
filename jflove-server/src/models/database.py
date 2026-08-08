import aiosqlite
from datetime import datetime, timezone

from src.config.settings import (
    DB_PATH,
    MEDIA_REPAIR_ENABLED_KEY,
    MEDIA_REPAIR_TRANSCODE_KEY,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

_CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    NOT NULL UNIQUE,
    password_hash TEXT  NOT NULL,
    role        TEXT    NOT NULL DEFAULT 'user',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    deleted_at  TEXT
)
"""

_CREATE_VIRTUAL_DISKS = """
CREATE TABLE IF NOT EXISTS virtual_disks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    real_path   TEXT    NOT NULL,
    created_by  INTEGER NOT NULL,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    deleted_at  TEXT
)
"""

_CREATE_USER_PERMISSIONS = """
CREATE TABLE IF NOT EXISTS user_permissions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    virtual_disk_id INTEGER NOT NULL,
    can_read        INTEGER NOT NULL DEFAULT 0,
    can_write       INTEGER NOT NULL DEFAULT 0,
    can_delete      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    deleted_at      TEXT
)
"""

_CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    session_id     TEXT    NOT NULL UNIQUE,
    jwt_token_hash TEXT    NOT NULL,
    expires_at     TEXT    NOT NULL,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL,
    deleted_at     TEXT
)
"""

_CREATE_CONFIG = """
CREATE TABLE IF NOT EXISTS config (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL UNIQUE,
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS users_username_idx ON users(username)",
    "CREATE INDEX IF NOT EXISTS sessions_session_id_idx ON sessions(session_id)",
    "CREATE INDEX IF NOT EXISTS user_permissions_user_id_idx ON user_permissions(user_id)",
    "CREATE INDEX IF NOT EXISTS user_permissions_disk_id_idx ON user_permissions(virtual_disk_id)",
]


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_CREATE_USERS)
        await db.execute(_CREATE_VIRTUAL_DISKS)
        await db.execute(_CREATE_USER_PERMISSIONS)
        # 注：v1.x 移除"笔记目录权限"概念——所有登录用户均可使用笔记功能；
        # 每个用户的笔记目录由 users.notes_disk_id / notes_path 字段独立配置；
        # 历史 notes_permissions 表保留（不再读写），不影响新部署。
        await db.execute(_CREATE_SESSIONS)
        await db.execute(_CREATE_CONFIG)
        for idx in _INDEXES:
            await db.execute(idx)
        # 运行时迁移：为 users 表添加缺失字段（已存在则跳过）
        async with db.execute("PRAGMA table_info(users)") as cur:
            columns = [row[1] async for row in cur]
        if "notes_disk_id" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN notes_disk_id INTEGER")
        if "notes_path" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN notes_path TEXT NOT NULL DEFAULT ''")
        # v1.1.6：完全移除 sync_configs 表（同步配置改为客户端本地存储）
        exists = None
        async with db.execute(
            "SELECT name FROM sqlite_master"
            " WHERE type='table' AND name='sync_configs'"
        ) as cur:
            exists = await cur.fetchone()
        if exists:
            await db.execute("DROP TABLE IF EXISTS sync_configs")
            await db.execute("DROP INDEX IF EXISTS sync_configs_user_id_idx")
            logger.info("运行时迁移：已删除 sync_configs 表（v1.1.6）")
        # v1.4.0：幂等初始化媒体修复配置默认键（已存在则跳过，不覆盖管理员设置）
        now = datetime.now(timezone.utc).isoformat()
        default_configs = [
            (MEDIA_REPAIR_ENABLED_KEY, "0"),
            (MEDIA_REPAIR_TRANSCODE_KEY, "0"),
        ]
        for key, value in default_configs:
            async with db.execute(
                "SELECT 1 FROM config WHERE key = ? AND deleted_at IS NULL", (key,)
            ) as cur:
                if await cur.fetchone() is None:
                    await db.execute(
                        "INSERT INTO config (key, value, created_at, updated_at)"
                        " VALUES (?, ?, ?, ?)",
                        (key, value, now, now),
                    )
                    logger.info("运行时迁移：初始化配置键 %s=%s（v1.4.0）", key, value)
        await db.commit()


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()
