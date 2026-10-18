-- v1.7.0 表结构回滚脚本（手动执行版）
-- 用途：把数据库从 v1.7.0 结构回滚到 v1.6.2 结构。
--
-- 执行方式：
--   sqlite3 jflove-prod.db < rollback-v1.7.0.sql
--
-- 本次回滚：**删除 `file_changes` 表及其索引**
--
-- ⚠ 回滚前冲突检查（必读）：
--   本表只保存变更日志，**不影响任何业务文件与用户数据**。
--   但回滚会**丢弃全部变更历史**，旧版客户端（若仍在使用挂载云盘）会因
--   `since_rev` 失效而回退到全量对账（`scan`），功能不受影响、只是变慢。
--   若你打算回滚到 **v1.6.2 或更早**（这些版本根本不认识本表），可直接执行。
--
--   可选：回滚前确认表内记录量（仅供参考，不阻断）
--   SELECT COUNT(*) FROM file_changes;
--
-- 幂等：重复执行无副作用。

BEGIN;

DROP INDEX IF EXISTS file_changes_rel_path_idx;
DROP INDEX IF EXISTS file_changes_disk_id_idx;
DROP TABLE IF EXISTS file_changes;

COMMIT;

-- 校验（可单独执行，确认结果）：
--   SELECT COUNT(*) FROM sqlite_master WHERE name='file_changes';  -- 期望 0
