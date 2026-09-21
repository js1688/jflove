import re
from datetime import datetime, timezone

import aiosqlite

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
    username    TEXT    NOT NULL,
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

# v1.4.2：手动离线媒体修复任务表（全平台共享，不做账户归属隔离——user_id 仅供展示）
_CREATE_MEDIA_REPAIR_TASKS = """
CREATE TABLE IF NOT EXISTS media_repair_tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    username      TEXT    NOT NULL DEFAULT '',
    disk_id       INTEGER NOT NULL,
    rel_path      TEXT    NOT NULL,
    filename      TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'pending',
    progress      INTEGER NOT NULL DEFAULT 0,
    error_message TEXT    NOT NULL DEFAULT '',
    source_size   INTEGER NOT NULL DEFAULT 0,
    output_name   TEXT    NOT NULL DEFAULT '',
    started_at    TEXT,
    finished_at   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    deleted_at    TEXT
)
"""

_INDEXES = [
    # username 用**普通索引**（v1.5.0 反馈修复：原来是 `UNIQUE` 列约束）。
    # 原因：软删除（AGENTS.md §5.2 要求 deleted_at）会让历史行永久占用用户名，
    # 管理员删了账号却无法用同名重建，表现为「账号删不干净」。
    # 改为普通索引后，"活跃账号不重名"由下面的**部分唯一索引**保证
    # （只对 deleted_at IS NULL 的行生效），已删除的用户名即可复用。
    "CREATE INDEX IF NOT EXISTS users_username_idx ON users(username)",
    # 关键约束：**活跃**账号的用户名仍需唯一 ——
    # `find_by_username` 按 (username, deleted_at IS NULL) 查单行，登录才能无歧义。
    "CREATE UNIQUE INDEX IF NOT EXISTS users_username_active_uidx"
    " ON users(username) WHERE deleted_at IS NULL",
    "CREATE INDEX IF NOT EXISTS sessions_session_id_idx ON sessions(session_id)",
    "CREATE INDEX IF NOT EXISTS user_permissions_user_id_idx ON user_permissions(user_id)",
    "CREATE INDEX IF NOT EXISTS user_permissions_disk_id_idx ON user_permissions(virtual_disk_id)",
    "CREATE INDEX IF NOT EXISTS media_repair_tasks_user_id_idx ON media_repair_tasks(user_id)",
    "CREATE INDEX IF NOT EXISTS media_repair_tasks_disk_id_idx ON media_repair_tasks(disk_id)",
]


#: **代码已不再创建**的历史遗留表 —— 老库里可能还在，但全新部署不会有。
#:
#: 为什么要显式声明：`scripts/check_db_schema.py` 以 dev 库为基准去对齐 prod，
#: 若不告诉它哪些表是"死表"，它会把这些表**当成 dev 有而 prod 必须有**，
#: 于是把历史垃圾复制进生产库，还报"结构一致 ✓"（v1.5.0 实测发生过）。
#: 声明后：这些表不参与结构比对、不会被复制到 prod。
#:
#: - `notes_permissions`：v1.x 起移除"笔记目录权限"概念，所有登录用户都可用笔记；
#:   每个用户的笔记目录改由 `users.notes_disk_id` / `notes_path` 独立配置。
#:   历史表**保留**（不再读写）—— `init_db()` 不创建也不删除。
#: - `sync_configs`：v1.1.6 起完全移除（同步配置改为客户端本地存储），
#:   `init_db()` 会主动 `DROP TABLE`；列在这里是"脏库兜底"。
LEGACY_TABLES = frozenset({"notes_permissions", "sync_configs"})


#: 代码侧的建表 DDL 清单（表名 → 建表语句）。
#:
#: 为什么要导出：`scripts/check_db_schema.py` 以 **dev 库**为结构基准去对齐 prod，
#: 但如果 **dev 库本身就是旧文件**（从没被当前代码的 `init_db()` 打开过），
#: 工具会"一致地"把 prod 一起带偏 —— 报"结构一致"却两边都不是当前代码的结构。
#: 导出本清单后，发布前可以先校验 `dev 库 == 当前代码 DDL`，把这个盲区补上。
#:
#: ⚠ `_CREATE_USERS` 里**没有** `notes_disk_id` / `notes_path` —— 这两列是
#: `init_db()` 用 `ALTER TABLE ADD COLUMN` 后补的（历史库升级路径）。所以
#: `expected_schema()` 必须把它们补进期望结构，否则会把正常库误报成"陈旧"。
_USERS_ALTERED_COLUMNS = (
    "notes_disk_id INTEGER",
    "notes_path TEXT NOT NULL DEFAULT ''",
)


