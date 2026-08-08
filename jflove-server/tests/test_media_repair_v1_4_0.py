"""
v1.4.0 媒体修复服务测试

覆盖范围：
  1. 媒体健康判定：普通 MP4（moov 尾部）/ faststart MP4 / WebM / MKV 判定
  2. 修复决策与开关：开关关→byte；开关开+健康→byte；开关开+需修复→time
  3. 修复流端到端：/stream 开启开关后，非流式文件返回 stream_mode=time 且为 fMP4 流
  4. 配置缓存：config_service 内存缓存 + 写后失效（立即生效）
  5. 并发上限：自动基线 / 管理员配置 / 硬上限截断

说明：媒体生成依赖 imageio-ffmpeg 内置二进制；不可用时相关用例自动 skip。
"""

from __future__ import annotations

import asyncio
import json
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
    MEDIA_REPAIR_ENABLED_KEY,
)
from src.services import config_service, media_repair_service
from src.utils import media_probe

from tests.conftest import decrypt_response, encrypted_request
from tests.test_stream_v1_1_0 import _parse_frames


# ── 测试媒体生成辅助 ────────────────────────────────────────────── #


@pytest.fixture(autouse=True, scope="module")
def _reset_repair_switch(client, env):
    """模块测试结束后把媒体修复开关复位为关闭，避免污染其他测试模块。"""
    yield
    encrypted_request(
        client, env["admin"], "PUT", "/api/v1/config",
        {"key": MEDIA_REPAIR_ENABLED_KEY, "value": "0"},
    )


@asynccontextmanager
async def _open_db():
    """异步上下文管理器：打开测试 DB 连接（带 row_factory=Row，与 get_db 一致）。"""
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
    assert exe, "imageio-ffmpeg 不可用"
    out = disk_root / name
    cmd = [
        exe, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
    ]
    if faststart:
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(out))
    subprocess.run(cmd, check=True)
    return out.read_bytes()


def _enable_repair(client, admin) -> None:
    """管理员开启媒体修复总开关（写 config，立即生效）。"""
    resp = encrypted_request(
        client, admin, "PUT", "/api/v1/config",
        {"key": MEDIA_REPAIR_ENABLED_KEY, "value": "1"},
    )
    assert resp.status_code == 200, resp.text
    assert decrypt_response(admin, resp)["message"] == "配置已更新"


def _stream_req(client, session, disk_id, filename, extra=None):
    """向 /api/v1/files/stream 发加密 GET 请求（v1.4.0 扩展）。"""
    body = {
        "disk_id": disk_id,
        "path": "",
        "filename": filename,
        "range_start": 0,
        "range_end": -1,
    }
    if extra:
        body.update(extra)
    return encrypted_request(client, session, "GET", "/api/v1/files/stream", body)


# ════════════════════════════════════════════════════════════════════ #
#   测试组 1：媒体健康判定
# ════════════════════════════════════════════════════════════════════ #

class Test媒体健康判定:
    """is_mse_friendly / needs_repair 对不同容器格式的判定"""

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_普通MP4_moov尾部_需修复(self, disk_root):
        """普通 MP4（moov 在尾部）→ 需修复"""
        _gen_media(disk_root, "probe_normal.mp4")
        probe = asyncio.run(media_probe.probe_media(str(disk_root / "probe_normal.mp4")))
        assert probe["has_stream"] is True
        assert media_probe.needs_repair(
            "probe_normal.mp4", probe, str(disk_root / "probe_normal.mp4")
        ) is True

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_faststartMP4_可流式_不需修复(self, disk_root):
        """faststart MP4（moov 在前）→ 健康，不需修复"""
        _gen_media(disk_root, "probe_fast.mp4", faststart=True)
        probe = asyncio.run(media_probe.probe_media(str(disk_root / "probe_fast.mp4")))
        assert media_probe.needs_repair(
            "probe_fast.mp4", probe, str(disk_root / "probe_fast.mp4")
        ) is False

    def test_webm_健康(self, disk_root, tmp_path):
        """WebM 扩展名 → 健康"""
        p = tmp_path / "a.webm"
        p.write_bytes(b"fake")
        probe = {"has_stream": True, "fatal": False}
        assert media_probe.is_mse_friendly("a.webm", probe, str(p)) is True

    def test_mkv_需修复(self, disk_root, tmp_path):
        """MKV（MSE 不支持容器）→ 需修复"""
        p = tmp_path / "a.mkv"
        p.write_bytes(b"fake")
        probe = {"has_stream": True, "fatal": False}
        assert media_probe.needs_repair("a.mkv", probe, str(p)) is True

    def test_probe无ffmpeg_降级不修复(self, monkeypatch):
        """imageio-ffmpeg 不可用时 probe 返回 available=False"""
        monkeypatch.setattr(media_probe, "get_ffmpeg_exe", lambda: None)
        probe = asyncio.run(media_probe.probe_media("whatever.mp4"))
        assert probe["available"] is False


