import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PROJECT_DIR = BASE_DIR.parent

DB_PATH = str(PROJECT_DIR / "jflove-db" / "jflove-dev.db")
LOG_DIR = str(BASE_DIR / "logs")
UPLOAD_TEMP_DIR = str(BASE_DIR / "build" / "upload_tmp")

JWT_ALGORITHM = "ES256"
# v1.1.4：JWT 有效期上限由 8 小时放宽到 30 天（支持 1天 / 7天 / 30天 选项）。
# - 默认值 = 1 天（86,400 秒），客户端默认勾选 7 天选项但不传则走此值
# - 硬上限 = 30 天（2,592,000 秒），配合桌面端 v1.1.4 新增的按天选项
# - 硬下限 = 60 秒（防止恶意客户端请求 0 秒导致登录立即失效）
JWT_EXPIRE_DEFAULT_SECONDS = 86400
JWT_EXPIRE_MAX_SECONDS = 2592000
JWT_EXPIRE_MIN_SECONDS = 60
# 兼容老代码引用：JWT_EXPIRE_HOURS 仍可被 import，但内部只读默认秒数派生
JWT_EXPIRE_HOURS = JWT_EXPIRE_DEFAULT_SECONDS // 3600
SESSION_EXPIRE_HOURS = 24
SESSION_KEY_SALT = b"jflove-v1"

HOST = os.getenv("JFLOVE_HOST", "0.0.0.0")
PORT = int(os.getenv("JFLOVE_PORT", "8989"))

# ── 媒体修复（v1.4.0）────────────────────────────────────────────
# config 表配置键（服务端配置，三端共享，管理员可配、立即生效）
MEDIA_REPAIR_ENABLED_KEY = "media_repair_enabled"          # 总开关："0" 关闭（默认）/ "1" 开启
MEDIA_REPAIR_TRANSCODE_KEY = "media_repair_allow_transcode"  # 重编码子开关："0" 关闭（默认）/ "1" 开启
MEDIA_REPAIR_CONCURRENT_KEY = "media_repair_max_concurrent"  # 并发数上限（空 = 自动基线）
# 修复并发数自动基线：按 CPU 核数推导（留一核给系统），最少 1
MEDIA_REPAIR_AUTO_CONCURRENT_BASE = max(1, (os.cpu_count() or 2) - 1)
# 并发数硬上限：防止管理员误配过高拖垮服务端
MEDIA_REPAIR_CONCURRENT_HARD_MAX = 8
# ffmpeg 无输出超时（秒）：超过视为卡死，强制终止（防僵尸/孤儿进程）
MEDIA_REPAIR_NO_OUTPUT_TIMEOUT = 30
# 修复并发等待超时（秒）：并发满时等待超过此值则放弃（防请求长时间挂起，M3）
MEDIA_REPAIR_WAIT_TIMEOUT = 30
# ffmpeg stdout 管道读取块大小（与流式分帧对齐）
MEDIA_REPAIR_PIPE_CHUNK_SIZE = 64 * 1024
# fMP4 init 段补读上限（字节）：首块可能只有 ftyp 无 moov，累加读取直到出现
# moov 或达到该上限，防止无限累加（v1.4.1）
MEDIA_REPAIR_INIT_MAX = 4 * 1024 * 1024

# ── 媒体修复 v1.4.2（手动离线修复）──────────────────────────────
# 修复产物隐藏目录名（位于原文件同目录，文件列表不展示，路径段访问一律拒绝）
REPAIR_DIR_NAME = ".jflove-repair"
# 修复执行中的临时产物后缀（完成前写临时名，成功后 rename 为正式产物）
REPAIR_TEMP_SUFFIX = ".repairing.mp4"
# 修复产物统一扩展名（fMP4）
REPAIR_OUTPUT_EXT = ".mp4"
# 修复中心轮询建议间隔（秒，文档约定，服务端不强约束）
REPAIR_POLL_INTERVAL = 2.5
# 损坏文件播放拒绝错误码前缀（/stream 415 detail 以此开头，三端据此弹修复引导）
MEDIA_NEEDS_REPAIR_CODE = "[MEDIA_NEEDS_REPAIR]"
# 修复进度 DB 节流间隔（秒）：执行中按产物大小估算进度，低于该间隔不写库
REPAIR_PROGRESS_THROTTLE = 1.0

