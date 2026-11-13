-- v1.10.0 回滚 DDL：删除远程连接配置表
--
-- 回滚前冲突检查（有数据时请先确认这些连接配置不再需要）：
--   SELECT COUNT(*) FROM ssh_connections WHERE deleted_at IS NULL;
--   SELECT COUNT(*) FROM ssh_groups WHERE deleted_at IS NULL;
-- 若计数不为 0，回滚将丢弃全部连接配置与凭据（属于预期行为，确认后再执行）。

DROP TABLE IF EXISTS ssh_connections;
DROP TABLE IF EXISTS ssh_groups;
