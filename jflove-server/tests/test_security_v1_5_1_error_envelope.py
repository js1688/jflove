"""
v1.5.1 Phase 6 补漏：**422 错误响应加密**（`AGENTS.md §9.1` 第 3 条 / testing SKILL 安全硬约束第 4 类）

**缺口是怎么发现的**（判据以仓库实况为准，不采信任何文档表述）：

    git grep -n "422" -- jflove-server/tests     → 0 命中

现行安全用例把错误响应加密覆盖到了 **404 / 405 / 400**（`test_security_e2e.py:147-168`）
与 **401 / 403 / 409**（`test_security_v1_1_3.py`、`test_stream_v1_1_0.py`），
**唯独没有 422（`RequestValidationError`）**。

而 `src/main.py:150-177` 明确注册了 422 的加密处理器，且 `main.py:155-158` 的注释写明理由
（"结构化错误数组含字段名，企业 MITM 可由此推断接口结构"）。
⇒ 这是一条**有实现、没有可执行判据**的安全约束（`LESSONS.md` L12：约束没绑定检查等于没写）。

本文件**只补这一条判据**，不改动任何既有用例的断言（testing SKILL 纪律 6：
失败原因是实现有 bug 就不改测试；本文件不为了通过而调低期望值）。

触发路径（真实可达，非构造）：`DELETE /api/v1/users/{user_id}` 的路径参数标注为 `int`
（`src/controllers/user_controller.py:102`），传入非数字即由 FastAPI 抛 422。
"""

from __future__ import annotations

import base64
import json

import pytest

from tests.conftest import (
    decrypt_response,
    encrypted_request,
)


def _assert_encrypted_envelope(body: dict | bytes, msg: str = "") -> None:
    """断言响应体是 {nonce, ciphertext} 加密信封（与既有用例同口径）"""
    if isinstance(body, (bytes, bytearray)):
        body = json.loads(body)
    assert isinstance(body, dict), f"{msg}：响应不是 dict"
    assert "nonce" in body, f"{msg}：响应缺少 nonce 字段"
    assert "ciphertext" in body, f"{msg}：响应缺少 ciphertext 字段"
    base64.b64decode(body["nonce"])
    base64.b64decode(body["ciphertext"])


def test_路径参数类型错误_422是加密信封(client, env):
    """
    类 4：路径参数校验失败（422）的响应必须是加密信封，**不能**是明文
    `{"detail": [{"loc": ["path", "user_id"], ...}]}`（后者会把接口字段结构暴露给 MITM）。
    """
    resp = encrypted_request(
        client, env["admin"], "DELETE", "/api/v1/users/not-a-number"
    )
    assert resp.status_code == 422, f"期望 422，实际 {resp.status_code}：{resp.text}"
    _assert_encrypted_envelope(resp.json(), "422 路径参数校验失败响应")


def test_路径参数类型错误_422解密后含校验明细(client, env):
    """
    类 4（续）：422 不仅能解密，解密后还应带 `detail` 与结构化 `errors`
    （`main.py:173` 的约定）—— 证明加密通道没有把错误信息吞掉。
    """
    resp = encrypted_request(
        client, env["admin"], "DELETE", "/api/v1/users/not-a-number"
    )
    assert resp.status_code == 422, resp.text
    data = decrypt_response(env["admin"], resp)
    assert isinstance(data, dict), f"解密结果应为 dict，实际 {type(data)!r}"
    assert data.get("detail") == "请求参数校验失败", f"detail 不符：{data.get('detail')!r}"
    errors = data.get("errors")
    assert isinstance(errors, list) and errors, f"errors 应为非空数组，实际 {errors!r}"
    # loc 里必须能看到出错的路径参数名（这是"明细确实带出来了"的判据）
    joined = json.dumps(errors, ensure_ascii=False)
    assert "user_id" in joined, f"errors 未包含出错字段名：{joined}"


def test_路径参数类型错误_422响应原始字节无明文校验结构(client, env):
    """
    类 4（续）：响应**原始字节**里不得出现 `"loc"` / `user_id` 这类明文结构关键词
    （对抗性检查：只断言"能解密"不够，必须排除"加密了但明文也带了一份"）。
    """
    resp = encrypted_request(
        client, env["admin"], "DELETE", "/api/v1/users/not-a-number"
    )
    assert resp.status_code == 422, resp.text
    raw = resp.content.decode("utf-8", errors="replace")
    for token in ('"loc"', '"user_id"', "RequestValidationError", "pydantic"):
        assert token not in raw, f"422 响应原始字节泄露明文结构关键词：{token}"


def test_非admin会话的422同样加密(client, env):
    """
    类 4（续）：422 处理器只依赖 `X-Session-ID`，与调用者角色无关 ——
    普通用户的畸形请求同样不能被退回明文（否则低权限账号反而是绕过加密的入口）。
    """
    resp = encrypted_request(
        client, env["alice"], "GET", "/api/v1/permissions/users/abc/disks"
    )
    assert resp.status_code == 422, f"期望 422，实际 {resp.status_code}：{resp.text}"
    _assert_encrypted_envelope(resp.json(), "普通用户 422 响应")


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/permissions/users/abc/disks"),
        ("POST", "/api/v1/permissions/users/abc/disks/1"),
        ("DELETE", "/api/v1/permissions/users/abc/disks/1"),
        ("PUT", "/api/v1/users/abc/password"),
        ("PUT", "/api/v1/users/abc/enabled"),
    ],
)
def test_所有带整型路径参数的路由_422均加密(client, env, method, path):
    """
    类 4（续）：把**每一条**带整型路径参数的路由都过一遍，避免只测一条留下同类空洞
    （`AGENTS.md §9.7` 自查项"路径上是否有可被枚举的 ID"）。

    注：方法必须与路由声明一致 —— 方法不匹配时 starlette 先返回 405（路径匹配成功、
    方法不允许），**不会**走到路径参数校验，因此这里逐条按各路由自己的方法构造。
    """
    resp = encrypted_request(client, env["admin"], method, path, {"enabled": True})
    assert resp.status_code == 422, (
        f"{method} {path} 期望 422，实际 {resp.status_code}：{resp.text}"
    )
    _assert_encrypted_envelope(resp.json(), f"{method} {path} 422 响应")
