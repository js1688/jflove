"""
pytest 共享 fixture。

关键职责：
  1. 把 DB_PATH 和 UPLOAD_TEMP_DIR 重定向到临时目录，避免污染开发库
  2. 提供 admin / alice / bob 三个已登录会话给所有测试用例
  3. 提供「执行加密请求 → 解密响应」的辅助函数
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 把 jflove-server 根目录加入 sys.path（pytest 默认从 tests/ 跑时不自动加）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 提前重定向 DB_PATH / UPLOAD_TEMP_DIR ────────────────
# 必须在任何 src.models / src.services 模块被 import 之前完成，
# 因为它们 from-import 这两个常量后会绑定到模块命名空间副本。
import src.config.settings as _settings  # noqa: E402

_TEST_TMP = Path(tempfile.mkdtemp(prefix="jflove_test_"))
_settings.DB_PATH = str(_TEST_TMP / "test.db")
_settings.UPLOAD_TEMP_DIR = str(_TEST_TMP / "upload_tmp")
Path(_settings.UPLOAD_TEMP_DIR).mkdir(parents=True, exist_ok=True)

# 此后再 import 业务模块（它们都会拿到上面修改过的常量）
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.main import app  # noqa: E402
from src.utils.crypto import (  # noqa: E402
    encrypt as srv_encrypt,
    decrypt as srv_decrypt,
    generate_x25519_keypair,
    derive_session_key,
)


@dataclass
class Session:
    """单个客户端会话状态（密钥交换 + 登录后的所有上下文）"""
    session_id: str
    session_key: bytes
    username: str | None = None
    token: str | None = None
    role: str | None = None
    user_id: int | None = None


# ── 业务级辅助函数 ────────────────────────────

def do_key_exchange(client: TestClient) -> Session:
    """完成一次密钥交换，返回新会话"""
    priv, pub = generate_x25519_keypair()
    resp = client.post(
        "/api/v1/auth/key-exchange",
        json={"client_public_key": pub, "refresh": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    sk = derive_session_key(priv, body["server_public_key"])
    return Session(session_id=body["session_id"], session_key=sk)


def encrypted_request(
    client: TestClient,
    session: Session,
    method: str,
    path: str,
    body: dict | None = None,
    extra_headers: dict | None = None,
):
    """发起加密请求（自动注入 token + 加密 body）"""
    payload = dict(body or {})
    if session.token and "token" not in payload:
        payload["token"] = session.token
    envelope = srv_encrypt(
        session.session_key, json.dumps(payload, ensure_ascii=False).encode()
    )
    headers = {"X-Session-ID": session.session_id}
    if extra_headers:
        headers.update(extra_headers)
    return client.request(method, path, json=envelope, headers=headers)


def decrypt_response(session: Session, resp) -> dict | list:
    """对响应做"如果是加密信封就解密"处理"""
    try:
        body = resp.json()
    except Exception:
        return {}
    if isinstance(body, dict) and "nonce" in body and "ciphertext" in body:
        plaintext = srv_decrypt(
            session.session_key, body["nonce"], body["ciphertext"]
        )
        return json.loads(plaintext)
    return body


def login(client: TestClient, session: Session, username: str, password: str) -> None:
    """加密登录 + 把 token 写回 session"""
    resp = encrypted_request(
        client, session, "POST", "/api/v1/auth/login",
        {"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    data = decrypt_response(session, resp)
    session.token = data["token"]
    session.username = data.get("username", username)
    session.role = data.get("role")
    session.user_id = data.get("user_id")


# ── module 级 fixture ────────────────────────────

@pytest.fixture(scope="session")
def client() -> TestClient:
    """全局 FastAPI TestClient（用 with 块触发 lifespan，从而调用 init_db）"""
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def disk_root(tmp_path_factory) -> Path:
    """虚拟磁盘的真实物理路径（一个临时目录）"""
    return tmp_path_factory.mktemp("jflove_disk_")


@pytest.fixture(scope="session")
def env(client: TestClient, disk_root: Path) -> dict[str, Any]:
    """
    建立完整测试环境：admin / alice / bob 三个登录会话，一个虚拟磁盘，两个笔记子目录。

    返回字典字段：
      admin / alice / bob: Session 对象
      alice_id / bob_id: 普通用户 ID
      disk_id: 虚拟磁盘 ID
      disk_root: 物理目录 Path
    """
    # 1. 管理员密钥交换
    admin = do_key_exchange(client)

    # 2. 系统首次启动：初始化 admin
    resp = client.get("/api/v1/auth/admin-exists")
    if not resp.json().get("exists", False):
        resp = encrypted_request(
            client, admin, "POST", "/api/v1/auth/init-admin",
            {"username": "test_admin", "password": "Admin@TestPass1"},
        )
        assert resp.status_code == 200, resp.text

    # 3. admin 登录拿 token
    login(client, admin, "test_admin", "Admin@TestPass1")

    # 4. 创建虚拟磁盘
    resp = encrypted_request(
        client, admin, "POST", "/api/v1/virtual-disks",
        {"name": "test_vdisk", "real_path": str(disk_root)},
    )
    assert resp.status_code == 200, resp.text
    disk_id = decrypt_response(admin, resp)["id"]

    # 5. 创建普通用户 alice / bob
    resp = encrypted_request(
        client, admin, "POST", "/api/v1/users",
        {"username": "alice", "password": "Alice@TestPass1"},
    )
    assert resp.status_code == 200, resp.text
    alice_id = decrypt_response(admin, resp)["id"]

    resp = encrypted_request(
        client, admin, "POST", "/api/v1/users",
        {"username": "bob", "password": "Bob@TestPass1"},
    )
    assert resp.status_code == 200, resp.text
    bob_id = decrypt_response(admin, resp)["id"]

    # 6. admin 配 alice / bob 对该磁盘的读+写+删权限
    for uid in (alice_id, bob_id):
        resp = encrypted_request(
            client, admin, "POST",
            f"/api/v1/permissions/users/{uid}/disks/{disk_id}",
            {"can_read": True, "can_write": True, "can_delete": True},
        )
        assert resp.status_code == 200, resp.text

    # 7. alice / bob 各自登录
    alice = do_key_exchange(client)
    login(client, alice, "alice", "Alice@TestPass1")
    bob = do_key_exchange(client)
    login(client, bob, "bob", "Bob@TestPass1")

    # 8. 给 alice / bob 在磁盘内各建一个独立笔记子目录并配置
    (disk_root / "alice_notes").mkdir(exist_ok=True)
    (disk_root / "bob_notes").mkdir(exist_ok=True)
    resp = encrypted_request(
        client, alice, "PUT", "/api/v1/notes/disk-config",
        {"disk_id": disk_id, "path": "alice_notes"},
    )
    assert resp.status_code == 200, resp.text
    resp = encrypted_request(
        client, bob, "PUT", "/api/v1/notes/disk-config",
        {"disk_id": disk_id, "path": "bob_notes"},
    )
    assert resp.status_code == 200, resp.text

    return {
        "admin": admin,
        "alice": alice,
        "bob": bob,
        "alice_id": alice_id,
        "bob_id": bob_id,
        "disk_id": disk_id,
        "disk_root": disk_root,
    }
