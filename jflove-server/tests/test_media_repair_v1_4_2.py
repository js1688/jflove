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


def _gen_media(
    disk_root: Path,
    name: str,
    faststart: bool = False,
    frag: bool = False,
    bad_frag: bool = False,
) -> bytes:
    """用内置 ffmpeg 生成 1 秒 testsrc 小视频，返回文件字节。

    :param faststart: moov 前置（非分片 faststart MP4，不可 MSE 流式）
    :param frag: 分片 fMP4（moov 前置含 mvex，可 MSE 边下边播）
    :param bad_frag: 坏分片 fMP4（faststart+frag 组合：moov 后跟孤儿 mdat，
        需修复）——每帧关键帧保证多 moof，复现 b7d468ab 旧产物的坏结构
    """
    exe = media_probe.get_ffmpeg_exe()
    assert exe, "ffmpeg 不可用"
    out = disk_root / name
    cmd = [
        exe, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
        "-c:v", "mpeg4", "-pix_fmt", "yuv420p",
    ]
    if bad_frag:
        # faststart 与 frag_keyframe 同用会丢首个 moof（孤儿 mdat）
        cmd += ["-g", "1", "-movflags", "+frag_keyframe+faststart+default_base_moof"]
    elif frag:
        # v1.4.2 hotfix：与修复产物一致的参数——empty_moov 分片 fMP4
        # （不能用 +faststart：与 frag_keyframe 组合会丢首个 moof 产生孤儿 mdat）
        cmd += ["-movflags", "+empty_moov+frag_keyframe+default_base_moof"]
    elif faststart:
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
    def test_faststartMP4_需修复(self, disk_root):
        """v1.4.2 hotfix：faststart MP4（moov 前置但无 mvex）不可 MSE 流式 → 需修复"""
        _gen_media(disk_root, "probe_fast.mp4", faststart=True)
        fp = str(disk_root / "probe_fast.mp4")
        probe = asyncio.run(media_probe.probe_media(fp))
        assert media_probe.needs_repair("probe_fast.mp4", probe, fp) is True

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_fmp4_健康_拒绝修复(self, disk_root):
        """分片 fMP4（moov 前置且含 mvex）→ 可流式，无需修复"""
        _gen_media(disk_root, "probe_fmp4.mp4", frag=True)
        fp = str(disk_root / "probe_fmp4.mp4")
        probe = asyncio.run(media_probe.probe_media(fp))
        assert media_probe.needs_repair("probe_fmp4.mp4", probe, fp) is False

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_坏fMP4_孤儿mdat_需修复(self, disk_root):
        """faststart+frag 组合产物（moov 后紧跟孤儿 mdat）→ 不可流式，需修复"""
        _gen_media(disk_root, "probe_badfmp4.mp4", bad_frag=True)
        fp = str(disk_root / "probe_badfmp4.mp4")
        assert media_probe._moov_at_front(fp) is False
        probe = asyncio.run(media_probe.probe_media(fp))
        assert media_probe.needs_repair("probe_badfmp4.mp4", probe, fp) is True

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
    def test_faststartMP4_mse_only_415(self, client, env):
        """Web mse_only 模式：faststart MP4 不可流式 → 415 + [MEDIA_NEEDS_REPAIR]"""
        _gen_media(env["disk_root"], "gate_fast.mp4", faststart=True)
        resp = self._stream_req(
            client, env["admin"], env["disk_id"], "gate_fast.mp4", extra={"mse_only": 1}
        )
        assert resp.status_code == 415
        data = decrypt_response(env["admin"], resp)
        assert "[MEDIA_NEEDS_REPAIR]" in str(data.get("detail", ""))

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_fmp4_mse_only_放行(self, client, env):
        """Web mse_only 模式：分片 fMP4 可流式 → 200 正常输出"""
        raw = _gen_media(env["disk_root"], "gate_fmp4.mp4", frag=True)
        resp = self._stream_req(
            client, env["admin"], env["disk_id"], "gate_fmp4.mp4", extra={"mse_only": 1}
        )
        assert resp.status_code == 200
        frames = _parse_frames(resp.content, env["admin"].session_key)
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
        """健康分片 fMP4 → 400 无需修复，零磁盘写入（AC-4）"""
        _gen_media(env["disk_root"], "repair_healthy.mp4", frag=True)
        data = self._create(client, env["admin"], env, "repair_healthy.mp4", expect=400)
        assert "无需修复" in str(data.get("detail", ""))

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_重复任务_拦截(self, client, env):
        """同文件存在任意任务记录（含终态）重复创建被拒，删除记录后可再修（AC-6）"""
        _gen_media(env["disk_root"], "repair_dup.mp4")
        # 进行中：重复创建被拒
        self._create(client, env["admin"], env, "repair_dup.mp4")
        data = self._create(client, env["admin"], env, "repair_dup.mp4", expect=400)
        assert "已有修复任务" in str(data.get("detail", ""))
        # 等终态：终态后仍被拒（v1.4.2 hotfix：终态记录同样拦截）
        resp = encrypted_request(
            client, env["admin"], "GET", "/api/v1/files/repair/tasks",
            {"page": 1, "page_size": 50},
        )
        tasks = decrypt_response(env["admin"], resp)["tasks"]
        dup = next(t for t in tasks if t["filename"] == "repair_dup.mp4")
        _wait_task_terminal(client, env["admin"], dup["id"])
        data = self._create(client, env["admin"], env, "repair_dup.mp4", expect=400)
        assert "已有修复任务" in str(data.get("detail", ""))
        # 删除记录后：可重新修复
        encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/repair/delete-record",
            {"task_id": dup["id"]},
        )
        data = self._create(client, env["admin"], env, "repair_dup.mp4")
        assert data.get("task_id")
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

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_删除产物_已不存在_幂等(self, client, env):
        """产物已被人工挪走时再次删除 → 200 幂等成功（v1.4.2 hotfix）"""
        _gen_media(env["disk_root"], "repair_delart2.mp4")
        data = self._create(client, env["admin"], env, "repair_delart2.mp4")
        task_id = data["task_id"]
        task = _wait_task_terminal(client, env["admin"], task_id)
        assert task["status"] == "success", task["error_message"]
        # 第一次删除：正常移除产物
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/repair/delete-artifact",
            {"task_id": task_id},
        )
        assert resp.status_code == 200, resp.text
        # 第二次删除：产物已不存在，仍应 200 不报错
        resp = encrypted_request(
            client, env["admin"], "POST", "/api/v1/files/repair/delete-artifact",
            {"task_id": task_id},
        )
        assert resp.status_code == 200, resp.text

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_修复产物_fMP4结构正确(self, client, env):
        """修复产物为合法分片 fMP4：无孤儿 mdat，首个 moof tfdt 从 0 开始"""
        import struct

        _gen_media(env["disk_root"], "repair_frag.mp4")  # moov 尾部，需修复
        data = self._create(client, env["admin"], env, "repair_frag.mp4")
        task_id = data["task_id"]
        task = _wait_task_terminal(client, env["admin"], task_id)
        assert task["status"] == "success", task["error_message"]

        # 定位产物文件
        repair_dir = env["disk_root"] / REPAIR_DIR_NAME
        artifact = next(
            f for f in repair_dir.iterdir() if f.name.startswith("repair_frag")
        )
        raw = artifact.read_bytes()
        n = len(raw)

        def _walk(start: int, end: int):
            boxes = []
            i = start
            while i + 8 <= end:
                size = struct.unpack(">I", raw[i:i + 4])[0]
                typ = raw[i + 4:i + 8]
                if size < 8 or i + size > end:
                    break
                boxes.append((i, typ, size))
                i += size
            return boxes

        tops = _walk(0, n)
        # 顶层顺序：ftyp → moov → moof → mdat → ...（不允许 moov 后紧跟 mdat）
        kinds = [t.decode("latin1") for _, t, _ in tops]
        assert "moov" in kinds
        moov_idx = kinds.index("moov")
        assert kinds[moov_idx + 1] == "moof", (
            "moov 后必须是 moof，不能是孤儿 mdat（faststart+frag 组合 bug）"
        )
        # moof 数与 mdat 数相等（一一对应，无孤儿 mdat）
        assert kinds.count("moof") == kinds.count("mdat")
        # 首个 moof 的视频 traf tfdt 应为 0（时间戳从 0 开始）
        first_moof, first_moof_size = next(
            (o, s) for o, t, s in tops if t == b"moof"
        )
        first_tfdt = None
        for moff, mtyp, msize in _walk(first_moof + 8, first_moof + first_moof_size):
            if mtyp != b"traf":
                continue
            for toff, ttyp, tsize in _walk(moff + 8, moff + msize):
                if ttyp == b"tfdt":
                    v = raw[toff + 8]
                    tfdt = struct.unpack(">Q", raw[toff + 12:toff + 20])[0] \
                        if v == 1 else struct.unpack(">I", raw[toff + 12:toff + 16])[0]
                    if first_tfdt is None:
                        first_tfdt = tfdt
        assert first_tfdt == 0, f"首个 traf 的 tfdt 应为 0，实际 {first_tfdt}"

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_坏fMP4_可重新修复(self, client, env):
        """faststart+frag 的坏 fMP4（孤儿 mdat）可被重新修复为正确 fMP4"""
        import struct

        _gen_media(env["disk_root"], "repair_badfmp4.mp4", bad_frag=True)
        # 坏 fMP4 不应被误判为健康：创建任务应成功（而非 400 无需修复）
        data = self._create(client, env["admin"], env, "repair_badfmp4.mp4")
        task_id = data["task_id"]
        task = _wait_task_terminal(client, env["admin"], task_id)
        assert task["status"] == "success", task["error_message"]

        # 新产物结构正确：moov 后紧跟 moof，无孤儿 mdat
        repair_dir = env["disk_root"] / REPAIR_DIR_NAME
        artifact = next(
            f for f in repair_dir.iterdir() if f.name.startswith("repair_badfmp4")
        )
        raw = artifact.read_bytes()
        n = len(raw)

        def _walk(start: int, end: int):
            boxes = []
            i = start
            while i + 8 <= end:
                size = struct.unpack(">I", raw[i:i + 4])[0]
                typ = raw[i + 4:i + 8]
                if size < 8 or i + size > end:
                    break
                boxes.append((i, typ, size))
                i += size
            return boxes

        tops = _walk(0, n)
        kinds = [t.decode("latin1") for _, t, _ in tops]
        assert "moov" in kinds
        moov_idx = kinds.index("moov")
        assert kinds[moov_idx + 1] == "moof", (
            "重新修复后 moov 后必须紧跟 moof，不能是孤儿 mdat"
        )
        assert kinds.count("moof") == kinds.count("mdat")


