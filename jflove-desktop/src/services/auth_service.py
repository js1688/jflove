"""
认证服务模块

封装与后端认证相关的所有操作：
  - X25519 密钥交换（建立会话密钥）
  - 管理员存在性检查
  - 管理员初始化
  - 用户登录 / 令牌刷新
  - 退出登录
  - 会话持久化（JSON 文件）与免登录恢复

v1.1.5 重构：
  - 用 JSON 文件存储（USER_DATA_DIR/storage/session.json）替代 QSettings
  - 用户数据保存在 %APPDATA%/JFLove/ 下，程序退出不丢失
  - 提供从旧 QSettings 的一次性数据迁移
"""

import json
import os
import time

from src.config.settings import (
    LOCAL_SESSION_TTL_DEFAULT, LOCAL_STORAGE_DIR,
)
from src.utils.crypto import generate_x25519_keypair, derive_session_key
from src.utils.http_client import http_client
from src.utils.session import session_manager
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── 会话持久化文件路径 ────────────────────────────────
_SESSION_FILE = os.path.join(LOCAL_STORAGE_DIR, "session.json")

# ── 旧 QSettings 键名（v1.1.5 之前用，仅用于数据迁移） ──
_LEGACY_SESSION_KEYS = {
    "server_url": "session/server_url",
    "token": "session/token",
    "username": "session/username",
    "role": "session/role",
    "user_id": "session/user_id",
    "expires_at": "session/expires_at",
    "local_max_seconds": "session/local_max_seconds",
}

# ── session.json 键名 ─────────────────────────────────
_SKEY_SERVER_URL = "server_url"
_SKEY_TOKEN = "token"
_SKEY_USERNAME = "username"
_SKEY_ROLE = "role"
_SKEY_USER_ID = "user_id"
_SKEY_EXPIRES_AT = "token_expires_at"
_SKEY_LOCAL_MAX_SECONDS = "local_session_max_seconds"

# 迁移标记：确保只执行一次
_MIGRATION_DONE_MARKER = "_migration_completed_v1_1_5"


def _decode_token_exp(token: str) -> float:
    """
    不校验签名地解码 JWT，提取 exp 声明（过期时间）。

    :param token: JWT 字符串
    :returns: Unix 时间戳（秒），解码失败返回 0
    """
    try:
        import base64
        parts = token.split(".")
        if len(parts) != 3:
            return 0
        payload_b64 = parts[1] + "=="
        import json as _json
        payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
        return float(payload.get("exp", 0))
    except Exception:
        return 0


# ── JSON 文件读写 ────────────────────────────────────

def _read_session_file() -> dict:
    """
    从 session.json 读取持久化数据。

    :returns: 字典，文件不存在或损坏时返回空字典
    """
    if not os.path.exists(_SESSION_FILE):
        return {}
    try:
        with open(_SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("读取 session.json 失败（将以空数据继续）: %s", exc)
        return {}


def _write_session_file(data: dict) -> None:
    """
    将字典原子写入 session.json。

    :param data: 要持久化的数据字典
    """
    try:
        os.makedirs(os.path.dirname(_SESSION_FILE), exist_ok=True)
        tmp = _SESSION_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _SESSION_FILE)
    except Exception as exc:
        logger.warning("写入 session.json 失败: %s", exc)


# ── 数据迁移（v1.1.5：从旧 QSettings / 旧文件路径迁移到用户目录） ──

