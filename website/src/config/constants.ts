// 官网版本号（单一来源 = 仓库根 version.json）
//
// 本文件的值由 `python scripts/sync_version.py` 同步，**禁止手改**：
// 官网版本号必须始终与项目版本号一致（AGENTS.md §8.1）。改版本只有一个动作：
//   echo '{"version":"x.y.z"}' > version.json && python scripts/sync_version.py
//
// 使用位置：页脚 / 下载页 / `<meta name="generator">` —— 用户可见处必须显示真实版本。

export const APP_VERSION = '1.10.24';

/** 官网对外展示的产品名 */
export const PRODUCT_NAME = 'JFLove';

/** 仓库地址（下载页/文档页外链） */
export const REPO_URL = 'https://github.com/js1688/jflove';

/** GHCR 镜像命名空间（下载页展示拉取命令用） */
export const GHCR_OWNER = 'js1688';
