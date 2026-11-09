"""
v1.9.0 桌面端下载断点续传单测（需求 F48，设计 §3.1 / §3.3）

覆盖：
  1. `range_start` 续传：part 追加写、进度按「含续传起点」的已写字节累计；
  2. 响应头 `X-Stream-Offset` / `X-Stream-Total` / `X-File-Version`；
  3. 版本不一致 ⇒ 删除 part 从头下载，不产生拼接坏文件；
  4. 416 偏移越界 ⇒ 自动回退从头下载；
  5. 失败保留 part/meta 供下次续传；用户取消删除 part/meta；
  6. `file_service.download_file` 读取 part+meta 计算 range_start、超过 7 天清理；
  7. 本地目标已完整 / part 已完整时直接收尾，不重复下载。
"""

from __future__ import annotations

import json
import os
import time

import pytest

from src.services import file_service
from src.utils.http_client import http_client


class _FakeResponse:
    """最小可用的流式响应替身（frames 由 parse_stream_frame 替身消费）"""

    def __init__(self, status: int = 200, headers: dict | None = None):
        self.status_code = status
        self.headers = headers or {}
        self.raw = object()
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def json(self) -> dict:
        return {"detail": "偏移越界"}


def _install_frames(monkeypatch, frames: list) -> dict:
    """
    把 `parse_stream_frame` 替换成按 frames 顺序返回（None 表示流结束，
    异常对象则抛出）。
    """
    state: dict = {"frames": list(frames), "calls": 0}

    def fake_parse(_raw, _key):
        state["calls"] += 1
        if not state["frames"]:
            return None
        item = state["frames"].pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    from src.utils import http_client as hc_module
    monkeypatch.setattr(hc_module, "parse_stream_frame", fake_parse)
    return state


def _install_responses(monkeypatch, responses: list) -> dict:
    """把 HTTP 发送层替换为按顺序返回预置响应（记录请求次数）"""
    state: dict = {"send": 0}

    def fake_send(_send_fn):
        state["send"] += 1
        return responses.pop(0)

    monkeypatch.setattr(http_client, "_send_with_auto_resync", fake_send)
    return state


