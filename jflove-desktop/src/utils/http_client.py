"""
HTTP 客户端模块

封装与后端的所有通信：
  - 自动注入 X-Session-ID 请求头
  - 自动将请求体加密（ChaCha20-Poly1305）
  - 自动将响应体解密
  - 统一错误处理（含加密错误响应自动识别）

文件下载/预览也走端到端加密流：服务端按帧加密（[4B 长度][12B nonce][密文+16B tag]），
客户端边收边解密落盘，公司 MITM 代理即便能解密 HTTPS 也只能看到密文帧。

v1.1.1 新增：ECDH 加密会话失效（服务端重启后内存 _session_store 清空等场景）的
           静默续约。客户端收到 401 且 detail 是 "会话不存在或已过期 / 缺少 X-Session-ID"
           类时，自动重做 key-exchange 并重发原请求；JWT 类 401 仍直接抛 ApiError 让
           上层登出。最多重试 1 次，避免无限循环。
"""

import os
import threading
import requests

from src.utils.crypto import (
    encrypt, decrypt,
    parse_stream_frame,
)
from src.utils.session import session_manager
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── ECDH 类 401 detail 识别串（来自 jflove-server/src/utils/middleware.py） ──
# 命中其一即认定为"加密会话失效"，触发静默续约；任何其他 detail 都视为 JWT 类。
_ECDH_401_PATTERNS = (
    "会话不存在或已过期",
    "会话已失效",
    "缺少 X-Session-ID",
)


def _is_ecdh_session_error(detail: str) -> bool:
    """判断 401 detail 是否属于"加密会话失效"类（应当静默续约而非登出）。"""
    if not detail:
        return False
    return any(p in detail for p in _ECDH_401_PATTERNS)


class ApiError(Exception):
    """
    API 请求错误，携带 HTTP 状态码和服务端返回的 detail。

    :param status_code: HTTP 状态码
    :param detail: 错误描述文字
    """

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"[{status_code}] {detail}")


