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

Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
Path(UPLOAD_TEMP_DIR).mkdir(parents=True, exist_ok=True)
