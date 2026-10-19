"""
v1.7.0 云盘挂载桥：卸载时序单测

聚焦「什么时候必须先解除系统挂载」——这两条在 FUSE 上代价最明显
（挂载随进程存活，配置删掉后就没有入口能停用它）。
"""

from __future__ import annotations

import pytest

from src.bridge import mount as mount_bridge
from src.services import mount_service, mount_sync_service
from src.utils import mount_index


def test_删除挂载点先卸载再删配置(monkeypatch):
    """disable_vfs 必须发生在 delete_mount 之前"""
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        mount_sync_service, "disable_vfs",
        lambda mid: events.append(("disable", mid)),
    )
    monkeypatch.setattr(
        mount_service, "delete_mount",
        lambda mid: events.append(("delete", mid)),
    )
    monkeypatch.setattr(mount_index, "reset_mount", lambda mid: events.append(("reset", mid)))

    assert mount_bridge._h_mount_delete({"mount_id": "m1"}) == {"ok": True}
    assert events == [("disable", "m1"), ("delete", "m1"), ("reset", "m1")]


def test_更新本地目录后按新目录重挂(monkeypatch):
    """本地目录变了：先卸载旧挂载点，再按新目录挂回"""
    state = {"local_path": "/old", "enabled": True}
    events: list[str] = []

    monkeypatch.setattr(mount_service, "get_mount", lambda mid: {"id": mid, **state})

    def fake_update(**kwargs) -> None:
        state["local_path"] = kwargs["local_path"]

    monkeypatch.setattr(mount_service, "update_mount", fake_update)
    monkeypatch.setattr(mount_sync_service, "disable_vfs", lambda mid: events.append("disable"))
    monkeypatch.setattr(
        mount_sync_service, "enable_vfs",
        lambda mount: events.append("enable") or True,
    )

    mount_bridge._h_mount_update({
        "mount_id": "m1",
        "name": "n",
        "disk_id": 1,
        "remote_path": "",
        "local_path": "/new",
    })
    assert events == ["disable", "enable"]


def test_只改名字不重挂(monkeypatch):
    """本地目录没变：不得打断已生效的挂载"""
    state = {"local_path": "/same", "enabled": True}
    events: list[str] = []

    monkeypatch.setattr(mount_service, "get_mount", lambda mid: {"id": mid, **state})
    monkeypatch.setattr(mount_service, "update_mount", lambda **kwargs: None)
    monkeypatch.setattr(mount_sync_service, "disable_vfs", lambda mid: events.append("disable"))
    monkeypatch.setattr(mount_sync_service, "enable_vfs", lambda mount: events.append("enable"))

    mount_bridge._h_mount_update({
        "mount_id": "m1",
        "name": "renamed",
        "disk_id": 1,
        "remote_path": "",
        "local_path": "/same",
    })
    assert events == []


@pytest.mark.parametrize("enabled", [False])
def test_未启用时改目录不触发挂载(monkeypatch, enabled):
    """没启用的挂载点：更新不应触发任何挂载动作"""
    state = {"local_path": "/old", "enabled": enabled}
    events: list[str] = []

    monkeypatch.setattr(mount_service, "get_mount", lambda mid: {"id": mid, **state})

    def fake_update(**kwargs) -> None:
        state["local_path"] = kwargs["local_path"]

    monkeypatch.setattr(mount_service, "update_mount", fake_update)
    monkeypatch.setattr(mount_sync_service, "disable_vfs", lambda mid: events.append("disable"))
    monkeypatch.setattr(mount_sync_service, "enable_vfs", lambda mount: events.append("enable"))

    mount_bridge._h_mount_update({
        "mount_id": "m1",
        "name": "n",
        "disk_id": 1,
        "remote_path": "",
        "local_path": "/new",
    })
    assert events == []
