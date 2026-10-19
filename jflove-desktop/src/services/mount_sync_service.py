"""
挂载点同步服务（v1.7.0）

职责：
  - `refresh_mount`：拉增量（`/api/v1/files/journal`）并写入本地索引
  - `reconcile_mount`：全量对账（`/api/v1/files/scan`），兜底"API 外变更"
  - `list_files`：读索引 + 本地存在性 ⇒ `cached` 状态（挂载生效时问挂载实例）
  - `download_file`：提交传输任务（复用 `transfer_manager`）
  - `release_file`：删除本地缓存（不影响远端）
  - `status`：统计已缓存 / 仅在线 / 同步中数量
  - 自动同步（v1.7.0 修复）：`start_auto_sync` / `stop_auto_sync`

自动同步语义（**自动、无需任何按钮**）：
  | 变化 | 行为 |
  | --- | --- |
  | 远端新增 / 修改 | 进入索引；本地缓存过期则删除，下次读取重新下载 |
  | 远端删除 | 本地缓存一并删除 |
  | 本地新增 / 修改（挂载目录内） | 排队上传到远端（FUSE 写入回调 + 定时扫描双保险） |
  | **本地删除 / 删目录** | **只删本地缓存，绝不回传远端**（远端删除只能在文件管理页做） |

设计说明：
  - 「是否已缓存」**由本地文件系统推导**（挂载生效时问挂载实例），不额外维护状态列，
    避免"索引说已缓存、磁盘上却没有"的不一致；
  - 所有函数均为同步阻塞，调用方（桥）应在工作线程执行；自动同步线程内部同样阻塞。
"""

from __future__ import annotations

import os
import stat
import tempfile
import threading
import time

from src.services import file_service
from src.utils import mount_index, mount_vfs
from src.utils.http_client import http_client
from src.utils.logger import get_logger
from src.utils.transfer_manager import transfer_manager

logger = get_logger(__name__)

#: 已启用的系统级挂载会话（mount_id → CfAPI / FUSE 实例）
_vfs: dict[str, object] = {}

#: 自动同步线程（mount_id → _AutoSync）
_sync_workers: dict[str, "_AutoSync"] = {}

#: 本地变更待上传（mount_id → {rel_path: 发现时刻 monotonic}）
_pending: dict[str, dict[str, float]] = {}

#: 正在传输（mount_id → {rel_path: task_id}）
_syncing: dict[str, dict[str, str]] = {}

#: 上传失败后的退避（mount_id → {rel_path: 可重试时刻 monotonic}）
_retry_at: dict[str, dict[str, float]] = {}

#: 上次兜底扫描时刻（mount_id → monotonic）
_last_scan: dict[str, float] = {}

#: 最近一次拉取内容（mount_id → {rel_path: monotonic}）
_recent_fetch: dict[str, dict[str, float]] = {}

#: 已知「本地有新改动、等待上传」的文件（mount_id → set(rel_path)）
_dirty: dict[str, set[str]] = {}

#: 正在水合（读取触发的下载）的文件（mount_id → set(rel_path)）
_hydrating: dict[str, set[str]] = {}

#: 上传失败的最近原因（mount_id → {rel_path: error}）
_failed: dict[str, dict[str, str]] = {}

_state_lock = threading.RLock()

#: 增量拉取的最大轮数（防御服务端 has_more 异常导致死循环）
_MAX_PAGES = 50

#: 自动同步轮询间隔（秒）
_SYNC_INTERVAL = 2.0
#: 远端增量拉取间隔（秒）
_PULL_INTERVAL = 10.0
#: 兜底全量核对间隔（秒）：清理「远端已被绕过 API 删除、本地索引残留」的条目
_RECONCILE_INTERVAL = 900.0
#: 本地文件需要稳定这么久才上传（秒），避免上传写了一半的文件
_SETTLE_SECONDS = 2.0
#: 上传失败后的重试退避（秒）
_RETRY_BACKOFF = 30.0
#: 兜底扫描间隔（秒）：FUSE 有写回调，扫描只是兜底；CfAPI 没有写回调，扫描是唯一信号
_SCAN_INTERVAL_FUSE = 30.0
_SCAN_INTERVAL_POLL = 10.0
#: 刚水合 / 刚下载的文件在这段时间内不参与上传（防止把「下载」误判成「本地修改」）
_FETCH_GRACE = 20.0
#: 传输多久没有进展就提示「等待中」（秒）
_STALL_SECONDS = 60.0
#: 临时文件后缀（下载中的半成品，不参与上传）
_TEMP_SUFFIXES = (".part", ".jflove-part")


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


