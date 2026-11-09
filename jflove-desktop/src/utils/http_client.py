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

v1.5.1 修复（缺陷 M-4）：响应体形状是加密信封但**解密失败**时，原先会把这个外壳
           当明文成功返回（`{"nonce":…,"ciphertext":…}` 直接交给业务层），
           表现为"目录是空的"这类**静默故障**。现在先由 `_is_envelope()` 判形状：
           不是信封 → 明文直返；是信封但解不开 → 抛 `ApiError(502, "响应解密失败")`。
"""

import json
import os
import threading
import time

import requests

from src.utils.crypto import (
    encrypt, decrypt,
    parse_stream_frame,
)
from src.utils.session import session_manager
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── 下载断点续传（v1.9.0，设计 §3.1 / F48.2） ──────────────
#: 本地未完成下载的临时文件后缀（与目标文件同目录，完成后 os.replace 覆盖）
DOWNLOAD_PART_SUFFIX = ".part"
#: part 元数据后缀：记录远端 size / version / 已写偏移，用于校验续传是否安全
DOWNLOAD_PART_META_SUFFIX = ".jflove-part.json"
#: part 元数据落盘间隔（字节）：进程被杀时最多丢这么多续传进度
_PART_META_FLUSH_BYTES = 4 * 1024 * 1024
#: 用户取消下载时的判定文案（调用方按 ValueError 处理取消）
_DOWNLOAD_CANCEL_MESSAGE = "下载已取消"

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


def _is_envelope(body) -> bool:
    """
    判断响应体是否为 ChaCha20-Poly1305 加密信封 —— **只判形状，不解密**。

    形状判据：`dict` 且**同时**包含 `nonce` 与 `ciphertext` 两个键。

    为什么要单独抽一个"只判形状"的函数（而不是看解密结果）：把"不是信封"与
    "是信封但解不开"两种截然不同的情况混为一谈，会让调用方在**认证失败**时
    把 `{"nonce":…,"ciphertext":…}` 外壳当成明文成功返回 —— 上层
    `resp.get("files", [])` 于是得到空列表，界面表现为"目录是空的"，
    不报错、不重试、不登出（v1.5.0 遗留的中等缺陷 M-4）。

    :param body: `resp.json()` 解析结果（可能是任意 JSON 类型）
    :returns: True 表示形状上是加密信封
    """
    return isinstance(body, dict) and "nonce" in body and "ciphertext" in body


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


class DownloadCancelled(ValueError):
    """下载被用户取消（ValueError 子类，兼容既有 `except ValueError` 调用方）"""


# ── 下载 part / meta 的纯函数工具（v1.9.0） ──────────────

def part_paths(save_path: str) -> tuple[str, str]:
    """
    计算下载断点续传的本地文件路径。

    :param save_path: 目标文件绝对路径
    :returns: `(part 路径, meta 路径)`，均在目标文件旁
    """
    return save_path + DOWNLOAD_PART_SUFFIX, save_path + DOWNLOAD_PART_META_SUFFIX


def load_part_meta(meta_path: str) -> dict | None:
    """
    读取 part 元数据（损坏 / 不存在返回 None，调用方按「无续传」处理）。

    :param meta_path: meta JSON 路径
    """
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def clear_part_files(part_path: str, meta_path: str) -> None:
    """删除 part 与 meta（尽力而为，不影响调用方主流程）"""
    for path in (part_path, meta_path):
        if not path:
            continue
        try:
            os.remove(path)
        except OSError:
            pass


def _save_part_meta(meta_path: str, size: int, version: str, offset: int) -> None:
    """原子写入 part 元数据（失败只告警：下次续传会退化为从头下载）"""
    if not meta_path:
        return
    payload = {
        "size": int(size) if size is not None and int(size) >= 0 else 0,
        "version": version or "",
        "offset": int(offset),
        "updated_at": int(time.time()),
    }
    tmp_path = meta_path + ".tmp"
    try:
        parent = os.path.dirname(os.path.abspath(meta_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp_path, meta_path)
    except OSError as e:
        logger.warning("保存下载续传元数据失败：%s", type(e).__name__)


def _header_int(resp: "requests.Response", name: str, default: int = -1) -> int:
    """从响应头读取整数（缺失 / 非法返回 default）"""
    raw = resp.headers.get(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _safe_size(path: str, fallback: int) -> int:
    """文件实际大小（不存在 / 无权限时返回 fallback）"""
    try:
        return os.path.getsize(path)
    except OSError:
        return fallback


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

        ⚠ 返回值 `None` 有**两种**含义（"不是信封" / "是信封但解不开"）。调用方若
        只判 `is not None`，就会把解不开的信封当明文返回（缺陷 M-4）。需要区分时
        请先调 `_is_envelope(body)` 判形状，再决定报错还是按明文处理。

        :param body: 已 json() 解析的响应字典
        :returns: 解密后字典，或 None（不是信封 / 解不开）
        """
        if _is_envelope(body):
            try:
                return decrypt(
                    session_manager.session_key, body["nonce"], body["ciphertext"]
                )
            except Exception as e:
                logger.error("响应体解密失败: %s", e)
                return None
        return None

    def _extract_error_detail(self, resp: requests.Response) -> str:
        """
        从错误响应中尽力解析出 detail 文案（含加密信封识别）。

        三种情况分开处理（对齐 `_parse_response` 的口径）：

        1. 信封且解得开 → 用里面的 `detail`；
        2. 信封但**解不开**（认证失败 / 会话密钥不对）→ 给出明确文案，
           **不**静默退化成 `resp.text`（那会把 `{"nonce":…,"ciphertext":…}` 外壳
           当成给用户看的错误信息）；
        3. 不是信封（明文白名单，如 `admin-exists`）→ 用 `detail` 字段，缺失才用 `resp.text`。
        """
        detail = "请求失败"
        try:
            err_body = resp.json()
        except Exception:
            return resp.text
        if _is_envelope(err_body):
            decrypted = self._decrypt_envelope_or_none(err_body)
            if decrypted is None:
                # 形状是信封但解不开：绝不回退成明文外壳
                return "响应解密失败（会话密钥不匹配或响应被篡改）"
            return decrypted.get("detail", detail)
        if isinstance(err_body, dict):
            return err_body.get("detail", resp.text)
        return resp.text

    def _parse_response(self, resp: requests.Response) -> dict:
        """
        解析并解密响应体（含错误响应）。

        判据顺序（**先判形状，再看解密结果**）：

        - 不是信封 → 明文直返（`admin-exists` / `health` 等明文白名单接口照旧）；
        - 是信封且解开了 → 返回解密后的 dict；
        - 是信封但**解不开** → 抛 `ApiError(502, "响应解密失败")`，
          **绝不**把 `{"nonce":…,"ciphertext":…}` 外壳当明文成功返回（缺陷 M-4）。
          该 502 是**客户端合成**的状态码（服务端此刻的状态是 200），
          表示"服务端返回了信封但本客户端解不开"，详见抛出点注释（R5-T7）。

        :param resp: requests 响应对象
        :returns: 解密后的原始 dict
        :raises ApiError: HTTP 状态码 >= 400，或加密响应体解密失败（客户端合成 502）
        """
        if resp.status_code >= 400:
            detail = self._extract_error_detail(resp)
            # 401 仅在最外层 _send_with_auto_resync 已重试过仍失败才走到这里，
            # 因此这里统一记录日志后抛 ApiError 即可。
            logger.error("API 请求失败 [%d]: %s", resp.status_code, detail)
            raise ApiError(resp.status_code, detail)

        body = resp.json()
        if not _is_envelope(body):
            # 明文响应（admin-exists / health 等）
            return body
        decrypted = self._decrypt_envelope_or_none(body)
        if decrypted is None:
            logger.error("加密响应体解密失败（形状为信封但认证/解密不通过）")
            # ⚠ 502 是**客户端合成**的状态码，**不是**服务端返回的（R5-T7）：
            # 此刻服务端的 HTTP 状态是 200，问题出在"客户端解不开这个信封"
            # （会话密钥不匹配 / 响应被中间人篡改）。之所以复用 502（Bad Gateway），
            # 是为了让上层 `except ApiError` 的既有处理路径（登出/重试/提示）无需新增分支 ——
            # 语义上它是"网关（本次会话隧道）这一侧出了故障"，与 502 的原义一致。
            # 不要把它理解为"服务端返回了 502"。
            raise ApiError(502, "响应解密失败（会话密钥不匹配或响应被篡改）")
        return decrypted

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

        错误文案统一走 `_extract_error_detail()`（R5-T4）：本方法虽然只服务明文白名单，
        但**口径必须与加密路径一致** —— 万一日后误用到加密接口上，也不会把
        `{"nonce":…,"ciphertext":…}` 外壳当 detail 抛给用户（与 M-4 同一类缺陷）。

        :param endpoint: API 路径，如 /api/v1/auth/admin-exists
        :returns: 响应 dict
        :raises ApiError: 请求失败
        """
        url = self._base_url() + endpoint
        resp = requests.get(url, timeout=10)
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, self._extract_error_detail(resp))
        return resp.json()

    def post_plain(self, endpoint: str, data: dict) -> dict:
        """
        发送明文 POST 请求（用于密钥交换接口）。

        错误文案同样走 `_extract_error_detail()`（R5-T4，理由见 `get_plain`）。

        :param endpoint: API 路径
        :param data: 请求体字典（不加密）
        :returns: 响应 dict
        :raises ApiError: 请求失败
        """
        url = self._base_url() + endpoint
        resp = requests.post(url, json=data, timeout=15)
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, self._extract_error_detail(resp))
        return resp.json()

    # ── 公开接口：加密请求 ────────────────────────────

    def post(
        self,
        endpoint: str,
        data: dict | None = None,
        timeout: float | tuple[float, float] = 30,
    ) -> dict:
        """
        发送加密 POST 请求。

        :param endpoint: API 路径
        :param data: 请求体字典（会自动注入 token 并加密）
        :param timeout: 超时秒数，或 `(连接, 读取)` 元组。**长耗时接口必须显式放大**：
            服务端合并大文件分片（`upload/complete`）可能持续数分钟，
            默认 30 秒会把「正在正常合并」误判成失败，进而触发整文件重传。
        :returns: 解密后的响应 dict
        :raises ApiError: 请求失败
        """
        url = self._base_url() + endpoint

        def _send() -> requests.Response:
            # 续约后会重新构造 payload，这样 token 会用新的 session_key 重新加密
            payload = self._build_payload(data)
            return requests.post(
                url, json=payload, headers=self._auth_headers(), timeout=timeout
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
        统一处理流式接口的错误响应（与 `_parse_response` 同款解密逻辑）。

        信封但解不开时同样给明确文案（不回退成密文外壳文本）。

        :raises ApiError: 总会抛出，携带解密后的 detail
        """
        try:
            err_body = resp.json()
        except Exception:
            err_body = None
        if _is_envelope(err_body):
            decrypted = self._decrypt_envelope_or_none(err_body)
            if decrypted is None:
                detail = "响应解密失败（会话密钥不匹配或响应被篡改）"
            else:
                detail = decrypted.get("detail", "文件下载失败")
        elif isinstance(err_body, dict):
            detail = err_body.get("detail", "文件下载失败")
        else:
            detail = "文件下载失败"
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
        range_start: int = 0,
        expected_meta: dict | None = None,
        meta_path: str = "",
        discard_on_cancel: bool = True,
    ) -> int:
        """
        下载加密文件流并边收边解密直接写入磁盘，支持断点续传（v1.9.0）。

        实现：服务端按 64KB 明文切片→每片独立 ChaCha20-Poly1305 加密为帧。
        客户端逐帧读取并解密，写入目标文件旁的 `<save_path>.part`；成功后
        `os.replace` 原子覆盖目标文件。**写入磁盘的内容均为已通过完整性校验的明文**。

        断点续传（F48.2 / 设计 §3.1）：
          - 请求携带 `range_start`（明文在加密 body）；响应头 `X-Stream-Offset` /
            `X-Stream-Total` / `X-File-Version` 用于校验续传是否安全；
          - 本地 part + `<save_path>.jflove-part.json` 记录远端 size/version/offset；
          - 版本不一致 / 服务端未按偏移返回 / 偏移越界（416）→ 删除 part 从头下载一次；
          - 失败默认**保留** part 供下次续传；用户取消时删除 part（`discard_on_cancel`）；
          - 进度按**已写字节累计**（含续传起点），total 来自 `X-Stream-Total`。

        :param endpoint: API 路径
        :param data: 请求参数字典
        :param save_path: 本地保存的绝对路径（含文件名），目录会自动创建
        :param progress_callback: 进度回调 fn(已写入明文字节含续传起点, 远端总字节或 0)
        :param cancelled_flag: 取消判断回调，返回 True 时中止
        :param range_start: 起始偏移（字节，0 = 从头）；调用方通常传 part 现有大小
        :param expected_meta: 上次下载留下的 meta（用于比对 `X-File-Version`）
        :param meta_path: meta 文件路径（缺省 `<save_path>.jflove-part.json`）
        :param discard_on_cancel: 用户取消时是否删除 part（默认 True）
        :returns: 下载完成后的文件总字节数（含续传已写部分）
        :raises ApiError: 请求失败
        :raises ValueError: 用户取消，或加密流被破坏（认证失败/截断/格式非法）
        """
        if not save_path:
            raise ValueError("save_path 不能为空")
        url = self._base_url() + endpoint
        part_path, default_meta_path = part_paths(save_path)
        meta_file = meta_path or default_meta_path
        os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)

        offset = max(0, int(range_start or 0))
        if offset > 0 and not os.path.exists(part_path):
            offset = 0
        meta: dict = dict(expected_meta or {})
        # 只允许一次「续传失效 → 从头下载」回退，避免服务端持续返回异常时死循环
        restart_left = 1

        while True:
            def _send() -> requests.Response:
                payload_data = dict(data or {})
                payload_data["range_start"] = int(offset)
                payload = self._build_payload(payload_data)
                return requests.get(
                    url,
                    json=payload,
                    headers=self._auth_headers(),
                    stream=True,
                    timeout=120,
                )

            resp = self._send_with_auto_resync(_send)
            try:
                if resp.status_code == 416 and offset > 0 and restart_left > 0:
                    # 偏移越界（服务端文件变短 / part 比远端还大）：回退从头下载
                    logger.warning("下载续传偏移越界（416），删除 part 从头下载")
                    restart_left -= 1
                    clear_part_files(part_path, meta_file)
                    offset = 0
                    meta = {}
                    continue
                if resp.status_code >= 400:
                    self._handle_stream_error(resp)

                server_offset = _header_int(resp, "X-Stream-Offset", 0)
                remote_total = _header_int(resp, "X-Stream-Total", -1)
                remote_version = str(resp.headers.get("X-File-Version") or "")

                append = offset > 0
                reason = ""
                if append:
                    try:
                        disk_size = os.path.getsize(part_path)
                    except OSError:
                        disk_size = 0
                    old_version = str(meta.get("version") or "")
                    if disk_size != offset:
                        reason = "本地 part 大小与续传偏移不一致"
                    elif server_offset != offset:
                        reason = "服务端未按请求偏移返回（旧服务端或文件已变化）"
                    elif not old_version:
                        reason = "缺少上次下载的远端版本标识，无法校验续传"
                    elif not remote_version:
                        reason = "服务端未返回远端版本标识，无法校验续传"
                    elif old_version != remote_version:
                        reason = "远端文件版本已变化"
                if reason:
                    logger.warning("下载续传不可用（%s），删除 part 从头下载", reason)
                    clear_part_files(part_path, meta_file)
                    offset = 0
                    meta = {}
                    if restart_left > 0:
                        restart_left -= 1
                        continue
                    append = False

                mode = "ab" if append else "wb"
                _save_part_meta(meta_file, remote_total, remote_version, offset)
                last_flush = offset
                downloaded = 0
                try:
                    with open(part_path, mode) as f:
                        raw = resp.raw
                        while True:
                            if cancelled_flag and cancelled_flag():
                                raise DownloadCancelled(_DOWNLOAD_CANCEL_MESSAGE)
                            plaintext = parse_stream_frame(
                                raw, session_manager.session_key
                            )
                            if plaintext is None:
                                break  # 流正常结束
                            f.write(plaintext)
                            downloaded += len(plaintext)
                            current = offset + downloaded
                            if current - last_flush >= _PART_META_FLUSH_BYTES:
                                _save_part_meta(
                                    meta_file, remote_total, remote_version, current,
                                )
                                last_flush = current
                            if progress_callback:
                                progress_callback(
                                    current,
                                    remote_total if remote_total >= 0 else 0,
                                )
                    current = offset + downloaded
                    if remote_total >= 0 and current != remote_total:
                        raise ApiError(
                            502,
                            f"下载数据不完整（{current}/{remote_total} 字节）",
                        )
                    os.replace(part_path, save_path)
                    # 成功后清掉 meta（保留 part 已 rename 成目标文件）
                    clear_part_files("", meta_file)
                    return current
                except DownloadCancelled:
                    if discard_on_cancel:
                        clear_part_files(part_path, meta_file)
                    else:
                        _save_part_meta(
                            meta_file, remote_total, remote_version,
                            _safe_size(part_path, offset),
                        )
                    raise
                except Exception:
                    # 失败（网络中断 / 认证失败 / 取消之外的异常）：保留 part 供续传
                    _save_part_meta(
                        meta_file, remote_total, remote_version,
                        _safe_size(part_path, offset),
                    )
                    raise
            finally:
                resp.close()


# 全局单例
http_client = HttpClient()


#: 可重试的 HTTP 状态码（服务端暂时性故障）
_TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def is_transient_error(exc: BaseException) -> bool:
    """
    判断异常是否是「重试可能成功」的瞬态错误。

    网络安全风险提示：这里只做**判定**，重试策略由调用方决定
    （分片上传可安全重试；`complete` 只能等结果 + 核验，不能盲目重发）。

    :param exc: 捕获到的异常
    :returns: True 表示值得重试
    """
    if isinstance(exc, (requests.exceptions.Timeout,
                        requests.exceptions.ConnectionError)):
        return True
    if isinstance(exc, ApiError):
        return exc.status_code in _TRANSIENT_STATUS
    return False
