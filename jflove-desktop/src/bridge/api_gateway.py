"""
桥的 HTTP 网关：把 renderer 的请求转给既有的 `http_client`

## 为什么是「路径白名单 + 单一入口」而不是每个接口一个桥方法

Web 端所有服务都收敛到 `utils/http-client.ts` 这一个出口。桌面端如果给每个接口
各写一个桥方法，就会出现一份需要与 Web 端同步维护的映射表 —— 新增接口要改两处，
且页面代码无法逐字复用。因此这里只暴露 `api.request(method, path, body, plain)`，
把「路径」当**参数**校验，而不是当方法名。

## 安全边界（宪法 §9.1 / §9.3 / §9.5.16）

1. **只允许 `/api/v1/` 下的路径**（外加 `/health`）；
2. **显式拒绝含 `://` 或非 `/` 开头的路径** —— renderer 永远无法指定目标主机，
   出网目标只由 Python 侧 `session_manager.server_url` 决定；
3. **明文接口白名单只有 3 个**（§9.1.1），不在名单里的路径不可能被当作明文发送；
4. **登录 / 刷新响应里的 JWT 会被剥掉** —— 令牌只留在 Python（§9.3）；
5. renderer 侧没有任何网络出口：出网一律经 `src/utils/http_client.py`（§9.5.16）。
"""

from __future__ import annotations

from typing import Any, Callable

from src.utils.http_client import ApiError, http_client
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: 允许的路径前缀
ALLOWED_PATH_PREFIXES = ("/api/v1/",)

#: 允许的精确路径（非 /api/v1 前缀的少数例外）
ALLOWED_EXACT_PATHS = ("/health",)

#: 明文白名单（必须与后端 `_PLAIN_PATHS` 及宪法 §9.1.1 一致）
PLAIN_PATHS = (
    "/health",
    "/api/v1/auth/key-exchange",
    "/api/v1/auth/admin-exists",
)

#: 响应里含 JWT 的接口：返回前把令牌剥掉（纵深防御；这几个路径其实下面已被禁止透传）
TOKEN_BEARING_PATHS = (
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
)

#: **禁止**经通用透传访问的路径：会话状态必须由 Python 服务层写入
#:
#: 教训（2026-09-20 实测踩到）：登录最初走了通用透传 → 前端拿到"HTTP 成功"，
#: 但 `session_manager.token` 从未被赋值，于是"登录成功"只停留在 HTTP 层，
#: 桥回答 `logged_in=False`，页面永远跳不进业务面板。
#: 根因是通用透传**绕过了 `auth_service`** —— 而会话的唯一真相在 `session_manager`。
#: 因此这里显式拒绝，把"必须走服务层"变成一道硬边界。
SESSION_MANAGED_PATHS = (
    "/api/v1/auth/login",
    "/api/v1/auth/init-admin",
    "/api/v1/auth/refresh",
)

ALLOWED_METHODS = ("GET", "POST", "PUT", "DELETE")


def _validate_path(path: str) -> str:
    """
    校验请求路径是否落在允许范围内。

    :param path: 形如 `/api/v1/users/list` 的路径
    :raises ValueError: 路径非法、不在白名单内，或属于必须走服务层的会话接口
    """
    if not path.startswith("/"):
        raise ValueError(f"路径必须以 / 开头（不允许指定主机）：{path!r}")
    if "://" in path or ".." in path:
        raise ValueError(f"路径包含非法片段：{path!r}")
    if path in SESSION_MANAGED_PATHS:
        raise ValueError(
            f"{path} 属于会话管理接口，禁止通用透传；"
            "请使用桥的 auth.* 方法（会话状态必须由 Python 服务层写入）"
        )
    if path in ALLOWED_EXACT_PATHS:
        return path
    if path.startswith(ALLOWED_PATH_PREFIXES):
        return path
    raise ValueError(f"路径不在允许范围内：{path!r}")


def _sanitize(path: str, result: Any) -> Any:
    """剥掉响应里的 JWT（只回答"令牌在 Python 手里"，不回答"是什么"）"""
    if path not in TOKEN_BEARING_PATHS or not isinstance(result, dict):
        return result
    cleaned = {k: v for k, v in result.items() if k != "token"}
    cleaned["token_held_by"] = "python"
    return cleaned


#: 这些接口按"路径"操作，排查时只需知道路径**长度与哈希**，绝不记原文
#: （安全宪法 §9.14：filename / path 在日志中截断或哈希化）
_PATH_OP_SUFFIXES = ("/files/delete", "/files/rename", "/files/mkdir", "/files/move")


def _log_path_op(path: str, params: dict) -> None:
    """诊断用：记录按路径操作时目标路径的长度与短哈希（不记原文）"""
    import hashlib

    if not path.endswith(_PATH_OP_SUFFIXES):
        return
    body = params.get("body") or {}
    if not isinstance(body, dict):
        return
    for key in ("path", "src_path", "dst_dir_path"):
        raw = body.get(key)
        if isinstance(raw, str) and raw:
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
            logger.debug("按路径操作 %s: %s_len=%d %s_hash=%s", path, key, len(raw), key, digest)


def handle_api_request(params: dict) -> dict:
    """
    执行一次 HTTP 请求（桥的 `api.request` 处理函数）。

    :param params: `{method, path, body, plain}`
    :returns: `{"status": int, "body": dict}` —— 业务错误也以状态码形式返回，
              而不是抛异常，方便前端沿用 Web 端 `ApiError` 的处理方式
    """
    method = str(params.get("method", "")).upper()
    path = _validate_path(str(params.get("path", "")))
    body = params.get("body") or {}
    plain = bool(params.get("plain"))

    if method not in ALLOWED_METHODS:
        raise ValueError(f"不支持的方法：{method!r}")
    if not isinstance(body, dict):
        raise ValueError("body 必须是 JSON 对象")
    if plain and path not in PLAIN_PATHS:
        raise ValueError(f"该路径不在明文白名单内：{path!r}")

    # 路径本身是固定端点（业务参数都在加密 body 里），可安全记录
    logger.debug("桥转发 HTTP %s %s（明文=%s）", method, path, plain)

    _log_path_op(path, params)

    try:
        if plain:
            if method == "POST":
                result: Any = http_client.post_plain(path, body)
            else:
                result = http_client.get_plain(path)
        else:
            caller: Callable[[str, Any], Any] = {
                "GET": http_client.get,
                "POST": http_client.post,
                "PUT": http_client.put,
                "DELETE": http_client.delete,
            }[method]
            result = caller(path, body)
    except ApiError as exc:
        # 业务错误（含加密的错误信封已由 http_client 解密）→ 交回前端处理
        return {
            "status": int(getattr(exc, "status_code", 0) or 0),
            "body": {"detail": getattr(exc, "detail", str(exc))},
        }

    return {"status": 200, "body": _sanitize(path, result)}


def reset_http_client_state() -> None:  # pragma: no cover - 预留
    """预留：会话状态由 `session_manager` 持有，这里无需额外清理"""
