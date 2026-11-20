"""
远程连接凭据清空语义（v1.10.13）

用户反馈：编辑连接时把错误口令填进了密码栏，却无法清空 —— 因为「留空 = 不修改」。
本文件钉住服务端的显式清空协议：

1. `clear_password` 真的把 password 列写成空串；
2. `clear_private_key` 把私钥与口令一起清空（不留孤儿口令）；
3. `clear_passphrase` 只清口令、保留私钥；
4. 只提交非空 passphrase（不换私钥）也能单独更新口令；
5. 不传清空标志时，空串仍然是「不修改」（旧语义不回归）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import Session, decrypt_response, encrypted_request

_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----"


def _post(client: TestClient, session: Session, path: str, body: dict | None = None):
    """发加密 POST 请求"""
    return encrypted_request(client, session, "POST", path, body)


def _data(session: Session, resp) -> dict:
    """解密响应并断言 2xx"""
    assert resp.status_code == 200, resp.text
    return decrypt_response(session, resp)


def _create(client: TestClient, session: Session) -> int:
    """建一条三凭据齐全的连接"""
    return _data(
        session,
        _post(
            client,
            session,
            "/api/v1/ssh/connections/create",
            {
                "name": "clear-creds",
                "host": "10.0.0.8",
                "username": "tester",
                "auth_type": "password_publickey",
                "password": "login-pw",
                "private_key": _KEY,
                "passphrase": "key-pass",
            },
        ),
    )["id"]


def _detail(client: TestClient, session: Session, conn_id: int) -> dict:
    """读全量详情"""
    return _data(
        session, _post(client, session, "/api/v1/ssh/connections/get", {"id": conn_id})
    )["connection"]


def _update(client: TestClient, session: Session, conn_id: int, body: dict) -> None:
    """更新连接（自动补 id）"""
    payload = {"id": conn_id, **body}
    _data(session, _post(client, session, "/api/v1/ssh/connections/update", payload))


def test_clear_password_writes_empty(client: TestClient, env: dict) -> None:
    """clear_password=true：数据库里 password 必须是空串"""
    alice = env["alice"]
    conn_id = _create(client, alice)
    assert _detail(client, alice, conn_id)["password"] == "login-pw"

    _update(client, alice, conn_id, {"clear_password": True, "password": ""})
    conn = _detail(client, alice, conn_id)
    assert conn["password"] == ""
    # 其它凭据不受影响
    assert conn["private_key"] == _KEY
    assert conn["passphrase"] == "key-pass"


def test_clear_private_key_also_clears_passphrase(client: TestClient, env: dict) -> None:
    """clear_private_key=true：私钥与口令一起清空"""
    alice = env["alice"]
    conn_id = _create(client, alice)
    _update(client, alice, conn_id, {"clear_private_key": True})

    conn = _detail(client, alice, conn_id)
    assert conn["private_key"] == ""
    assert conn["passphrase"] == ""
    assert conn["password"] == "login-pw"


def test_clear_passphrase_keeps_private_key(client: TestClient, env: dict) -> None:
    """clear_passphrase=true：只清口令，私钥保留"""
    alice = env["alice"]
    conn_id = _create(client, alice)
    _update(client, alice, conn_id, {"clear_passphrase": True})

    conn = _detail(client, alice, conn_id)
    assert conn["passphrase"] == ""
    assert conn["private_key"] == _KEY


def test_update_passphrase_only(client: TestClient, env: dict) -> None:
    """不换私钥、只提交新口令：口令可单独修正（旧实现会忽略）"""
    alice = env["alice"]
    conn_id = _create(client, alice)
    _update(client, alice, conn_id, {"passphrase": "new-key-pass"})

    conn = _detail(client, alice, conn_id)
    assert conn["passphrase"] == "new-key-pass"
    assert conn["private_key"] == _KEY


def test_empty_without_clear_flag_still_means_unchanged(client: TestClient, env: dict) -> None:
    """旧语义不回归：不传 clear_* 时，空凭据仍表示「不修改」"""
    alice = env["alice"]
    conn_id = _create(client, alice)
    _update(client, alice, conn_id, {"password": "", "private_key": "", "passphrase": ""})

    conn = _detail(client, alice, conn_id)
    assert conn["password"] == "login-pw"
    assert conn["private_key"] == _KEY
    assert conn["passphrase"] == "key-pass"


# ── has_passphrase（v1.10.16 补）────────────────────────


def _summary(client: TestClient, session: Session, conn_id: int) -> dict:
    """从列表接口取某条连接的摘要（列表不含凭据原文）"""
    rows = _data(session, _post(client, session, "/api/v1/ssh/connections/list", {}))[
        "connections"
    ]
    return next(row for row in rows if row["id"] == conn_id)


def test_has_passphrase_reflects_real_column(client: TestClient, env: dict) -> None:
    """
    `has_passphrase` 必须反映 `passphrase` 列真实内容。

    以前前端拿 `has_private_key` 推断「口令已设置」，于是只要存过私钥，
    表单里就永远显示「已设置，留空不修改」—— 用户以为库里有个自己没设过的口令。
    """
    alice = env["alice"]
    conn_id = _create(client, alice)

    # 三凭据齐全：口令确实存在
    assert _summary(client, alice, conn_id)["has_passphrase"] is True

    # 清掉口令、私钥仍在：has_private_key 为真，但 has_passphrase 必须为假
    _update(client, alice, conn_id, {"clear_passphrase": True})
    row = _summary(client, alice, conn_id)
    assert row["has_private_key"] is True
    assert row["has_passphrase"] is False

    # 清掉私钥（连带清口令）：两者都为假
    _update(client, alice, conn_id, {"clear_private_key": True})
    row = _summary(client, alice, conn_id)
    assert row["has_private_key"] is False
    assert row["has_passphrase"] is False
    #: 凭据原文绝不出现在列表摘要里（安全宪法 §9.4）
    assert "passphrase" not in row
