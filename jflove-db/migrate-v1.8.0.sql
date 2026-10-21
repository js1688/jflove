-- v1.8.0 表结构同步脚本（手动执行版）
-- 用途：把任意 jflove 数据库（开发库 jflove-dev.db / 生产库 jflove-prod.db）
--       同步到 v1.8.0 最新表结构。**不触碰任何业务数据**（只新增表与索引）。
--
-- 执行方式（sqlite3 CLI 或任意 SQLite 客户端）：
--   sqlite3 jflove-prod.db < migrate-v1.8.0.sql
--
-- 本次变更：**新增 3 张表 + 4 个索引**（离线下载：网址分析 + 媒体抓取）
--   1. `offline_download_tasks`   —— 一次「网址 → 分析 → 选择 → 下载」的任务
--   2. `offline_download_items`   —— 清单里的可下载项（视频变体/音轨/图片/字幕）
--   3. `offline_download_settings`—— **账号级**默认目录（每个账号一行）
--
-- 设计约定（对应 文档记录/设计文档/v1.8.0.md §2.1）：
--   - 记录按账号隔离（D10）：tasks 带 `user_id`，列表/详情/操作一律按它过滤；
--   - 账号目录唯一（D9）：`settings` 用**部分唯一索引**（只约束 `deleted_at IS NULL`），
--     不用列级 UNIQUE —— 否则软删除后该账号再也插不回配置；
--   - 不建外键约束、必备 `created_at/updated_at/deleted_at`（AGENTS.md §5.2）；
--   - 全部 `IF NOT EXISTS`，**幂等、可重复执行**。
--
-- 注：本脚本与 `src/models/database.py` 的 DDL 必须保持一致；
--     发布前用 `python scripts/check_db_schema.py` 校验（退出码 0 才算对齐）。

BEGIN;

CREATE TABLE IF NOT EXISTS offline_download_tasks (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    -- 创建者（记录可见性按账号隔离，D10）
    user_id            INTEGER NOT NULL,
    username           TEXT    NOT NULL DEFAULT '',
    -- 完整来源 URL（重试/重新分析用；界面只展示 source_domain）
    source_url         TEXT    NOT NULL,
    source_domain      TEXT    NOT NULL DEFAULT '',
    page_title         TEXT    NOT NULL DEFAULT '',
    -- queued/analyzing/ready/downloading/merging/success/failed/canceled
    status             TEXT    NOT NULL DEFAULT 'queued',
    -- l1(站点解析)/l2(静态抓取)/l3(无头浏览器)
    analysis_level     TEXT    NOT NULL DEFAULT '',
    -- ready 状态清单的失效时间（媒体地址带签名，久放必失效）
    analysis_expire_at TEXT,
    -- 图片分页追加游标
    image_page_index   INTEGER NOT NULL DEFAULT 1,
    -- 落盘目标：虚拟磁盘 + 磁盘内相对路径（绝不存绝对路径）
    target_disk_id     INTEGER NOT NULL DEFAULT 0,
    target_rel_path    TEXT    NOT NULL DEFAULT '',
    pack_zip           INTEGER NOT NULL DEFAULT 0,
    item_total         INTEGER NOT NULL DEFAULT 0,
    item_done          INTEGER NOT NULL DEFAULT 0,
    bytes_total        INTEGER NOT NULL DEFAULT 0,
    bytes_done         INTEGER NOT NULL DEFAULT 0,
    speed_bps          INTEGER NOT NULL DEFAULT 0,
    -- 失败分类（中文原因同时在 error_message）
    error_code         TEXT    NOT NULL DEFAULT '',
    error_message      TEXT    NOT NULL DEFAULT '',
    started_at         TEXT,
    finished_at        TEXT,
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL,
    deleted_at         TEXT
);

CREATE TABLE IF NOT EXISTS offline_download_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       INTEGER NOT NULL,
    -- video/audio/image/subtitle
    kind          TEXT    NOT NULL,
    display_name  TEXT    NOT NULL,
    ext           TEXT    NOT NULL DEFAULT 'mp4',
    source_url    TEXT    NOT NULL,
    -- 分离音频轨地址（DASH）：与 source_url 一起合并成一个成品
    aux_url       TEXT    NOT NULL DEFAULT '',
    -- http / m3u8 / mpd_segments
    protocol      TEXT    NOT NULL DEFAULT 'http',
    variant_id    TEXT    NOT NULL DEFAULT '',
    vcodec        TEXT    NOT NULL DEFAULT '',
    acodec        TEXT    NOT NULL DEFAULT '',
    width         INTEGER NOT NULL DEFAULT 0,
    height        INTEGER NOT NULL DEFAULT 0,
    duration_ms   INTEGER NOT NULL DEFAULT 0,
    est_size      INTEGER NOT NULL DEFAULT 0,
    is_estimated  INTEGER NOT NULL DEFAULT 1,
    is_selected   INTEGER NOT NULL DEFAULT 0,
    -- pending/downloading/success/failed/skipped
    status        TEXT    NOT NULL DEFAULT 'pending',
    bytes_done    INTEGER NOT NULL DEFAULT 0,
    output_name   TEXT    NOT NULL DEFAULT '',
    error_code    TEXT    NOT NULL DEFAULT '',
    error_message TEXT    NOT NULL DEFAULT '',
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    deleted_at    TEXT
);

CREATE TABLE IF NOT EXISTS offline_download_settings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,
    default_disk_id  INTEGER NOT NULL,
    default_rel_path TEXT    NOT NULL DEFAULT '',
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL,
    deleted_at       TEXT
);

CREATE INDEX IF NOT EXISTS offline_download_tasks_user_id_idx
    ON offline_download_tasks(user_id);
CREATE INDEX IF NOT EXISTS offline_download_tasks_status_idx
    ON offline_download_tasks(status);
CREATE INDEX IF NOT EXISTS offline_download_items_task_id_idx
    ON offline_download_items(task_id);
-- 一个账号只允许一行**活跃**配置（D9）；软删除后可用同名重建
CREATE UNIQUE INDEX IF NOT EXISTS offline_download_settings_user_active_uidx
    ON offline_download_settings(user_id) WHERE deleted_at IS NULL;

COMMIT;
