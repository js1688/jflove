# JFLove 服务端 Docker 镜像
# 镜像内**不携带** dev 数据库 / 私钥 / 测试账号；prod 数据库通过 -v 挂载或镜像内置空表结构二选一。
# 加密协议版本：X-Encrypted-Stream: v1（下载/预览旧接口）/ v2（stream_file 流式预览接口）

FROM python:3.14-slim

LABEL maintainer="jflove devops"
LABEL version="1.8.1"
LABEL description="JFLove 后端服务（FastAPI + SQLite + 端到端加密）"

WORKDIR /app

# 系统依赖：bcrypt 编译需要 build-essential；ffmpeg 供媒体修复（v1.4.1 起优先使用系统 ffmpeg）
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libffi-dev ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# v1.4.1：构建时强制验证 ffmpeg 可用（媒体修复依赖）。不可用直接构建失败，
# 防止「静默上线一个坏 ffmpeg」导致边下边播失效。
RUN ffmpeg -version >/dev/null 2>&1 || (echo '[FATAL] 系统 ffmpeg 不可用，媒体修复将失效' && exit 1)

# 优先复制 requirements，便于利用 docker layer 缓存
COPY requirements.txt /app/requirements.txt

# pip 国内镜像源（默认清华 TUNA，加速依赖下载；可用 --build-arg PIP_INDEX_URL=https://pypi.org/simple 覆盖为官方源）
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} --upgrade pip && \
    pip install --no-cache-dir -i ${PIP_INDEX_URL} -r /app/requirements.txt

# v1.8.0：离线下载的站点解析组件必须随镜像版本固定（I-6：禁止运行时在线自更新）
RUN python -c "import yt_dlp, httpx; print('yt-dlp', yt_dlp.version.__version__, 'httpx', httpx.__version__)"

# v1.8.0：深度分析（L3）的 Chromium 运行时。
#   WITH_BROWSER=1（默认）安装 Chromium 并做启动校验；
#   WITH_BROWSER=0 明确降级并**响亮报警**（AC-239：缺失不得静默）。
ARG WITH_BROWSER=1
# 可选：Chromium 下载慢时可切镜像源（如 https://npmmirror.com/mirrors/playwright）
ARG PLAYWRIGHT_DOWNLOAD_HOST=""
RUN if [ "${WITH_BROWSER}" = "1" ]; then \
        if [ -n "${PLAYWRIGHT_DOWNLOAD_HOST}" ]; then \
            export PLAYWRIGHT_DOWNLOAD_HOST="${PLAYWRIGHT_DOWNLOAD_HOST}"; \
        fi; \
        python -m playwright install --with-deps chromium; \
    else \
        echo '[WARN] WITH_BROWSER=0：镜像未安装 Chromium，深度分析不可用（/offline/meta 会返回 deep_analyze_available=false）'; \
    fi

# v1.8.0：构建期验证深度分析组件真的能启动（不能只“装上了”）。
RUN if [ "${WITH_BROWSER}" = "1" ]; then \
        python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True, args=['--no-sandbox']); b.close(); p.stop(); print('chromium ok')" \
        || (echo '[FATAL] Chromium 启动校验失败，深度分析不可用' && exit 1); \
    else \
        echo '[WARN] 已跳过 Chromium 启动校验（WITH_BROWSER=0）'; \
    fi

# v1.8.0：ffprobe / ffmpeg 只允许处理本地文件（设计 §1.4），构建期确认二进制存在
RUN ffprobe -version >/dev/null 2>&1 || (echo '[FATAL] 系统 ffprobe 不可用' && exit 1)

# v1.4.1：imageio-ffmpeg 二进制做可执行兜底（无系统 ffmpeg 的开发机仍可用；
# 主路径已在 media_probe.get_ffmpeg_exe 优先用系统 ffmpeg）。失败不阻断构建。
RUN python -c "import imageio_ffmpeg, os; os.chmod(imageio_ffmpeg.get_ffmpeg_exe(), 0o755)" 2>/dev/null || true

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
