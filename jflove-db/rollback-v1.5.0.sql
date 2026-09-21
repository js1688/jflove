-- v1.5.0 生产库表结构同步回滚脚本
--
-- 用途：如需回退 v1.5.0，把 users.username 恢复为**列级 UNIQUE**（去掉部分唯一索引）。
--
-- ⚠ 回滚前必须处理冲突数据：
--   列级 UNIQUE 不区分是否软删除，若库里存在「活跃行 + 同名的已软删除历史行」，
--   重建带 UNIQUE 的表会失败（UNIQUE constraint failed）。
--   请先执行下面的「冲突检查」；若有结果，需要先处理这些历史行
--   （物理删除历史行，或把历史行的 username 改名，例如追加 `#deleted<id>`）。
--
-- 冲突检查（回滚前先跑这句）：
--   SELECT username, COUNT(*) AS n FROM users GROUP BY username HAVING n > 1;
--
-- 执行方式：
--   sqlite3 jflove-prod.db < rollback-v1.5.0.sql

BEGIN;

DROP TABLE IF EXISTS users_old;

CREATE TABLE users_old (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,   -- 恢复列级 UNIQUE
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user',
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    deleted_at    TEXT,
    notes_disk_id INTEGER,
    notes_path    TEXT    NOT NULL DEFAULT ''
);

-- 主键 id 原样保留（其它表按 user_id 引用）
INSERT INTO users_old
    (id, username, password_hash, role, enabled,
     created_at, updated_at, deleted_at, notes_disk_id, notes_path)
SELECT
     id, username, password_hash, role, enabled,
     created_at, updated_at, deleted_at,
     notes_disk_id,
     COALESCE(notes_path, '')
FROM users;

DROP TABLE users;
ALTER TABLE users_old RENAME TO users;

-- 回到 v1.5.0 之前的索引形态（只有普通索引；列级 UNIQUE 自带隐式自动索引）
DROP INDEX IF EXISTS users_username_active_uidx;
CREATE INDEX IF NOT EXISTS users_username_idx ON users(username);

COMMIT;

-- 校验：
--   SELECT sql FROM sqlite_master WHERE name='users';   -- 应重新出现 UNIQUE
--   SELECT COUNT(*) FROM users;                         -- 行数应与回滚前一致
