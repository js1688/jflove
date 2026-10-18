"""
挂载点同步服务（v1.7.0）

职责：
  - `refresh_mount`：拉增量（`/api/v1/files/journal`）并写入本地索引
  - `reconcile_mount`：全量对账（`/api/v1/files/scan`），兜底"API 外变更"
  - `list_files`：读索引 + 本地存在性 ⇒ `cached` 状态
  - `download_file`：提交传输任务（复用 `transfer_manager`）
  - `release_file`：删除本地缓存（不影响远端）
  - `status`：统计已缓存 / 仅在线数量

设计说明：
  - 「是否已缓存」**由本地文件系统推导**（`os.path.isfile`），不额外维护状态列，
    避免"索引说已缓存、磁盘上却没有"的不一致；
  - 所有函数均为同步阻塞，调用方（桥）应在工作线程执行。
"""

from __future__ import annotations

import os
import tempfile

from src.services import file_service
from src.utils import mount_index, mount_vfs
from src.utils.http_client import http_client
from src.utils.logger import get_logger
from src.utils.transfer_manager import transfer_manager

logger = get_logger(__name__)

#: 已启用的 CfAPI 会话（mount_id → WinCfapiMount）
_vfs: dict[str, object] = {}

#: 增量拉取的最大轮数（防御服务端 has_more 异常导致死循环）
_MAX_PAGES = 50


def _prefix_of(mount: dict) -> str:
    """挂载点的远端前缀（已去首尾斜杠）"""
    return (mount.get("remote_path") or "").strip().strip("/")


def _to_rel(path: str, prefix: str) -> str | None:
    """
    把"相对磁盘根"的路径转成"相对挂载点根"的路径。

    :returns: 相对路径；不属于该前缀时返回 None
    """
    if not prefix:
        return path.lstrip("/")
    if path == prefix:
        return None
    boundary = prefix + "/"
    if path.startswith(boundary):
        return path[len(boundary):]
    return None


def _local_path(mount: dict, rel_path: str) -> str:
    """相对路径 → 本地绝对路径"""
    return os.path.join(
        mount["local_path"],
        rel_path.replace("/", os.sep),
    )


def refresh_mount(mount: dict) -> dict:
    """
    拉取增量并更新索引。

    :param mount: 挂载点配置字典
    :returns: `{applied, deleted, latest_rev, reset_required}`
    """
    mount_id = mount["id"]
    disk_id = int(mount["disk_id"])
    prefix = _prefix_of(mount)
    since = mount_index.get_last_rev(mount_id)

    # 同一路径可能多次变更：以最后一次为准
    final: dict[str, dict | None] = {}
    latest = since
    reset_required = False

    for _ in range(_MAX_PAGES):
        resp = http_client.post("/api/v1/files/journal", {
            "disk_id": disk_id,
            "prefix": prefix,
            "since_rev": since,
            "limit": 500,
        })
        if resp.get("reset_required"):
            reset_required = True
            break

        changes = resp.get("changes") or []
        for c in changes:
            rel = _to_rel(str(c.get("path", "")), prefix)
            if rel is None:
                continue
            if c.get("op") == "delete":
                final[rel] = None
            else:
                final[rel] = {
                    "rel_path": rel,
                    "size": int(c.get("size") or 0),
                    "mtime": float(c.get("mtime") or 0),
                    "rev": int(c.get("rev") or 0),
                    "is_dir": bool(c.get("is_dir")),
                }

        latest = int(resp.get("latest_rev") or latest)
        if not resp.get("has_more") or not changes:
            break
        since = latest

    if reset_required:
        # 游标过旧：清空索引并全量重建
        mount_index.reset_mount(mount_id)
        return reconcile_mount(mount)

    upserts = [v for v in final.values() if v is not None]
    deletes = [k for k, v in final.items() if v is None]
    mount_index.upsert_files(mount_id, upserts)
    mount_index.delete_files(mount_id, deletes)
    mount_index.set_last_rev(mount_id, latest)

    logger.info(
        "挂载点增量同步：id=%s 更新=%s 删除=%s rev=%s",
        mount_id, len(upserts), len(deletes), latest,
    )
    sync_placeholders(mount)
    return {
        "applied": len(upserts),
        "deleted": len(deletes),
        "latest_rev": latest,
        "reset_required": False,
    }


def reconcile_mount(mount: dict) -> dict:
    """
    全量对账：用 `/files/scan` 重建索引（发现绕过 API 直接写入磁盘的变更）。

    :param mount: 挂载点配置字典
    :returns: `{applied, deleted, latest_rev, reset_required}`
    """
    mount_id = mount["id"]
    disk_id = int(mount["disk_id"])
    prefix = _prefix_of(mount)

    cursor = ""
    seen: set[str] = set()
    rows: list[dict] = []
    latest = 0

    for _ in range(200):
        resp = http_client.post("/api/v1/files/scan", {
            "disk_id": disk_id,
            "prefix": prefix,
            "cursor": cursor,
            "limit": 2000,
        })
        for e in resp.get("entries") or []:
            rel = _to_rel(str(e.get("path", "")), prefix)
            if rel is None:
                continue
            seen.add(rel)
            rows.append({
                "rel_path": rel,
                "size": int(e.get("size") or 0),
                "mtime": float(e.get("mtime") or 0),
                "rev": 0,
                "is_dir": bool(e.get("is_dir")),
            })
        latest = int(resp.get("latest_rev") or latest)
        if not resp.get("has_more"):
            break
        cursor = str(resp.get("next_cursor") or "")
        if not cursor:
            break

    mount_index.upsert_files(mount_id, rows)
    existing = {r["rel_path"] for r in mount_index.list_files(mount_id)}
    stale = [p for p in existing if p not in seen]
    mount_index.delete_files(mount_id, stale)
    mount_index.set_last_rev(mount_id, latest)

    logger.info(
        "挂载点全量对账：id=%s 文件=%s 清掉=%s rev=%s",
        mount_id, len(rows), len(stale), latest,
    )
    sync_placeholders(mount)
    return {
        "applied": len(rows),
        "deleted": len(stale),
        "latest_rev": latest,
        "reset_required": False,
    }


