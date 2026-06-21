# JFLove 服务端 Docker 镜像
# 镜像内**不携带** dev 数据库 / 私钥 / 测试账号；prod 数据库通过 -v 挂载或镜像内置空表结构二选一。
# 加密协议版本：X-Encrypted-Stream: v1（下载/预览旧接口）/ v2（stream_file 流式预览接口）

FROM python:3.14-slim

LABEL maintainer="jflove devops"
LABEL version="1.1.6"
LABEL description="JFLove 后端服务（FastAPI + SQLite + 端到端加密）"

WORKDIR /app

# 系统依赖：bcrypt 编译需要 build-essential（构建后再清理）
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

# 优先复制 requirements，便于利用 docker layer 缓存
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# 业务源代码
COPY src /app/src

# 启动器（不动业务源代码，仅在 import src.main 前覆盖 DB_PATH）
COPY run_server.py /app/run_server.py

# 内置一份**空表结构**的数据库，作为「不挂载」启动模式的默认 DB；
# 若用户 -v 挂载外部目录，启动脚本会优先使用挂载位置。
COPY db/jflove-prod.db /app/db/jflove-prod.db

# 启动脚本
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV JFLOVE_HOST=0.0.0.0
ENV JFLOVE_PORT=8989
# 数据库路径默认指向镜像内空库；挂载 /data 时由 entrypoint 改为 /data/jflove-prod.db
ENV JFLOVE_DB_PATH=/app/db/jflove-prod.db

EXPOSE 8989

# ── 约定挂载点（不声明 VOLUME，避免强制匿名卷；由用户 docker run -v 决定是否挂载）──
#
#   /data    —— SQLite 业务数据库持久化目录
#               不挂载：使用镜像内置空表结构 DB（容器销毁数据丢失，演示用）
#               挂载：  -v /opt/jflove/data:/data
#
#   /storage —— 物理大容量磁盘挂载点
#               服务端创建虚拟磁盘时 real_path 填 /storage 或其子目录，即可让文件落到宿主机大盘上
#               单盘：  -v /mnt/big-disk:/storage
#               多盘：  -v /mnt/disk-a:/storage/disk-a -v /mnt/disk-b:/storage/disk-b
#
# entrypoint 启动时会打印两个挂载点的实际状态。

# 健康检查（明文白名单 /health）
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8989/health',timeout=3).status==200 else 1)" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
