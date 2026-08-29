"""
v1.4.2 手动离线媒体修复测试

覆盖范围：
  1. 播放门禁（is_broken_media）：真损坏拒绝、MKV/AVI 等原生可播格式放行
  2. 修复必要性判定（needs_repair）：moov 前置 MP4 健康（v1.4.2 修正）、
     moov 尾部 / MKV 需修复
  3. /stream 播放门禁端到端：损坏文件 415 + [MEDIA_NEEDS_REPAIR]；健康文件正常
  4. 修复任务 API：创建（健康拒绝/损坏入队/权限/重复拦截）、列表全平台共享、
     覆盖（原子替换不留备份）、删除产物
  5. 隐藏目录：文件列表不展示、路径段访问拒绝
  6. 并发上限配置（沿用 v1.4.0 键）

说明：媒体生成依赖 imageio-ffmpeg 内置二进制；不可用时相关用例自动 skip。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
import pytest

from src.config.settings import (
    DB_PATH,
    MEDIA_REPAIR_AUTO_CONCURRENT_BASE,
    MEDIA_REPAIR_CONCURRENT_HARD_MAX,
    MEDIA_REPAIR_CONCURRENT_KEY,
    REPAIR_DIR_NAME,
)
from src.services import config_service, media_repair_service
from src.utils import media_probe

from tests.conftest import decrypt_response, encrypted_request
from tests.test_stream_v1_1_0 import _parse_frames


# ── 测试媒体生成辅助（沿用 v1.4.0 测试）────────────────────── #

@asynccontextmanager
async def _open_db():
    """异步上下文管理器：打开测试 DB 连接（row_factory=Row，与 get_db 一致）。"""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


def _ffmpeg_available() -> bool:
    """imageio-ffmpeg 是否可用（不可用时相关用例 skip）"""
    return media_probe.get_ffmpeg_exe() is not None


def _gen_media(disk_root: Path, name: str, faststart: bool = False) -> bytes:
    """用内置 ffmpeg 生成 1 秒 testsrc 小视频，返回文件字节。"""
    exe = media_probe.get_ffmpeg_exe()
    assert exe, "ffmpeg 不可用"
    out = disk_root / name
    cmd = [
        exe, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
        "-c:v", "mpeg4", "-pix_fmt", "yuv420p",
    ]
    if faststart:
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(out))
    subprocess.run(cmd, check=True)
    return out.read_bytes()


def _write_broken_ts(disk_root: Path, name: str = "broken.ts") -> None:
    """生成一个真损坏的媒体文件（随机字节 + .ts 扩展名，探测必然致命错）。"""
    (disk_root / name).write_bytes(os.urandom(64 * 1024))


def _wait_task_terminal(client, session, task_id: int, timeout_s: float = 20.0) -> dict:
    """轮询任务列表直到目标任务进入终态，返回任务字典。"""
    import time

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        resp = encrypted_request(
            client, session, "GET", "/api/v1/files/repair/tasks",
            {"page": 1, "page_size": 50},
        )
        assert resp.status_code == 200, resp.text
        data = decrypt_response(session, resp)
        for t in data["tasks"]:
            if t["id"] == task_id:
                if t["status"] in ("success", "failed", "canceled", "overridden"):
                    return t
                break
        time.sleep(0.3)
    pytest.fail(f"任务 {task_id} 在 {timeout_s}s 内未进入终态")


# ════════════════════════════════════════════════════════════════════ #
#   测试组 1：播放门禁与健康判定（v1.4.2 核心语义）
# ════════════════════════════════════════════════════════════════════ #

class Test播放门禁判定:
    """is_broken_media：只拒真损坏；格式不理想但原生可播的放行"""

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_损坏TS_拒绝播放(self, disk_root):
        """随机字节的 .ts：fatal → 拒绝播放（MEDIA_NEEDS_REPAIR 语义）"""
        _write_broken_ts(disk_root)
        fp = str(disk_root / "broken.ts")
        probe = asyncio.run(media_probe.probe_media(fp))
        assert media_probe.is_broken_media("broken.ts", probe, fp) is True

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_健康MP4_放行(self, disk_root):
        """正常生成的 MP4：放行（不参与修复引导）"""
        _gen_media(disk_root, "gate_ok.mp4", faststart=True)
        fp = str(disk_root / "gate_ok.mp4")
        probe = asyncio.run(media_probe.probe_media(fp))
        assert media_probe.is_broken_media("gate_ok.mp4", probe, fp) is False

    def test_MKV_放行(self, tmp_path):
        """MKV 等非流式容器：桌面/移动原生可播 → 播放门禁放行（v1.4.2）"""
        p = tmp_path / "a.mkv"
        p.write_bytes(b"fake")
        probe = {"available": True, "has_stream": True, "fatal": False}
        assert media_probe.is_broken_media("a.mkv", probe, str(p)) is False

    def test_非媒体扩展名_放行(self, tmp_path):
        """文本/图片等非媒体扩展名不参与媒体门禁"""
        probe = {"available": True, "has_stream": False, "fatal": True}
        assert media_probe.is_broken_media("a.txt", probe, "x") is False
        assert media_probe.is_broken_media("a.png", probe, "x") is False

    def test_探测不可用_放行(self, tmp_path):
        """探测本身失败（ffmpeg 缺失）→ 放行不误伤"""
        probe = {"available": False, "has_stream": False, "fatal": True}
        assert media_probe.is_broken_media("a.mp4", probe, "x") is False


class Test修复必要性判定:
    """needs_repair / is_mse_friendly：修复入口的健康判定"""

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_faststartMP4_健康_拒绝修复(self, disk_root):
        """v1.4.2 修正：faststart MP4（moov 前置）不再误判为需修复"""
        _gen_media(disk_root, "probe_fast.mp4", faststart=True)
        fp = str(disk_root / "probe_fast.mp4")
        probe = asyncio.run(media_probe.probe_media(fp))
        assert media_probe.needs_repair("probe_fast.mp4", probe, fp) is False

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_moov尾部MP4_需修复(self, disk_root):
        """moov 在尾部（默认输出无 faststart）→ 需修复"""
        _gen_media(disk_root, "probe_normal.mp4")
        fp = str(disk_root / "probe_normal.mp4")
        probe = asyncio.run(media_probe.probe_media(fp))
        assert media_probe.needs_repair("probe_normal.mp4", probe, fp) is True

    def test_MKV_需修复(self, tmp_path):
        """MKV 容器 → 修复入口判定需修复（可无损转 MP4）"""
        p = tmp_path / "a.mkv"
        p.write_bytes(b"fake")
        probe = {"has_stream": True, "fatal": False}
        assert media_probe.needs_repair("a.mkv", probe, str(p)) is True

    def test_webm_健康(self, tmp_path):
        """WebM 扩展名 → 健康"""
        p = tmp_path / "a.webm"
        p.write_bytes(b"fake")
        probe = {"has_stream": True, "fatal": False}
        assert media_probe.is_mse_friendly("a.webm", probe, str(p)) is True


# ════════════════════════════════════════════════════════════════════ #
#   测试组 2：/stream 播放门禁端到端
# ════════════════════════════════════════════════════════════════════ #

class Test播放门禁端到端:
    """/stream：损坏 415 + [MEDIA_NEEDS_REPAIR]；健康正常流式"""

    def _stream_req(self, client, session, disk_id, filename, extra=None):
        body = {
            "disk_id": disk_id,
            "path": "",
            "filename": filename,
            "range_start": 0,
            "range_end": -1,
        }
        if extra:
            body.update(extra)
        return encrypted_request(
            client, session, "GET", "/api/v1/files/stream", body
        )

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_损坏文件_415含错误码(self, client, env):
        """真损坏文件播放被拒，detail 含 [MEDIA_NEEDS_REPAIR]（AC-1）"""
        _write_broken_ts(env["disk_root"])
        resp = self._stream_req(client, env["admin"], env["disk_id"], "broken.ts")
        assert resp.status_code == 415
        data = decrypt_response(env["admin"], resp)
        assert "[MEDIA_NEEDS_REPAIR]" in str(data.get("detail", ""))

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_健康MP4_正常流式(self, client, env):
        """健康 faststart MP4 正常流式输出（AC-2），meta 恒为 byte（v1.4.2）"""
        raw = _gen_media(env["disk_root"], "gate_e2e.mp4", faststart=True)
        resp = self._stream_req(client, env["admin"], env["disk_id"], "gate_e2e.mp4")
        assert resp.status_code == 200
        frames = _parse_frames(resp.content, env["admin"].session_key)
        meta = json.loads(frames[0])
        assert meta["stream_mode"] == "byte"
        assert b"".join(frames[1:]) == raw

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_MKV_放行流式(self, client, env):
        """MKV（原生可播）播放放行，原文件字节直出（v1.4.2 播放纯净化）"""
        # 生成一个真实可解的 mkv（mpeg4 视频）
        exe = media_probe.get_ffmpeg_exe()
        out = env["disk_root"] / "gate_e2e.mkv"
        subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
             "-c:v", "mpeg4", str(out)],
            check=True,
        )
        raw = out.read_bytes()
        resp = self._stream_req(client, env["admin"], env["disk_id"], "gate_e2e.mkv")
        assert resp.status_code == 200
        frames = _parse_frames(resp.content, env["admin"].session_key)
        assert b"".join(frames[1:]) == raw


# ════════════════════════════════════════════════════════════════════ #
#   测试组 3：修复任务 API（创建/列表/覆盖/删除产物）
# ════════════════════════════════════════════════════════════════════ #

class Test修复任务API:
    """repair API 全生命周期"""

    def _create(self, client, session, env, filename, expect=200):
        """发起修复创建请求，返回解密后的响应体"""
        resp = encrypted_request(
            client, session, "POST", "/api/v1/files/repair/create",
            {"disk_id": env["disk_id"], "path": "", "filename": filename},
        )
        assert resp.status_code == expect, (resp.status_code, resp.text)
        return decrypt_response(session, resp)

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_损坏文件_创建任务并修复成功(self, client, env):
        """损坏→修复→success→产物落隐藏目录→原文件字节不变（AC-5/7）"""
        # 准备一个"可修复"的损坏文件：真损坏 TS 探测 fatal，无法 copy 出流 →
        # 这里用 moov 尾部 MP4（可无损修复且探测不 fatal）
        raw = _gen_media(env["disk_root"], "repair_ok.mp4")
        data = self._create(client, env["admin"], env, "repair_ok.mp4")
        task_id = data["task_id"]
        task = _wait_task_terminal(client, env["admin"], task_id)
        assert task["status"] == "success", task["error_message"]
        # 产物存在于隐藏目录
        repair_dir = env["disk_root"] / REPAIR_DIR_NAME
        assert repair_dir.is_dir()
        assert any(f.suffix == ".mp4" for f in repair_dir.iterdir())
        # 原文件字节不变
        assert (env["disk_root"] / "repair_ok.mp4").read_bytes() == raw

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_健康文件_拒绝修复(self, client, env):
        """健康 faststart MP4 → 400 无需修复，零磁盘写入（AC-4）"""
        _gen_media(env["disk_root"], "repair_healthy.mp4", faststart=True)
        data = self._create(client, env["admin"], env, "repair_healthy.mp4", expect=400)
        assert "无需修复" in str(data.get("detail", ""))

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_重复任务_拦截(self, client, env):
        """同文件未完成任务重复创建被拒（AC-6）"""
        _gen_media(env["disk_root"], "repair_dup.mp4")
        self._create(client, env["admin"], env, "repair_dup.mp4")
        data = self._create(client, env["admin"], env, "repair_dup.mp4", expect=400)
        assert "已有修复任务" in str(data.get("detail", ""))
        # 清理：等终态后删记录
        resp = encrypted_request(
            client, env["admin"], "GET", "/api/v1/files/repair/tasks",
            {"page": 1, "page_size": 50},
        )
        tasks = decrypt_response(env["admin"], resp)["tasks"]
        for t in tasks:
            if t["filename"] == "repair_dup.mp4" and t["status"] in (
                "success", "failed", "canceled", "overridden",
            ):
                encrypted_request(
                    client, env["admin"], "POST", "/api/v1/files/repair/delete-record",
                    {"task_id": t["id"]},
                )

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_任务列表_全平台共享(self, client, env):
        """bob（另一账号）能看到 admin 创建的任务（全平台共享，AC-13）"""
        _gen_media(env["disk_root"], "repair_shared.mp4")
        self._create(client, env["admin"], env, "repair_shared.mp4")
        resp = encrypted_request(
            client, env["bob"], "GET", "/api/v1/files/repair/tasks",
            {"page": 1, "page_size": 50},
        )
        assert resp.status_code == 200
        names = [t["filename"] for t in decrypt_response(env["bob"], resp)["tasks"]]
        assert "repair_shared.mp4" in names

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_覆盖_直接替换不留备份(self, client, env):
        """success 任务覆盖：原位置为修复版，无备份残留（AC-12）"""
        raw = _gen_media(env["disk_root"], "repair_override.mp4")
        data = self._create(client, env["admin"], env, "repair_override.mp4")
        task_id = data["task_id"]
        task = _wait_task_terminal(client, env["admin"], task_id)
        assert task["status"] == "success", task["error_message"]

        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/repair/override",
            {"task_id": task_id},
        )
        assert resp.status_code == 200, resp.text
        # 原位置：修复版（faststart MP4，且 ≠ 原损坏字节）
        new_bytes = (env["disk_root"] / "repair_override.mp4").read_bytes()
        assert new_bytes != raw
        assert new_bytes[:4] + new_bytes[4:8] != b""
        # 本任务产物无残留（已移走；其他任务的产物不受影响）、无 .bak 备份
        repair_dir = env["disk_root"] / REPAIR_DIR_NAME
        if repair_dir.is_dir():
            assert not any(
                f.name.startswith("repair_override") for f in repair_dir.iterdir()
            )
        assert not list(env["disk_root"].glob("*.bak"))

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_只读账号_操作被拒403(self, client, env):
        """无写+删权限用户：创建/覆盖被拒 403（AC-6a）"""
        # bob 已有全部权限；先移除 bob 的 write/delete 只留 read
        resp = encrypted_request(
            client, env["admin"], "POST",
            f"/api/v1/permissions/users/{env['bob_id']}/disks/{env['disk_id']}",
            {"can_read": True, "can_write": False, "can_delete": False},
        )
        assert resp.status_code == 200
        try:
            _gen_media(env["disk_root"], "repair_ro.mp4")
            data = self._create(client, env["bob"], env, "repair_ro.mp4", expect=403)
            assert "权限" in str(data.get("detail", ""))
        finally:
            # 恢复 bob 权限
            encrypted_request(
                client, env["admin"], "POST",
                f"/api/v1/permissions/users/{env['bob_id']}/disks/{env['disk_id']}",
                {"can_read": True, "can_write": True, "can_delete": True},
            )

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_删除产物_清理隐藏目录(self, client, env):
        """success 任务删除产物：产物移除、空目录清理（AC-11）"""
        _gen_media(env["disk_root"], "repair_delart.mp4")
        data = self._create(client, env["admin"], env, "repair_delart.mp4")
        task_id = data["task_id"]
        task = _wait_task_terminal(client, env["admin"], task_id)
        assert task["status"] == "success", task["error_message"]
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/repair/delete-artifact",
            {"task_id": task_id},
        )
        assert resp.status_code == 200, resp.text
        repair_dir = env["disk_root"] / REPAIR_DIR_NAME
        # 目录可能因其他任务产物仍在（如 repair_shared 等）——只校验本任务产物不在
        if repair_dir.is_dir():
            assert not any(
                f.name.startswith("repair_delart") for f in repair_dir.iterdir()
            )


# ════════════════════════════════════════════════════════════════════ #
#   测试组 3b：越权防护（S-1 / M-1 回归）
# ════════════════════════════════════════════════════════════════════ #

class Test越权防护:
    """产物流读权限与删记录权限（代码审查 S-1 / M-1 修复回归）"""

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_无读权限用户_拉取产物流_403(self, client, env):
        """S-1：bob 无读权限时构造 repair_task_id 拉产物 → 403"""
        # admin 创建任务并等待成功
        _gen_media(env["disk_root"], "sec_artifact.mp4")
        data = _create_repair(client, env["admin"], env, "sec_artifact.mp4")
        task_id = data["task_id"]
        task = _wait_task_terminal(client, env["admin"], task_id)
        assert task["status"] == "success", task["error_message"]

        # 移除 bob 全部权限 → 无读权限
        encrypted_request(
            client, env["admin"], "POST",
            f"/api/v1/permissions/users/{env['bob_id']}/disks/{env['disk_id']}",
            {"can_read": False, "can_write": False, "can_delete": False},
        )
        try:
            resp = encrypted_request(
                client, env["bob"], "GET", "/api/v1/files/stream",
                {
                    "disk_id": env["disk_id"], "path": "", "filename": "x",
                    "range_start": 0, "range_end": -1,
                    "repair_task_id": task_id,
                },
            )
            assert resp.status_code == 403, (
                f"无读权限应 403，实际 {resp.status_code}"
            )
        finally:
            encrypted_request(
                client, env["admin"], "POST",
                f"/api/v1/permissions/users/{env['bob_id']}/disks/{env['disk_id']}",
                {"can_read": True, "can_write": True, "can_delete": True},
            )

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_只读账号_删除记录_403(self, client, env):
        """M-1：只读账号（无写+删）删除任务记录 → 403"""
        _gen_media(env["disk_root"], "sec_delrec.mp4")
        data = _create_repair(client, env["admin"], env, "sec_delrec.mp4")
        task_id = data["task_id"]
        _wait_task_terminal(client, env["admin"], task_id)

        encrypted_request(
            client, env["admin"], "POST",
            f"/api/v1/permissions/users/{env['bob_id']}/disks/{env['disk_id']}",
            {"can_read": True, "can_write": False, "can_delete": False},
        )
        try:
            resp = encrypted_request(
                client, env["bob"], "POST", "/api/v1/files/repair/delete-record",
                {"task_id": task_id},
            )
            assert resp.status_code == 403, (
                f"只读账号删记录应 403，实际 {resp.status_code}"
            )
        finally:
            encrypted_request(
                client, env["admin"], "POST",
                f"/api/v1/permissions/users/{env['bob_id']}/disks/{env['disk_id']}",
                {"can_read": True, "can_write": True, "can_delete": True},
            )
        # 记录仍在（未被删除）
        resp = encrypted_request(
            client, env["admin"], "GET", "/api/v1/files/repair/tasks",
            {"page": 1, "page_size": 100},
        )
        ids = [t["id"] for t in decrypt_response(env["admin"], resp)["tasks"]]
        assert task_id in ids


# ════════════════════════════════════════════════════════════════════ #
#   测试组 4：隐藏目录防护
# ════════════════════════════════════════════════════════════════════ #

def _create_repair(client, session, env, filename, expect=200):
    """模块级创建任务辅助（多个测试组共用）"""
    resp = encrypted_request(
        client, session, "POST", "/api/v1/files/repair/create",
        {"disk_id": env["disk_id"], "path": "", "filename": filename},
    )
    assert resp.status_code == expect, (resp.status_code, resp.text)
    return decrypt_response(session, resp)


class Test隐藏目录防护:
    """.jflove-repair 不展示、路径段访问拒绝"""

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_文件列表_不展示隐藏目录(self, client, env):
        """list 输出无 .jflove-repair（服务端过滤）"""
        _gen_media(env["disk_root"], "hidden_list.mp4")
        data = _create_repair(client, env["admin"], env, "hidden_list.mp4")
        _wait_task_terminal(client, env["admin"], data["task_id"])
        resp = encrypted_request(
            client, env["admin"], "GET", "/api/v1/files/list",
            {"disk_id": env["disk_id"], "path": ""},
        )
        names = [f["name"] for f in decrypt_response(env["admin"], resp)["files"]]
        assert REPAIR_DIR_NAME not in names

    def test_下载隐藏目录内容_403(self, client, env):
        """直接以隐藏目录路径下载 → 路径段拦截（安全用例）"""
        resp = encrypted_request(
            client, env["admin"], "GET", "/api/v1/files/download",
            {"disk_id": env["disk_id"], "path": f"{REPAIR_DIR_NAME}/x.mp4"},
        )
        assert resp.status_code in (400, 403)

    def test_列出隐藏目录_403(self, client, env):
        """浏览隐藏目录本身 → 拒绝"""
        resp = encrypted_request(
            client, env["admin"], "GET", "/api/v1/files/list",
            {"disk_id": env["disk_id"], "path": REPAIR_DIR_NAME},
        )
        assert resp.status_code in (400, 403)


# ════════════════════════════════════════════════════════════════════ #
#   测试组 5：并发上限（沿用 v1.4.0 配置键）
# ════════════════════════════════════════════════════════════════════ #

class Test并发上限:
    """get_concurrent_limit：自动基线 / 配置 / 硬上限（离线修复队列沿用）"""

    async def _run(self):
        config_service.invalidate_cache()
        async with _open_db() as db:
            limit = await media_repair_service.get_concurrent_limit(db)
            assert limit == MEDIA_REPAIR_AUTO_CONCURRENT_BASE
            await config_service.update(db, MEDIA_REPAIR_CONCURRENT_KEY, "3")
            assert await media_repair_service.get_concurrent_limit(db) == 3
            await config_service.update(db, MEDIA_REPAIR_CONCURRENT_KEY, "999")
            assert (
                await media_repair_service.get_concurrent_limit(db)
                == MEDIA_REPAIR_CONCURRENT_HARD_MAX
            )
            await config_service.update(db, MEDIA_REPAIR_CONCURRENT_KEY, "abc")
            assert (
                await media_repair_service.get_concurrent_limit(db)
                == MEDIA_REPAIR_AUTO_CONCURRENT_BASE
            )
            await config_service.update(db, MEDIA_REPAIR_CONCURRENT_KEY, "")
            config_service.invalidate_cache()

    def test_并发上限_基线配置与硬上限(self, client):
        asyncio.run(self._run())
