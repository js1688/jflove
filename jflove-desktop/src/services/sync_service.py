"""
同步配置服务（客户端 v1.2.0 账号隔离版）

v1.2.0 变更：
  - 同步配置文件按账号隔离，每个登录用户使用独立的 sync_configs_{username}.json，
    避免 A 账号看到 B 账号的配置
  - 未登录时使用 sync_configs_default.json 作为兜底

v1.1.6 变更：
  - 所有同步配置改为客户端本地 JSON 文件管理
  - 不再调服务端 CRUD 接口（服务端已移除 sync_configs 表）
  - create_config/update_config/delete_config 仅读写本地文件
  - get_remote_snapshot 改为 POST /api/v1/sync/snapshot（传 disk_id + remote_path）
  - touch_synced 改为更新本地 last_synced_at 字段

设计约束：
  - 所有 HTTP 调用统一走 http_client
  - 本地文件使用原子写入（tmp 文件 → os.replace）
  - 每个配置使用 uuid 短 ID（8 位 hex），单客户端内唯一即可
"""

import json
import os
import uuid
from datetime import datetime, timezone

from src.config.settings import LOCAL_STORAGE_DIR
from src.utils.http_client import http_client
from src.utils.logger import get_logger
from src.utils.session import session_manager

logger = get_logger(__name__)

# ── 本地存储文件路径（按账号隔离） ──────────────────


def _get_sync_configs_path() -> str:
    """
    获取当前账号对应的同步配置文件路径。

    每个登录用户拥有独立的 sync_configs_{username}.json，避免 A 账号看到 B 账号的配置。
    未登录时使用 sync_configs_default.json 作为兜底。
    """
    username = (session_manager.username or "").strip()
    if not username:
        filename = "sync_configs_default.json"
    else:
        filename = f"sync_configs_{username}.json"
    return os.path.join(LOCAL_STORAGE_DIR, filename)


# ── 本地文件读写 ──────────────────────────────────


def _read_local_configs() -> list[dict]:
    """
    从当前账号对应的本地 JSON 文件读取全部同步配置。

    :returns: 配置列表，文件不存在或损坏时返回空列表
    """
    path = _get_sync_configs_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("configs", []) if isinstance(data, dict) else []
    except Exception as e:
        logger.warning("读取 %s 失败：%s", os.path.basename(path), e)
        return []


def _write_local_configs(configs: list[dict]) -> None:
    """
    将同步配置列表原子写入当前账号对应的本地 JSON 文件。

    :param configs: 配置列表
    """
    path = _get_sync_configs_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"configs": configs}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ── CRUD（全本地操作） ────────────────────────────


def list_configs() -> list[dict]:
    """
    获取本地全部同步配置。

    :returns: 配置 dict 列表，每项含：
        id / name / local_path / disk_id / remote_path /
        auto_sync / sync_interval / last_synced_at / enabled
    """
    return _read_local_configs()


def create_config(
    name: str,
    local_path: str,
    disk_id: int,
    remote_path: str = "",
    auto_sync: bool = False,
    sync_interval: int = 300,
) -> str:
    """
    创建同步配置（仅写本地 JSON，不调服务端）。

    :param name: 用户起的别名
    :param local_path: 本地目录绝对路径
    :param disk_id: 远端虚拟磁盘 ID
    :param remote_path: 磁盘内子目录相对路径，默认为根目录
    :param auto_sync: 是否启用自动同步
    :param sync_interval: 自动同步间隔秒数（最小 30）
    :returns: 本地配置 ID（8 位 hex 字符串）
    """
    cfg_id = uuid.uuid4().hex[:8]
    configs = _read_local_configs()
    configs.append({
        "id": cfg_id,
        "name": name,
        "local_path": local_path,
        "disk_id": disk_id,
        "remote_path": remote_path,
        "auto_sync": auto_sync,
        "sync_interval": sync_interval,
        "enabled": True,
        "last_synced_at": None,
    })
    _write_local_configs(configs)
    logger.info("创建同步配置：id=%s name=%s local_path=%s", cfg_id, name, local_path)
    return cfg_id


def update_config(
    config_id: str,
    name: str,
    local_path: str,
    disk_id: int,
    remote_path: str,
    auto_sync: bool,
    sync_interval: int,
    enabled: bool,
) -> None:
    """
    更新同步配置（仅写本地 JSON，不调服务端）。

    :param config_id: 本地配置 ID
    """
    configs = _read_local_configs()
    found = False
    for c in configs:
        if c["id"] == config_id:
            c.update({
                "name": name,
                "local_path": local_path,
                "disk_id": disk_id,
                "remote_path": remote_path,
                "auto_sync": auto_sync,
                "sync_interval": sync_interval,
                "enabled": enabled,
            })
            found = True
            break
    if not found:
        raise ValueError(f"同步配置不存在：{config_id}")
    _write_local_configs(configs)
    logger.info("更新同步配置：id=%s name=%s", config_id, name)


def delete_config(config_id: str) -> None:
    """
    删除同步配置（仅删本地 JSON，不调服务端）。

    :param config_id: 本地配置 ID
    """
    configs = _read_local_configs()
    before = len(configs)
    configs = [c for c in configs if c["id"] != config_id]
    if len(configs) == before:
        raise ValueError(f"同步配置不存在：{config_id}")
    _write_local_configs(configs)
    logger.info("删除同步配置：id=%s", config_id)


# ── 远端快照 ──────────────────────────────────────


def get_remote_snapshot(disk_id: int, remote_path: str) -> list[dict]:
    """
    获取远端目录快照（递归扫描）。

    v1.1.6 改为 POST /api/v1/sync/snapshot，参数直接传 disk_id + remote_path。

    :param disk_id: 虚拟磁盘 ID
    :param remote_path: 磁盘内子目录相对路径
    :returns: 文件清单，每项：path（相对路径）/ size / modified_at
    """
    resp = http_client.post("/api/v1/sync/snapshot", {
        "disk_id": disk_id,
        "remote_path": remote_path,
    })
    return resp.get("files", [])


# ── touch 本地化 ──────────────────────────────────


def touch_synced(config_id: str) -> None:
    """
    标记一次性同步完成（更新本地 last_synced_at）。

    v1.1.6 不再调服务端 touch 接口，改为纯本地记录。

    :param config_id: 本地配置 ID
    """
    configs = _read_local_configs()
    now = datetime.now(timezone.utc).isoformat()
    found = False
    for c in configs:
        if c["id"] == config_id:
            c["last_synced_at"] = now
            found = True
            break
    if found:
        _write_local_configs(configs)
