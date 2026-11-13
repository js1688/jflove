-- v1.10.2 回滚 DDL：移除连接类型列
--
-- SQLite 3.35+ 支持 DROP COLUMN；执行前确认没有任务依赖该列：
--   SELECT COUNT(*) FROM ssh_connections WHERE mode = 'sftp';
-- 若 > 0，回滚后这些连接会按 ssh 处理（需要 shell），请先确认。

ALTER TABLE ssh_connections DROP COLUMN mode;
