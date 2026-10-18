"""
桥的「云盘挂载」处理函数（v1.7.0）

替代原「同步管理」：挂载点 =（远端目录 → 本地缓存目录），
界面提供文件级状态（已缓存 / 仅在线）与按需下载 / 释放。

## 线程

全部方法都会读本地索引（sqlite3）或发网络请求，因此**走工作线程**；
`transfer_manager.submit_download` 只投递任务（Qt 信号跨线程安全），不阻塞。

## 与旧 sync.* 的关系

旧 `sync.*` 桥**保留**（过渡期兼容），但新界面只调用 `mount.*`。
旧同步配置在首次调用 `mount.list` 时自动迁移成挂载点（`enabled = 旧 auto_sync`）。
"""

from __future__ import annotations

from typing import Any, Callable

from src.services import mount_service, mount_sync_service
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _status_of(mount: dict) -> dict:
    """安全地取挂载点状态（索引尚未建立时返回零值）"""
    try:
        return mount_sync_service.status(mount)
    except Exception as e:  # noqa: BLE001 - 状态统计失败不应阻断列表
        logger.warning("读取挂载点状态失败：%s", type(e).__name__)
        return {"total": 0, "cached": 0, "online": 0, "local_path": mount.get("local_path", "")}


def _h_mount_list(_p: dict) -> dict:
    """列出全部挂载点（含状态统计）"""
    mounts = mount_service.list_mounts()
    return {
        "mounts": [
            {**m, "status": _status_of(m) if m.get("enabled") else {
                "total": 0,
                "cached": 0,
                "online": 0,
                "local_path": m.get("local_path", ""),
            }}
            for m in mounts
        ]
    }


def _h_mount_create(p: dict) -> dict:
    """新建挂载点（默认关闭）"""
    mount_id = mount_service.create_mount(
        name=str(p.get("name", "")),
        disk_id=int(p.get("disk_id", 0)),
        remote_path=str(p.get("remote_path", "")),
        local_path=str(p.get("local_path", "")),
    )
    return {"id": mount_id}


def _h_mount_update(p: dict) -> dict:
    """更新挂载点"""
    mount_service.update_mount(
        mount_id=str(p["mount_id"]),
        name=str(p.get("name", "")),
        disk_id=int(p.get("disk_id", 0)),
        remote_path=str(p.get("remote_path", "")),
        local_path=str(p.get("local_path", "")),
    )
    return {"ok": True}


def _h_mount_delete(p: dict) -> dict:
    """删除挂载点（不动本地文件与远端）"""
    mount_id = str(p["mount_id"])
    mount_service.delete_mount(mount_id)
    from src.utils import mount_index

    mount_index.reset_mount(mount_id)
    return {"ok": True}


def _h_mount_set_enabled(p: dict) -> dict:
    """
    启用 / 停用挂载点。

    启用时会尝试注册 CfAPI 系统级挂载（Windows）；失败则自动降级为
    应用内视图（`system_mount=false`，由界面提示）。
    """
    mount_id = str(p["mount_id"])
    enabled = bool(p.get("enabled", False))
    mount_service.set_enabled(mount_id, enabled)
    if not enabled:
        mount_sync_service.disable_vfs(mount_id)
        return {"ok": True, "system_mount": False}

    mount = mount_service.get_mount(mount_id)
    try:
        mount_sync_service.refresh_mount(mount)
    except Exception as e:  # noqa: BLE001 - 拉取失败不应阻止启用
        logger.warning("启用挂载点时拉取增量失败：%s", type(e).__name__)
    system_mount = mount_sync_service.enable_vfs(mount)
    return {"ok": True, "system_mount": system_mount}


def _h_mount_refresh(p: dict) -> dict:
    """拉增量并更新索引"""
    mount = mount_service.get_mount(str(p["mount_id"]))
    if not mount.get("enabled"):
        raise ValueError("挂载点未启用，请先打开开关")
    result = mount_sync_service.refresh_mount(mount)
    return {**result, "status": _status_of(mount)}


def _h_mount_reconcile(p: dict) -> dict:
    """全量对账（兜底 API 外变更）"""
    mount = mount_service.get_mount(str(p["mount_id"]))
    if not mount.get("enabled"):
        raise ValueError("挂载点未启用，请先打开开关")
    result = mount_sync_service.reconcile_mount(mount)
    return {**result, "status": _status_of(mount)}


def _h_mount_files(p: dict) -> dict:
    """列出挂载点的文件与缓存状态"""
    mount = mount_service.get_mount(str(p["mount_id"]))
    return {"files": mount_sync_service.list_files(mount)}


def _h_mount_download(p: dict) -> dict:
    """把远端文件下载到本地缓存目录"""
    mount = mount_service.get_mount(str(p["mount_id"]))
    return mount_sync_service.download_file(mount, str(p.get("rel_path", "")))


def _h_mount_release(p: dict) -> dict:
    """释放本地缓存（不影响远端）"""
    mount = mount_service.get_mount(str(p["mount_id"]))
    return mount_sync_service.release_file(mount, str(p.get("rel_path", "")))


def _h_mount_status(p: dict) -> dict:
    """取单个挂载点的状态统计"""
    mount = mount_service.get_mount(str(p["mount_id"]))
    return _status_of(mount)


#: 白名单登记表：{service: {method: 处理函数}}
HANDLERS: dict[str, dict[str, Callable[[dict], Any]]] = {
    "mount": {
        "list": _h_mount_list,
        "create": _h_mount_create,
        "update": _h_mount_update,
        "delete": _h_mount_delete,
        "set_enabled": _h_mount_set_enabled,
        "refresh": _h_mount_refresh,
        "reconcile": _h_mount_reconcile,
        "files": _h_mount_files,
        "download": _h_mount_download,
        "release": _h_mount_release,
        "status": _h_mount_status,
    },
}