def _write_meta(meta_path: str, size: int, version: str, offset: int,
                updated_at: float | None = None) -> dict:
    """写一份 part 元数据并返回内容"""
    meta = {
        "size": size,
        "version": version,
        "offset": offset,
        "updated_at": int(time.time()) if updated_at is None else updated_at,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return meta


def test_续传追加part且进度含起点(monkeypatch, tmp_path):
    """part 已有 3 字节，续传写 3 字节：进度必须是 6/6，完成后 os.replace"""
    save_path = str(tmp_path / "movie.bin")
    part_path, meta_path = save_path + ".part", save_path + ".jflove-part.json"
    with open(part_path, "wb") as f:
        f.write(b"ABC")
    meta = _write_meta(meta_path, 6, "6:100", 3)
    _install_frames(monkeypatch, [b"DEF"])
    reports: list[tuple[int, int]] = []
    responses = [
        _FakeResponse(200, {
            "X-Stream-Offset": "3",
            "X-Stream-Total": "6",
            "X-File-Version": "6:100",
        }),
    ]
    first_resp = responses[0]
    state = _install_responses(monkeypatch, responses)

    written = http_client.download_to_file(
        "/api/v1/files/download",
        {"disk_id": 1, "path": "movie.bin"},
        save_path=save_path,
        progress_callback=lambda done, total: reports.append((done, total)),
        range_start=3,
        expected_meta=meta,
        meta_path=meta_path,
    )

    assert written == 6
    assert reports == [(6, 6)]
    with open(save_path, "rb") as f:
        assert f.read() == b"ABCDEF"
    assert not os.path.exists(part_path)
    assert not os.path.exists(meta_path)
    assert state["send"] == 1
    assert first_resp.closed is True


def test_版本变化时删除part从头下载(monkeypatch, tmp_path):
    """响应 X-File-Version 与 meta 不一致 ⇒ 丢弃旧 part，重新请求偏移 0"""
    save_path = str(tmp_path / "movie.bin")
    part_path, meta_path = save_path + ".part", save_path + ".jflove-part.json"
    with open(part_path, "wb") as f:
        f.write(b"OLD")
    meta = _write_meta(meta_path, 6, "6:aaa", 3)
    _install_frames(monkeypatch, [b"NEW", b"FIL"])
    responses = [
        _FakeResponse(200, {
            "X-Stream-Offset": "3",
            "X-Stream-Total": "6",
            "X-File-Version": "6:bbb",
        }),
        _FakeResponse(200, {
            "X-Stream-Offset": "0",
            "X-Stream-Total": "6",
            "X-File-Version": "6:bbb",
        }),
    ]
    first_resp = responses[0]
    state = _install_responses(monkeypatch, responses)

    written = http_client.download_to_file(
        "/api/v1/files/download",
        {"disk_id": 1, "path": "movie.bin"},
        save_path=save_path,
        range_start=3,
        expected_meta=meta,
        meta_path=meta_path,
    )

    assert written == 6
    with open(save_path, "rb") as f:
        # 旧 part 的 OLD 绝不能被拼进结果
        assert f.read() == b"NEWFIL"
    assert state["send"] == 2
    assert first_resp.closed is True
    assert not os.path.exists(meta_path)


def test_416偏移越界自动回退(monkeypatch, tmp_path):
    """服务端 416 ⇒ 删除 part、从偏移 0 重新请求一次"""
    save_path = str(tmp_path / "movie.bin")
    part_path, meta_path = save_path + ".part", save_path + ".jflove-part.json"
    with open(part_path, "wb") as f:
        f.write(b"ABC")
    meta = _write_meta(meta_path, 6, "6:aaa", 3)
    _install_frames(monkeypatch, [b"ABC", b"DEF"])
    responses = [
        _FakeResponse(416, {}),
        _FakeResponse(200, {"X-Stream-Offset": "0", "X-Stream-Total": "6"}),
    ]
    first_resp = responses[0]
    state = _install_responses(monkeypatch, responses)

    written = http_client.download_to_file(
        "/api/v1/files/download",
        {"disk_id": 1, "path": "movie.bin"},
        save_path=save_path,
        range_start=3,
        expected_meta=meta,
        meta_path=meta_path,
    )

    assert written == 6
    with open(save_path, "rb") as f:
        assert f.read() == b"ABCDEF"
    assert state["send"] == 2
    assert first_resp.closed is True


def test_失败保留part与meta(monkeypatch, tmp_path):
    """流中途失败：part 保留、meta 记录实际偏移，供下次续传"""
    save_path = str(tmp_path / "movie.bin")
    part_path, meta_path = save_path + ".part", save_path + ".jflove-part.json"
    with open(part_path, "wb") as f:
        f.write(b"ABC")
    meta = _write_meta(meta_path, 10, "10:100", 3)
    _install_frames(monkeypatch, [b"DEF", ValueError("加密流被截断")])
    _install_responses(monkeypatch, [
        _FakeResponse(200, {
            "X-Stream-Offset": "3",
            "X-Stream-Total": "10",
            "X-File-Version": "10:100",
        }),
    ])

    with pytest.raises(ValueError):
        http_client.download_to_file(
            "/api/v1/files/download",
            {"disk_id": 1, "path": "movie.bin"},
            save_path=save_path,
            range_start=3,
            expected_meta=meta,
            meta_path=meta_path,
        )

    assert not os.path.exists(save_path)
    with open(part_path, "rb") as f:
        assert f.read() == b"ABCDEF"
    saved_meta = json_load(meta_path)
    assert saved_meta["offset"] == 6
    assert saved_meta["size"] == 10
    assert saved_meta["version"] == "10:100"


def test_用户取消删除part与meta(monkeypatch, tmp_path):
    """用户取消：part/meta 删除，抛 ValueError（调用方按取消处理）"""
    save_path = str(tmp_path / "movie.bin")
    part_path, meta_path = save_path + ".part", save_path + ".jflove-part.json"
    with open(part_path, "wb") as f:
        f.write(b"ABC")
    meta = _write_meta(meta_path, 6, "6:100", 3)
    flags = {"got": False}

    def fake_parse(_raw, _key):
        flags["got"] = True
        return b"DEF" if not flags.get("done") else None

    from src.utils import http_client as hc_module
    monkeypatch.setattr(hc_module, "parse_stream_frame", fake_parse)
    _install_responses(monkeypatch, [
        _FakeResponse(200, {
            "X-Stream-Offset": "3",
            "X-Stream-Total": "6",
            "X-File-Version": "6:100",
        }),
    ])

    with pytest.raises(ValueError):
        http_client.download_to_file(
            "/api/v1/files/download",
            {"disk_id": 1, "path": "movie.bin"},
            save_path=save_path,
            cancelled_flag=lambda: flags["got"],
            range_start=3,
            expected_meta=meta,
            meta_path=meta_path,
        )

    assert not os.path.exists(part_path)
    assert not os.path.exists(meta_path)
    assert not os.path.exists(save_path)


def test_download_file读取part续传(monkeypatch, tmp_path):
    """服务层读 part 大小作为 range_start，并透传 meta 供版本校验"""
    save_path = str(tmp_path / "movie.bin")
    part_path, meta_path = save_path + ".part", save_path + ".jflove-part.json"
    with open(part_path, "wb") as f:
        f.write(b"x" * 100)
    meta = _write_meta(meta_path, 1000, "1000:100", 100)
    captured: dict = {}

    def fake_download(endpoint, data=None, save_path="", progress_callback=None,
                      cancelled_flag=None, range_start=0, expected_meta=None,
                      meta_path="", discard_on_cancel=True):
        captured.update({
            "endpoint": endpoint, "data": data, "save_path": save_path,
            "range_start": range_start, "expected_meta": expected_meta,
            "meta_path": meta_path,
        })
        if progress_callback:
            progress_callback(1000, 1000)
        return 1000

    monkeypatch.setattr(file_service.http_client, "download_to_file", fake_download)

    written = file_service.download_file(1, "movie.bin", save_path)

    assert written == 1000
    assert captured["range_start"] == 100
    assert captured["expected_meta"] == meta
    assert captured["meta_path"] == meta_path
    assert captured["data"] == {"disk_id": 1, "path": "movie.bin"}


def test_part超过7天清理后从头下载(monkeypatch, tmp_path):
    """meta.updated_at 超过 7 天 ⇒ 删除 part/meta，range_start=0"""
    save_path = str(tmp_path / "movie.bin")
    part_path, meta_path = save_path + ".part", save_path + ".jflove-part.json"
    with open(part_path, "wb") as f:
        f.write(b"x" * 100)
    _write_meta(meta_path, 1000, "1000:100", 100,
                updated_at=time.time() - 8 * 24 * 3600)
    captured: dict = {}

    def fake_download(_endpoint, data=None, save_path="", progress_callback=None,
                      cancelled_flag=None, range_start=0, expected_meta=None,
                      meta_path="", discard_on_cancel=True):
        captured.update({
            "range_start": range_start, "expected_meta": expected_meta,
            "part_exists": os.path.exists(save_path + ".part"),
        })
        return 1000

    monkeypatch.setattr(file_service.http_client, "download_to_file", fake_download)

    file_service.download_file(1, "movie.bin", save_path)

    assert captured["range_start"] == 0
    assert captured["expected_meta"] is None
    assert captured["part_exists"] is False


def test_无meta的part不可信从头下载(monkeypatch, tmp_path):
    """part 存在但 meta 缺失 ⇒ 无法校验版本，删除 part 从头下载"""
    save_path = str(tmp_path / "movie.bin")
    part_path = save_path + ".part"
    with open(part_path, "wb") as f:
        f.write(b"x" * 100)
    captured: dict = {}

    def fake_download(_endpoint, data=None, save_path="", progress_callback=None,
                      cancelled_flag=None, range_start=0, expected_meta=None,
                      meta_path="", discard_on_cancel=True):
        captured.update({
            "range_start": range_start,
            "part_exists": os.path.exists(save_path + ".part"),
        })
        return 1000

    monkeypatch.setattr(file_service.http_client, "download_to_file", fake_download)

    file_service.download_file(1, "movie.bin", save_path)

    assert captured["range_start"] == 0
    assert captured["part_exists"] is False


def test_目标文件已完整时跳过下载(monkeypatch, tmp_path):
    """目标文件大小 == meta.size ⇒ 判定已完成，不发下载请求"""
    save_path = str(tmp_path / "movie.bin")
    with open(save_path, "wb") as f:
        f.write(b"x" * 100)
    meta_path = save_path + ".jflove-part.json"
    _write_meta(meta_path, 100, "100:1", 100)

    def fake_download(*_args, **_kwargs):
        raise AssertionError("不应重复下载已完整的目标文件")

    monkeypatch.setattr(file_service.http_client, "download_to_file", fake_download)

    assert file_service.download_file(1, "movie.bin", save_path) == 100
    assert not os.path.exists(meta_path)


def test_part已完整时仍请求一次校验版本(monkeypatch, tmp_path):
    """part 大小 == meta.size（上次改名之前中断）⇒ 仍带 range_start=size 请求，
    用响应头 X-File-Version 确认远端未变后才 os.replace（AC-336 同大小替换防护）"""
    save_path = str(tmp_path / "movie.bin")
    part_path, meta_path = save_path + ".part", save_path + ".jflove-part.json"
    with open(part_path, "wb") as f:
        f.write(b"x" * 100)
    _write_meta(meta_path, 100, "100:1", 100)
    captured: dict = {}

    def fake_download(_endpoint, data=None, save_path="", progress_callback=None,
                      cancelled_flag=None, range_start=0, expected_meta=None,
                      meta_path="", discard_on_cancel=True):
        captured.update({
            "range_start": range_start,
            "expected_meta": expected_meta,
        })
        # 模拟 http_client 在服务端返回 0 字节流 + 版本一致后的原子改名
        with open(save_path + ".part", "rb") as f:
            payload = f.read()
        with open(save_path, "wb") as f:
            f.write(payload)
        os.remove(save_path + ".part")
        os.remove(save_path + ".jflove-part.json")
        return 100

    monkeypatch.setattr(file_service.http_client, "download_to_file", fake_download)

    assert file_service.download_file(1, "movie.bin", save_path) == 100
    assert captured["range_start"] == 100
    assert captured["expected_meta"] is not None
    with open(save_path, "rb") as f:
        assert len(f.read()) == 100
    assert not os.path.exists(part_path)
    assert not os.path.exists(meta_path)


def json_load(path: str) -> dict:
    """读取 JSON 文件（测试辅助）"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