def list_files(mount: dict) -> list[dict]:
    """
    列出挂载点的全部远端文件，并标记本地是否已缓存。

    :param mount: 挂载点配置字典
    :returns: 每项含 rel_path / size / mtime / is_dir / cached
    """
    result: list[dict] = []
    for row in mount_index.list_files(mount["id"]):
        local = _local_path(mount, row["rel_path"])
        result.append({
            **row,
            "cached": os.path.isfile(local),
            "local_path": local,
        })
    return result


def download_file(mount: dict, rel_path: str) -> dict:
    """
    提交下载任务（把远端文件缓存到本地目录）。

    :param mount: 挂载点配置字典
    :param rel_path: 相对挂载点根的路径
    :returns: `{task_id, local_path}`
    :raises ValueError: 文件不在索引中
    """
    target = next(
        (r for r in mount_index.list_files(mount["id"]) if r["rel_path"] == rel_path),
        None,
    )
    if target is None:
        raise ValueError("文件不在挂载点索引中，请先刷新")

    local = _local_path(mount, rel_path)
    parent = os.path.dirname(local)
    if parent:
        os.makedirs(parent, exist_ok=True)

    remote_rel = "/".join(
        p for p in [_prefix_of(mount), rel_path] if p
    )
    task_id = transfer_manager.submit_download(
        int(mount["disk_id"]),
        remote_rel,
        local,
        filename=os.path.basename(rel_path),
        file_size=int(target.get("size") or 0),
        remote_mtime=float(target.get("mtime") or 0),
    )
    return {"task_id": task_id, "local_path": local}


def release_file(mount: dict, rel_path: str) -> dict:
    """
    释放本地缓存（删除本地文件，**不影响远端**）。

    :param mount: 挂载点配置字典
    :param rel_path: 相对挂载点根的路径
    :returns: `{removed}`
    """
    local = _local_path(mount, rel_path)
    removed = False
    if os.path.isfile(local):
        os.remove(local)
        removed = True

    # 清理变空的父目录（最多向上 5 级，且不得越过挂载点根）
    root = os.path.abspath(mount["local_path"])
    parent = os.path.dirname(local)
    for _ in range(5):
        if not os.path.isdir(parent) or os.path.abspath(parent) == root:
            break
        try:
            if os.listdir(parent):
                break
            os.rmdir(parent)
        except OSError:
            break
        parent = os.path.dirname(parent)

    return {"removed": removed}


def _fetch_content(mount: dict, rel_path: str) -> bytes:
    """
    CfAPI 回调用：取远端文件完整内容（经加密下载通道）。

    :param mount: 挂载点配置
    :param rel_path: 相对挂载点根的路径
    :returns: 文件字节
    """
    remote_rel = "/".join(p for p in [_prefix_of(mount), rel_path] if p)
    fd, tmp = tempfile.mkstemp(prefix="jflove_vfs_")
    os.close(fd)
    try:
        file_service.download_file(int(mount["disk_id"]), remote_rel, tmp)
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def enable_vfs(mount: dict) -> bool:
    """
    启用 CfAPI 系统级挂载（Windows）。

    :param mount: 挂载点配置
    :returns: 是否成功（失败时调用方应降级为应用内视图）
    """
    if not mount_vfs.is_available():
        return False
    mid = mount["id"]
    if _vfs.get(mid) is not None:
        return True

    vfs = mount_vfs.WinCfapiMount(
        mid,
        mount["local_path"],
        lambda rel: _fetch_content(mount, rel),
    )
    if not vfs.start():
        return False
    _vfs[mid] = vfs
    sync_placeholders(mount)
    logger.info("CfAPI 系统挂载已启用：id=%s", mid)
    return True


def disable_vfs(mount_id: str) -> None:
    """停用 CfAPI 挂载（断开连接 + 注销 sync root）"""
    vfs = _vfs.pop(mount_id, None)
    if vfs is None:
        return
    try:
        vfs.stop()
    except Exception as e:  # noqa: BLE001
        logger.warning("停止 CfAPI 挂载失败：%s", type(e).__name__)


def disable_all_vfs() -> None:
    """
    解除**全部** CfAPI 挂载。

    必须在应用退出前调用：只结束进程而不注销，目录会继续被 Cloud Filter 接管，
    表现为资源管理器进不去、也删不掉（v1.7.0 实测缺陷）。
    """
    for mount_id in list(_vfs.keys()):
        disable_vfs(mount_id)


def sync_placeholders(mount: dict) -> int:
    """
    按索引把远端文件补成占位（幂等：已存在的跳过）。

    :returns: 本次新增的占位数
    """
    vfs = _vfs.get(mount["id"])
    if vfs is None:
        return 0
    count = 0
    for row in mount_index.list_files(mount["id"]):
        if row["is_dir"]:
            continue
        if vfs.add_file(row["rel_path"], int(row["size"]), float(row["mtime"])):
            count += 1
    return count


def status(mount: dict) -> dict:
    """
    统计挂载点状态。

    :returns: `{total, cached, online, local_path}`
    """
    rows = list_files(mount)
    cached = sum(1 for r in rows if r["cached"])
    return {
        "total": len(rows),
        "cached": cached,
        "online": len(rows) - cached,
        "local_path": mount.get("local_path", ""),
    }
