"""
v1.5.1 回归：加密响应体的**信封判定**（缺陷 M-4）

## 缺陷背景

`_parse_response()` 原先的逻辑是"能解密就返回明文，否则把 `body` 当明文返回"。
于是当响应体**形状是加密信封但解密失败**（会话密钥不匹配 / 响应被篡改）时，
`{"nonce":…,"ciphertext":…}` 这个外壳会被当成**成功的明文**交给业务层：

    resp = http_client.get("/api/v1/files")     # 200 + 解不开的信封
    resp.get("files", [])                       # → []  ⇒ 界面显示"目录是空的"

不报错、不重试、不登出 —— 典型的**静默故障**（`LESSONS.md` L34：
"接口 200 但没效果"永远先怀疑契约，而不是前端状态）。

## 修法与判据

- `_is_envelope(body)`：**只判形状**（dict 且同时含 `nonce` 与 `ciphertext`）；
- `_parse_response()`：不是信封 → 明文直返（明文白名单照旧）；
  是信封且解开了 → 返回解密结果；是信封但**解不开** → `ApiError(502, "响应解密失败")`，
  **绝不**回退成明文外壳；
- `_extract_error_detail()`：是信封但解不开 → 明确文案，不静默退化成 `resp.text`。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.utils.http_client import ApiError, HttpClient, _is_envelope

#: 一个"形状合法但密钥不对"的信封（base64 内容是垃圾，解密必然失败）
_BAD_ENVELOPE = {"nonce": "AAAAAAAAAAAAAAAA", "ciphertext": "AAAAAAAAAAAAAAAAAAAAAA=="}


def _resp(status: int, body) -> MagicMock:
    """构造 mock Response：status_code + json() 返回给定 body + text 为 JSON 字符串"""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    resp.text = json.dumps(body, ensure_ascii=False)
    return resp


class TestIsEnvelope:
    """`_is_envelope()` 只判形状"""

    def test_完整信封被判为信封(self) -> None:
        assert _is_envelope({"nonce": "n", "ciphertext": "c"}) is True

    def test_只有nonce不算信封(self) -> None:
        """半截信封（只有 nonce）按明文处理"""
        assert _is_envelope({"nonce": "n"}) is False

    def test_只有ciphertext不算信封(self) -> None:
        assert _is_envelope({"ciphertext": "c"}) is False

    def test_明文业务响应不算信封(self) -> None:
        assert _is_envelope({"files": [], "total": 0}) is False

    def test_非dict不算信封(self) -> None:
        assert _is_envelope([1, 2, 3]) is False
        assert _is_envelope("plain text") is False
        assert _is_envelope(None) is False


class TestParseResponseEnvelope:
    """`_parse_response()` 对信封/明文/半截信封的处置"""

    def setup_method(self) -> None:
        self.client = HttpClient()

    def test_200加解不开的信封_必须抛502(self) -> None:
        """
        M-4 的核心用例：200 + 形状是信封但解密失败 → 必须抛 `ApiError(502)`。

        绝不能返回 `{"nonce":…,"ciphertext":…}` 外壳（那会让上层拿到空列表）。
        """
        resp = _resp(200, dict(_BAD_ENVELOPE))
        with pytest.raises(ApiError) as exc_info:
            self.client._parse_response(resp)
        assert exc_info.value.status_code == 502, (
            f"期望 502，实际 {exc_info.value.status_code}：{exc_info.value.detail}"
        )
        assert "解密失败" in exc_info.value.detail

    def test_200加明文body_直返成功(self) -> None:
        """明文白名单接口（admin-exists / health）照旧直返"""
        payload = {"exists": True, "message": "ok"}
        assert self.client._parse_response(_resp(200, payload)) == payload

    def test_200加半截信封_按明文处理(self) -> None:
        """只有 nonce 的"半截信封"不是信封形状 → 按明文直返"""
        payload = {"nonce": "only-nonce", "data": 1}
        assert self.client._parse_response(_resp(200, payload)) == payload

    def test_200加可解开的信封_返回解密结果(self) -> None:
        """正常路径：真信封能解开 → 返回解密后的 dict"""
        decrypted = {"files": [{"name": "a.txt"}], "total": 1}
        with patch.object(self.client, "_decrypt_envelope_or_none", return_value=decrypted):
            assert self.client._parse_response(_resp(200, dict(_BAD_ENVELOPE))) == decrypted

    def test_200加非dict明文_直返(self) -> None:
        """非 dict 的明文响应也直返（不误判为信封）"""
        assert self.client._parse_response(_resp(200, [1, 2])) == [1, 2]


class TestExtractErrorDetail:
    """`_extract_error_detail()` 对信封但解不开的情况给明确文案"""

    def setup_method(self) -> None:
        self.client = HttpClient()

    def test_错误响应是解不开的信封_给明确文案(self) -> None:
        """不得把 `{"nonce":…,"ciphertext":…}` 外壳文本当成给用户看的 detail"""
        detail = self.client._extract_error_detail(_resp(401, dict(_BAD_ENVELOPE)))
        assert "解密失败" in detail, f"期望明确文案，实际 {detail!r}"
        assert "nonce" not in detail and "ciphertext" not in detail

    def test_错误响应是明文dict_取detail(self) -> None:
        detail = self.client._extract_error_detail(_resp(403, {"detail": "权限不足"}))
        assert detail == "权限不足"

    def test_错误响应不是JSON_退回text(self) -> None:
        resp = MagicMock()
        resp.status_code = 500
        resp.json.side_effect = ValueError("not json")
        resp.text = "Internal Server Error"
        assert self.client._extract_error_detail(resp) == "Internal Server Error"

    def test_错误响应是可解开信封_取内部detail(self) -> None:
        with patch.object(
            self.client, "_decrypt_envelope_or_none", return_value={"detail": "令牌无效"}
        ):
            assert self.client._extract_error_detail(
                _resp(401, dict(_BAD_ENVELOPE))
            ) == "令牌无效"


class TestPlainPathEnvelopeConsistency:
    """
    R5-T4：`get_plain()` / `post_plain()` 的第四处口径也必须统一。

    这两个方法只服务**明文白名单**接口（当前不可触发信封分支），但口径必须与
    `_extract_error_detail()` 一致 —— 否则万一日后误用到加密接口上，
    就会把 `{"nonce":…,"ciphertext":…}` 外壳当 detail 抛给用户（与 M-4 同一类缺陷）。
    """

    def setup_method(self) -> None:
        self.client = HttpClient()

    def test_get_plain_错误响应是解不开的信封_文案不含密文字段(self) -> None:
        fake = _resp(401, dict(_BAD_ENVELOPE))
        with patch("src.utils.http_client.requests.get", return_value=fake):
            with pytest.raises(ApiError) as exc_info:
                self.client.get_plain("/api/v1/auth/admin-exists")
        detail = exc_info.value.detail
        assert "解密失败" in detail, f"期望明确文案，实际 {detail!r}"
        assert "nonce" not in detail, f"文案泄漏了信封字段：{detail!r}"
        assert "ciphertext" not in detail, f"文案泄漏了信封字段：{detail!r}"

    def test_post_plain_错误响应是解不开的信封_文案不含密文字段(self) -> None:
        fake = _resp(500, dict(_BAD_ENVELOPE))
        with patch("src.utils.http_client.requests.post", return_value=fake):
            with pytest.raises(ApiError) as exc_info:
                self.client.post_plain("/api/v1/auth/key-exchange", {})
        detail = exc_info.value.detail
        assert "解密失败" in detail, f"期望明确文案，实际 {detail!r}"
        assert "nonce" not in detail, f"文案泄漏了信封字段：{detail!r}"
        assert "ciphertext" not in detail, f"文案泄漏了信封字段：{detail!r}"

    def test_get_plain_明文错误响应_正常取detail(self) -> None:
        """回归保护：改成统一口径后，明文接口的正常错误文案不能被破坏"""
        fake = _resp(403, {"detail": "需要管理员权限"})
        with patch("src.utils.http_client.requests.get", return_value=fake):
            with pytest.raises(ApiError) as exc_info:
                self.client.get_plain("/api/v1/auth/admin-exists")
        assert exc_info.value.detail == "需要管理员权限"

    def test_post_plain_明文成功响应_直返(self) -> None:
        """回归保护：明文成功路径不受影响"""
        payload = {"session_id": "abc", "server_public_key": "xyz"}
        with patch("src.utils.http_client.requests.post", return_value=_resp(200, payload)):
            assert self.client.post_plain("/api/v1/auth/key-exchange", {}) == payload

    def test_get_plain_错误响应不是JSON_退回text(self) -> None:
        """非 JSON 错误响应仍退回 `resp.text`（口径 3）"""
        fake = MagicMock()
        fake.status_code = 502
        fake.json.side_effect = ValueError("not json")
        fake.text = "Bad Gateway"
        with patch("src.utils.http_client.requests.get", return_value=fake):
            with pytest.raises(ApiError) as exc_info:
                self.client.get_plain("/api/v1/auth/admin-exists")
        assert exc_info.value.detail == "Bad Gateway"


class TestStreamErrorEnvelope:
    """`_handle_stream_error()` 同步对齐：解不开的信封给明确文案"""

    def setup_method(self) -> None:
        self.client = HttpClient()

    def test_流错误是解不开的信封_给明确文案(self) -> None:
        with pytest.raises(ApiError) as exc_info:
            self.client._handle_stream_error(_resp(500, dict(_BAD_ENVELOPE)))
        assert "解密失败" in exc_info.value.detail
        assert "nonce" not in exc_info.value.detail

    def test_流错误是明文dict_取detail(self) -> None:
        with pytest.raises(ApiError) as exc_info:
            self.client._handle_stream_error(_resp(404, {"detail": "文件不存在"}))
        assert exc_info.value.detail == "文件不存在"


class TestGetPathIntegration:
    """端到端（mock requests）：`get()` 遇到解不开的信封必须报错，而不是静默返回空目录"""

    def setup_method(self) -> None:
        self.client = HttpClient()
        # `get()` 会先加密请求体（需要 32B 会话密钥）；这里只关心响应解析路径，
        # 故直接注入一个临时密钥（测试结束由 monkeypatch 自动还原）。
        self._patcher = patch(
            "src.utils.http_client.session_manager.session_key", b"\x01" * 32
        )
        self._patcher.start()

    def teardown_method(self) -> None:
        self._patcher.stop()

    def test_get_遇到解不开的信封_抛502而不是返回空列表(self) -> None:
        """
        复现原始故障场景：调用方 `resp.get("files", [])` 拿到 `[]`（不报错）。
        修复后必须在 `http_client` 层就抛 `ApiError(502)`。
        """
        fake = _resp(200, dict(_BAD_ENVELOPE))
        with patch("src.utils.http_client.requests.get", return_value=fake):
            with pytest.raises(ApiError) as exc_info:
                self.client.get("/api/v1/files", {})
        assert exc_info.value.status_code == 502

    def test_get_遇到明文响应_直返(self) -> None:
        payload = {"files": [{"name": "a.txt"}], "total": 1}
        with patch("src.utils.http_client.requests.get", return_value=_resp(200, payload)):
            assert self.client.get("/api/v1/files", {}) == payload

    def test_get_遇到解不开的信封_不会静默返回空列表(self) -> None:
        """
        反面加固：即使调用方沿用"拿不到就空列表"的旧写法，也必须**先抛异常**，
        绝不能安静地拿到 `[]`（那正是用户看到的"目录是空的"）。
        """
        fake = _resp(200, dict(_BAD_ENVELOPE))
        got_empty_files = False
        with patch("src.utils.http_client.requests.get", return_value=fake):
            try:
                body = self.client.get("/api/v1/files", {})
                got_empty_files = body.get("files", []) == []
            except ApiError:
                got_empty_files = False
        assert got_empty_files is False, "解不开的信封被静默降级成了空列表（M-4 复现）"
