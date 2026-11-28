-- v1.10.26 生产库表结构同步回滚脚本
--
-- 用途：如需回退 v1.10.26，把 `sessions.session_id` 恢复为**列级 UNIQUE**
--       （去掉部分唯一索引 sessions_session_active_uidx）。
--
-- ⚠⚠ 回滚前**必须**先处理冲突数据，否则会失败或丢语义：
--
--   1) 列级 UNIQUE 不区分是否软删除。v1.10.26 之后，同一个 session_id 会同时存在
--      「1 条活跃行 + N 条已软删除历史行」（这正是本次修复的目的）。
--      这些数据在列级 UNIQUE 下**不合法**，直接重建会报
--      `UNIQUE constraint failed: sessions.session_id`。
--
--      冲突检查（回滚前先跑，有任何输出就说明必须先清理）：
--        SELECT session_id, COUNT(*) AS n
--        FROM sessions
--        GROUP BY session_id
--        HAVING n > 1;
--
--      处理办法（二选一，**都要先备份库**）：
--        a. 物理删除已吊销的历史行（会话记录本身不需要长期保留）：
--             DELETE FROM sessions WHERE deleted_at IS NOT NULL;
--        b. 给历史行的 session_id 加后缀保留审计痕迹：
--             UPDATE sessions
--                SET session_id = session_id || '#revoked' || id
--              WHERE deleted_at IS NOT NULL;
--
--   2) 回滚后**token 续期会重新变成 500**（refresh 恒定失败）——
--      这正是本版本修掉的问题。回滚仅在需要退回旧行为时使用。
--
-- 冲突检查 + 清理 + 回滚的完整顺序：
--   sqlite3 jflove-prod.db "SELECT session_id, COUNT(*) n FROM sessions GROUP BY session_id HAVING n>1;"
--   sqlite3 jflove-prod.db "DELETE FROM sessions WHERE deleted_at IS NOT NULL;"   -- 或选 b
--   sqlite3 jflove-prod.db < rollback-1.10.26.sql
--
-- 校验（执行后应为「无输出」）：
--   SELECT sql FROM sqlite_master WHERE name='sessions';
--     -- 期望：session_id TEXT NOT NULL UNIQUE（UNIQUE 回来了）
--   SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='sessions';
--     -- 期望：只有 sessions_session_id_idx（部分唯一索引已删除）

BEGIN;

DROP TABLE IF EXISTS sessions_old;

CREATE TABLE sessions_old (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    session_id     TEXT    NOT NULL UNIQUE,   -- 恢复列级 UNIQUE
    jwt_token_hash TEXT    NOT NULL,
    expires_at     TEXT    NOT NULL,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL,
    deleted_at     TEXT
);

-- 主键 id 与全部行原样保留（此时同一 session_id 已被上面第 1 步收敛为 1 行）
INSERT INTO sessions_old
    (id, user_id, session_id, jwt_token_hash, expires_at, created_at, updated_at, deleted_at)
SELECT
     id, user_id, session_id, jwt_token_hash, expires_at, created_at, updated_at, deleted_at
FROM sessions;

DROP TABLE sessions;
ALTER TABLE sessions_old RENAME TO sessions;

-- 回到旧索引形态（只有普通索引；列级 UNIQUE 自带隐式自动索引）
DROP INDEX IF EXISTS sessions_session_active_uidx;
DROP INDEX IF EXISTS sessions_session_id_idx;
CREATE INDEX IF NOT EXISTS sessions_session_id_idx ON sessions(session_id);

COMMIT;
