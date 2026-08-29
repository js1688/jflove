-- v1.4.2 表结构同步脚本（手动执行版）
-- 用途：把任意 jflove 数据库（开发库 jflove-dev.db / 生产库 jflove-prod.db）
--       同步到 v1.4.2 最新表结构。幂等（IF NOT EXISTS），可重复执行，
--       不触碰任何业务数据。
--
-- 执行方式（sqlite3 CLI 或任意 SQLite 客户端）：
--   sqlite3 jflove-dev.db < migrate-v1.4.2.sql
--
-- 说明：v1.4.2 服务端启动时 init_db 也会自动执行同样的迁移；
--       本脚本用于「不启动服务」场景下的手动同步。

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
);

CREATE INDEX IF NOT EXISTS media_repair_tasks_user_id_idx ON media_repair_tasks(user_id);
CREATE INDEX IF NOT EXISTS media_repair_tasks_disk_id_idx ON media_repair_tasks(disk_id);

-- 回滚：见同目录 rollback-v1.4.2.sql（DROP 新表 + 索引）
