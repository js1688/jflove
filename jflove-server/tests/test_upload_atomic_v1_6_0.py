"""
v1.6.0 BL-8：`complete_upload` 覆盖写原子化 —— 回归用例 + 负向对照用例

- 对应需求：`文档记录/需求文档/v1.6.0.md` §4 BL-8
- 对应设计：`文档记录/设计文档/v1.6.0.md` §6.8（临时文件 + `os.replace`）
- 验收判据：AC-B9 ① ~ ⑤

用例设计要点（为什么这样测）：
  1. 每个覆盖场景都**先预置一个已存在的同名文件**（`origin` fixture）——否则
     "原文件有没有被破坏"根本无从断言（原缺陷只在"覆盖同名文件"时暴露）；
  2. ① 中途失败用**两种注入**：monkeypatch 分片读取抛错（模拟 IO 异常 / 连接中断）
     与从磁盘删掉分片文件（模拟缺分片导致读取失败），断言原文件**逐字节 + sha256 不变**；
  3. ② hash 不匹配走校验失败分支，断言原文件**仍然存在**（旧实现此处 `os.remove`）；
  4. ③ 失败后断言目标目录内**无 `.part` 残留**；
  5. ④ 成功路径断言内容 / mtime / 无残留，且响应字段（`message` / `path`）不变；
  6. ⑤ 三端共用的接口契约：归属校验（`is_upload_owned_by`）不削弱、400 文案不变。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from pathlib import Path

import aiofiles
import pytest

from src.services import file_service
from tests.conftest import decrypt_response, encrypted_request

#: 分片大小（极小值）：便于构造"合并到一半失败"的注入点
CHUNK_SIZE = 4

#: 预置原文件的内容（含二进制，便于暴露"被截断成部分内容"）
ORIGIN_CONTENT = b"ORIGINAL-CONTENT-MUST-SURVIVE-" + bytes(range(64))


# ─────────────────────── 辅助函数 ─────────────────────── #


def _split_chunks(data: bytes) -> list[bytes]:
    """按 CHUNK_SIZE 切片（空文件至少 1 片）"""
    chunks = [data[i:i + CHUNK_SIZE] for i in range(0, len(data), CHUNK_SIZE)]
    return chunks or [b""]


def _init_upload(
    client, session, env, filename, content, file_hash=None, mtime=None
) -> tuple[str, list[bytes]]:
    """通过加密接口 init（**不**调用 complete），返回 (upload_id, 分片列表)"""
    chunks = _split_chunks(content)
    resp = encrypted_request(
        client, session, "POST", "/api/v1/files/upload/init",
        {
            "disk_id": env["disk_id"],
            "path": "alice_notes",
            "filename": filename,
            "file_size": len(content),
            "total_chunks": len(chunks),
            "file_hash": (
                file_hash if file_hash is not None
                else hashlib.sha256(content).hexdigest()
            ),
            "mtime": mtime,
        },
    )
    assert resp.status_code == 200, resp.text
    return decrypt_response(session, resp)["upload_id"], chunks


def _send_chunks(client, session, upload_id, chunks) -> None:
    """逐片上传（加密信封内 base64）"""
    for i, chunk in enumerate(chunks):
        resp = encrypted_request(
            client, session, "POST", "/api/v1/files/upload/chunk",
            {
                "upload_id": upload_id,
                "chunk_index": i,
                "chunk_data": base64.b64encode(chunk).decode(),
            },
        )
        assert resp.status_code == 200, resp.text


def _complete(client, session, upload_id):
    """调用 complete 接口"""
    return encrypted_request(
        client, session, "POST", "/api/v1/files/upload/complete",
        {"upload_id": upload_id},
    )


def _drop_session(upload_id: str) -> None:
    """清理内存上传会话（失败用例的会话需显式取消，避免跨用例残留）"""
    asyncio.run(file_service.cancel_upload(upload_id))


def _part_leftovers(directory: Path, filename: str) -> list[str]:
    """目标目录内残留的 BL-8 临时文件（`.<文件名>.jflove-upload-*.part`）"""
    return sorted(p.name for p in Path(directory).glob(f".{filename}*.part"))


# ─────────────────────── 用例 ─────────────────────── #


class Test覆盖写原子化:
    """AC-B9 ① ② ③：任何失败路径下原文件不得被破坏（服务层直调，便于精确注入）"""

    @pytest.fixture
    def dest_name(self) -> str:
        return "atomic_target.bin"

    @pytest.fixture
    def origin(self, env, dest_name) -> Path:
        """在 alice 的笔记目录内预置一个已存在的同名文件（被覆盖的目标）"""
        target = Path(env["disk_root"]) / "alice_notes" / dest_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ORIGIN_CONTENT)
        return target

    def test_合并中途分片读取抛错_原文件内容与sha256不变(
        self, client, env, dest_name, origin, monkeypatch
    ):
        """① 注入 IO 异常（分片读取抛错）→ 原文件内容与 sha256 逐字节不变"""
        new_content = b"NEW-PAYLOAD-LONGER-THAN-THE-ORIGINAL-FILE"
        upload_id, chunks = _init_upload(
            client, env["alice"], env, dest_name, new_content
        )
        _send_chunks(client, env["alice"], upload_id, chunks)

        before = origin.read_bytes()
        before_sha = hashlib.sha256(before).hexdigest()

        # 注入点：读取第 2 个分片时抛 OSError（等价于合并中途 IO 异常 / 连接中断）
        tmp_dir = file_service._upload_sessions[upload_id]["tmp_dir"]
        poison_chunk = os.path.join(tmp_dir, "000001.chunk")
        real_open = aiofiles.open

        def boom_open(file, mode="r", *args, **kwargs):
            if mode == "rb" and str(file) == poison_chunk:
                raise OSError("模拟分片读取失败（IO 异常 / 连接中断）")
            return real_open(file, mode, *args, **kwargs)

        monkeypatch.setattr(file_service.aiofiles, "open", boom_open)
        try:
            with pytest.raises(OSError):
                asyncio.run(file_service.complete_upload(upload_id))
        finally:
            monkeypatch.undo()
            _drop_session(upload_id)

        after = origin.read_bytes()
        assert after == before, "中途失败后原文件内容被破坏（旧实现：先截断再写）"
        assert hashlib.sha256(after).hexdigest() == before_sha, "原文件 sha256 变了"
        # ③ 失败后不残留半截临时文件
        assert _part_leftovers(origin.parent, dest_name) == []

    def test_合并中途分片文件缺失_原文件不变且无残留(
        self, client, env, dest_name, origin
    ):
        """① 分片在合并前从磁盘消失（缺分片 / IO 异常）→ 原文件不变、无 .part 残留"""
        new_content = b"ANOTHER-NEW-PAYLOAD-FOR-ATOMIC-UPLOAD"
        upload_id, chunks = _init_upload(
            client, env["alice"], env, dest_name, new_content
        )
        _send_chunks(client, env["alice"], upload_id, chunks)

        before = origin.read_bytes()
        # 删掉第 2 个分片文件：会话里 "received" 仍标记已收到，因此能穿透
        # "缺少分片"前置检查，真正走到合并中途的文件读取失败
        tmp_dir = file_service._upload_sessions[upload_id]["tmp_dir"]
        os.remove(os.path.join(tmp_dir, "000001.chunk"))

        try:
            with pytest.raises(OSError):
                asyncio.run(file_service.complete_upload(upload_id))
        finally:
            _drop_session(upload_id)

        assert origin.read_bytes() == before, "缺分片导致的失败破坏了原文件"
        assert origin.exists(), "原文件被删除"
        assert _part_leftovers(origin.parent, dest_name) == []

    def test_hash不匹配_原文件仍存在且内容不变(
        self, client, env, dest_name, origin
    ):
        """② 注入 sha256 不匹配 → 原文件仍然存在且内容不变（旧实现此处 os.remove）"""
        new_content = b"CONTENT-WHOSE-HASH-WILL-NOT-MATCH"
        # 声明的 file_hash 与实际上传内容不一致 ⇒ 校验必然失败
        wrong_hash = hashlib.sha256(b"a-different-payload").hexdigest()
        upload_id, chunks = _init_upload(
            client, env["alice"], env, dest_name, new_content, file_hash=wrong_hash
        )
        _send_chunks(client, env["alice"], upload_id, chunks)

        before = origin.read_bytes()
        try:
            with pytest.raises(ValueError) as ei:
                asyncio.run(file_service.complete_upload(upload_id))
        finally:
            _drop_session(upload_id)

        # 异常类型与文案不变（控制器层 / 前端契约）
        assert "文件校验失败，hash 不匹配" in str(ei.value)
        assert origin.exists(), "hash 不匹配时原文件被删除（旧实现缺陷）"
        assert origin.read_bytes() == before
        assert _part_leftovers(origin.parent, dest_name) == []

    def test_接口层hash不匹配_返回400且原文件不被删除(
        self, client, env, dest_name, origin
    ):
        """② 走完整 HTTP 链路：400 + 加密错误文案；原文件不被删除（三端共用契约）"""
        new_content = b"HTTP-LEVEL-HASH-MISMATCH-PAYLOAD"
        wrong_hash = hashlib.sha256(b"nope").hexdigest()
        upload_id, chunks = _init_upload(
            client, env["alice"], env, dest_name, new_content, file_hash=wrong_hash
        )
        _send_chunks(client, env["alice"], upload_id, chunks)

        before = origin.read_bytes()
        resp = _complete(client, env["alice"], upload_id)
        assert resp.status_code == 400, resp.text
        detail = decrypt_response(env["alice"], resp).get("detail", "")
        assert "hash 不匹配" in detail, f"错误文案变更：{detail}"

        assert origin.exists() and origin.read_bytes() == before
        assert _part_leftovers(origin.parent, dest_name) == []

    def test_成功覆盖_内容与mtime正确且无临时文件残留(
        self, client, env, dest_name, origin
    ):
        """④ 正向：覆盖同名文件成功 → 内容 = 新内容、mtime 按传入值还原、无残留"""
        new_content = b"NEW-BYTES-AFTER-ATOMIC-REPLACE-0123456789"
        mtime = 1_600_000_000.0
        upload_id, chunks = _init_upload(
            client, env["alice"], env, dest_name, new_content, mtime=mtime
        )
        _send_chunks(client, env["alice"], upload_id, chunks)

        resp = _complete(client, env["alice"], upload_id)
        assert resp.status_code == 200, resp.text
        data = decrypt_response(env["alice"], resp)
        assert data["message"] == "上传完成"
        assert data["path"] == str(origin)

        assert origin.read_bytes() == new_content, "覆盖后内容不等于新上传内容"
        assert (
            hashlib.sha256(origin.read_bytes()).hexdigest()
            == hashlib.sha256(new_content).hexdigest()
        )
        assert abs(origin.stat().st_mtime - mtime) < 1.0, "mtime 未按传入值还原"
        assert _part_leftovers(origin.parent, dest_name) == []

    def test_写入使用目标目录内的临时文件(
        self, client, env, dest_name, origin, monkeypatch
    ):
        """设计要点：临时文件必须与目标同目录（跨文件系统时 os.replace 不再原子）"""
        new_content = b"SAME-DIRECTORY-TEMP-FILE-CHECK"
        upload_id, chunks = _init_upload(
            client, env["alice"], env, dest_name, new_content
        )
        _send_chunks(client, env["alice"], upload_id, chunks)

        captured: list[tuple[str, str]] = []
        real_replace = file_service._replace_with_retry

        def spy(src: str, dst: str) -> None:
            captured.append((src, dst))
            real_replace(src, dst)

        monkeypatch.setattr(file_service, "_replace_with_retry", spy)
        resp = _complete(client, env["alice"], upload_id)
        assert resp.status_code == 200, resp.text

        assert len(captured) == 1, "未走 os.replace 原子替换链路"
        src, dst = captured[0]
        assert dst == str(origin)
        assert os.path.dirname(src) == os.path.dirname(dst), (
            f"临时文件不在目标目录内（跨文件系统会失去原子性）: {src}"
        )
        assert os.path.basename(src).startswith(f".{dest_name}.jflove-upload-")
        assert os.path.basename(src).endswith(".part")
        assert origin.read_bytes() == new_content

    def test_归属校验未削弱_他人upload_id仍403且原文件不变(
        self, client, env, dest_name, origin
    ):
        """⑤ 回归：`is_upload_owned_by` 逻辑不得削弱（换 upload_id 不能覆盖他人文件）"""
        new_content = b"HIJACK-ATTEMPT-SHOULD-NEVER-LAND"
        upload_id, chunks = _init_upload(
            client, env["alice"], env, dest_name, new_content
        )
        _send_chunks(client, env["alice"], upload_id, chunks)

        before = origin.read_bytes()
        resp = _complete(client, env["bob"], upload_id)
        assert resp.status_code == 403, f"bob 用 alice 的 upload_id 应 403：{resp.status_code}"

        assert origin.read_bytes() == before, "越权请求破坏了他人文件"
        assert _part_leftovers(origin.parent, dest_name) == []

        # 会话仍应属于 alice，后续由 alice 正常完成
        resp = _complete(client, env["alice"], upload_id)
        assert resp.status_code == 200, resp.text
        assert origin.read_bytes() == new_content

    def test_会话不存在_异常类型与文案不变(self):
        """⑤ 契约回归：不存在的 upload_id 仍抛 ValueError 且文案不变"""
        with pytest.raises(ValueError) as ei:
            asyncio.run(file_service.complete_upload("not-a-real-upload-id"))
        assert str(ei.value) == "上传会话不存在或已过期"


class Test替换重试:
    """`_replace_with_retry`：Windows 目标文件被短暂占用时的短重试行为"""

    def test_瞬时占用_重试后成功(self, monkeypatch, tmp_path):
        """前两次 PermissionError → 第 3 次成功；重试间隔为 0.1s"""
        src = tmp_path / "a.part"
        dst = tmp_path / "a.bin"
        src.write_bytes(b"payload")

        calls = {"n": 0}
        sleeps: list[float] = []
        real_replace = os.replace

        def flaky_replace(s, d):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("模拟目标文件被占用（WinError 32）")
            return real_replace(s, d)

        monkeypatch.setattr(file_service.os, "replace", flaky_replace)
        monkeypatch.setattr(file_service.time, "sleep", sleeps.append)

        file_service._replace_with_retry(str(src), str(dst))

        assert calls["n"] == 3
        assert sleeps == [0.1, 0.1]
        assert dst.read_bytes() == b"payload"
        assert not src.exists()

    def test_持续失败_重试上限后抛出原异常(self, monkeypatch, tmp_path):
        """始终被占用 → 尝试 5 次（间隔 0.1s ×4）后抛出最后一次异常"""
        src = tmp_path / "b.part"
        dst = tmp_path / "b.bin"
        src.write_bytes(b"payload")

        calls = {"n": 0}
        sleeps: list[float] = []

        def always_fail(s, d):
            calls["n"] += 1
            raise PermissionError("模拟目标文件被长期占用")

        monkeypatch.setattr(file_service.os, "replace", always_fail)
        monkeypatch.setattr(file_service.time, "sleep", sleeps.append)

        with pytest.raises(PermissionError):
            file_service._replace_with_retry(str(src), str(dst))

        assert calls["n"] == file_service._REPLACE_MAX_ATTEMPTS == 5
        assert sleeps == [0.1] * (file_service._REPLACE_MAX_ATTEMPTS - 1)
        assert src.exists(), "替换失败时临时文件应由调用方（finally）清理，此处仍存在"

    def test_重试参数写死为常量(self):
        """重试次数 / 间隔是显式常量（便于审查与文档对齐）"""
        assert file_service._REPLACE_MAX_ATTEMPTS == 5
        assert file_service._REPLACE_RETRY_INTERVAL_SEC == 0.1
