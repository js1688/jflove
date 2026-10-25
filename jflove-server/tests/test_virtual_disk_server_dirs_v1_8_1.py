"""服务器目录浏览接口单测（v1.8.1 / AC-257、AC-258、AC-262、AC-263）。

覆盖 `GET /api/v1/virtual-disks/server-dirs`：
  - 管理员 200 / 普通用户 403 / 无 token 401；
  - 只列目录、过滤隐藏目录、排序、roots 去重；
  - 相对路径 400、不存在 404、无权限 403；
  - 加密信封与错误响应不泄露完整路径；
  - 超限 truncated。

不触碰真实用户目录：所有被浏览的路径都来自 pytest 的 `tmp_path`。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from src.services import virtual_disk_service
from tests.conftest import decrypt_response


def encrypted_get(client, session, path: str, payload: dict | None = None):
    """GET 请求使用 query 加密信封（与 v1.8.0 controller 测试同口径）。"""
    from src.utils.crypto import encrypt as srv_encrypt

    data = {"token": session.token, **(payload or {})}
    envelope = srv_encrypt(
        session.session_key, json.dumps(data, ensure_ascii=False).encode()
    )
    return client.get(
        path,
        params={"nonce": envelope["nonce"], "ciphertext": envelope["ciphertext"]},
        headers={"X-Session-ID": session.session_id},
    )


def _make_tree(root: Path) -> None:
    """构造：两个可见目录、一个隐藏目录、一个文件。"""
    (root / "beta").mkdir()
    (root / "alpha").mkdir()
    (root / ".hidden").mkdir()
    (root / "note.txt").write_text("x", encoding="utf-8")


def test_admin_can_browse_dirs_only(client, env, tmp_path: Path) -> None:
    """管理员 200；只返回目录、按名称排序、隐藏目录不出现。"""
    _make_tree(tmp_path)
    resp = encrypted_get(
        client, env["admin"], "/api/v1/virtual-disks/server-dirs", {"path": str(tmp_path)}
    )
    assert resp.status_code == 200, resp.text
    assert set(resp.json()) == {"nonce", "ciphertext"}, "响应必须是加密信封"
    data = decrypt_response(env["admin"], resp)
    names = [item["name"] for item in data["dirs"]]
    assert names == ["alpha", "beta"]
    assert "note.txt" not in names
    assert ".hidden" not in names
    assert data["path"] == os.path.normpath(str(tmp_path))
    assert data["parent"] == os.path.normpath(str(tmp_path.parent))
    assert all(os.path.isdir(item["path"]) for item in data["dirs"])


def test_parent_is_null_at_root(client, env) -> None:
    """根目录 / 盘符根的 parent 必须为 null。"""
    root = os.path.abspath(os.sep)
    resp = encrypted_get(
        client, env["admin"], "/api/v1/virtual-disks/server-dirs", {"path": root}
    )
    assert resp.status_code == 200, resp.text
    data = decrypt_response(env["admin"], resp)
    assert data["parent"] is None


def test_roots_exist_and_unique(client, env) -> None:
    """roots 只含真实存在的目录且去重。"""
    resp = encrypted_get(
        client, env["admin"], "/api/v1/virtual-disks/server-dirs", {"path": str(Path.home())}
    )
    assert resp.status_code == 200, resp.text
    data = decrypt_response(env["admin"], resp)
    roots = data["roots"]
    assert roots, "roots 不应为空"
    paths = [item["path"] for item in roots]
    assert len(paths) == len(set(os.path.normcase(p) for p in paths))
    assert all(os.path.isdir(path) for path in paths)


def test_non_admin_forbidden(client, env, tmp_path: Path) -> None:
    """普通用户调用一律 403（错误响应加密）。"""
    _make_tree(tmp_path)
    resp = encrypted_get(
        client, env["alice"], "/api/v1/virtual-disks/server-dirs", {"path": str(tmp_path)}
    )
    assert resp.status_code == 403, resp.text
    assert set(resp.json()) == {"nonce", "ciphertext"}
    data = decrypt_response(env["alice"], resp)
    assert data["detail"] == "仅管理员可操作"


def test_missing_token_unauthorized(client, env) -> None:
    """无 token ⇒ 401。"""
    from src.utils.crypto import encrypt as srv_encrypt

    envelope = srv_encrypt(env["admin"].session_key, b"{}")
    resp = client.get(
        "/api/v1/virtual-disks/server-dirs",
        params={"nonce": envelope["nonce"], "ciphertext": envelope["ciphertext"]},
        headers={"X-Session-ID": env["admin"].session_id},
    )
    assert resp.status_code == 401, resp.text


def test_relative_path_rejected(client, env) -> None:
    """相对路径 ⇒ 400。"""
    resp = encrypted_get(
        client, env["admin"], "/api/v1/virtual-disks/server-dirs", {"path": "relative/dir"}
    )
    assert resp.status_code == 400, resp.text
    data = decrypt_response(env["admin"], resp)
    assert data["detail"] == "路径必须是绝对路径"


def test_missing_dir_not_found_without_path_echo(client, env, tmp_path: Path) -> None:
    """不存在 ⇒ 404；错误 detail 不回显完整路径。"""
    missing = tmp_path / "no-such-dir"
    resp = encrypted_get(
        client, env["admin"], "/api/v1/virtual-disks/server-dirs", {"path": str(missing)}
    )
    assert resp.status_code == 404, resp.text
    data = decrypt_response(env["admin"], resp)
    assert data["detail"] == "目录不存在"
    assert str(missing) not in json.dumps(data, ensure_ascii=False)


def test_permission_error_returns_403(client, env, tmp_path: Path, monkeypatch) -> None:
    """scandir 抛 PermissionError ⇒ 403，且不回显路径。"""

    def deny(_path):
        raise PermissionError

    monkeypatch.setattr(virtual_disk_service.os, "scandir", deny)
    resp = encrypted_get(
        client, env["admin"], "/api/v1/virtual-disks/server-dirs", {"path": str(tmp_path)}
    )
    assert resp.status_code == 403, resp.text
    data = decrypt_response(env["admin"], resp)
    assert data["detail"] == "无权限访问该目录"
    assert str(tmp_path) not in json.dumps(data, ensure_ascii=False)


def test_truncated_when_over_limit(client, env, tmp_path: Path, monkeypatch) -> None:
    """目录数超过上限 ⇒ truncated=true 且最多返回上限条数。"""
    _make_tree(tmp_path)
    monkeypatch.setattr(virtual_disk_service, "SERVER_DIR_LIMIT", 1)
    resp = encrypted_get(
        client, env["admin"], "/api/v1/virtual-disks/server-dirs", {"path": str(tmp_path)}
    )
    assert resp.status_code == 200, resp.text
    data = decrypt_response(env["admin"], resp)
    assert data["truncated"] is True
    assert len(data["dirs"]) == 1
    # v1.8.2 / C1：三端文案用接口下发值，不再硬编码 2000
    assert data["limit"] == 1


def test_default_path_uses_first_root(client, env) -> None:
    """不传 path ⇒ 从 roots 第一项开始。"""
    resp = encrypted_get(client, env["admin"], "/api/v1/virtual-disks/server-dirs", {})
    assert resp.status_code == 200, resp.text
    data = decrypt_response(env["admin"], resp)
    assert data["roots"]
    assert data["path"] == data["roots"][0]["path"]
