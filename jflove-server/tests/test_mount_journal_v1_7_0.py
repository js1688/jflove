"""
v1.7.0 变更日志（journal）与对账（scan）接口测试

覆盖需求文档 AC-111 / AC-112 / AC-113 / AC-131（后端侧）/ AC-132 / AC-135。

设计要点：
  1. 每个用例使用**独立子目录前缀**，避免共享磁盘目录时互相干扰；
  2. `since_rev` 是全局游标，断言只比较"本次新增"的那条；
  3. 权限与越界用例复用 conftest 的 `no_perm_session` 与真实路径；
  4. 清理用例用 `disk_id=999999` 写入隔离数据，避免删掉其它用例依赖的记录。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib

import aiosqlite

from src.repositories import file_change_repository
from tests.conftest import decrypt_response, encrypted_request


# ─────────────────────── 辅助函数 ─────────────────────── #


def _upload(client, session, env, subdir: str, filename: str, content: bytes) -> None:
    """单分片上传一个文件（走加密信封的 init → chunk → complete）"""
    file_hash = hashlib.sha256(content).hexdigest()
    resp = encrypted_request(
        client, session, "POST", "/api/v1/files/upload/init",
        {
            "disk_id": env["disk_id"],
            "path": subdir,
            "filename": filename,
            "file_size": len(content),
            "total_chunks": 1,
            "file_hash": file_hash,
        },
    )
    assert resp.status_code == 200, resp.text
    upload_id = decrypt_response(session, resp)["upload_id"]

    resp = encrypted_request(
        client, session, "POST", "/api/v1/files/upload/chunk",
        {
            "upload_id": upload_id,
            "chunk_index": 0,
            "chunk_data": base64.b64encode(content).decode(),
        },
    )
    assert resp.status_code == 200, resp.text

    resp = encrypted_request(
        client, session, "POST", "/api/v1/files/upload/complete",
        {"upload_id": upload_id},
    )
    assert resp.status_code == 200, resp.text


def _journal(client, session, env, prefix: str = "", since_rev: int = 0, limit: int = 500):
    """调用 /files/journal 并解密"""
    resp = encrypted_request(
        client, session, "POST", "/api/v1/files/journal",
        {
            "disk_id": env["disk_id"],
            "prefix": prefix,
            "since_rev": since_rev,
            "limit": limit,
        },
    )
    assert resp.status_code == 200, resp.text
    return decrypt_response(session, resp)


def _scan(client, session, env, prefix: str = "", cursor: str = "", limit: int = 2000):
    """调用 /files/scan 并解密"""
    resp = encrypted_request(
        client, session, "POST", "/api/v1/files/scan",
        {
            "disk_id": env["disk_id"],
            "prefix": prefix,
            "cursor": cursor,
            "limit": limit,
        },
    )
    assert resp.status_code == 200, resp.text
    return decrypt_response(session, resp)


# ─────────────────────── 增量语义 ─────────────────────── #


class Test增量语义:
    def test_上传后产生upsert记录(self, client, env):
        """AC-113：上传完成后，journal 里必须出现该文件的 upsert"""
        _upload(client, env["alice"], env, "jr_up", "a.txt", b"hello-journal")
        data = _journal(client, env["alice"], env, prefix="jr_up")
        upserts = {c["path"] for c in data["changes"] if c["op"] == "upsert"}
        assert "jr_up/a.txt" in upserts
        # 记录里带 size / hash，供客户端判定"内容是否变了"
        item = next(c for c in data["changes"] if c["path"] == "jr_up/a.txt")
        assert item["size"] == len(b"hello-journal")
        assert item["hash"] == hashlib.sha256(b"hello-journal").hexdigest()

    def test_增量只返回since之后的变更(self, client, env):
        """AC-111：since_rev 之后只新增了 1 个文件 ⇒ 恰好返回 1 条"""
        _upload(client, env["alice"], env, "jr_inc", "1.txt", b"one")
        rev = _journal(client, env["alice"], env, prefix="jr_inc")["latest_rev"]

        _upload(client, env["alice"], env, "jr_inc", "2.txt", b"two")
        data = _journal(client, env["alice"], env, prefix="jr_inc", since_rev=rev)

        assert [c["path"] for c in data["changes"]] == ["jr_inc/2.txt"]
        assert data["has_more"] is False

    def test_范围隔离(self, client, env):
        """AC-112：查 jr_a 的增量，绝不包含 jr_b 的路径"""
        _upload(client, env["alice"], env, "jr_a", "only-a.txt", b"a")
        _upload(client, env["alice"], env, "jr_b", "only-b.txt", b"b")

        data = _journal(client, env["alice"], env, prefix="jr_a")
        paths = [c["path"] for c in data["changes"]]
        assert "jr_a/only-a.txt" in paths
        assert all("jr_b/" not in p for p in paths)

    def test_前缀边界不误伤兄弟目录(self, client, env):
        """`jr_pre` 不能匹配 `jr_prefix2`（substr 精确边界）"""
        _upload(client, env["alice"], env, "jr_pre", "a.txt", b"a")
        _upload(client, env["alice"], env, "jr_prefix2", "b.txt", b"b")

        data = _journal(client, env["alice"], env, prefix="jr_pre")
        paths = [c["path"] for c in data["changes"]]
        assert "jr_pre/a.txt" in paths
        assert all(not p.startswith("jr_prefix2/") for p in paths)

    def test_删除产生delete记录(self, client, env):
        """删除文件后 journal 出现 delete，且不再把它当成存在"""
        _upload(client, env["alice"], env, "jr_del", "gone.txt", b"x")
        resp = encrypted_request(
            client, env["alice"], "DELETE", "/api/v1/files",
            {"disk_id": env["disk_id"], "path": "jr_del/gone.txt"},
        )
        assert resp.status_code == 200, resp.text

        data = _journal(client, env["alice"], env, prefix="jr_del")
        deletes = [c for c in data["changes"] if c["op"] == "delete"]
        assert deletes and deletes[-1]["path"] == "jr_del/gone.txt"

    def test_目录删除写整棵子树(self, client, env):
        """删除目录时，每个子孙都要有一条 delete（否则客户端会残留占位）"""
        _upload(client, env["alice"], env, "jr_tree/sub", "deep.txt", b"deep")
        resp = encrypted_request(
            client, env["alice"], "DELETE", "/api/v1/files",
            {"disk_id": env["disk_id"], "path": "jr_tree"},
        )
        assert resp.status_code == 200, resp.text

        data = _journal(client, env["alice"], env, prefix="jr_tree")
        deleted = {c["path"] for c in data["changes"] if c["op"] == "delete"}
        assert "jr_tree/sub/deep.txt" in deleted
        assert "jr_tree/sub" in deleted
        assert "jr_tree" in deleted

    def test_重命名写旧删新增(self, client, env):
        """重命名 = 旧路径 delete + 新路径 upsert"""
        _upload(client, env["alice"], env, "jr_rn", "old.txt", b"rn")
        resp = encrypted_request(
            client, env["alice"], "POST", "/api/v1/files/rename",
            {
                "disk_id": env["disk_id"],
                "path": "jr_rn/old.txt",
                "new_name": "new.txt",
            },
        )
        assert resp.status_code == 200, resp.text

        data = _journal(client, env["alice"], env, prefix="jr_rn")
        ops = {(c["path"], c["op"]) for c in data["changes"]}
        assert ("jr_rn/old.txt", "delete") in ops
        assert ("jr_rn/new.txt", "upsert") in ops


# ─────────────────────── 对账 ─────────────────────── #


class Test对账扫描:
    def test_scan返回完整清单(self, client, env):
        """AC-113：scan 结果与当前目录状态一致"""
        _upload(client, env["alice"], env, "jr_scan", "s1.txt", b"1")
        _upload(client, env["alice"], env, "jr_scan", "s2.txt", b"22")

        data = _scan(client, env["alice"], env, prefix="jr_scan")
        paths = {e["path"] for e in data["entries"]}
        assert {"jr_scan/s1.txt", "jr_scan/s2.txt"} <= paths
        sizes = {e["path"]: e["size"] for e in data["entries"]}
        assert sizes["jr_scan/s1.txt"] == 1
        assert sizes["jr_scan/s2.txt"] == 2

    def test_scan分页游标(self, client, env):
        """分页：limit=1 时 has_more 为真，next_cursor 指向下一页起点"""
        for i in range(3):
            _upload(client, env["alice"], env, "jr_page", f"p{i}.txt", b"x")

        first = _scan(client, env["alice"], env, prefix="jr_page", limit=1)
        assert first["has_more"] is True
        assert first["next_cursor"]

        second = _scan(
            client, env["alice"], env, prefix="jr_page",
            cursor=first["next_cursor"], limit=1,
        )
        assert second["entries"][0]["path"] > first["entries"][0]["path"]

    def test_scan发现API外变更(self, client, env):
        """用户直接往磁盘目录写文件（绕过 API）⇒ scan 必须看得到"""
        target = env["disk_root"] / "jr_external"
        target.mkdir(exist_ok=True)
        (target / "outside.txt").write_bytes(b"outside")

        data = _scan(client, env["alice"], env, prefix="jr_external")
        assert "jr_external/outside.txt" in {e["path"] for e in data["entries"]}


# ─────────────────────── 权限与安全 ─────────────────────── #


class Test权限与安全:
    def test_无权限用户查journal返回403(self, client, env, no_perm_session):
        """AC-131：无读权限的用户不能拉增量"""
        resp = encrypted_request(
            client, no_perm_session, "POST", "/api/v1/files/journal",
            {"disk_id": env["disk_id"], "prefix": "", "since_rev": 0},
        )
        assert resp.status_code == 403, resp.text

    def test_无权限用户查scan返回403(self, client, env, no_perm_session):
        """AC-131：无读权限的用户不能对账"""
        resp = encrypted_request(
            client, no_perm_session, "POST", "/api/v1/files/scan",
            {"disk_id": env["disk_id"], "prefix": "", "cursor": "", "limit": 10},
        )
        assert resp.status_code == 403, resp.text

    def test_越界前缀被拒绝(self, client, env):
        """AC-132：`../` 前缀不得逃出磁盘根"""
        resp = encrypted_request(
            client, env["alice"], "POST", "/api/v1/files/journal",
            {"disk_id": env["disk_id"], "prefix": "../", "since_rev": 0},
        )
        assert resp.status_code in (400, 403), resp.text

    def test_响应为加密信封(self, client, env):
        """AC-130：新接口响应仍是 `{nonce, ciphertext}` 信封"""
        resp = encrypted_request(
            client, env["alice"], "POST", "/api/v1/files/journal",
            {"disk_id": env["disk_id"], "prefix": "", "since_rev": 0},
        )
        assert resp.status_code == 200, resp.text
        assert set(resp.json().keys()) == {"nonce", "ciphertext"}


# ─────────────────────── 清理 ─────────────────────── #


class Test变更日志清理:
    def test_清理保留最近记录并抬高最小rev(self, client, env):
        """AC-135 配套：清理后 min_rev 变大（客户端据此回退到全量对账）"""
        from src.models.database import DB_PATH

        async def run() -> None:
            async with aiosqlite.connect(DB_PATH) as db:
                # 用不存在的 disk_id 隔离，避免影响其它用例
                await file_change_repository.insert_many(db, 999999, [
                    {"rel_path": f"iso/{i}.txt", "op": "upsert"}
                    for i in range(10)
                ])
                before_min = await file_change_repository.min_rev(db)
                await file_change_repository.cleanup_old(db, keep_rows=3)
                after_min = await file_change_repository.min_rev(db)
                assert after_min > before_min

        asyncio.run(run())