def expected_schema() -> dict[str, str]:
    """
    返回当前代码期望的表结构（表名 → 建表 SQL）。

    返回的建表 SQL **已包含 `init_db()` 通过 ALTER 追加的列**，可直接与库里
    `sqlite_master.sql` 做列定义级比较。
    """
    users = _CREATE_USERS.rstrip()
    if not users.endswith(")"):
        raise AssertionError("users 建表语句格式变化，请同步 expected_schema()")
    extra = ",\n    " + ",\n    ".join(_USERS_ALTERED_COLUMNS)
    users = users[:-1].rstrip().rstrip(",") + extra + "\n)"
    return {
        "users": users,
        "virtual_disks": _CREATE_VIRTUAL_DISKS,
        "user_permissions": _CREATE_USER_PERMISSIONS,
        "sessions": _CREATE_SESSIONS,
        "config": _CREATE_CONFIG,
        "media_repair_tasks": _CREATE_MEDIA_REPAIR_TASKS,
    }


def expected_indexes() -> list[str]:
    """返回当前代码期望的索引 DDL 清单"""
    return list(_INDEXES)


async def _migrate_users_drop_username_unique(db: aiosqlite.Connection) -> None:
    """
    去掉 `users.username` 的列级 UNIQUE 约束（v1.5.0 反馈修复）。

    **为什么必须去掉**：软删除（AGENTS.md §5.2 要求 `deleted_at`）保留历史行，
    而列级 UNIQUE 不区分是否删除 ⇒ 被删账号的用户名被永久占用，管理员删了账号
    却无法用同名重建，报的是数据库原始错误 —— 用户反馈的「账号删除后添加相同
    账号会报错」就是这么来的。

    去掉之后，"活跃账号不重名"由部分唯一索引
    `users_username_active_uidx ... WHERE deleted_at IS NULL` 保证，业务语义不变。

    SQLite 不支持 `ALTER TABLE ... DROP CONSTRAINT`，只能**重建表**
    （建新表 → 拷贝数据 → 替换）。

    ⚠ 曾尝试用 `PRAGMA writable_schema` 直接改写 sqlite_master 里的建表语句
    （不碰数据、更快），**实测不可行**：它会留下悬空的自动索引
    （`sqlite_autoindex_users_1`），之后任何访问都报
    `malformed database schema ... orphan index`，而且**当时不抛异常**、
    看上去"成功"了。因此本函数只走重建表路径 —— 这是 SQLite 官方推荐的
    schema 变更方式。
    """
    async with db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ) as cur:
        row = await cur.fetchone()
    if not row or not row[0]:
        return
    create_sql = row[0]
    # 幂等：新结构里 username 已无 UNIQUE，直接返回。
    # ⚠ 必须先剥掉 `--` 注释再判断 —— 建表语句里可能带说明文字
    # （如 `username TEXT NOT NULL, -- 去掉了原来的 UNIQUE`），
    # 直接搜 "UNIQUE" 子串会误判成"还没迁移"，导致重复重建表。
    if not re.search(
        r"username\s+TEXT\s+NOT\s+NULL\s+UNIQUE",
        _strip_sql_comments(create_sql),
        re.IGNORECASE,
    ):
        return
    logger.info("检测到 users.username 仍有 UNIQUE 列约束，开始重建表迁移")
    await _rebuild_users_table(db, columns_sql=_USERS_COLUMNS_SQL)


def _strip_sql_comments(sql: str) -> str:
    """去掉 SQL 里的 `--` 行注释与 `/* */` 块注释（用于结构判断）"""
    no_line = re.sub(r"--[^\n]*", " ", sql)
    return re.sub(r"/\*.*?\*/", " ", no_line, flags=re.DOTALL)


