-- v1.5.0 表结构同步脚本（手动执行版）
-- 用途：把任意 jflove 数据库（开发库 jflove-dev.db / 生产库 jflove-prod.db）
--       同步到 v1.5.0 最新表结构。**不触碰任何业务数据**（只重建表结构）。
--
-- 执行方式（sqlite3 CLI 或任意 SQLite 客户端）：
--   sqlite3 jflove-prod.db < migrate-v1.5.0.sql
--
-- 本次变更：**去掉 users.username 的列级 UNIQUE 约束**
--
-- 为什么改：
--   删除用户是软删除（deleted_at，遵守 AGENTS.md §5.2），历史行会保留；而列级
--   UNIQUE 不区分是否删除 ⇒ 被删账号的用户名被永久占用，管理员删了账号却无法用
--   同名重建，报的是数据库原始错误（UNIQUE constraint failed）。
--   改为「普通索引 + 活跃行部分唯一索引」后：
--     - 活跃账号仍然不允许重名（登录/查重不会有歧义）；
--     - 被软删除的历史行不再占用用户名，可以复用。
--
-- 实现方式：SQLite 不支持 ALTER TABLE ... DROP CONSTRAINT，只能重建表。
--   （曾尝试 PRAGMA writable_schema 直接改写 sqlite_master，实测会留下悬空自动
--     索引、之后任何访问都报 "malformed database schema ... orphan index"，
--     且当时不抛异常，故不采用。）
--
-- ⚠ 重要：主键 id 必须原样保留 —— 其它表（user_permissions / sessions /
--   media_repair_tasks 等）按 user_id 整数引用它。
--
-- 幂等：已迁移过的库重复执行本脚本无副作用（结束时 username 仍无 UNIQUE）。
--
-- 提示：v1.5.0 服务端启动时 init_db() 也会自动执行同样的迁移；
--       本脚本用于「不启动服务」场景下的手动同步 / 预先对齐。

BEGIN;

-- 1. 旧库才需要重建；已是新结构则跳过（用一句 SELECT 判断无法在纯 SQL 里分支，
--    因此这里无条件重建一次 —— 新结构重建后结果相同，保持幂等）。
DROP TABLE IF EXISTS users_new;

CREATE TABLE users_new (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL,          -- 去掉了原来的 UNIQUE
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user',
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    deleted_at    TEXT,
    notes_disk_id INTEGER,
    notes_path    TEXT    NOT NULL DEFAULT ''
);

-- 2. 拷贝全部业务行（含已软删除的历史行），主键 id 原样保留
INSERT INTO users_new
    (id, username, password_hash, role, enabled,
     created_at, updated_at, deleted_at, notes_disk_id, notes_path)
SELECT
     id, username, password_hash, role, enabled,
     created_at, updated_at, deleted_at,
     notes_disk_id,
     COALESCE(notes_path, '')
FROM users;

-- 3. 替换（旧索引随旧表一起消失）
DROP TABLE users;
ALTER TABLE users_new RENAME TO users;

-- 4. 重建索引：普通索引负责查询，部分唯一索引负责"活跃账号不重名"
CREATE INDEX IF NOT EXISTS users_username_idx ON users(username);
CREATE UNIQUE INDEX IF NOT EXISTS users_username_active_uidx
    ON users(username) WHERE deleted_at IS NULL;

COMMIT;

-- 校验（可单独执行，确认结果）：
--   SELECT COUNT(*) FROM users;                              -- 迁移前后行数应一致
--   SELECT sql FROM sqlite_master WHERE name='users';        -- 不应再出现 UNIQUE
--   SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='users';
--     -- 期望：users_username_active_uidx、users_username_idx
--
-- 回滚：见同目录 rollback-v1.5.0.sql
