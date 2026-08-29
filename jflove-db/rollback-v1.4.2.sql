-- v1.4.2 生产库表结构同步回滚脚本
-- 用途：如需回退 v1.4.2 发布，执行本脚本删除 media_repair_tasks 表与索引。
-- 注意：业务数据（任务记录）将随表一并删除；修复产物（.jflove-repair/ 目录）
-- 为磁盘文件不受本脚本影响，需另行清理。

DROP TABLE IF EXISTS media_repair_tasks;
DROP INDEX IF EXISTS media_repair_tasks_user_id_idx;
DROP INDEX IF EXISTS media_repair_tasks_disk_id_idx;