#: 新表列定义（与上方 `_CREATE_USERS` 保持一致，去掉 UNIQUE）。
#: 供 `_rebuild_users_table` 兜底路径使用 —— 必须是普通常量，
#: 早先写成 `async def` 被同步调用，返回了未 await 的协程，兜底路径直接失效。
_USERS_COLUMNS_SQL = (
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "username TEXT NOT NULL, "
    "password_hash TEXT NOT NULL, "
    "role TEXT NOT NULL DEFAULT 'user', "
    "enabled INTEGER NOT NULL DEFAULT 1, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, "
    "deleted_at TEXT, "
    "notes_disk_id INTEGER, "
    "notes_path TEXT NOT NULL DEFAULT ''"
)


async def _rebuild_users_table(db: aiosqlite.Connection, columns_sql: str) -> None:
    """
    重建 users 表（保数据）：建 `users_new` → 拷贝 → 替换 → 重建索引。

    这是 SQLite 官方推荐的 schema 变更方式（不支持 DROP CONSTRAINT）。
    主键 id 原样拷贝 —— 其它表按 `user_id` 整数引用，**绝不能重新分配**。
    任何失败都整体回滚，保证数据不动。
    """
    try:
        await db.execute("BEGIN")
        await db.execute("DROP INDEX IF EXISTS users_username_idx")
        await db.execute(f"CREATE TABLE users_new ({columns_sql})")
        # 显式列名拷贝：避免新老列顺序不一致
        await db.execute(
            "INSERT INTO users_new (id, username, password_hash, role, enabled,"
            " created_at, updated_at, deleted_at, notes_disk_id, notes_path)"
            " SELECT id, username, password_hash, role, enabled,"
            " created_at, updated_at, deleted_at, notes_disk_id, notes_path FROM users"
        )
        await db.execute("DROP TABLE users")
        await db.execute("ALTER TABLE users_new RENAME TO users")
        await db.commit()
        logger.info("运行时迁移：已重建 users 表并移除 username UNIQUE 约束（v1.5.0）")
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        logger.error("重建 users 表失败（数据未改动）：%s", e)


async def _dedupe_active_usernames(db: aiosqlite.Connection) -> None:
    """
    把"活跃行重名"收敛掉，保证部分唯一索引能建立。

    正常情况下不会出现（旧约束就不允许），但历史脏数据可能导致。策略：
    同名活跃行中保留 `id` 最小的那条，其余标记为软删除（不物理删数据）。
    """
    try:
        async with db.execute(
            "SELECT username FROM users WHERE deleted_at IS NULL"
            " GROUP BY username HAVING COUNT(*) > 1"
        ) as cur:
            dupes = [r[0] async for r in cur]
        if not dupes:
            return
        now = datetime.now(timezone.utc).isoformat()
        for username in dupes:
            async with db.execute(
                "SELECT id FROM users WHERE username = ? AND deleted_at IS NULL"
                " ORDER BY id",
                (username,),
            ) as cur:
                ids = [r[0] async for r in cur]
            for keep_alive_id in ids[1:]:
                await db.execute(
                    "UPDATE users SET deleted_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, keep_alive_id),
                )
            logger.warning(
                "运行时迁移：用户名 %s 存在 %d 条活跃记录，已软删除多余的 %d 条"
                "（保留 id=%s）",
                username, len(ids), len(ids) - 1, ids[0],
            )
        await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.error("活跃用户名去重失败：%s", e)


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
        await db.execute(_CREATE_MEDIA_REPAIR_TASKS)

        # ── 运行时迁移：为 users 表添加缺失字段（已存在则跳过）──
        async with db.execute("PRAGMA table_info(users)") as cur:
            columns = [row[1] async for row in cur]
        if "notes_disk_id" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN notes_disk_id INTEGER")
        if "notes_path" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN notes_path TEXT NOT NULL DEFAULT ''")

        # ── 运行时迁移：去掉 users.username 的 UNIQUE 列约束（v1.5.0）──
        # 必须在建索引之前执行：老库的列约束仍在时，
        # `CREATE UNIQUE INDEX ... WHERE deleted_at IS NULL` 不受影响，
        # 但"同名的已删除行 + 活跃行"会因列级 UNIQUE 而无法共存 —— 那正是
        # 用户反馈的「账号删除后重建同名会报错」。SQLite 不支持 DROP CONSTRAINT，
        # 只能重建表。
        await _migrate_users_drop_username_unique(db)

        # 去重保护：部分唯一索引要求"活跃行"用户名唯一，先把历史脏数据收敛掉
        await _dedupe_active_usernames(db)

        for idx in _INDEXES:
            await db.execute(idx)

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