def _local_root(mount: dict, vfs: object | None = None) -> str:
    """
    本地缓存的**真实**根目录。

    挂载生效时不能用 `mount["local_path"]`：FUSE 下那是视图本身，
    直接遍历会递归进文件系统。挂载实例提供 `local_root()` 指向底层目录。
    """
    vfs = vfs if vfs is not None else _vfs.get(mount["id"])
    if vfs is not None:
        try:
            root = vfs.local_root()
            if root:
                return root
        except Exception as e:  # noqa: BLE001 - 取不到就退回普通路径
            logger.warning("读取挂载底层目录失败：%s", type(e).__name__)
    return mount["local_path"]


def _backing_path(mount: dict, rel_path: str, vfs: object | None = None) -> str:
    """相对路径 → 本地**底层**绝对路径（挂载生效时绕过 FUSE 视图）"""
    vfs = vfs if vfs is not None else _vfs.get(mount["id"])
    if vfs is not None:
        try:
            return vfs.real_path(rel_path)
        except Exception as e:  # noqa: BLE001
            logger.warning("解析底层路径失败 rel=%s: %s", rel_path, type(e).__name__)
    return _local_path(mount, rel_path)


def _iter_local_files(root: str):
    """遍历本地缓存里的普通文件（跳过下载中的临时文件）"""
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.endswith(_TEMP_SUFFIXES):
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if rel.startswith("../"):
                continue
            yield rel, st


def _remove_local(mount: dict, rel_path: str) -> bool:
    """删除本地缓存文件（并清理空父目录），**不动远端**"""
    path = _backing_path(mount, rel_path)
    if not os.path.isfile(path):
        return False
    try:
        os.remove(path)
    except OSError as e:
        logger.warning("删除本地缓存失败 rel=%s: %s", rel_path, type(e).__name__)
        return False

    root = os.path.abspath(_local_root(mount))
    parent = os.path.dirname(path)
    for _ in range(5):
        if os.path.abspath(parent) == root:
            break
        try:
            if os.listdir(parent):
                break
            os.rmdir(parent)
        except OSError:
            break
        parent = os.path.dirname(parent)
    return True


def note_local_change(mount_id: str, rel_path: str) -> None:
    """
    记录「本挂载点内某文件被新建 / 修改」。

    由 FUSE 写回调在文件系统线程调用，必须**极快返回**：只记录时间戳，
    真正的上传在自动同步线程里做。
    """
    rel = (rel_path or "").strip("/")
    if not rel or rel.endswith(_TEMP_SUFFIXES):
        return
    with _state_lock:
        _pending.setdefault(mount_id, {}).setdefault(rel, time.monotonic())
        _dirty.setdefault(mount_id, set()).add(rel)
        _failed.get(mount_id, {}).pop(rel, None)


def _mark_fetched(mount_id: str, rel_path: str) -> None:
    """记录「刚把远端内容拉到本地」的时刻，供上传判定排他"""
    with _state_lock:
        _recent_fetch.setdefault(mount_id, {})[rel_path] = time.monotonic()


def _recently_fetched(mount_id: str, rel_path: str) -> bool:
    """该文件是否刚被水合 / 下载过（此时不应当作本地修改上传）"""
    with _state_lock:
        stamp = _recent_fetch.get(mount_id, {}).get(rel_path)
    return bool(stamp and time.monotonic() - stamp < _FETCH_GRACE)


def _scan_interval(vfs: object | None) -> float:
    """FUSE 有写回调 ⇒ 扫描可稀疏；其余平台（含 CfAPI）依赖扫描发现变更"""
    if vfs is not None and mount_vfs.backend() == "fuse":
        return _SCAN_INTERVAL_FUSE
    return _SCAN_INTERVAL_POLL


