"""
客户端本地配置模块

管理应用基本信息、本地目录、上传策略等配置。

v1.1.5 重构：将持久化数据（日志、存储）从项目目录迁移到用户数据目录，
进程退出后不再丢失。用户数据目录位置：
  - Windows: %APPDATA%/JFLove/
  - Linux:   ~/.local/share/JFLove/
  - macOS:   ~/Library/Application Support/JFLove/
"""

import os
import sys
from pathlib import Path

# ── 应用基本信息 ──────────────────────────────────────
APP_NAME = "JFLove"
APP_VERSION = "1.4.2"
APP_ORG = "JFLove"

# ── 登录有效期下拉选项 (秒, 中文显示) ──
# 用户在登录界面可选择本地会话保留时长；最终生效时长 = min(用户选择, 服务端 JWT exp)。
# v1.1.4：选项从分钟/小时级改为天级：1 天 / 7 天 / 30 天；默认 30 天。
LOCAL_SESSION_TTL_OPTIONS = [
    (86400, "1 天"),
    (604800, "7 天"),
    (2592000, "30 天"),
]
LOCAL_SESSION_TTL_DEFAULT = 2592000  # 默认 30 天


# ── 用户数据目录（持久化数据存放位置） ────────────────
# 区分于 BASE_DIR（资源目录），确保打包后日志/缓存不丢失。

def _get_user_data_dir() -> str:
    """
    获取平台对应的用户数据目录（持久化配置与日志存放位置）。

    Windows: %APPDATA%/JFLove
    Linux:   ~/.local/share/JFLove  （或 $XDG_DATA_HOME/JFLove）
    macOS:   ~/Library/Application Support/JFLove
    """
    system = sys.platform
    if system == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, APP_NAME)
    elif system == "darwin":
        return os.path.join(
            os.path.expanduser("~"), "Library", "Application Support", APP_NAME
        )
    else:
        # Linux / 其他 Unix-like
        xdg_home = os.environ.get(
            "XDG_DATA_HOME",
            os.path.join(os.path.expanduser("~"), ".local", "share"),
        )
        return os.path.join(xdg_home, APP_NAME)


USER_DATA_DIR = _get_user_data_dir()

# ── 本地目录 ──────────────────────────────────────────
# 资源根目录：开发环境是仓库 jflove-desktop/，PyInstaller --onefile 模式下
# 是临时解压目录 _MEIPASS。两种情形下子目录布局保持一致（assets / images / ...）。
if hasattr(sys, "_MEIPASS"):
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).resolve().parent.parent.parent

LOG_DIR = os.path.join(USER_DATA_DIR, "logs")
LOCAL_STORAGE_DIR = os.path.join(USER_DATA_DIR, "storage")

# ── 应用图标路径（PNG 通用 / ICO 兼容 Windows 任务栏） ──
IMAGES_DIR = str(BASE_DIR / "images")
ICON_PNG_PATH = str(BASE_DIR / "images" / "icon.png")
ICON_ICO_PATH = str(BASE_DIR / "images" / "icon.ico")

# ── 服务端默认地址 ────────────────────────────────────
DEFAULT_SERVER_URL = "http://localhost:8989"

# ── 上传策略 ──────────────────────────────────────────
# 分片大小：1 MB
CHUNK_SIZE = 1024 * 1024

# ── JWT 令牌刷新阈值（剩余有效期小于此值时自动刷新，单位：秒） ──
TOKEN_REFRESH_THRESHOLD = 300

# ── HKDF 派生盐（须与后端保持一致） ───────────────────
SESSION_KEY_SALT = b"jflove-v1"

# ── 本地存储目录初始化（用户数据目录持久化） ──────────
Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
Path(LOCAL_STORAGE_DIR).mkdir(parents=True, exist_ok=True)
