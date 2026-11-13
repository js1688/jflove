"""
远程 SSH/SFTP 连接配置接口单测（v1.10.0）

重点钉住四类约束（`文档记录/设计文档/v1.10.0.md` §2 / §5）：

1. **按账号隔离**：bob 拿着别人的连接 ID 读取，必须 404（按不存在处理）。
2. **凭据不回列表**：`connections/list` 只有 `has_*`，没有 `password` / `private_key`。
3. **跳板环**：A→B→A 保存必须被拒绝。
4. **删除保护**：被引用为跳板的连接不能删。

另含启动命令单行校验、分组删除后连接落回未分组、复制、touch 的回归。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import Session, decrypt_response, encrypted_request


def _post(client: TestClient, session: Session, path: str, body: dict | None = None):
    """发一个加密 POST 请求"""
    return encrypted_request(client, session, "POST", path, body)


def _data(session: Session, resp) -> dict:
    """解密响应并断言 2xx（失败时打印原文）"""
    assert resp.status_code == 200, resp.text
    return decrypt_response(session, resp)


def _create(client: TestClient, session: Session, **overrides) -> int:
    """建一条最小可用的连接，返回 ID"""
    body = {
        "name": "web-01",
        "host": "192.168.1.21",
        "port": 22,
        "username": "tanjun",
        "auth_type": "password",
        "password": "p@ssw0rd",
    }
    body.update(overrides)
    return _data(session, _post(client, session, "/api/v1/ssh/connections/create", body))["id"]


def test_create_and_list_hides_secret(client: TestClient, env: dict) -> None:
    """创建后列表可见，但列表里没有凭据原文"""
    alice = env["alice"]
    conn_id = _create(client, alice, name="hides-secret")

    data = _data(alice, _post(client, alice, "/api/v1/ssh/connections/list", {}))
    item = next(c for c in data["connections"] if c["id"] == conn_id)
    assert item["has_password"] is True
    assert "password" not in item
    assert "private_key" not in item
    assert item["name"] == "hides-secret"


def test_get_returns_secret_and_fingerprint(client: TestClient, env: dict) -> None:
    """详情接口返回凭据原文，并派生私钥指纹"""
    alice = env["alice"]
    conn_id = _create(
        client,
        alice,
        name="get-secret",
        auth_type="publickey",
        password="",
        private_key="-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----",
        passphrase="pp",
    )
    conn = _data(
        alice, _post(client, alice, "/api/v1/ssh/connections/get", {"id": conn_id})
    )["connection"]
    assert conn["private_key"].startswith("-----BEGIN")
    assert conn["passphrase"] == "pp"
    assert conn["private_key_fingerprint"].startswith("SHA256:")


def test_other_user_cannot_read(client: TestClient, env: dict) -> None:
    """bob 拿 alice 的连接 ID 读取 → 404（按不存在处理，不泄露存在性）"""
    alice, bob = env["alice"], env["bob"]
    conn_id = _create(client, alice, name="private-conn")

    resp = _post(client, bob, "/api/v1/ssh/connections/get", {"id": conn_id})
    assert resp.status_code == 404, resp.text

    resp = _post(client, bob, "/api/v1/ssh/connections/delete", {"id": conn_id})
    assert resp.status_code == 404, resp.text


def test_update_keeps_password_when_empty(client: TestClient, env: dict) -> None:
    """更新时凭据留空 = 不修改（避免编辑表单把密码抹掉）"""
    alice = env["alice"]
    conn_id = _create(client, alice, name="keep-pass")

    _data(
        alice,
        _post(
            client,
            alice,
            "/api/v1/ssh/connections/update",
            {"id": conn_id, "name": "keep-pass-2", "host": "10.0.0.9", "password": ""},
        ),
    )
    conn = _data(
        alice, _post(client, alice, "/api/v1/ssh/connections/get", {"id": conn_id})
    )["connection"]
    assert conn["name"] == "keep-pass-2"
    assert conn["host"] == "10.0.0.9"
    assert conn["password"] == "p@ssw0rd"


def test_init_command_must_be_single_line(client: TestClient, env: dict) -> None:
    """启动命令含换行 → 400；单行 → 通过"""
    alice = env["alice"]
    resp = _post(
        client,
        alice,
        "/api/v1/ssh/connections/create",
        {
            "name": "bad-init",
            "host": "h",
            "auth_type": "password",
            "password": "x",
            "init_command": "cd /a\nrm -rf /",
        },
    )
    assert resp.status_code == 400, resp.text

    conn_id = _create(client, alice, name="good-init", init_command="cd /opt/app && ls")
    conn = _data(
        alice, _post(client, alice, "/api/v1/ssh/connections/get", {"id": conn_id})
    )["connection"]
    assert conn["init_command"] == "cd /opt/app && ls"


def test_jump_cycle_rejected(client: TestClient, env: dict) -> None:
    """A→B→A 的跳板链必须在保存时被拒绝"""
    alice = env["alice"]
    a_id = _create(client, alice, name="cycle-a")
    b_id = _create(client, alice, name="cycle-b", jump_connection_id=a_id)

    resp = _post(
        client,
        alice,
        "/api/v1/ssh/connections/update",
        {"id": a_id, "name": "cycle-a", "host": "192.168.1.21", "jump_connection_id": b_id},
    )
    assert resp.status_code == 400, resp.text
    assert "循环" in decrypt_response(alice, resp)["detail"]


def test_delete_referenced_connection_blocked(client: TestClient, env: dict) -> None:
    """被当作跳板的连接不能直接删；解除引用后可删"""
    alice = env["alice"]
    a_id = _create(client, alice, name="jump-a")
    b_id = _create(client, alice, name="jump-b", jump_connection_id=a_id)

    resp = _post(client, alice, "/api/v1/ssh/connections/delete", {"id": a_id})
    assert resp.status_code == 409, resp.text

    _data(alice, _post(client, alice, "/api/v1/ssh/connections/delete", {"id": b_id}))
    _data(alice, _post(client, alice, "/api/v1/ssh/connections/delete", {"id": a_id}))


def test_group_crud_and_reassign(client: TestClient, env: dict) -> None:
    """分组：重名 409；删除分组后连接落回未分组（连接不删）"""
    alice = env["alice"]
    gid = _data(
        alice, _post(client, alice, "/api/v1/ssh/groups/create", {"name": "生产环境"})
    )["id"]

    resp = _post(client, alice, "/api/v1/ssh/groups/create", {"name": "生产环境"})
    assert resp.status_code == 409, resp.text

    conn_id = _create(client, alice, name="grouped", ssh_group_id=gid)
    groups = _data(alice, _post(client, alice, "/api/v1/ssh/groups/list", {}))["groups"]
    target = next(g for g in groups if g["id"] == gid)
    assert target["connection_count"] == 1

    _data(alice, _post(client, alice, "/api/v1/ssh/groups/delete", {"id": gid}))
    conn = _data(
        alice, _post(client, alice, "/api/v1/ssh/connections/get", {"id": conn_id})
    )["connection"]
    assert conn["ssh_group_id"] == 0


def test_duplicate_connection(client: TestClient, env: dict) -> None:
    """复制连接：新 ID、名称追加「副本」"""
    alice = env["alice"]
    src = _create(client, alice, name="copy-me")
    new_id = _data(
        alice, _post(client, alice, "/api/v1/ssh/connections/duplicate", {"id": src})
    )["id"]
    assert new_id != src
    conn = _data(
        alice, _post(client, alice, "/api/v1/ssh/connections/get", {"id": new_id})
    )["connection"]
    assert "副本" in conn["name"]


def test_touch_sets_last_connected(client: TestClient, env: dict) -> None:
    """连接成功后回写最近连接时间"""
    alice = env["alice"]
    conn_id = _create(client, alice, name="touch-me")
    _data(alice, _post(client, alice, "/api/v1/ssh/connections/touch", {"id": conn_id}))
    conn = _data(
        alice, _post(client, alice, "/api/v1/ssh/connections/get", {"id": conn_id})
    )["connection"]
    assert conn["last_connected_at"]


def test_port_and_auth_validation(client: TestClient, env: dict) -> None:
    """端口越界、认证方式非法 → 400"""
    alice = env["alice"]
    for body in (
        {"name": "p", "host": "h", "port": 70000, "auth_type": "password", "password": "x"},
        {"name": "p", "host": "h", "port": 22, "auth_type": "magic", "password": "x"},
    ):
        resp = _post(client, alice, "/api/v1/ssh/connections/create", body)
        assert resp.status_code == 400, resp.text


def test_jump_depth_limit(client: TestClient, env: dict) -> None:
    """跳板链超过 16 级必须被拒绝（防病态配置拖死客户端）"""
    alice = env["alice"]
    prev = 0
    for index in range(16):
        prev = _create(
            client,
            alice,
            name=f"depth-{index}",
            jump_connection_id=prev,
        )
    resp = _post(
        client,
        alice,
        "/api/v1/ssh/connections/create",
        {
            "name": "depth-too-deep",
            "host": "192.168.1.21",
            "auth_type": "password",
            "password": "x",
            "jump_connection_id": prev,
        },
    )
    assert resp.status_code == 400, resp.text
    assert "16" in decrypt_response(alice, resp)["detail"]
