"""
v1.7.0 Linux FUSE 系统级挂载集成测试

验证 `src/utils/mount_fuse.py` 的完整链路（真实挂载，非 mock）：
  挂载 → 目录可见在线文件 → 读取触发按需下载 → 内容一致 → 删除只删本地 → 停用后恢复普通目录。

对应需求 AC-102 / AC-103 / AC-104（系统挂载部分）。
无 FUSE 环境（非 Linux / 缺 fusepy / 缺 /dev/fuse）时自动跳过。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading

import pytest

from src.services import mount_sync_service
from src.utils import mount_fuse, mount_vfs

pytestmark = pytest.mark.skipif(
    not mount_fuse.is_available(), reason="本机不具备 FUSE 挂载能力",
)

_CONTENT = b"online-content-" * 32


def _rows(size: int) -> list[dict]:
    """构造索引条目：一个目录 + 一个未缓存文件"""
    return [
        {"rel_path": "docs", "size": 0, "mtime": 0.0, "is_dir": True},
        {"rel_path": "docs/a.txt", "size": size, "mtime": 1700000000.0, "is_dir": False},
    ]


def _mount(root: str, rows: list[dict], counter: list[str]) -> mount_fuse.LinuxFuseMount:
    """按给定索引挂载一个 FUSE 实例"""
    return mount_fuse.LinuxFuseMount(
        "test-mount",
        root,
        lambda rel: (counter.append(rel), _CONTENT)[1],
        listing=lambda: rows,
    )


def test_读取未缓存文件触发按需下载_内容一致():
    """getattr 来自索引，open/read 触发 fetch，内容必须逐字节一致"""
    root = tempfile.mkdtemp(prefix="jflove_fuse_test_")
    calls: list[str] = []
    vfs = _mount(root, _rows(len(_CONTENT)), calls)
    assert vfs.start() is True, "FUSE 挂载失败"
    try:
        assert sorted(os.listdir(root)) == ["docs"]
        assert sorted(os.listdir(os.path.join(root, "docs"))) == ["a.txt"]

        stat = os.stat(os.path.join(root, "docs/a.txt"))
        assert stat.st_size == len(_CONTENT)
        assert stat.st_mtime == pytest.approx(1700000000.0)

        with open(os.path.join(root, "docs/a.txt"), "rb") as f:
            assert f.read() == _CONTENT
        assert calls == ["docs/a.txt"], "首次读取必须恰好下载一次"
    finally:
        vfs.stop()
        shutil.rmtree(root, ignore_errors=True)


def test_已缓存文件不重复下载():
    """缓存命中后直接读本地，不再发起下载"""
    root = tempfile.mkdtemp(prefix="jflove_fuse_test_")
    calls: list[str] = []
    vfs = _mount(root, _rows(len(_CONTENT)), calls)
    assert vfs.start() is True
    try:
        path = os.path.join(root, "docs/a.txt")
        for _ in range(3):
            with open(path, "rb") as f:
                assert f.read() == _CONTENT
        assert len(calls) == 1
    finally:
        vfs.stop()
        shutil.rmtree(root, ignore_errors=True)


def test_并发读取同一文件只下载一次():
    """同一文件的并发 open 必须串行化，避免重复下载"""
    root = tempfile.mkdtemp(prefix="jflove_fuse_test_")
    calls: list[str] = []
    release = threading.Event()

    def slow_fetch(rel: str) -> bytes:
        release.wait(timeout=2)
        calls.append(rel)
        return _CONTENT

    vfs = mount_fuse.LinuxFuseMount(
        "test-concurrent", root, slow_fetch, listing=lambda: _rows(len(_CONTENT)),
    )
    assert vfs.start() is True
    try:
        results: list[bytes] = []

        def _read() -> None:
            with open(os.path.join(root, "docs/a.txt"), "rb") as f:
                results.append(f.read())

        threads = [threading.Thread(target=_read) for _ in range(4)]
        for t in threads:
            t.start()
        release.set()
        for t in threads:
            t.join(timeout=20)
        assert results == [_CONTENT] * 4
        assert len(calls) == 1
    finally:
        vfs.stop()
        shutil.rmtree(root, ignore_errors=True)


def test_删除只删本地缓存_远端索引不变():
    """unlink 只删本地缓存；索引条目仍在（远端不动，文件回到「仅在线」）"""
    root = tempfile.mkdtemp(prefix="jflove_fuse_test_")
    calls: list[str] = []
    rows = _rows(len(_CONTENT))
    vfs = _mount(root, rows, calls)
    assert vfs.start() is True
    try:
        path = os.path.join(root, "docs/a.txt")
        with open(path, "rb") as f:
            f.read()
        os.remove(path)
        # 本地缓存已删；索引没变 ⇒ 视图里它是「仅在线」
        assert os.path.isfile(os.path.join(root, "docs/a.txt"))
        assert rows[1]["rel_path"] == "docs/a.txt"
        assert calls == ["docs/a.txt"], "删除不得触发远端下载或删除"
    finally:
        vfs.stop()
    # 卸载后底层目录里确实没有该缓存文件
    assert not os.path.exists(os.path.join(root, "docs", "a.txt"))
    shutil.rmtree(root, ignore_errors=True)


def test_挂载目录内写入只落本地():
    """新建 / 写入只作用于本地缓存，远端不受影响"""
    root = tempfile.mkdtemp(prefix="jflove_fuse_test_")
    calls: list[str] = []
    vfs = _mount(root, _rows(len(_CONTENT)), calls)
    assert vfs.start() is True
    try:
        path = os.path.join(root, "local.txt")
        with open(path, "wb") as f:
            f.write(b"local-only")
        with open(path, "rb") as f:
            assert f.read() == b"local-only"
        os.remove(path)
    finally:
        vfs.stop()
    assert calls == []
    shutil.rmtree(root, ignore_errors=True)


def test_停用后目录恢复普通文件夹_可删():
    """stop 后卸载干净：不再是挂载点，且目录可直接删除"""
    root = tempfile.mkdtemp(prefix="jflove_fuse_test_")
    vfs = _mount(root, _rows(len(_CONTENT)), [])
    assert vfs.start() is True
    assert os.path.ismount(root) is True

    vfs.stop()

    assert os.path.ismount(root) is False
    with open(os.path.join(root, "plain.txt"), "wb") as f:
        f.write(b"plain")
    shutil.rmtree(root)
    assert not os.path.exists(root)


def test_重复start幂等():
    """重复调用 start 不重复挂载"""
    root = tempfile.mkdtemp(prefix="jflove_fuse_test_")
    vfs = _mount(root, _rows(len(_CONTENT)), [])
    try:
        assert vfs.start() is True
        assert mount_fuse._is_mountpoint(root) is True
        assert vfs.start() is True
        assert os.path.ismount(root) is True
    finally:
        vfs.stop()
        shutil.rmtree(root, ignore_errors=True)


def test_平台门面选择FUSE后端():
    """Linux 上门面必须走 fuse 后端且可用"""
    assert mount_vfs.backend() == "fuse"
    assert mount_vfs.is_available() is True
    root = tempfile.mkdtemp(prefix="jflove_gate_")
    try:
        assert isinstance(
            mount_vfs.create_mount("x", root, lambda r: b""),
            mount_fuse.LinuxFuseMount,
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_恢复已启用挂载点_跳过停用与异常(monkeypatch):
    """restore_mounts 只恢复 enabled 的挂载点，单个失败不影响其它"""
    called: list[str] = []

    def fake_enable(mount: dict) -> bool:
        called.append(mount["id"])
        if mount["id"] == "bad":
            raise RuntimeError("boom")
        return mount["id"] != "slow"

    monkeypatch.setattr(mount_sync_service, "enable_vfs", fake_enable)
    result = mount_sync_service.restore_mounts([
        {"id": "off", "enabled": False},
        {"id": "ok", "enabled": True},
        {"id": "bad", "enabled": True},
        {"id": "slow", "enabled": True},
    ])
    assert called == ["ok", "bad", "slow"]
    assert result == {"restored": 1, "failed": 2}
