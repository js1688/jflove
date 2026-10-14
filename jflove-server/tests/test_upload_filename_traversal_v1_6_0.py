"""
v1.6.0 BL-10：上传文件名不得逃出虚拟磁盘目录（**认证后**目录穿越）

- 对应台账：`文档记录/项目管理记录/BACKLOG.md` BL-10（严重 · 安全）
- 缺陷：`upload/init` 的 `filename` 原只做 `strip()` + 非空校验，`complete_upload`
  再用 `os.path.join(dest_dir, filename)` 拼目标路径 ⇒ `filename = "../../evil.txt"`
  可让写入逃出虚拟磁盘目录（需已认证 + 对该磁盘有写权限）。
- 验收判据：
  ① 恶意文件名 → 400 且磁盘根**之外**无文件产生；
  ② 正常文件名（中文 / 空格 / 多点 / 隐藏文件）不受影响，完整上传后落在目标目录内；
  ③ 第二层兜底：init 侧校验被绕过（monkeypatch）后，complete 仍拒绝越界目标路径且磁盘外零产物；
  ④ 负向对照：还原 HEAD 的 `os.path.join(dest_dir, filename)` 写法 → 目标路径确实落在
     磁盘根之外（证明本组用例拦的是**真漏洞**，而不是纸上判据）。

为什么同时测服务层与接口层：
  - 服务层（`_validate_filename`）是**口径**，参数化覆盖各类穿越写法最经济；
  - 接口层（`POST /api/v1/files/upload/init`）是**真实攻击面**，必须证明拒绝会走到 400、
    且磁盘外零产物（含"没有半个文件""没有 .part 残留"）。
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

import pytest

from src.services import file_service
from tests.conftest import decrypt_response, encrypted_request

#: 会逃出目标目录的恶意文件名（覆盖 POSIX / Windows / 盘符 / 上级目录段 / NUL 五类写法）
MALICIOUS_NAMES = [
    "../../evil.txt",
    "..\\..\\evil.txt",
    "sub/evil.txt",
    "sub\\evil.txt",
    "/etc/passwd",
    "C:evil.txt",
    "..",
    ".",
    "   ",
    "evil\x00.txt",
]

#: 合法文件名（不得被新校验误伤）
LEGAL_NAMES = [
    "中文 文件名.txt",
    "a.b.c.txt",
    ".hidden",
    "with space.txt",
    "semi;colon&amp.txt",
]

PAYLOAD = b"bl10-payload"


def _snapshot(root: Path) -> set[str]:
    """递归列出目录下所有文件的绝对路径字符串（用于断言"零产物"）"""
    root = Path(root)
    return {str(p) for p in root.rglob("*") if p.is_file()}


def _init(client, session, env, filename, content: bytes = PAYLOAD):
    """发起 `POST /files/upload/init`（单分片）"""
    return encrypted_request(
        client,
        session,
        "POST",
        "/api/v1/files/upload/init",
        {
            "disk_id": env["disk_id"],
            "path": "alice_notes",
            "filename": filename,
            "file_size": len(content),
            "total_chunks": 1,
            "file_hash": hashlib.sha256(content).hexdigest(),
        },
    )


def _upload_one_chunk_and_complete(client, session, upload_id) -> None:
    """上传唯一分片并调用 complete（分片先到齐，才会走到目标路径拼接）"""
    resp = encrypted_request(
        client,
        session,
        "POST",
        "/api/v1/files/upload/chunk",
        {
            "upload_id": upload_id,
            "chunk_index": 0,
            "chunk_data": base64.b64encode(PAYLOAD).decode(),
        },
    )
    assert resp.status_code == 200, resp.text
    return encrypted_request(
        client, session, "POST", "/api/v1/files/upload/complete", {"upload_id": upload_id}
    )


# ─────────────────────── ① 服务层口径 ─────────────────────── #


class Test文件名校验口径:
    """`_validate_filename`：拒绝一切可逃出目标目录的写法，不误伤合法名"""

    @pytest.mark.parametrize("name", MALICIOUS_NAMES)
    def test_恶意文件名一律拒绝(self, name):
        with pytest.raises(ValueError):
            file_service._validate_filename(name)

    @pytest.mark.parametrize("name", LEGAL_NAMES)
    def test_合法文件名原样通过(self, name):
        assert file_service._validate_filename(name) == name

    def test_首尾空白被去除(self):
        assert file_service._validate_filename("  ok.txt \n") == "ok.txt"

    @pytest.mark.parametrize("bad", ["", None, "\x00", "  "])
    def test_空值与非字符串一律拒绝(self, bad):
        with pytest.raises(ValueError):
            file_service._validate_filename(bad)

    def test_当前平台分隔符一律拒绝(self):
        """口径覆盖当前平台的 sep / altsep（不写死单一分隔符）"""
        seps = {s for s in (os.path.sep, os.path.altsep) if s}
        assert seps, "当前平台至少应有一个路径分隔符常量"
        for sep in seps:
            with pytest.raises(ValueError):
                file_service._validate_filename(f"a{sep}b")


# ─────────────────────── ① 接口层攻击面 ─────────────────────── #


class Test接口层拒绝穿越:
    """`POST /files/upload/init`：恶意文件名 → 400，且磁盘根之外零产物"""

    @pytest.mark.parametrize("name", MALICIOUS_NAMES)
    def test_恶意文件名被400拒绝且磁盘外无产物(self, client, env, name):
        disk_root = Path(env["disk_root"])
        before = _snapshot(disk_root.parent)
        resp = _init(client, env["alice"], env, name)
        assert resp.status_code == 400, f"{name!r} 应被拒绝，实际 {resp.status_code}"
        # 磁盘根之外（含 pytest 临时基目录）不得多出任何文件
        assert _snapshot(disk_root.parent) == before
        # 显式断言：不得出现在磁盘根之外
        assert not list(disk_root.parent.glob("evil.txt"))

    def test_拒绝文案不含用户输入与路径(self, client, env):
        """安全宪法 §9.4.13：错误 detail 不回显完整路径 / 用户输入"""
        resp = _init(client, env["alice"], env, "../../evil.txt")
        assert resp.status_code == 400
        detail = str(decrypt_response(env["alice"], resp).get("detail", ""))
        assert "evil" not in detail
        assert "../../" not in detail

    @pytest.mark.parametrize("name", LEGAL_NAMES)
    def test_合法文件名完整上传落在目标目录内(self, client, env, name):
        resp = _init(client, env["alice"], env, name)
        assert resp.status_code == 200, resp.text
        upload_id = decrypt_response(env["alice"], resp)["upload_id"]

        resp = _upload_one_chunk_and_complete(client, env["alice"], upload_id)
        assert resp.status_code == 200, resp.text

        disk_root = Path(env["disk_root"]).resolve()
        target = disk_root / "alice_notes" / name
        assert target.is_file(), f"合法文件名未落盘：{name}"
        assert target.read_bytes() == PAYLOAD
        assert str(target).startswith(str(disk_root)), "合法文件越出了磁盘根"


# ─────────────────────── ③ 第二层兜底 ─────────────────────── #


class Test第二层兜底:
    """`complete_upload` 侧的 `_safe_join` 兜底：init 校验被绕过也必须拦住越界"""

    def test_init校验被绕过后complete仍拒绝(self, client, env, monkeypatch):
        # 模拟"init 侧校验被绕过 / 上传会话被篡改"：把校验还原为只 strip
        monkeypatch.setattr(
            file_service, "_validate_filename", lambda n: (n or "").strip()
        )
        malicious = "../../bl10-escape.txt"
        resp = _init(client, env["alice"], env, malicious)
        assert resp.status_code == 200, "本用例要验证的是 complete 侧兜底，init 需先放行"
        upload_id = decrypt_response(env["alice"], resp)["upload_id"]

        disk_root = Path(env["disk_root"])
        before = _snapshot(disk_root.parent)
        resp = _upload_one_chunk_and_complete(client, env["alice"], upload_id)
        assert resp.status_code == 400, f"越界目标路径应被兜底拦住，实际 {resp.status_code}"
        detail = str(decrypt_response(env["alice"], resp).get("detail", ""))
        assert "filename 非法" in detail
        assert _snapshot(disk_root.parent) == before
        assert not list(disk_root.parent.rglob("bl10-escape.txt"))


# ─────────────────────── ④ 负向对照 ─────────────────────── #


class Test负向对照:
    """证明用例拦的是真漏洞：还原 HEAD 的拼接写法，目标路径确实落在磁盘根之外"""

    def test_旧拼接写法确实逃出磁盘根(self, env):
        disk_root = Path(env["disk_root"]).resolve()
        dest_dir = disk_root / "alice_notes"
        # HEAD 写法：os.path.join(dest_dir, session["filename"])
        escaped = Path(os.path.realpath(os.path.join(str(dest_dir), "../../evil.txt")))
        assert not str(escaped).startswith(str(disk_root)), (
            "负向对照不成立：旧写法并未越界，说明本组用例拦的不是真问题"
        )
        # 新实现把同一条输入挡在第一层
        with pytest.raises(ValueError):
            file_service._validate_filename("../../evil.txt")
