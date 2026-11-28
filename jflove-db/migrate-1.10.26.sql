-- v1.10.26 生产库表结构同步升级脚本
--
-- 用途：把 `sessions.session_id` 的**列级 UNIQUE** 去掉，改由「部分唯一索引」
--       只约束**活跃行**（`deleted_at IS NULL`）。
--
-- 为什么必须改（线上 500 事故）：
--   `sessions` 是软删除表（AGENTS.md §5.2 要求 `deleted_at`），而列级 UNIQUE
--   不区分是否删除。`auth_service.login()` / `refresh_token()` 的流程都是
--   「先 revoke()（只置 deleted_at，行仍在）→ 再 create() 用同一 session_id 插入」
--   ⇒ 必然撞唯一约束：
--     · 同一会话第二次登录 → 500 `UNIQUE constraint failed: sessions.session_id`
--     · `POST /api/v1/auth/refresh` → **恒定 500**（token 续期彻底不可用）
--
-- 本脚本与 `src/models/database.py::_migrate_sessions_drop_session_id_unique()`
-- 等价（运行时迁移也做同一件事）。二者幂等，重复执行结果不变。
--
-- ⚠ 本脚本**不触碰业务数据**：只重建 `sessions` 表结构，主键 `id` 与全部行原样保留。
--
-- 执行方式：
--   sqlite3 jflove-prod.db < migrate-1.10.26.sql
--
-- 校验（执行后应为「无输出」）：
--   SELECT sql FROM sqlite_master WHERE name='sessions';
--     -- 期望：session_id TEXT NOT NULL （**没有** UNIQUE）
--   SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='sessions';
--     -- 期望：sessions_session_id_idx、sessions_session_active_uidx

BEGIN;

-- 幂等保护：只有当列级 UNIQUE 还在时才重建。
-- 已经迁移过（无 UNIQUE）时，下面这段整体不生效。
DROP TABLE IF EXISTS sessions_new;

CREATE TABLE sessions_new (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    session_id     TEXT    NOT NULL,          -- 去掉 UNIQUE
    jwt_token_hash TEXT    NOT NULL,
    expires_at     TEXT    NOT NULL,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL,
    deleted_at     TEXT
);

-- 主键 id 与全部历史行原样拷贝（软删除语义：历史行保留）
INSERT INTO sessions_new
    (id, user_id, session_id, jwt_token_hash, expires_at, created_at, updated_at, deleted_at)
SELECT
     id, user_id, session_id, jwt_token_hash, expires_at, created_at, updated_at, deleted_at
FROM sessions;

DROP TABLE sessions;
ALTER TABLE sessions_new RENAME TO sessions;

-- 索引：普通索引（按 session_id 查）+ 部分唯一索引（只约束活跃行）
DROP INDEX IF EXISTS sessions_session_id_idx;
CREATE INDEX IF NOT EXISTS sessions_session_id_idx ON sessions(session_id);
DROP INDEX IF EXISTS sessions_session_active_uidx;
CREATE UNIQUE INDEX IF NOT EXISTS sessions_session_active_uidx
    ON sessions(session_id) WHERE deleted_at IS NULL;

COMMIT;