def _is_busy(mount_id: str, rel_path: str) -> bool:
    """该文件是否正在传输或处于失败退避中"""
    with _state_lock:
        if rel_path in _syncing.get(mount_id, {}):
            return True
        until = _retry_at.get(mount_id, {}).get(rel_path)
    return bool(until and time.monotonic() < until)


def _reap_transfers(mount: dict) -> None:
    """
    回收已结束的传输任务。

    成功：用本地 size/mtime 更新索引，避免下一轮又被判成「本地新增」；
    失败：退避一段时间再重试，避免每 2 秒重复提交。
    """
    mount_id = mount["id"]
    tasks = {t.id: t for t in transfer_manager.tasks}
    done: list[tuple[str, str]] = []
    with _state_lock:
        current = dict(_syncing.get(mount_id, {}))

    for rel, task_id in current.items():
        task = tasks.get(task_id)
        if task is None:
            done.append((rel, "gone"))
            continue
        status = getattr(task.status, "value", task.status)
        if status == "completed":
            path = _backing_path(mount, rel)
            try:
                st = os.stat(path)
                existing = next(
                    (r for r in mount_index.list_files(mount_id) if r["rel_path"] == rel),
                    None,
                )
                mount_index.upsert_files(mount_id, [{
                    "rel_path": rel,
                    "size": int(st.st_size),
                    "mtime": st.st_mtime,
                    "rev": int((existing or {}).get("rev") or 0),
                    "is_dir": False,
                }])
            except OSError:
                pass
            done.append((rel, "completed"))
        elif status in ("failed", "cancelled"):
            done.append((rel, status))

    for rel, reason in done:
        with _state_lock:
            _syncing.get(mount_id, {}).pop(rel, None)
            if reason in ("failed", "cancelled"):
                _retry_at.setdefault(mount_id, {})[rel] = (
                    time.monotonic() + _RETRY_BACKOFF
                )
                _dirty.setdefault(mount_id, set()).add(rel)
                if reason == "failed":
                    task = next(
                        (t for t in transfer_manager.tasks
                         if t.local_path == _backing_path(mount, rel)),
                        None,
                    )
                    _failed.setdefault(mount_id, {})[rel] = (
                        (getattr(task, "error", "") or "上传失败").strip()
                    )
            else:
                _dirty.get(mount_id, set()).discard(rel)
                _failed.get(mount_id, {}).pop(rel, None)
    if done:
        logger.debug("传输回收：%s", done)


def _flush_uploads(mount: dict) -> int:
    """
    把本地新增 / 修改的文件排队上传到远端。

    :returns: 本轮新提交的任务数
    """
    mount_id = mount["id"]
    vfs = _vfs.get(mount_id)
    root = _local_root(mount, vfs)
    if not root or not os.path.isdir(root):
        return 0

    mono = time.monotonic()
    with _state_lock:
        has_pending = bool(_pending.get(mount_id))
        last_scan = _last_scan.get(mount_id, 0.0)
    # 有明确变更通知就立刻处理；否则按兜底间隔整目录扫描
    if not has_pending and mono - last_scan < _scan_interval(vfs):
        return 0
    with _state_lock:
        _last_scan[mount_id] = mono

    rows = {r["rel_path"]: r for r in mount_index.list_files(mount_id)}
    now = time.time()
    submitted = 0

    for rel, st in _iter_local_files(root):
        if _is_busy(mount_id, rel):
            with _state_lock:
                _dirty.setdefault(mount_id, set()).add(rel)
            continue
        if vfs is not None and getattr(vfs, "is_open_for_write", None) is not None:
            try:
                if vfs.is_open_for_write(rel):
                    continue
            except Exception:  # noqa: BLE001 - 判断失败按可上传处理
                pass

        # Windows CfAPI：脱水占位是「普通文件」，但它没有本地内容。
        # 若索引缺失/被重置，扫描会把它当成本地文件 —— 一旦上传就会先触发水合
        # （整文件下载）再传回，纯属浪费。未水合的占位一律跳过。
        if vfs is not None and getattr(vfs, "is_cached", None) is not None:
            try:
                if not vfs.is_cached(rel):
                    continue
            except Exception:  # noqa: BLE001 - 判定失败按可上传处理
                pass

        with _state_lock:
            first_seen = _pending.get(mount_id, {}).get(rel)
        # 文件刚写完 / 刚被通知：等它稳定，避免上传半截内容
        if now - st.st_mtime < _SETTLE_SECONDS:
            continue
        if first_seen is not None and mono - first_seen < _SETTLE_SECONDS:
            continue

        row = rows.get(rel)
        # 刚水合 / 刚下载的文件：CfAPI 水合可能刷新本地 mtime，不能据此判定「本地修改」
        if _recently_fetched(mount_id, rel) and (
            row is None or int(row["size"]) == int(st.st_size)
        ):
            continue
        if row is not None and not row["is_dir"]:
            unchanged = (
                int(row["size"]) == int(st.st_size)
                and abs(float(row["mtime"]) - st.st_mtime) < 1.0
            )
            if unchanged:
                with _state_lock:
                    _pending.get(mount_id, {}).pop(rel, None)
                    _dirty.get(mount_id, set()).discard(rel)
                continue

        remote_dir = "/".join(
            p for p in [_prefix_of(mount), os.path.dirname(rel)] if p
        )
        local = os.path.join(root, rel.replace("/", os.sep))
        try:
            task_id = transfer_manager.submit_upload(
                int(mount["disk_id"]), remote_dir, local,
            )
        except Exception as e:  # noqa: BLE001 - 单个文件失败不应中断整轮
            logger.warning("提交上传失败 rel=%s: %s", rel, type(e).__name__)
            with _state_lock:
                _retry_at.setdefault(mount_id, {})[rel] = mono + _RETRY_BACKOFF
            continue

        with _state_lock:
            _syncing.setdefault(mount_id, {})[rel] = task_id
            _pending.get(mount_id, {}).pop(rel, None)
            _dirty.setdefault(mount_id, set()).add(rel)
            _failed.get(mount_id, {}).pop(rel, None)
        submitted += 1
        logger.info("本地变更已排队上传：%s", rel)

    return submitted