class HttpClient:
    """
    加密 HTTP 客户端。

    除密钥交换和 admin-exists 接口外，所有请求均自动加密。
    JWT 令牌通过加密 Body 传递（字段名 token），不放明文 header。

    v1.1.1：内置 ECDH 静默续约能力（详见模块 docstring）。
    """

    # ── ECDH 静默续约的并发控制（类级单例：所有线程共享）──
    # 多个线程同时收到 ECDH 类 401 时，只让第一个真正去续约，其余等待结果。
    _ecdh_resync_lock = threading.Lock()
    _ecdh_resync_inflight: bool = False
    _ecdh_resync_cond = threading.Condition(_ecdh_resync_lock)

    # ── 内部辅助方法 ──────────────────────────────────

    def _try_resync_ecdh(self) -> bool:
        """
        尝试对 ECDH 加密会话做单飞续约。

        - 若当前已有线程正在续约：本线程在 Condition 上等待该次续约完成；
        - 否则本线程持锁发起续约；
        - 续约成功/失败结果返回给所有等待者，每个调用方自行决定后续重试。

        :returns: True 续约后可重试；False 续约失败，不应再重试
        """
        # 延迟 import，避免与 services.auth_service 互相 import 形成循环依赖
        from src.services import auth_service

        with self._ecdh_resync_cond:
            if self._ecdh_resync_inflight:
                # 别的线程已在续约，等它完成
                self._ecdh_resync_cond.wait(timeout=20)
                # 持有者通过 ok 标志告知是否成功；这里直接看 session_manager.session_id
                # 是否仍然存在并且 key_exchange_time 是新值。简化处理：续约成功后
                # session_id / key 必然有效，否则也无别的方式判定，直接返回 True 让调用方重试一次。
                return True
            HttpClient._ecdh_resync_inflight = True

        ok = False
        try:
            auth_service.resync_session()
            ok = True
        except Exception as e:
            logger.warning("ECDH 静默续约失败: %s", e)
        finally:
            with self._ecdh_resync_cond:
                HttpClient._ecdh_resync_inflight = False
                self._ecdh_resync_cond.notify_all()
        return ok

    def _base_url(self) -> str:
        """返回服务端根地址（去除末尾斜杠）"""
        return session_manager.server_url.rstrip("/")

    def _auth_headers(self) -> dict:
        """构建携带 X-Session-ID 的请求头"""
        return {"X-Session-ID": session_manager.session_id}

    def _build_payload(self, data: dict | None) -> dict:
        """
        将数据字典注入 token 后加密，返回加密信封。

        :param data: 原始请求数据字典
        :returns: {"nonce": "...", "ciphertext": "..."}
        """
        payload = dict(data or {})
        # 自动注入 JWT 令牌
        if session_manager.token and "token" not in payload:
            payload["token"] = session_manager.token
        return encrypt(session_manager.session_key, payload)

    def _decrypt_envelope_or_none(self, body: dict) -> dict | None:
        """
        若 body 是 {nonce, ciphertext} 加密信封，则解密返回原 dict；否则返回 None。

        :param body: 已 json() 解析的响应字典
        :returns: 解密后字典，或 None 表示不是加密信封
        """
        if isinstance(body, dict) and "nonce" in body and "ciphertext" in body:
            try:
                return decrypt(
                    session_manager.session_key, body["nonce"], body["ciphertext"]
                )
            except Exception as e:
                logger.error("响应体解密失败: %s", e)
                return None
        return None

    def _extract_error_detail(self, resp: requests.Response) -> str:
        """从错误响应中尽力解析出 detail 文案（含加密信封识别）。"""
        detail = "请求失败"
        try:
            err_body = resp.json()
            decrypted = self._decrypt_envelope_or_none(err_body)
            if decrypted is not None:
                detail = decrypted.get("detail", detail)
            elif isinstance(err_body, dict):
                detail = err_body.get("detail", resp.text)
            else:
                detail = resp.text
        except Exception:
            detail = resp.text
        return detail

    def _parse_response(self, resp: requests.Response) -> dict:
        """
        解析并解密响应体（含错误响应）。

        :param resp: requests 响应对象
        :returns: 解密后的原始 dict
        :raises ApiError: HTTP 状态码 >= 400
        """
        if resp.status_code >= 400:
            detail = self._extract_error_detail(resp)
            # 401 仅在最外层 _send_with_auto_resync 已重试过仍失败才走到这里，
            # 因此这里统一记录日志后抛 ApiError 即可。
            logger.error("API 请求失败 [%d]: %s", resp.status_code, detail)
            raise ApiError(resp.status_code, detail)

        body = resp.json()
        decrypted = self._decrypt_envelope_or_none(body)
        if decrypted is not None:
            return decrypted
        # 明文响应（admin-exists / health 等）
        return body

    def _send_with_auto_resync(self, send_fn) -> "requests.Response":
        """
        包裹一次发送动作，遇到 ECDH 类 401 时自动续约后**重发一次**。

        - send_fn: 无参可调用，每次调用执行一次完整的 requests 发送，返回 Response。
                   续约后会用新的 session_id / session_key 重新构造请求体并再次调用。
        - JWT 类 401、网络错误、其他 HTTP 错误：直接返回原 Response，由上层 _parse_response 处理。
        - 单次请求最多续约重试 1 次，第二次仍 401 即视为真失败上抛。

        :returns: Response 对象（可能是首发或重发的结果）
        """
        resp = send_fn()
        if resp.status_code != 401:
            return resp
        detail = self._extract_error_detail(resp)
        if not _is_ecdh_session_error(detail):
            # JWT 类 / 其他 401：交给上层处理（最终触发登出）
            return resp
        # ECDH 类 401：尝试静默续约
        try:
            resp.close()
        except Exception:
            pass
        logger.info("检测到加密会话失效（detail=%s），触发静默续约", detail)
        ok = self._try_resync_ecdh()
        if not ok:
            # 续约失败：构造一个伪造的 Response 已不现实，直接重发让上层拿到真实失败
            return send_fn()
        # 续约成功：用新会话密钥重发一次
        return send_fn()

    # ── 公开接口：明文（无需会话密钥） ───────────────────

    def get_plain(self, endpoint: str) -> dict:
        """
        发送明文 GET 请求（用于 admin-exists 等不需要加密的接口）。

        :param endpoint: API 路径，如 /api/v1/auth/admin-exists
        :returns: 响应 dict
        :raises ApiError: 请求失败
        """
        url = self._base_url() + endpoint
        resp = requests.get(url, timeout=10)
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.json().get("detail", resp.text))
        return resp.json()

    def post_plain(self, endpoint: str, data: dict) -> dict:
        """
        发送明文 POST 请求（用于密钥交换接口）。

        :param endpoint: API 路径
        :param data: 请求体字典（不加密）
        :returns: 响应 dict
        :raises ApiError: 请求失败
        """
        url = self._base_url() + endpoint
        resp = requests.post(url, json=data, timeout=15)
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, resp.json().get("detail", resp.text))
        return resp.json()

    # ── 公开接口：加密请求 ────────────────────────────

    def post(self, endpoint: str, data: dict | None = None) -> dict:
        """
        发送加密 POST 请求。

        :param endpoint: API 路径
        :param data: 请求体字典（会自动注入 token 并加密）
        :returns: 解密后的响应 dict
        :raises ApiError: 请求失败
        """
        url = self._base_url() + endpoint

        def _send() -> requests.Response:
            # 续约后会重新构造 payload，这样 token 会用新的 session_key 重新加密
            payload = self._build_payload(data)
            return requests.post(
                url, json=payload, headers=self._auth_headers(), timeout=30
            )

        resp = self._send_with_auto_resync(_send)
        return self._parse_response(resp)

    def get(self, endpoint: str, data: dict | None = None) -> dict:
        """
        发送加密 GET 请求（请求体为加密 JSON，服务端从 body 读取参数）。

        :param endpoint: API 路径
        :param data: 请求参数字典（会自动注入 token 并加密）
        :returns: 解密后的响应 dict
        :raises ApiError: 请求失败
        """
        url = self._base_url() + endpoint

        def _send() -> requests.Response:
            payload = self._build_payload(data)
            return requests.get(
                url, json=payload, headers=self._auth_headers(), timeout=30
            )

        resp = self._send_with_auto_resync(_send)
        return self._parse_response(resp)

    def put(self, endpoint: str, data: dict | None = None) -> dict:
        """
        发送加密 PUT 请求。

        :param endpoint: API 路径
        :param data: 请求体字典
        :returns: 解密后的响应 dict
        :raises ApiError: 请求失败
        """
        url = self._base_url() + endpoint

        def _send() -> requests.Response:
            payload = self._build_payload(data)
            return requests.put(
                url, json=payload, headers=self._auth_headers(), timeout=30
            )

        resp = self._send_with_auto_resync(_send)
        return self._parse_response(resp)

    def delete(self, endpoint: str, data: dict | None = None) -> dict:
        """
        发送加密 DELETE 请求。

        :param endpoint: API 路径
        :param data: 请求体字典
        :returns: 解密后的响应 dict
        :raises ApiError: 请求失败
        """
        url = self._base_url() + endpoint

        def _send() -> requests.Response:
            payload = self._build_payload(data)
            return requests.delete(
                url, json=payload, headers=self._auth_headers(), timeout=30
            )

        resp = self._send_with_auto_resync(_send)
        return self._parse_response(resp)

    def _handle_stream_error(self, resp: requests.Response) -> None:
        """
        统一处理流式接口的错误响应（与 _parse_response 同款解密逻辑）。

        :raises ApiError: 总会抛出，携带解密后的 detail
        """
        detail = "文件下载失败"
        try:
            err_body = resp.json()
            decrypted = self._decrypt_envelope_or_none(err_body)
            if decrypted is not None:
                detail = decrypted.get("detail", detail)
            elif isinstance(err_body, dict):
                detail = err_body.get("detail", detail)
        except Exception:
            pass
        logger.error("文件流请求失败 [%d]: %s", resp.status_code, detail)
        raise ApiError(resp.status_code, detail)

    def stream_request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
    ) -> "requests.Response":
        """
        发起流式请求，返回原始 Response（调用方负责关闭连接）。

        payload 被加密后放入 JSON body；响应体是 v2 帧序列（元数据帧 + 数据帧）。
        连接超时 5s，读取超时 30s。

        v1.1.1：建连阶段若服务端返回 ECDH 类 401，会静默续约后重发一次。
                续约只发生在"建连"瞬间；一旦帧流开始读取，期间出错由调用方按
                网络错误处理（流期间换密钥也救不回当前流）。

        :param method: HTTP 方法，如 "GET"
        :param path: API 路径，如 /api/v1/files/stream
        :param payload: 请求参数字典（自动注入 token 并加密）
        :returns: 原始 requests.Response（stream=True，调用方负责迭代与关闭）
        :raises ApiError: HTTP 错误状态码
        """
        url = self._base_url() + path

        def _send() -> requests.Response:
            encrypted = self._build_payload(payload)
            return requests.request(
                method, url,
                json=encrypted,
                headers=self._auth_headers(),
                stream=True,
                timeout=(5, 30),
            )

        resp = self._send_with_auto_resync(_send)
        if resp.status_code >= 400:
            self._handle_stream_error(resp)
        return resp

    def download_stream(
        self,
        endpoint: str,
        data: dict | None = None,
        progress_callback=None,
    ) -> bytes:
        """
        下载加密文件流并整体载入内存返回（适合预览等小文件场景）。

        服务端把文件按 64KB 明文切片，每片 ChaCha20-Poly1305 加密为
        [4B 长度][12B nonce][密文+16B tag] 帧后流式输出。本方法逐帧读取并
        解密，最终拼接出完整明文。

        :param endpoint: API 路径
        :param data: 请求参数字典（自动注入 token 并加密 body 发出）
        :param progress_callback: 进度回调 fn(downloaded_plaintext_bytes, 0)；
                                  total 传 0 表示由调用方维护
        :returns: 解密后的文件原始字节
        :raises ApiError: 请求失败（含权限/路径错误等加密 detail）
        """
        url = self._base_url() + endpoint

        def _send() -> requests.Response:
            payload = self._build_payload(data)
            return requests.get(
                url,
                json=payload,
                headers=self._auth_headers(),
                stream=True,
                timeout=120,
            )

        resp = self._send_with_auto_resync(_send)
        if resp.status_code >= 400:
            self._handle_stream_error(resp)

        chunks: list[bytes] = []
        downloaded = 0
        raw = resp.raw
        while True:
            plaintext = parse_stream_frame(raw, session_manager.session_key)
            if plaintext is None:
                break
            chunks.append(plaintext)
            downloaded += len(plaintext)
            if progress_callback:
                progress_callback(downloaded, 0)
        return b"".join(chunks)

    def download_to_file(
        self,
        endpoint: str,
        data: dict | None = None,
        save_path: str = "",
        progress_callback=None,
        cancelled_flag=None,
    ) -> int:
        """
        下载加密文件流并边收边解密直接写入磁盘，避免大文件全部入内存。

        实现：服务端按 64KB 明文切片→每片独立 ChaCha20-Poly1305 加密为帧。
        客户端逐帧读取（4B 长度→12B nonce→密文+认证标签），解密后写入临时文件
        `xxx.part`；下载完成后原子重命名为目标文件。任意一帧认证失败立即抛错并
        清理临时文件，**保证写入磁盘的内容均为已通过完整性校验的明文**。

        :param endpoint: API 路径
        :param data: 请求参数字典
        :param save_path: 本地保存的绝对路径（含文件名），目录会自动创建
        :param progress_callback: 进度回调 fn(已写入明文字节, 0)
        :param cancelled_flag: 取消判断回调，返回 True 时中止并清理临时文件
        :returns: 实际写入的明文字节数
        :raises ApiError: 请求失败
        :raises ValueError: 用户取消，或加密流被破坏（认证失败/截断/格式非法）
        """
        url = self._base_url() + endpoint

        def _send() -> requests.Response:
            payload = self._build_payload(data)
            return requests.get(
                url,
                json=payload,
                headers=self._auth_headers(),
                stream=True,
                timeout=120,
            )

        resp = self._send_with_auto_resync(_send)
        if resp.status_code >= 400:
            self._handle_stream_error(resp)

        if save_path:
            os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)

        # 写入临时文件，下载成功后再 rename 到目标路径，避免半成品被误用
        tmp_path = save_path + ".part"
        downloaded = 0
        raw = resp.raw
        try:
            with open(tmp_path, "wb") as f:
                while True:
                    if cancelled_flag and cancelled_flag():
                        raise ValueError("下载已取消")
                    plaintext = parse_stream_frame(
                        raw, session_manager.session_key
                    )
                    if plaintext is None:
                        break  # 流正常结束
                    f.write(plaintext)
                    downloaded += len(plaintext)
                    if progress_callback:
                        progress_callback(downloaded, 0)
            os.replace(tmp_path, save_path)
        except Exception:
            # 出错或取消时，清理临时文件
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            raise
        return downloaded


# 全局单例
http_client = HttpClient()
