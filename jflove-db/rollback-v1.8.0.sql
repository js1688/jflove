-- v1.8.0 表结构回滚脚本（手动执行版）
-- 用途：把数据库从 v1.8.0 结构回滚到 v1.7.0 结构。
--
-- 执行方式：
--   sqlite3 jflove-prod.db < rollback-v1.8.0.sql
--
-- 本次回滚：**删除离线下载的 3 张表及其索引**
--
-- ⚠ 回滚前冲突检查（必读）：
--   回滚会**丢弃**离线下载的任务记录与账号级目录配置（用户需重新配置目录），
--   但**不会删除磁盘上已经下载完成的文件**（文件在虚拟磁盘里，属文件管理范畴）。
--
--   执行前先看清"会丢什么"：
--     -- 1) 还有没有没跑完的任务（这些任务回滚后不可恢复）
--     SELECT COUNT(*) FROM offline_download_tasks
--      WHERE deleted_at IS NULL AND status NOT IN ('success','failed','canceled');
--     -- 2) 有多少账号配过默认目录（回滚后要重配）
--     SELECT COUNT(*) FROM offline_download_settings WHERE deleted_at IS NULL;
--
--   若上面第 1 条 > 0：建议先等服务端把任务跑完 / 或在旧版本里取消，
--   否则"回滚 + 再升级"后这些任务会永远停在未完成状态（表已不存在）。
--
--   若你计划回滚到 v1.7.0 或更早（这些版本不认识这 3 张表），可直接执行。
--
-- 判据（回滚后）：
--   python scripts/check_db_schema.py   # 以 dev 库为基准比对时，不应再报这 3 张表

BEGIN;

DROP INDEX IF EXISTS offline_download_settings_user_active_uidx;
DROP INDEX IF EXISTS offline_download_items_task_id_idx;
DROP INDEX IF EXISTS offline_download_tasks_status_idx;
DROP INDEX IF EXISTS offline_download_tasks_user_id_idx;

DROP TABLE IF EXISTS offline_download_items;
DROP TABLE IF EXISTS offline_download_settings;
DROP TABLE IF EXISTS offline_download_tasks;

COMMIT;