def _migrate_legacy_data() -> None:
    """
    一次性迁移旧版本（v1.1.4 及更早）的数据到新用户数据目录。

    迁移源（按优先级）：
      1. QSettings（Windows 注册表）：会话信息（token、server_url 等）
      2. 旧 `jflove-desktop/storage/server_history.json`：服务端地址历史

    迁移目标：
      1. `USER_DATA_DIR/storage/session.json`
      2. `USER_DATA_DIR/storage/server_history.json`

    迁移条件：目标文件尚不存在（不覆盖已有数据）。
    迁移后：在 session.json 中写入 _migration_completed_v1_1_5 = true，
            后续启动不再重复迁移。
    """
    session_data = _read_session_file()
    # 已迁移过 or 目标文件已有数据 → 跳过
    if session_data.get(_MIGRATION_DONE_MARKER):
        return

    migrated = False

    # ── 迁移 1：从 QSettings 读取旧会话数据 ──
    try:
        from PySide6.QtCore import QSettings
        from src.config.settings import APP_NAME, APP_ORG
        s = QSettings(APP_ORG, APP_NAME)

        qsettings_data = {}
        for key, qkey in _LEGACY_SESSION_KEYS.items():
            val = s.value(qkey)
            if val is not None:
                qsettings_data[key] = val

        if qsettings_data:
            # 映射旧键名 → 新键名
            mapping = {
                "server_url": _SKEY_SERVER_URL,
                "token": _SKEY_TOKEN,
                "username": _SKEY_USERNAME,
                "role": _SKEY_ROLE,
                "user_id": _SKEY_USER_ID,
                "expires_at": _SKEY_EXPIRES_AT,
                "local_max_seconds": _SKEY_LOCAL_MAX_SECONDS,
            }
            for old_key, new_key in mapping.items():
                if old_key in qsettings_data and new_key not in session_data:
                    session_data[new_key] = qsettings_data[old_key]

            if not session_data.get(_MIGRATION_DONE_MARKER):
                migrated = True
                logger.info("已从旧 QSettings 迁移会话数据到 %s", _SESSION_FILE)
    except Exception as exc:
        logger.warning("从 QSettings 迁移数据失败（不影响启动）: %s", exc)

    # ── 迁移 2：从旧 storage/server_history.json 迁移地址历史 ──
    try:
        # 旧路径：开发目录下的 storage，或 _MEIPASS 下的 storage
        old_history_paths = []
        # 尝试当前目录（开发模式）
        old_history_paths.append(
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "storage",
                "server_history.json",
            )
        )
        # 尝试 _MEIPASS（旧打包模式）
        if hasattr(__import__("sys"), "_MEIPASS"):
            import sys as _sys
            old_history_paths.append(
                os.path.join(_sys._MEIPASS, "storage", "server_history.json")
            )

        new_history_file = os.path.join(LOCAL_STORAGE_DIR, "server_history.json")
        if not os.path.exists(new_history_file):
            for old_path in old_history_paths:
                if os.path.exists(old_path):
                    import shutil
                    os.makedirs(os.path.dirname(new_history_file), exist_ok=True)
                    shutil.copy2(old_path, new_history_file)
                    logger.info("已从 %s 迁移服务端地址历史到 %s", old_path, new_history_file)
                    migrated = True
                    break
    except Exception as exc:
        logger.warning("迁移地址历史失败（不影响启动）: %s", exc)

    # ── 标记迁移完成 ──
    if migrated:
        session_data[_MIGRATION_DONE_MARKER] = True
        _write_session_file(session_data)
    else:
        # 即使无数据可迁移，也写入标记防止重复扫描
        session_data[_MIGRATION_DONE_MARKER] = True
        _write_session_file(session_data)


# ── 会话持久化（替代 QSettings） ──────────────────────

def _save_session() -> None:
    """将当前会话信息持久化到 session.json。"""
    data = session_manager.to_dict()
    _write_session_file(data)


def save_local_session_max_seconds(seconds: int) -> None:
    """
    单独持久化"登录有效期上限"配置（用于登录界面下拉记忆，无须等到登录成功）。

    :param seconds: 用户选择的本地会话上限秒数
    """
    data = _read_session_file()
    data[_SKEY_LOCAL_MAX_SECONDS] = int(seconds)
    _write_session_file(data)


def load_local_session_max_seconds() -> int:
    """
    读取上次记忆的"登录有效期上限"，没有则返回默认值。

    :returns: 秒数，默认 LOCAL_SESSION_TTL_DEFAULT
    """
    data = _read_session_file()
    raw = data.get(_SKEY_LOCAL_MAX_SECONDS, LOCAL_SESSION_TTL_DEFAULT)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return LOCAL_SESSION_TTL_DEFAULT