# ── 离线下载 v1.8.0（网址分析 + 媒体抓取）─────────────────────────
# 设计依据：`文档记录/设计文档/v1.8.0.md` §2.6.2「固定常量」段。
#
# 以下均为**不可由管理员配置**的实现常量；可配项走 config 表（键见 OFFLINE_*_KEY）。
# 目标目录内的隐藏临时目录名（文件列表不展示、路径段访问一律拒绝）
OFFLINE_DIR_NAME = ".jflove-download"
# 单次分析总预算（秒，含无头浏览器启动与页面等待）
OFFLINE_ANALYZE_TOTAL_TIMEOUT = 30.0
# ready 清单有效期（秒）：媒体地址普遍带签名，久放必失效
OFFLINE_ANALYSIS_TTL = 600.0
# 同址分析结果复用窗口（秒）：窗口内直接复用 items（AC-208）
OFFLINE_ANALYSIS_REUSE_TTL = 600.0
# 分析队列排队上限（超出直接拒绝创建，避免任务无限堆积）
OFFLINE_QUEUE_MAX = 20
# 图片分页追加最大页数
OFFLINE_MAX_IMAGE_PAGES = 20
# 单任务分片并发（不把带宽/磁盘吃满）
OFFLINE_SEGMENT_CONCURRENCY = 4
# 出站重定向最大跳数（每跳都必须重新做 SSRF 校验）
OFFLINE_MAX_REDIRECTS = 5
# 出站各类响应体上限（字节）
OFFLINE_HTML_MAX_BYTES = 5 * 1024 * 1024
OFFLINE_IMAGE_MAX_BYTES = 20 * 1024 * 1024
OFFLINE_SEGMENT_MAX_BYTES = 8 * 1024 * 1024
OFFLINE_PLAYLIST_MAX_BYTES = 2 * 1024 * 1024
# 出站超时（秒）：建立连接 / 读取
OFFLINE_CONNECT_TIMEOUT = 10.0
OFFLINE_READ_TIMEOUT = 30.0
# 进度落库节流（秒）
OFFLINE_PROGRESS_THROTTLE = 1.0
# 无头浏览器闲置回收（秒）
OFFLINE_BROWSER_IDLE_SECONDS = 60.0
# 图片过小阈值（像素）：宽高都小于该值的图视为统计像素/图标，不进入清单
OFFLINE_MIN_IMAGE_SIDE = 50
# 磁盘预检余量系数（要求剩余空间 ≥ 预估 × 该系数）
OFFLINE_DISK_SAFETY_FACTOR = 1.2
# 出站统一 UA（不伪装成浏览器 UA）
OFFLINE_USER_AGENT = "JFLove-OfflineDownload/1.8 (+self-hosted)"

# config 表配置键（服务端全局策略，仅管理员可改；**不含账号级目录**）
OFFLINE_ENABLED_KEY = "offline_download_enabled"             # 总开关："0" 关（默认）/ "1" 开
OFFLINE_DEEP_ANALYZE_KEY = "offline_download_deep_analyze"   # 深度分析（无头浏览器）开关
OFFLINE_MAX_CONCURRENT_KEY = "offline_download_max_concurrent"    # 下载并发（1~4）
OFFLINE_MAX_FILE_SIZE_KEY = "offline_download_max_file_size_mb"   # 单文件体积上限（MB）
OFFLINE_MAX_ITEMS_KEY = "offline_download_max_items"              # 单次清单项数上限
OFFLINE_MAX_IMAGES_KEY = "offline_download_max_images"            # 单页图片张数上限
# 配置项默认值与硬边界（服务端校验，不信任客户端）
OFFLINE_DEFAULT_MAX_CONCURRENT = 2
OFFLINE_HARD_MAX_CONCURRENT = 4
OFFLINE_DEFAULT_MAX_FILE_SIZE_MB = 4096
OFFLINE_HARD_MAX_FILE_SIZE_MB = 102400
OFFLINE_DEFAULT_MAX_ITEMS = 50
OFFLINE_HARD_MAX_ITEMS = 200
OFFLINE_DEFAULT_MAX_IMAGES = 200
OFFLINE_HARD_MAX_IMAGES = 1000

Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
Path(UPLOAD_TEMP_DIR).mkdir(parents=True, exist_ok=True)
