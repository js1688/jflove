-- v1.10.2 升级 DDL：远程连接类型（ssh / sftp）
-- 幂等；仅新增列，不触碰业务数据。
-- mode = 'ssh'：终端 + 可选 SFTP；mode = 'sftp'：纯 SFTP（不依赖 shell）。

ALTER TABLE ssh_connections ADD COLUMN mode TEXT NOT NULL DEFAULT 'ssh';