def _clear_saved_session() -> None:
    """清除 session.json 中的会话信息（保留 local_max_seconds 偏好和迁移标记）。"""
    data = _read_session_file()
    # 保留用户偏好和迁移标记
    keep_keys = {_SKEY_LOCAL_MAX_SECONDS, _MIGRATION_DONE_MARKER}
    cleaned = {k: v for k, v in data.items() if k in keep_keys}
    _write_session_file(cleaned)


def try_restore_session() -> bool:
    """
    尝试从 session.json 恢复上次的会话，并重新执行密钥交换。

    token 仍在有效期内（留有 60 秒余量）才会恢复；过期则返回 False。

    :returns: True 表示恢复成功，可以直接进入主界面；False 表示需要重新登录
    """
    data = _read_session_file()
    token = data.get(_SKEY_TOKEN, "")
    server_url = data.get(_SKEY_SERVER_URL, "")
    username = data.get(_SKEY_USERNAME, "")
    role = data.get(_SKEY_ROLE, "")
    user_id = data.get(_SKEY_USER_ID)
    expires_at_raw = data.get(_SKEY_EXPIRES_AT, 0)
    try:
        expires_at = float(expires_at_raw or 0)
    except (TypeError, ValueError):
        expires_at = 0.0

    if not token or not server_url:
        return False

    # token 距离过期不足 60 秒则视为无效
    if expires_at and time.time() >= expires_at - 60:
        _clear_saved_session()
        return False

    try:
        # 恢复用户偏好（登录有效期上限）—— 在 do_key_exchange 之前设置好，
        # 保证免登录恢复后 effective_expire_at 计算口径正确
        session_manager.local_session_max_seconds = load_local_session_max_seconds()
        # 重新执行密钥交换建立加密通道
        do_key_exchange(server_url)
        # 恢复会话信息
        session_manager.token = token
        session_manager.username = username
        session_manager.role = role
        session_manager.user_id = int(user_id) if user_id is not None else None
        session_manager.token_expires_at = expires_at
        logger.info("免登录恢复会话成功: %s (role=%s)", username, role)
        return True
    except Exception as e:
        logger.warning("免登录恢复失败，需要重新登录: %s", e)
        _clear_saved_session()
        return False


def do_key_exchange(server_url: str) -> None:
    """
    与服务端执行 X25519 密钥交换，建立会话密钥。

    会话密钥仅存于内存，服务重启后需重新交换。

    :param server_url: 服务端根地址，如 http://localhost:8989
    :raises ApiError: 连接失败或交换失败
    """
    session_manager.server_url = server_url
    private_key, client_pub = generate_x25519_keypair()

    resp = http_client.post_plain("/api/v1/auth/key-exchange", {
        "client_public_key": client_pub,
        "refresh": False,
    })

    server_pub = resp["server_public_key"]
    session_id = resp["session_id"]
    session_key = derive_session_key(private_key, server_pub)

    session_manager.session_id = session_id
    session_manager.session_key = session_key
    session_manager.key_exchange_time = time.time()
    logger.info("密钥交换完成，session_id=%s", session_id)


def resync_session() -> None:
    """
    静默重新执行 X25519 密钥交换（v1.1.1 新增）。

    用途：当 http_client 收到"会话不存在或已过期"类 401 时，
    在后台透明地重建加密通道，对用户不可见。

    成功后：
      - session_manager.session_id / session_key 被替换为新值（旧的不再可用）
      - JWT (token) / username / role 等保持不变（不刷新 JWT，遵守 §9.2 PFS）
      - 不调用 _save_session：session_id 本就不持久化，且静默续约可能高频发生

    :raises ApiError: 续约失败（网络不通 / 服务端无响应等），由调用方决定后续
    """
    private_key, client_pub = generate_x25519_keypair()
    resp = http_client.post_plain("/api/v1/auth/key-exchange", {
        "client_public_key": client_pub,
        "refresh": False,
    })
    server_pub = resp["server_public_key"]
    new_session_id = resp["session_id"]
    new_session_key = derive_session_key(private_key, server_pub)

    session_manager.session_id = new_session_id
    session_manager.session_key = new_session_key
    session_manager.key_exchange_time = time.time()
    logger.info("ECDH 静默续约完成（用户无感知）")


