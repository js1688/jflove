"""
会话状态管理模块

维护客户端与服务端的全局会话状态（单例），包含：
  - 服务端地址
  - X-Session-ID（会话标识）
  - 会话密钥（内存中，不持久化）
  - JWT 令牌
  - 当前用户信息
  - 登录有效期上限（v1.1.1 新增，由 UI 下拉框配置）
"""

import threading

from src.config.settings import LOCAL_SESSION_TTL_DEFAULT


class SessionManager:
    """
    会话状态单例管理器。

    线程安全，持有客户端当前所有会话相关状态。
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_state()
        return cls._instance

    def _init_state(self) -> None:
        """初始化/重置所有会话状态字段"""
        # 服务端连接信息
        self.server_url: str = ""
        # 会话标识（由服务端在密钥交换时分配）
        self.session_id: str = ""
        # 32 字节会话密钥（ECDH 派生，仅存内存）
        self.session_key: bytes | None = None
        # JWT 访问令牌
        self.token: str = ""
        # 当前登录用户信息
        self.user_id: int | None = None
        self.username: str = ""
        self.role: str = ""
        # 密钥交换时间戳（用于判断是否需要刷新）
        self.key_exchange_time: float = 0.0
        # JWT 过期时间戳（Unix 秒，0 表示未知）
        self.token_expires_at: float = 0.0
        # 登录有效期上限（秒）：用户在登录界面选择的本地会话最长保留时长
        # 实际生效时长 = min(JWT exp, key_exchange_time + local_session_max_seconds)
        # v1.1.1 新增：让用户能"更严格"地缩短本地会话，不能延长服务端 JWT
        self.local_session_max_seconds: int = LOCAL_SESSION_TTL_DEFAULT

    def is_session_ready(self) -> bool:
        """
        判断会话密钥是否已建立（密钥交换完成）。

        :returns: True 表示可以发送加密请求
        """
        return bool(self.session_id and self.session_key)

    def is_logged_in(self) -> bool:
        """
        判断用户是否已登录。

        :returns: True 表示已完成登录并持有有效令牌
        """
        return bool(self.token and self.is_session_ready())

    def effective_expire_at(self) -> float:
        """
        计算当前会话的实际失效时间戳（Unix 秒）。

        取下列两者中较小者：
          1. 服务端签发 JWT 的 exp（token_expires_at）
          2. 本地会话上限：key_exchange_time + local_session_max_seconds

        :returns: 实际失效时间戳；若无任何有效字段则返回 0
        """
        candidates = []
        if self.token_expires_at:
            candidates.append(self.token_expires_at)
        if self.local_session_max_seconds and self.key_exchange_time:
            candidates.append(
                self.key_exchange_time + self.local_session_max_seconds
            )
        return min(candidates) if candidates else 0.0

    def is_admin(self) -> bool:
        """
        判断当前用户是否为管理员。

        :returns: True 表示管理员角色
        """
        return self.role == "admin"

    def to_dict(self) -> dict:
        """
        将会话状态导出为可序列化的字典（用于持久化到 JSON 文件）。

        v1.1.5 新增：替代 QSettings，所有可持久化字段统一写入 session.json。

        :returns: 包含当前会话信息的字典（不含 session_key / session_id）
        """
        return {
            "server_url": self.server_url,
            "token": self.token,
            "username": self.username,
            "role": self.role,
            "user_id": self.user_id,
            "token_expires_at": self.token_expires_at,
            "local_session_max_seconds": self.local_session_max_seconds,
        }

    def from_dict(self, data: dict) -> None:
        """
        从字典恢复会话状态（不覆盖 session_key / session_id / key_exchange_time）。

        v1.1.5 新增：从 session.json 读回数据时调用。

        :param data: to_dict() 先前导出的字典
        """
        self.server_url = data.get("server_url", "")
        self.token = data.get("token", "")
        self.username = data.get("username", "")
        self.role = data.get("role", "")
        self.user_id = data.get("user_id")
        self.token_expires_at = float(data.get("token_expires_at", 0))
        self.local_session_max_seconds = int(
            data.get("local_session_max_seconds", LOCAL_SESSION_TTL_DEFAULT)
        )

    def clear(self) -> None:
        """清除所有会话状态（退出登录时调用）"""
        self._init_state()


# 全局单例
session_manager = SessionManager()
