-- v1.7.0 表结构同步脚本（手动执行版）
-- 用途：把任意 jflove 数据库（开发库 jflove-dev.db / 生产库 jflove-prod.db）
--       同步到 v1.7.0 最新表结构。**不触碰任何业务数据**（只新增表与索引）。
--
-- 执行方式（sqlite3 CLI 或任意 SQLite 客户端）：
--   sqlite3 jflove-prod.db < migrate-v1.7.0.sql
--
-- 本次变更：**新增 `file_changes` 表（文件变更日志）**
--
-- 为什么需要：
--   桌面端「挂载云盘」要从"全量扫描 diff"改成"增量同步"。客户端只记一个
--   单调递增的 `rev`，服务端按 `rev` 回放变更，数据量与目录总文件数无关。
--   `id` 同时充当 `rev`（AUTOINCREMENT，删除不回收号，永不回退）。
--
-- 设计约定：
--   - 同一路径允许多条历史记录（append-only），因此**不建唯一索引**
--     （避免与 AGENTS.md §5.2 的软删除约定冲突）；
--   - 不建外键约束（AGENTS.md §5.2）；
--   - 保留最近 10 万条 / 由服务端清理任务滚动删除。
--
-- 幂等：重复执行无副作用（全部 IF NOT EXISTS）。
--
-- 提示：v1.7.0 服务端启动时 init_db() 也会自动建表与索引；
--       本脚本用于「不启动服务」场景下的手动同步 / 预先对齐。

BEGIN;

CREATE TABLE IF NOT EXISTS file_changes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    disk_id      INTEGER NOT NULL,
    rel_path     TEXT    NOT NULL,
    op           TEXT    NOT NULL,
    is_dir       INTEGER NOT NULL DEFAULT 0,
    size         INTEGER NOT NULL DEFAULT 0,
    mtime        REAL    NOT NULL DEFAULT 0,
    content_hash TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    deleted_at   TEXT
);

CREATE INDEX IF NOT EXISTS file_changes_disk_id_idx ON file_changes(disk_id);
CREATE INDEX IF NOT EXISTS file_changes_rel_path_idx ON file_changes(rel_path);

COMMIT;

-- 校验（可单独执行，确认结果）：
--   SELECT name FROM sqlite_master WHERE type='table' AND name='file_changes';
--   SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='file_changes';
--     -- 期望：file_changes_disk_id_idx、file_changes_rel_path_idx
--
-- 回滚：见同目录 rollback-v1.7.0.sql