def _apply_remote_changes(
    mount: dict,
    upserts: list[dict],
    deletes: list[str],
) -> None:
    """
    把远端变化落到本地缓存，保证「本地 == 远端」。

    - 远端删除 ⇒ 本地缓存删除；
    - 远端修改（服务端 mtime 更新）⇒ 本地缓存过期，删除后下次读取重新下载。
    """
    mount_id = mount["id"]
    vfs = _vfs.get(mount_id)
    with _state_lock:
        dirty = set(_dirty.get(mount_id, set()))
    for rel in deletes:
        if _is_busy(mount_id, rel):
            continue
        if rel in dirty:
            # 本地有尚未上传的改动：宁可保留本地文件（下一轮会重新上传），也不删用户数据
            logger.info("远端已删除但本地有改动，保留本地文件：%s", rel)
            continue
        _remove_local(mount, rel)

    for row in upserts:
        if row.get("is_dir"):
            continue
        rel = row["rel_path"]
        if _is_busy(mount_id, rel):
            continue
        path = _backing_path(mount, rel, vfs)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        remote_mtime = float(row.get("mtime") or 0)
        if remote_mtime > st.st_mtime + 1.0:
            logger.info("远端已更新，丢弃本地旧缓存：%s", rel)
            _remove_local(mount, rel)


def refresh_mount(mount: dict, quiet: bool = False) -> dict:
    """
    拉取增量并更新索引，同时把远端变化落到本地缓存。

    :param mount: 挂载点配置字典
    :param quiet: True 表示由自动同步线程调用，不打印 INFO 日志
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
    _apply_remote_changes(mount, upserts, deletes)

    if not quiet:
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


def reconcile_mount(mount: dict, quiet: bool = False) -> dict:
    """
    全量对账：用 `/files/scan` 重建索引（发现绕过 API 直接写入磁盘的变更）。

    :param mount: 挂载点配置字典
    :param quiet: True 表示由自动同步线程调用，不打印 INFO 日志
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
            if rel is None or rel in ("", "."):
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
    # ⚠ 只更新索引，**不删本地文件**：
    # 「远端清单里没有」既可能是远端删除（应删本地缓存），也可能是
    #   - 本地新建、尚未上传的文件（绝不能删！）
    #   - 服务端磁盘路径配错 / 目录被重建（更不能删！）
    # 真正「远端删除」以 journal 的 delete 事件为准（见 `_apply_remote_changes` 调用点），
    # 那里才允许删除本地缓存。宁可把本地文件重新上传一次，也不能误删用户数据。
    _apply_remote_changes(mount, rows, [])

    if not quiet:
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