def refresh_key_exchange() -> None:
    """
    使用旧会话密钥重新执行密钥交换（密钥刷新）。

    :raises ApiError: 刷新失败
    """
    private_key, client_pub = generate_x25519_keypair()

    resp = http_client.post_plain("/api/v1/auth/key-exchange", {
        "client_public_key": client_pub,
        "refresh": True,
    })

    server_pub = resp["server_public_key"]
    new_session_id = resp["session_id"]
    new_session_key = derive_session_key(private_key, server_pub)

    session_manager.session_id = new_session_id
    session_manager.session_key = new_session_key
    session_manager.key_exchange_time = time.time()
    logger.info("密钥刷新完成，新 session_id=%s", new_session_id)


def check_admin_exists() -> bool:
    """
    检查服务端是否已存在管理员账号。

    :returns: True 表示管理员已存在
    :raises ApiError: 请求失败
    """
    resp = http_client.get_plain("/api/v1/auth/admin-exists")
    return resp.get("exists", False)


def init_admin(username: str, password: str) -> None:
    """
    初始化系统管理员账号（仅限系统首次使用时调用）。

    :param username: 管理员用户名（3-20 个字符）
    :param password: 管理员密码（至少 8 个字符）
    :raises ApiError: 创建失败
    """
    http_client.post("/api/v1/auth/init-admin", {
        "username": username,
        "password": password,
    })
    logger.info("管理员账号初始化成功: %s", username)


def login(username: str, password: str, local_max_seconds: int | None = None) -> None:
    """
    使用用户名和密码登录，成功后写入 session_manager 并持久化到 session.json。

    :param username: 用户名
    :param password: 密码
    :param local_max_seconds: v1.1.1 新增。用户在登录界面选择的"登录有效期"上限秒数。
                              v1.1.2 起：同时作为 requested_ttl_seconds 上传给服务端，
                              请求服务端按此 TTL 签发 JWT（服务端 clamp 到 [60, 28800]）。
                              None 表示不指定，服务端走默认值（1 小时）。
    :raises ApiError: 登录失败
    """
    payload = {
        "username": username,
        "password": password,
    }
    if local_max_seconds is not None:
        # v1.1.2：让服务端真正按用户选择签发 JWT，而不是只在本地做"提前到期"
        payload["requested_ttl_seconds"] = int(local_max_seconds)
    resp = http_client.post("/api/v1/auth/login", payload)
    token = resp["token"]
    session_manager.token = token
    session_manager.username = resp.get("username", username)
    session_manager.role = resp.get("role", "user")
    session_manager.user_id = resp.get("user_id")
    session_manager.token_expires_at = _decode_token_exp(token)
    # 应用用户选择的本地会话上限；未传则保持当前值（默认 LOCAL_SESSION_TTL_DEFAULT）
    if local_max_seconds is not None:
        session_manager.local_session_max_seconds = int(local_max_seconds)
    _save_session()
    logger.info("用户登录成功: %s (role=%s)", username, session_manager.role)


def refresh_token() -> None:
    """
    刷新 JWT 令牌，延长登录有效期。

    :raises ApiError: 刷新失败（令牌已过期或无效）
    """
    resp = http_client.post("/api/v1/auth/refresh", {
        "token": session_manager.token,
    })
    token = resp["token"]
    session_manager.token = token
    session_manager.token_expires_at = _decode_token_exp(token)
    _save_session()
    logger.info("令牌刷新成功")


def logout() -> None:
    """
    退出登录，清除内存会话状态和持久化数据。
    """
    _clear_saved_session()
    session_manager.clear()
    logger.info("用户已退出登录")