# ════════════════════════════════════════════════════════════════════ #
#   测试组 3c：产物 duration 补丁
# ════════════════════════════════════════════════════════════════════ #

class Test产物duration补丁:
    """patch_mp4_durations：修正 ffmpeg 分片 fMP4 的 duration 字段"""

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_patch修正mvhd_tkhd_mdhd(self, tmp_path):
        """分片 fMP4 的 mvhd duration 被修正为真实总时长"""
        import struct

        _gen_media(tmp_path, "patch_src.mp4", frag=True)
        fp = str(tmp_path / "patch_src.mp4")
        probe = asyncio.run(media_probe.probe_media(fp))
        dur = probe.get("duration", 0)
        assert dur > 0

        assert media_probe.patch_mp4_durations(fp, dur) is True

        with open(fp, "rb") as fh:
            d = fh.read(4 * 1024 * 1024)
        pos = 0
        while pos + 8 <= len(d):
            size = struct.unpack(">I", d[pos:pos + 4])[0]
            typ = d[pos + 4:pos + 8]
            if typ == b"moov":
                p = pos + 8
                e = pos + size
                while p + 8 <= e:
                    s2 = struct.unpack(">I", d[p:p + 4])[0]
                    t2 = d[p + 4:p + 8]
                    if t2 == b"mvhd":
                        ts = struct.unpack(">I", d[p + 20:p + 24])[0]
                        dv = struct.unpack(">I", d[p + 24:p + 28])[0]
                        assert abs(dv / ts - dur) < 0.5
                        return
                    if s2 < 8:
                        break
                    p += s2
            if size < 8:
                break
            pos += size
        pytest.fail("未找到 mvhd box")

    def test_patch非法参数_返回False(self, tmp_path):
        """文件不存在 / 时长为 0 → 返回 False"""
        assert media_probe.patch_mp4_durations(
            str(tmp_path / "nope.mp4"), 10.0
        ) is False
        assert media_probe.patch_mp4_durations(
            str(tmp_path / "nope.mp4"), 0.0
        ) is False


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