# ════════════════════════════════════════════════════════════════════ #
#   测试组 2：配置缓存（立即生效）
# ════════════════════════════════════════════════════════════════════ #

class Test配置缓存:
    """config_service 内存缓存：读取缓存 + 写后失效"""

    async def _roundtrip(self):
        config_service.invalidate_cache()
        async with _open_db() as db:
            # init_db 已初始化默认键 media_repair_enabled=0
            assert await config_service.get(db, MEDIA_REPAIR_ENABLED_KEY, "0") == "0"
            await config_service.update(db, MEDIA_REPAIR_ENABLED_KEY, "1")
            # 写后缓存失效 → 立即读到新值
            assert await config_service.get(db, MEDIA_REPAIR_ENABLED_KEY, "0") == "1"
            # 未设置键返回默认值
            assert await config_service.get(db, "not_exist_key", "dft") == "dft"
            config_service.invalidate_cache()

    def test_缓存读取与写后失效(self, client):
        asyncio.run(self._roundtrip())


# ════════════════════════════════════════════════════════════════════ #
#   测试组 3：并发上限
# ════════════════════════════════════════════════════════════════════ #

class Test并发上限:
    """get_concurrent_limit：自动基线 / 配置 / 硬上限"""

    async def _run(self):
        config_service.invalidate_cache()
        async with _open_db() as db:
            # 未配置 → 自动基线
            limit = await media_repair_service.get_concurrent_limit(db)
            assert limit == MEDIA_REPAIR_AUTO_CONCURRENT_BASE
            # 配置合法值 → 生效
            await config_service.update(db, MEDIA_REPAIR_CONCURRENT_KEY, "3")
            assert await media_repair_service.get_concurrent_limit(db) == 3
            # 配置超硬上限 → 截断到硬上限
            await config_service.update(db, MEDIA_REPAIR_CONCURRENT_KEY, "999")
            assert (
                await media_repair_service.get_concurrent_limit(db)
                == MEDIA_REPAIR_CONCURRENT_HARD_MAX
            )
            # 配置非法（非数字）→ 自动基线
            await config_service.update(db, MEDIA_REPAIR_CONCURRENT_KEY, "abc")
            assert (
                await media_repair_service.get_concurrent_limit(db)
                == MEDIA_REPAIR_AUTO_CONCURRENT_BASE
            )
            config_service.invalidate_cache()

    def test_并发上限_基线配置与硬上限(self, client):
        asyncio.run(self._run())


# ════════════════════════════════════════════════════════════════════ #
#   测试组 4：修复决策与开关
# ════════════════════════════════════════════════════════════════════ #

class Test修复决策:
    """ensure_playable 决策：开关关→byte；开+健康→byte；开+需修复→time"""

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_开关关闭_需修复文件也走byte(self, client, env):
        """开关默认关闭：即使非流式文件也走 byte 模式（零额外开销）"""
        _gen_media(env["disk_root"], "decision_normal.mp4")
        fp = str(env["disk_root"] / "decision_normal.mp4")
        # 确保开关关闭
        resp = encrypted_request(
            client, env["admin"], "PUT", "/api/v1/config",
            {"key": MEDIA_REPAIR_ENABLED_KEY, "value": "0"},
        )
        assert resp.status_code == 200, resp.text
        decision = asyncio.run(self._decide(fp))
        assert decision["mode"] == "byte"

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_开关开启_健康文件走byte_需修复走time(self, client, env):
        """开关开启：faststart→byte；普通 MP4（moov 尾部）→time"""
        _gen_media(env["disk_root"], "dec_fast.mp4", faststart=True)
        _gen_media(env["disk_root"], "dec_normal.mp4")
        _enable_repair(client, env["admin"])

        fast_fp = str(env["disk_root"] / "dec_fast.mp4")
        normal_fp = str(env["disk_root"] / "dec_normal.mp4")
        assert asyncio.run(self._decide(fast_fp))["mode"] == "byte"
        assert asyncio.run(self._decide(normal_fp))["mode"] == "time"

    async def _decide(self, fp: str):
        """用临时 DB 连接执行 ensure_playable（开关状态来自 config 表）"""
        async with _open_db() as db:
            return await media_repair_service.ensure_playable(
                db, fp, Path(fp).name
            )


