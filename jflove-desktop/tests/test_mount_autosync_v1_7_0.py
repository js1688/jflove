"""
v1.7.0 云盘挂载自动同步（本地 → 远端 / 远端 → 本地）单测

覆盖：
  - 本地新增 / 修改 ⇒ 排队上传；
  - 内容未变 ⇒ 不重复上传；
  - 下载中的半成品（`.part` / `.jflove-part`）⇒ 不上传；
  - 正在传输 / 失败退避中的文件 ⇒ 不重复提交；
  - 远端删除 ⇒ 本地缓存一并删除；
  - 远端更新 ⇒ 本地旧缓存丢弃（下次读取重新下载）；
  - 自动同步线程可启动 / 停止且幂等。

用真实临时目录，不依赖 FUSE（`_vfs` 为空时自动退回普通路径）。
"""

from __future__ import annotations

import os
import time

import pytest

from src.services import mount_sync_service as sync
from src.utils import mount_index


@pytest.fixture()
def mount(tmp_path, monkeypatch) -> dict:
    """构造一个挂载点 + 隔离的本地索引目录"""
    monkeypatch.setattr(mount_index, "_DB_PATH", str(tmp_path / "mount_index.db"))
    local = tmp_path / "local"
    local.mkdir()
    return {
        "id": f"test-{tmp_path.name}",
        "disk_id": 1,
        "remote_path": "",
        "local_path": str(local),
        "enabled": True,
    }


@pytest.fixture()
def uploads(monkeypatch) -> list[tuple[int, str, str]]:
    """拦截上传提交，只记录参数"""
    calls: list[tuple[int, str, str]] = []

    def fake_submit(disk_id: int, rel_dir: str, local_path: str) -> str:
        calls.append((disk_id, rel_dir, local_path))
        return f"task-{len(calls)}"

    # `tasks` 是只读属性：整体替换成假对象即可（测试不关心真实任务队列）
    class _FakeManager:
        tasks: list = []

        submit_upload = staticmethod(fake_submit)

    monkeypatch.setattr(sync, "transfer_manager", _FakeManager())
    return calls


