"""
挂载点配置服务（v1.7.0）

按账号隔离的本地 JSON：`mounts_{username}.json`。
配置只存客户端本地，服务端不感知（需求 D12）。

迁移：首次读取时把旧的 `sync_configs_{username}.json` 转成挂载点
（`enabled` 取旧的 `auto_sync`），旧文件保留不删，便于回滚。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from src.config.settings import LOCAL_STORAGE_DIR
from src.services import sync_service
from src.utils.logger import get_logger
from src.utils.session import session_manager

logger = get_logger(__name__)


def _mounts_path() -> str:
    """按当前登录账号返回挂载点配置文件路径"""
    username = (session_manager.username or "").strip()
    filename = f"mounts_{username}.json" if username else "mounts_default.json"
    return os.path.join(LOCAL_STORAGE_DIR, filename)


def _migration_marker() -> str:
    """迁移完成标记文件路径（每个账号一份）"""
    return _mounts_path() + ".migrated"


def _read() -> list[dict]:
    """读取挂载点列表；文件不存在或损坏时返回空列表"""
    path = _mounts_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("mounts", []) if isinstance(data, dict) else []
    except Exception as e:  # noqa: BLE001
        logger.warning("读取挂载点配置失败：%s", type(e).__name__)
        return []


def _write(mounts: list[dict]) -> None:
    """原子写入挂载点列表"""
    path = _mounts_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"mounts": mounts}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _migrate_if_needed(mounts: list[dict]) -> list[dict]:
    """
    首次访问时把旧同步配置迁移为挂载点。

    :param mounts: 已存在的挂载点列表（可能为空）
    :returns: 迁移后的列表
    """
    marker = _migration_marker()
    if os.path.exists(marker):
        return mounts

    existing_ids = {m.get("id") for m in mounts}
    migrated = 0
    try:
        for old in sync_service.list_configs():
            old_id = str(old.get("id") or "")
            if not old_id or old_id in existing_ids:
                continue
            mounts.append({
                "id": old_id,
                "name": old.get("name") or "迁移的同步目录",
                "disk_id": int(old.get("disk_id") or 0),
                "remote_path": str(old.get("remote_path") or ""),
                "local_path": str(old.get("local_path") or ""),
                # 旧配置的 auto_sync = 新模型的「启用」
                "enabled": bool(old.get("auto_sync")),
                "migrated": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            migrated += 1
    except Exception as e:  # noqa: BLE001
        # 迁移失败不能让挂载点列表不可用；标记不写，下次再试
        logger.warning("迁移旧同步配置失败：%s", type(e).__name__)
        return mounts

    if migrated:
        _write(mounts)
        logger.info("已把 %s 条旧同步配置迁移为挂载点", migrated)

    try:
        with open(marker, "w", encoding="utf-8") as f:
            f.write(datetime.now(timezone.utc).isoformat())
    except OSError:
        pass
    return mounts


def list_mounts() -> list[dict]:
    """列出全部挂载点（首次调用会触发旧配置迁移）"""
    return _migrate_if_needed(_read())


def get_mount(mount_id: str) -> dict:
    """
    取单个挂载点。

    :raises ValueError: 挂载点不存在
    """
    for m in list_mounts():
        if m.get("id") == mount_id:
            return m
    raise ValueError(f"挂载点不存在：{mount_id}")


def create_mount(
    name: str,
    disk_id: int,
    remote_path: str,
    local_path: str,
) -> str:
    """
    新建挂载点。

    按用户裁决：新挂载点**默认关闭**（enabled=False），由用户手动打开。

    :param name: 显示名
    :param disk_id: 远端虚拟磁盘 ID
    :param remote_path: 磁盘内子目录（空 = 根）
    :param local_path: 本地缓存目录
    :returns: 挂载点 ID
    """
    if not name.strip():
        raise ValueError("名称不能为空")
    if not local_path.strip():
        raise ValueError("本地目录不能为空")
    if disk_id <= 0:
        raise ValueError("远端磁盘不能为空")

    mount_id = uuid.uuid4().hex[:8]
    mounts = list_mounts()
    mounts.append({
        "id": mount_id,
        "name": name.strip(),
        "disk_id": int(disk_id),
        "remote_path": (remote_path or "").strip().strip("/"),
        "local_path": local_path.strip(),
        "enabled": False,
        "migrated": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    _write(mounts)
    logger.info("创建挂载点：id=%s name=%s", mount_id, name)
    return mount_id


def update_mount(
    mount_id: str,
    name: str,
    disk_id: int,
    remote_path: str,
    local_path: str,
) -> None:
    """
    更新挂载点（不改变启用状态）。

    :raises ValueError: 挂载点不存在
    """
    mounts = list_mounts()
    for m in mounts:
        if m.get("id") == mount_id:
            m.update({
                "name": name.strip(),
                "disk_id": int(disk_id),
                "remote_path": (remote_path or "").strip().strip("/"),
                "local_path": local_path.strip(),
            })
            _write(mounts)
            return
    raise ValueError(f"挂载点不存在：{mount_id}")


def set_enabled(mount_id: str, enabled: bool) -> None:
    """
    启用 / 停用挂载点。

    :raises ValueError: 挂载点不存在
    """
    mounts = list_mounts()
    for m in mounts:
        if m.get("id") == mount_id:
            m["enabled"] = bool(enabled)
            _write(mounts)
            return
    raise ValueError(f"挂载点不存在：{mount_id}")


def delete_mount(mount_id: str) -> None:
    """
    删除挂载点配置（不删除本地已缓存文件，也不影响远端）。

    :raises ValueError: 挂载点不存在
    """
    mounts = list_mounts()
    before = len(mounts)
    mounts = [m for m in mounts if m.get("id") != mount_id]
    if len(mounts) == before:
        raise ValueError(f"挂载点不存在：{mount_id}")
    _write(mounts)
    logger.info("删除挂载点：id=%s", mount_id)
