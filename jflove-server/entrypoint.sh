#!/bin/sh
# JFLove 服务端启动脚本
# 行为：
#   1. /data：SQLite 业务数据库持久化目录
#      - 已挂载 → 使用 /data/jflove-prod.db（首次会从镜像内空表结构拷贝过去）
#      - 未挂载 → 使用镜像内置 /app/db/jflove-prod.db（容器销毁数据丢失，演示用）
#   2. /storage：物理大容量磁盘挂载点
#      - 已挂载 → 服务端可在添加虚拟磁盘时把 real_path 填 /storage 或其子目录
#      - 未挂载 → 服务端只能选容器内目录，不推荐生产
#   3. 通过 src.config.settings 的 DB_PATH 环境变量化方式注入（main 模块 import 前生效）。

set -e

# ── 数据库挂载策略 ────────────────────────────
if [ -d "/data" ]; then
    EXTERNAL_DB="/data/jflove-prod.db"
    if [ ! -f "$EXTERNAL_DB" ]; then
        echo "[entrypoint] 首次挂载 /data，从镜像内置空表结构初始化数据库..."
        cp /app/db/jflove-prod.db "$EXTERNAL_DB"
    fi
    export JFLOVE_DB_PATH="$EXTERNAL_DB"
    echo "[entrypoint] [DB] 使用挂载数据库: $JFLOVE_DB_PATH"
else
    export JFLOVE_DB_PATH="/app/db/jflove-prod.db"
    echo "[entrypoint] [DB] 使用镜像内置数据库（容器销毁数据丢失，仅演示）: $JFLOVE_DB_PATH"
fi

# ── 物理大容量磁盘挂载状态 ───────────────────
if [ -d "/storage" ]; then
    echo "[entrypoint] [STORAGE] /storage 已存在，添加虚拟磁盘时可使用："
    echo "[entrypoint] [STORAGE]   - real_path = /storage（整盘）"
    # 列出 /storage 下的一级子目录（多盘子挂载场景）
    SUBS=$(find /storage -maxdepth 1 -mindepth 1 -type d 2>/dev/null | head -20)
    if [ -n "$SUBS" ]; then
        echo "[entrypoint] [STORAGE]   - 检测到子目录（可能是多盘挂载）："
        echo "$SUBS" | sed 's/^/[entrypoint] [STORAGE]       /'
    fi
    # 显示 /storage 可用空间，提醒用户磁盘容量
    DF_INFO=$(df -h /storage 2>/dev/null | tail -1 || true)
    if [ -n "$DF_INFO" ]; then
        echo "[entrypoint] [STORAGE]   - df: $DF_INFO"
    fi
else
    echo "[entrypoint] [STORAGE] /storage 未挂载，服务端添加虚拟磁盘时只能选容器内目录"
    echo "[entrypoint] [STORAGE]   推荐重新启动时加：-v /mnt/your-big-disk:/storage"
fi

cd /app
exec python /app/run_server.py