def _write(path, content: bytes, age: float = 10.0) -> None:
    """写入文件并把 mtime 调到「已稳定」"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))


def test_本地新增文件会排队上传(mount, uploads):
    _write(open_path := __import__("pathlib").Path(mount["local_path"]) / "a.txt", b"hello")

    assert sync._flush_uploads(mount) == 1
    assert uploads == [(1, "", str(open_path))]
    with sync._state_lock:
        assert "a.txt" in sync._syncing[mount["id"]]


def test_修改后的文件会再次上传(mount, uploads):
    local = __import__("pathlib").Path(mount["local_path"])
    _write(local / "a.txt", b"v1")
    assert sync._flush_uploads(mount) == 1
    with sync._state_lock:
        sync._syncing[mount["id"]].clear()

    _write(local / "a.txt", b"v2-longer")
    with sync._state_lock:
        sync._last_scan.pop(mount["id"], None)  # 解除兜底扫描节流
    # 索引里仍是旧大小 ⇒ 判定为「已修改」
    mount_index.upsert_files(mount["id"], [
        {"rel_path": "a.txt", "size": 2, "mtime": time.time() - 60, "rev": 1, "is_dir": False},
    ])
    assert sync._flush_uploads(mount) == 1
    assert len(uploads) == 2


def test_内容未变不重复上传(mount, uploads):
    local = __import__("pathlib").Path(mount["local_path"])
    _write(local / "a.txt", b"same")
    st = os.stat(local / "a.txt")
    mount_index.upsert_files(mount["id"], [
        {"rel_path": "a.txt", "size": st.st_size, "mtime": st.st_mtime, "rev": 1, "is_dir": False},
    ])
    assert sync._flush_uploads(mount) == 0
    assert uploads == []


def test_下载中的临时文件不上传(mount, uploads):
    from pathlib import Path

    local = Path(mount["local_path"])
    _write(local / "a.txt.part", b"half")
    _write(local / "b.txt.jflove-part", b"half")
    _write(local / "real.txt", b"done")

    assert sync._flush_uploads(mount) == 1
    assert [Path(c[2]).name for c in uploads] == ["real.txt"]


def test_正在传输的文件不重复提交(mount, uploads):
    from pathlib import Path

    _write(Path(mount["local_path"]) / "a.txt", b"x")
    with sync._state_lock:
        sync._syncing.setdefault(mount["id"], {})["a.txt"] = "task-running"
    assert sync._flush_uploads(mount) == 0
    assert uploads == []


def test_失败退避期内不重复提交(mount, uploads):
    from pathlib import Path

    _write(Path(mount["local_path"]) / "a.txt", b"x")
    with sync._state_lock:
        sync._retry_at.setdefault(mount["id"], {})["a.txt"] = time.monotonic() + 60
    assert sync._flush_uploads(mount) == 0
    assert uploads == []


def test_远端删除会删掉本地缓存(mount):
    from pathlib import Path

    path = Path(mount["local_path"]) / "gone.txt"
    _write(path, b"cached")
    sync._apply_remote_changes(mount, [], ["gone.txt"])
    assert not path.exists()


def test_远端更新会丢弃本地旧缓存(mount):
    from pathlib import Path

    path = Path(mount["local_path"]) / "a.txt"
    _write(path, b"old", age=100)
    st = os.stat(path)
    sync._apply_remote_changes(mount, [{
        "rel_path": "a.txt", "size": 3, "mtime": st.st_mtime + 50, "rev": 2, "is_dir": False,
    }], [])
    assert not path.exists()


def test_远端时间未变时保留本地缓存(mount):
    from pathlib import Path

    path = Path(mount["local_path"]) / "a.txt"
    _write(path, b"same", age=100)
    st = os.stat(path)
    sync._apply_remote_changes(mount, [{
        "rel_path": "a.txt", "size": st.st_size, "mtime": st.st_mtime, "rev": 1, "is_dir": False,
    }], [])
    assert path.exists()


def test_兜底扫描节流(mount, uploads):
    """没有变更通知时，5 秒内不重复整目录扫描"""
    from pathlib import Path

    _write(Path(mount["local_path"]) / "a.txt", b"x")
    assert sync._flush_uploads(mount) == 1
    _write(Path(mount["local_path"]) / "b.txt", b"y")
    assert sync._flush_uploads(mount) == 0      # 节流生效
    with sync._state_lock:
        sync._last_scan.pop(mount["id"], None)
    assert sync._flush_uploads(mount) == 1      # 节流解除后能发现 b.txt


def test_本地变更通知去重(mount):
    sync.note_local_change(mount["id"], "a.txt")
    sync.note_local_change(mount["id"], "a.txt")
    sync.note_local_change(mount["id"], "a.txt.part")
    with sync._state_lock:
        pending = dict(sync._pending.get(mount["id"], {}))
    assert list(pending) == ["a.txt"]
    sync.stop_auto_sync(mount["id"])


def test_自动同步线程可启动可停止(mount, monkeypatch):
    monkeypatch.setattr(sync, "refresh_mount", lambda m, quiet=False: {"applied": 0})
    monkeypatch.setattr(sync, "_SYNC_INTERVAL", 0.05)

    sync.start_auto_sync(mount)
    with sync._state_lock:
        worker = sync._sync_workers[mount["id"]]
    assert worker.is_alive()
    time.sleep(0.2)
    sync.start_auto_sync(mount)  # 幂等：不应新建第二个线程
    with sync._state_lock:
        assert sync._sync_workers[mount["id"]] is worker

    sync.stop_auto_sync(mount["id"])
    assert not worker.is_alive()
    assert mount["id"] not in sync._sync_workers


def test_本地新建文件在上传前就出现在列表里(mount, uploads):
    """可见性判据：尚未进索引的本地新文件必须能列出（state=pending）"""
    from pathlib import Path

    _write(Path(mount["local_path"]) / "fresh.txt", b"new")
    sync.note_local_change(mount["id"], "fresh.txt")

    rows = {r["rel_path"]: r for r in sync.list_files(mount)}
    assert "fresh.txt" in rows, "本地新文件必须立即可见"
    assert rows["fresh.txt"]["state"] == "pending"
    assert rows["fresh.txt"]["size"] == 3


def test_上传中的文件在列表里标记为uploading(mount, uploads):
    """正在上传：state=uploading，且带上方向与进度字段"""
    from pathlib import Path

    _write(Path(mount["local_path"]) / "up.txt", b"uploading")
    with sync._state_lock:
        sync._syncing.setdefault(mount["id"], {})["up.txt"] = "task-x"
        sync._dirty.setdefault(mount["id"], set()).add("up.txt")

    row = {r["rel_path"]: r for r in sync.list_files(mount)}["up.txt"]
    assert row["state"] == "uploading"
    assert row["direction"] == "upload"

    status = sync.status(mount)
    assert status["uploading"] == 1
    assert status["active"][0]["rel_path"] == "up.txt"


def test_水合期间标记为下载中(mount, monkeypatch):
    """读取在线文件时，界面必须能看到「下载中」（含文件名）"""
    import threading

    from src.services import file_service

    mount_index.upsert_files(mount["id"], [
        {"rel_path": "big.bin", "size": 10, "mtime": 0, "rev": 1, "is_dir": False},
    ])

    started = threading.Event()
    release = threading.Event()

    def slow_download(disk_id, rel_path, local_path, **kwargs):
        started.set()
        release.wait(timeout=5)
        with open(local_path, "wb") as f:
            f.write(b"0123456789")
        return 10

    monkeypatch.setattr(file_service, "download_file", slow_download)

    worker = threading.Thread(target=sync._fetch_content, args=(mount, "big.bin"))
    worker.start()
    assert started.wait(timeout=5)

    row = {r["rel_path"]: r for r in sync.list_files(mount)}["big.bin"]
    assert row["state"] == "downloading"
    assert row["direction"] == "download"
    assert sync.status(mount)["downloading"] == 1

    release.set()
    worker.join(timeout=5)
    row = {r["rel_path"]: r for r in sync.list_files(mount)}["big.bin"]
    assert row["state"] != "downloading"


def test_合并阶段标记为服务端处理中(mount, monkeypatch):
    """分片传完、服务端还在合并：必须与「上传中」区分，否则 100% 会被误认为已完成"""
    from pathlib import Path
    from types import SimpleNamespace

    _write(Path(mount["local_path"]) / "big.bin", b"x" * 10)

    class _Task:
        id = "task-fin"
        kind = SimpleNamespace(value="upload")
        status = SimpleNamespace(value="running")
        phase = "finalizing"
        percent = 100
        local_path = str(Path(mount["local_path"]) / "big.bin")
        error = ""
        # 合并已进行 90 秒：界面应显示「已 1 分 30 秒」，而不是判为卡住
        last_progress_at = time.monotonic() - 90
        phase_started_at = time.monotonic() - 90

    class _Mgr:
        tasks = [_Task()]

    monkeypatch.setattr(sync, "transfer_manager", _Mgr())
    with sync._state_lock:
        sync._syncing.setdefault(mount["id"], {})["big.bin"] = "task-fin"

    row = {r["rel_path"]: r for r in sync.list_files(mount)}["big.bin"]
    assert row["state"] == "finalizing"
    assert row["progress"] == 100
    assert row["stalled"] == 0, "合并阶段不能因为没百分比就判为卡住"
    assert row["elapsed"] >= 90, "合并阶段必须给出已耗时，让用户知道还在动"
    assert sync.status(mount)["finalizing"] == 1


def test_Windows脱水占位不参与上传(mount, uploads, monkeypatch):
    """
    CfAPI 的脱水占位也是「普通文件」。索引缺失时不能被当成本地上传，
    否则会先触发水合（整文件下载）再传回，纯属浪费。
    """
    from pathlib import Path

    path = Path(mount["local_path"]) / "cloud-only.bin"
    _write(path, b"placeholder")
    _write(Path(mount["local_path"]) / "local.bin", b"real")

    class _FakeVfs:
        """模拟 CfAPI：cloud-only.bin 未水合，local.bin 已水合"""

        def is_open_for_write(self, rel):  # noqa: ANN001, D401
            return False

        def is_cached(self, rel):  # noqa: ANN001
            return rel != "cloud-only.bin"

    monkeypatch.setitem(sync._vfs, mount["id"], _FakeVfs())
    with sync._state_lock:
        sync._last_scan.pop(mount["id"], None)

    assert sync._flush_uploads(mount) == 1
    assert [Path(c[2]).name for c in uploads] == ["local.bin"]


def test_全量核对不得删除本地文件(mount, monkeypatch):
    """
    数据安全回归：远端清单里没有 ≠ 远端已删除。

    全量核对（`reconcile_mount`）只能刷新索引；本地新建但尚未上传的文件
    必须保留（否则服务端目录配错 / 被重建时会误删用户数据）。
    """
    from pathlib import Path

    path = Path(mount["local_path"]) / "mine.bin"
    _write(path, b"local data")
    mount_index.upsert_files(mount["id"], [
        {"rel_path": "mine.bin", "size": 10, "mtime": 0, "rev": 1, "is_dir": False},
    ])

    monkeypatch.setattr(sync, "sync_placeholders", lambda m: 0)
    monkeypatch.setattr(sync.http_client, "post", lambda *a, **k: {
        "entries": [], "has_more": False, "latest_rev": 9,
    })

    sync.reconcile_mount(mount)

    assert path.exists(), "全量核对不得删除本地文件"
    assert mount_index.list_files(mount["id"]) == []


def test_远端删除时保留有改动的本地文件(mount):
    """远端删了、但本地有未上传改动：保留本地（数据优先）"""
    from pathlib import Path

    path = Path(mount["local_path"]) / "edited.txt"
    _write(path, b"my edit")
    with sync._state_lock:
        sync._dirty.setdefault(mount["id"], set()).add("edited.txt")

    sync._apply_remote_changes(mount, [], ["edited.txt"])
    assert path.exists()