# ════════════════════════════════════════════════════════════════════ #
#   测试组 5：修复流端到端（/stream）
# ════════════════════════════════════════════════════════════════════ #

class Test修复流端到端:
    """开启开关后 /stream 对非流式文件返回 stream_mode=time 的 fMP4 流"""

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_修复开启_非流式文件返回time模式fMP4流(self, client, env):
        """普通 MP4（moov 尾部）→ meta.stream_mode=time，数据为 fMP4"""
        _gen_media(env["disk_root"], "e2e_normal.mp4")
        _enable_repair(client, env["admin"])

        resp = _stream_req(client, env["alice"], env["disk_id"], "e2e_normal.mp4")
        assert resp.status_code == 200, resp.text
        assert resp.headers.get("X-Encrypted-Stream") == "v2"

        frames = _parse_frames(resp.content, env["alice"].session_key)
        meta = json.loads(frames[0])
        assert meta["type"] == "meta"
        assert meta["stream_mode"] == "time"
        assert meta["content_type"] == "video/mp4"

        data = b"".join(frames[1:])
        # fMP4 特征：ftyp box + moov（empty_moov）/ moof
        assert len(data) >= 64
        assert b"ftyp" in data[:64]
        assert (b"moov" in data[:256]) or (b"moof" in data[:256])

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_修复开启_健康文件仍走byte原文件(self, client, env):
        """faststart MP4（健康）→ 仍为 byte 模式，数据帧等于原文件字节"""
        raw = _gen_media(env["disk_root"], "e2e_fast.mp4", faststart=True)
        _enable_repair(client, env["admin"])

        resp = _stream_req(client, env["alice"], env["disk_id"], "e2e_fast.mp4")
        assert resp.status_code == 200, resp.text

        frames = _parse_frames(resp.content, env["alice"].session_key)
        meta = json.loads(frames[0])
        # byte 模式：无 stream_mode 或为 byte
        assert meta.get("stream_mode", "byte") == "byte"
        data = b"".join(frames[1:])
        assert data == raw, "健康文件应原样返回"

    @pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
    def test_修复关闭_非流式文件仍byte原文件(self, client, env):
        """开关默认关闭：非流式文件走 byte 原文件（不影响现有行为）"""
        raw = _gen_media(env["disk_root"], "e2e_off.mp4")
        # 确保开关为关闭（init_db 默认 0；若之前用例开启则关闭）
        resp = encrypted_request(
            client, env["admin"], "PUT", "/api/v1/config",
            {"key": MEDIA_REPAIR_ENABLED_KEY, "value": "0"},
        )
        assert resp.status_code == 200, resp.text

        resp = _stream_req(client, env["alice"], env["disk_id"], "e2e_off.mp4")
        assert resp.status_code == 200, resp.text
        frames = _parse_frames(resp.content, env["alice"].session_key)
        meta = json.loads(frames[0])
        assert meta.get("stream_mode", "byte") == "byte"
        data = b"".join(frames[1:])
        assert data == raw, "开关关闭时应返回原文件字节"

    def test_修复流不落盘_原始文件未改动(self, client, env, tmp_path):
        """修复仅用于在线播放：原始文件字节不变、无临时产物落盘"""
        # 复用 e2e_normal.mp4 的生成（无 ffmpeg 时跳过）
        if not _ffmpeg_available():
            pytest.skip("imageio-ffmpeg 不可用")
        fp = env["disk_root"] / "e2e_normal.mp4"
        if not fp.exists():
            _gen_media(env["disk_root"], "e2e_normal.mp4")
        before = fp.read_bytes()
        _enable_repair(client, env["admin"])
        _stream_req(client, env["alice"], env["disk_id"], "e2e_normal.mp4")
        after = fp.read_bytes()
        assert after == before, "原始文件不得被修复过程修改"
