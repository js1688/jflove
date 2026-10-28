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
APP_VERSION = "1.8.4"
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

# ── v1.6.0 文本编辑器（Scratch Editor）常量 ──────────────────────────
# 说明：编辑器是**纯本地**功能（设计 §7.3「零远端」），下列常量是 Python 侧与
# 渲染层共用的**唯一取值来源**；渲染层通过桥读取，不得各写一份。

# 草稿库目录（设计 §6.3.1）：与既有 storage/ 同级，随用户数据目录，
# 卸载时由既有 CurUninstallStepChanged 流程一并询问是否删除。
SCRATCH_DIR = os.path.join(LOCAL_STORAGE_DIR, "scratch")

# 自动保存 / 草稿暂存的防抖间隔（毫秒）。设计 §6.2.2 / §6.3.2 规定它是**唯一常量**：
# 渲染层按它做 debounce，Python 侧只负责落盘（不重复防抖）。
AUTO_SAVE_DEBOUNCE_MS = 1500

# 快捷键映射与「文件关联勾选」的落盘位置（设计 §4.2.4 / §4.3）。
# 都是**纯渲染层偏好、由 Python 侧读写**：渲染层不碰文件系统，也不用 localStorage
# （R-1 已实测默认 profile 是 off-the-record，localStorage 不跨重启）。
SHORTCUTS_FILE = os.path.join(LOCAL_STORAGE_DIR, "shortcuts.json")
FILE_ASSOC_FILE = os.path.join(LOCAL_STORAGE_DIR, "file_assoc.json")

# ── 类型校验阈值（设计 §5.2.1，AC-49 ~ AC-53） ──
# 二进制嗅探「前 8 KB」（F8 的判据措辞）；但编码判定需要更大样本才有区分度
# （gb18030 小样本有假阴性）⇒ **一次有界读 64 KB**，前 8 KB 用于嗅探、
# 整份样本用于编码判定，不引入第二次 I/O。
TEXT_SNIFF_BYTES = 8 * 1024
TEXT_SAMPLE_BYTES = 64 * 1024
# > 5 MB 且 ≤ 50 MB ⇒ 降级**只读**打开（内容完整，不截断）；> 50 MB ⇒ 拒绝打开。
TEXT_READONLY_BYTES = 5 * 1024 * 1024
TEXT_MAX_BYTES = 50 * 1024 * 1024