def _is_cached(mount: dict, rel_path: str, local: str) -> bool:
    """
    判断文件是否已缓存到本地。

    系统挂载生效时**必须问挂载实例**：FUSE / CfAPI 的 stat 会命中远端索引，
    对「仅在线」文件也返回文件类型，`os.path.isfile` 恒为 True。
    """
    vfs = _vfs.get(mount["id"])
    if vfs is not None:
        try:
            return bool(vfs.is_cached(rel_path))
        except Exception as e:  # noqa: BLE001 - 判定失败退回文件系统探测
            logger.warning("查询缓存状态失败 rel=%s: %s", rel_path, type(e).__name__)
    return os.path.isfile(local)


def _transfer_progress() -> dict[str, int]:
    """任务 ID → 进度百分比（仅进行中的任务）"""
    result: dict[str, int] = {}
    for task in transfer_manager.tasks:
        status = getattr(task.status, "value", task.status)
        if status in ("pending", "hashing", "running"):
            result[task.id] = task.percent
    return result


def list_files(mount: dict) -> list[dict]:
    """
    列出挂载点的全部远端文件，并标记本地状态与传输进度。

    :param mount: 挂载点配置字典
    :returns: 每项含 rel_path / size / mtime / is_dir / cached / state / progress /
              direction / error

    `state` 取值：
      - `synced`      已同步（本地有实体，且与远端一致）
      - `pending`     待上传（本地有改动，排队中）
      - `hashing`     校验中（上传前先算 SHA256；进度按字节计，到 100% 后**才开始上传**）
      - `uploading`   上传中（`progress` 为 0-100）
      - `finalizing`  服务端处理中（分片已传完，正在合并 + 校验；大文件可能持续数十秒）
      - `downloading` 下载中（读取触发的按需下载，或手动预取）
      - `failed`      传输失败（`error` 为原因，30s 后自动重试）
      - `online`      在线（本地无实体，读取时自动下载）
    """
    mount_id = mount["id"]
    with _state_lock:
        syncing = dict(_syncing.get(mount_id, {}))
        dirty = set(_dirty.get(mount_id, set()))
        hydrating = set(_hydrating.get(mount_id, set()))
        failed = dict(_failed.get(mount_id, {}))

    tasks = {t.id: t for t in transfer_manager.tasks}
    progress = _transfer_progress()

    rows = mount_index.list_files(mount_id)
    # 关键：**本地新建、尚未上传完成的文件还不在索引里**。
    # 不补出来的话，用户完全看不到「正在上传什么」。
    known = {r["rel_path"] for r in rows}
    for rel in sorted((dirty | set(syncing) | set(failed)) - known):
        path = _backing_path(mount, rel)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        rows.append({
            "rel_path": rel,
            "size": int(st.st_size),
            "mtime": st.st_mtime,
            "rev": 0,
            "is_dir": False,
        })

    result: list[dict] = []
    for row in sorted(rows, key=lambda r: r["rel_path"]):
        rel = row["rel_path"]
        local = _local_path(mount, rel)
        task_id = syncing.get(rel)
        task = tasks.get(task_id) if task_id else None
        kind = getattr(getattr(task, "kind", None), "value", "upload")
        task_status = getattr(getattr(task, "status", None), "value", "")

        if rel in hydrating:
            state, direction, percent = "downloading", "download", 0
        elif task_id is not None:
            if kind == "download":
                state, direction = "downloading", "download"
            elif task_status == "hashing":
                # 校验阶段进度也会到 100%，不单独标出来会被误认为「传完了」
                state, direction = "hashing", "upload"
            elif getattr(task, "phase", "") == "finalizing":
                # 分片已传完：服务端正在合并/校验，远端目录此时还看不到成品
                state, direction = "finalizing", "upload"
            else:
                state, direction = "uploading", "upload"
            percent = progress.get(task_id, 0)
        elif rel in failed:
            state, direction, percent = "failed", "upload", 0
        elif rel in dirty:
            state, direction, percent = "pending", "upload", 0
        elif _is_cached(mount, rel, local):
            state, direction, percent = "synced", "", 100
        else:
            state, direction, percent = "online", "", 0

        stalled_for = 0
        elapsed = 0
        if task is not None:
            now = time.monotonic()
            if state == "finalizing":
                # 合并阶段服务端无法上报百分比：显示「已处理 N 秒」，不判为卡住
                elapsed = int(now - getattr(task, "phase_started_at", now))
            elif state in ("hashing", "uploading", "downloading"):
                last = getattr(task, "last_progress_at", 0.0)
                if last and now - last > _STALL_SECONDS:
                    stalled_for = int(now - last)

        result.append({
            **row,
            "cached": state in ("synced", "uploading", "pending", "failed"),
            "state": state,
            "direction": direction,
            "progress": percent,
            "error": failed.get(rel, ""),
            "stalled": stalled_for,
            "elapsed": elapsed,
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

    # 写底层路径：挂载生效时 `local_path` 是 FUSE 视图，绕开可避免自触发
    local = _backing_path(mount, rel_path)
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
    with _state_lock:
        _syncing.setdefault(mount["id"], {})[rel_path] = task_id
    _mark_fetched(mount["id"], rel_path)
    return {"task_id": task_id, "local_path": _local_path(mount, rel_path)}


def release_file(mount: dict, rel_path: str) -> dict:
    """
    释放本地缓存（删除本地文件，**不影响远端**）。

    :param mount: 挂载点配置字典
    :param rel_path: 相对挂载点根的路径
    :returns: `{removed}`
    """
    # 必须用底层路径：挂载生效时 `local_path` 是 FUSE 视图，
    # `os.path.isfile` 会命中索引、对「仅在线」文件也返回 True。
    removed = _remove_local(mount, rel_path)
    return {"removed": removed}


def _fetch_content(mount: dict, rel_path: str) -> bytes:
    """
    系统挂载回调用：取远端文件完整内容（经加密下载通道）。

    :param mount: 挂载点配置
    :param rel_path: 相对挂载点根的路径
    :returns: 文件字节
    """
    remote_rel = "/".join(p for p in [_prefix_of(mount), rel_path] if p)
    fd, tmp = tempfile.mkstemp(prefix="jflove_vfs_")
    os.close(fd)
    with _state_lock:
        _hydrating.setdefault(mount["id"], set()).add(rel_path)
    try:
        file_service.download_file(int(mount["disk_id"]), remote_rel, tmp)
        _mark_fetched(mount["id"], rel_path)
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        with _state_lock:
            _hydrating.get(mount["id"], set()).discard(rel_path)
        try:
            os.remove(tmp)
        except OSError:
            pass


def enable_vfs(mount: dict) -> bool:
    """
    启用挂载点：启动自动同步，并在可能时建立系统级挂载。

    自动同步（上传本地新增 / 修改、把远端变化落到本地）**不依赖**系统挂载：
    即使 CfAPI / FUSE 不可用，挂载点的本地目录也应保持与远端一致。

    :param mount: 挂载点配置
    :returns: 是否建立了系统级挂载（False 时上层降级为应用内视图）
    """
    mid = mount["id"]
    start_auto_sync(mount)

    if not mount_vfs.is_available():
        return False

    if _vfs.get(mid) is not None:
        return True

    vfs = mount_vfs.create_mount(
        mid,
        mount["local_path"],
        lambda rel: _fetch_content(mount, rel),
        listing=lambda: mount_index.list_files(mid),
        on_local_change=lambda rel: note_local_change(mid, rel),
    )
    if not vfs.start():
        return False
    _vfs[mid] = vfs
    sync_placeholders(mount)
    logger.info("系统挂载已启用：id=%s backend=%s", mid, mount_vfs.backend())
    return True


def restore_mounts(mounts: list[dict]) -> dict:
    """
    恢复「配置为已启用」的挂载点。

    CfAPI 注册会留在系统里，FUSE 挂载则随进程消失；进程异常退出还会留下
    不可访问的死挂载点。进入挂载页时调用本函数，可把状态拉回配置期望：
    `enable_vfs` 内部会先清理残留挂载再重新挂载。

    :param mounts: 挂载点配置列表
    :returns: `{restored, failed}`
    """
    restored = 0
    failed = 0
    for mount in mounts:
        if not mount.get("enabled"):
            continue
        if _vfs.get(mount.get("id", "")) is not None:
            continue
        try:
            ok = enable_vfs(mount)
        except Exception as e:  # noqa: BLE001 - 单个挂载点失败不应拖垮列表
            logger.warning("恢复挂载点失败 id=%s: %s",
                           mount.get("id"), type(e).__name__)
            ok = False
        if ok:
            restored += 1
        else:
            failed += 1
    if restored or failed:
        logger.info("恢复已启用的系统挂载：成功=%s 失败=%s", restored, failed)
    return {"restored": restored, "failed": failed}


def disable_vfs(mount_id: str) -> None:
    """停用挂载点：先停自动同步，再解除系统级挂载"""
    stop_auto_sync(mount_id)
    vfs = _vfs.pop(mount_id, None)
    if vfs is None:
        return
    try:
        vfs.stop()
    except Exception as e:  # noqa: BLE001
        logger.warning("停止系统挂载失败：%s", type(e).__name__)


def disable_all_vfs() -> None:
    """
    解除**全部**系统级挂载。

    必须在应用退出前调用：只结束进程而不注销，Windows 上目录会继续被
    Cloud Filter 接管（资源管理器进不去、也删不掉，v1.7.0 实测缺陷）；
    Linux 上则会残留 FUSE 挂载点，同样不可用。
    """
    for mount_id in list(_vfs.keys()):
        disable_vfs(mount_id)
    for mount_id in list(_sync_workers.keys()):
        stop_auto_sync(mount_id)


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
    统计挂载点状态（供界面显示「还有多少没同步」）。

    :returns: `{total, synced, online, pending, uploading, downloading, failed,
               local_path, active}`

    `active` 是正在传输的文件列表（最多 20 条）：
    `{rel_path, direction: upload|download, progress, size, error}`。
    """
    rows = [r for r in list_files(mount) if not r["is_dir"]]
    counts = {
        "synced": 0, "online": 0, "pending": 0,
        "hashing": 0, "uploading": 0, "finalizing": 0, "downloading": 0, "failed": 0,
    }
    active: list[dict] = []
    for row in rows:
        state = row["state"]
        if state in counts:
            counts[state] += 1
        if state in ("hashing", "uploading", "finalizing", "downloading"):
            active.append({
                "rel_path": row["rel_path"],
                "direction": row["direction"],
                "progress": row["progress"],
                "size": row["size"],
                "error": "",
                "stalled": row.get("stalled", 0),
                "elapsed": row.get("elapsed", 0),
            })
        elif state == "failed":
            active.append({
                "rel_path": row["rel_path"],
                "direction": "upload",
                "progress": 0,
                "size": row["size"],
                "error": row.get("error", ""),
            })

    active.sort(key=lambda a: (a["direction"], a["rel_path"]))
    return {
        "total": len(rows),
        # 兼容旧字段：cached 现在等于「已同步」
        "cached": counts["synced"],
        **counts,
        "local_path": mount.get("local_path", ""),
        "active": active[:20],
        "active_total": len(active),
    }


class _AutoSync(threading.Thread):
    """
    单个挂载点的自动同步线程。

    每 `_SYNC_INTERVAL` 秒：
      1. 回收已结束的传输任务；
      2. 每 `_PULL_INTERVAL` 秒拉一次远端增量并落到本地；
      3. 扫描本地新增 / 修改并排队上传。

    「删除」不参与上传：FUSE 侧删除不回调，扫描也只看存在的文件。
    """

    def __init__(self, mount: dict):
        super().__init__(daemon=True, name=f"jflove-sync-{mount['id']}")
        self.mount_id = mount["id"]
        self._mount = mount
        self._stop = threading.Event()
        self._last_pull = 0.0
        self._last_reconcile = time.monotonic()
        # 启动后先做一次全量核对：索引可能残留「远端已删除」的条目（journal 重放），
        # 那些条目会让本地真实改动被误判成「未变化」而永远不上传。
        self._need_initial_sync = True

    def run(self) -> None:
        logger.info("自动同步已启动：id=%s", self.mount_id)
        while not self._stop.wait(_SYNC_INTERVAL):
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001 - 线程不能因单轮失败退出
                logger.warning("自动同步失败 id=%s: %s",
                               self.mount_id, type(e).__name__)
        logger.info("自动同步已停止：id=%s", self.mount_id)

    def _tick(self) -> None:
        from src.utils.session import session_manager

        if not session_manager.is_logged_in():
            return
        if self._need_initial_sync:
            self._need_initial_sync = False
            try:
                reconcile_mount(self._mount, quiet=True)
                logger.info("启动全量核对完成：id=%s", self.mount_id)
            except Exception as e:  # noqa: BLE001 - 断网时保持本地可用
                logger.warning("启动全量核对失败 id=%s: %s",
                               self.mount_id, type(e).__name__)
                self._need_initial_sync = True
        if time.monotonic() - self._last_pull >= _PULL_INTERVAL:
            self._last_pull = time.monotonic()
            try:
                refresh_mount(self._mount, quiet=True)
            except Exception as e:  # noqa: BLE001 - 断网时保持本地可用
                logger.warning("拉取远端增量失败 id=%s: %s",
                               self.mount_id, type(e).__name__)
        if time.monotonic() - self._last_reconcile >= _RECONCILE_INTERVAL:
            self._last_reconcile = time.monotonic()
            try:
                # 低频全量核对：兜底「远端被绕过 API 修改 / 删除」造成的索引残留
                reconcile_mount(self._mount, quiet=True)
            except Exception as e:  # noqa: BLE001
                logger.warning("全量核对失败 id=%s: %s",
                               self.mount_id, type(e).__name__)
        _reap_transfers(self._mount)
        _flush_uploads(self._mount)

    def stop(self) -> None:
        """请求停止并等待线程退出（幂等）"""
        self._stop.set()
        if self.is_alive():
            self.join(timeout=5.0)


def ensure_auto_sync() -> int:
    """
    保证「配置为已启用」的挂载点都已挂载并运行自动同步。

    供应用启动 / 定时兜底调用：挂载与同步**不依赖用户打开挂载页**
    （否则用户以为在同步，实际什么都没发生）。

    :returns: 本次恢复的挂载点数量
    """
    from src.services import mount_service
    from src.utils.session import session_manager

    if not session_manager.is_logged_in():
        return 0
    try:
        mounts = mount_service.list_mounts()
    except Exception as e:  # noqa: BLE001 - 配置读取失败不影响应用运行
        logger.warning("读取挂载点配置失败：%s", type(e).__name__)
        return 0

    restored = 0
    for mount in mounts:
        if not mount.get("enabled"):
            continue
        mid = mount.get("id", "")
        with _state_lock:
            running = mid in _sync_workers and _sync_workers[mid].is_alive()
        if running and (mid in _vfs or not mount_vfs.is_available()):
            continue
        try:
            if enable_vfs(mount):
                restored += 1
            elif not running:
                start_auto_sync(mount)
                restored += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("恢复挂载点失败 id=%s: %s", mid, type(e).__name__)
    return restored


def start_auto_sync(mount: dict) -> None:
    """启动挂载点的自动同步线程（幂等）"""
    mount_id = mount["id"]
    with _state_lock:
        existing = _sync_workers.get(mount_id)
        if existing is not None and existing.is_alive():
            return
        worker = _AutoSync(mount)
        _sync_workers[mount_id] = worker
    worker.start()


def stop_auto_sync(mount_id: str) -> None:
    """停止挂载点的自动同步线程并清理运行态（幂等）"""
    with _state_lock:
        worker = _sync_workers.pop(mount_id, None)
    if worker is not None:
        worker.stop()
    with _state_lock:
        _pending.pop(mount_id, None)
        _syncing.pop(mount_id, None)
        _retry_at.pop(mount_id, None)
        _last_scan.pop(mount_id, None)
        _recent_fetch.pop(mount_id, None)
        _dirty.pop(mount_id, None)
        _hydrating.pop(mount_id, None)
        _failed.pop(mount_id, None)


def stop_all_auto_sync() -> None:
    """停止全部自动同步线程（退出应用前调用）"""
    for mount_id in list(_sync_workers.keys()):
        stop_auto_sync(mount_id)
