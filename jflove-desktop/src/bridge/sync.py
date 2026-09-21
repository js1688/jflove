"""
桥的「同步管理」处理函数（N9）

## 为什么这一页要照原桌面端做

Web 端的同步管理页是**降级说明页**（浏览器沙箱碰不到本地文件系统），
所以这一页没有 Web 参考。桌面端沿用**原桌面端既有实现**，不另发明语义：

    渲染层（本页界面）
      ↕ 桥 sync.*
    src/services/sync_service.py   同步规则的持久化（本地 JSON）+ 远端快照接口
    src/utils/sync_engine.py       双向增量同步引擎（QThread + 定时器 + 信号）

引擎信号 → 桥事件（`sync.started` / `sync.finished` / `sync.error`）→ 渲染层更新进度。

## 线程

- `list/create/update/delete/snapshot`：有网络或磁盘 I/O → 走工作线程
- `run`（触发同步）/`reload`（重载规则）/`status`：只碰引擎对象与信号 → **GUI 线程**
  （`trigger_sync` 内部起 QThread，本身不阻塞）
"""

from __future__ import annotations

from typing import Any, Callable

from src.services import sync_service
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: 同步引擎（由 `setup()` 创建）
_engine: Any = None


def setup(publish_event: Callable[[str, Any], None]) -> None:
    """创建同步引擎并把它的信号转成桥事件"""
    global _engine
    from src.utils.sync_engine import SyncEngine

    _engine = SyncEngine()
    _engine.sync_started.connect(
        lambda config_id: publish_event("sync.started", {"config_id": str(config_id)})
    )
    _engine.sync_finished.connect(
        lambda result: publish_event("sync.finished", _serialize_result(result))
    )
    _engine.sync_error.connect(
        lambda config_id, message: publish_event(
            "sync.error", {"config_id": str(config_id), "message": str(message)}
        )
    )
    reload_configs()
    logger.info("同步引擎已就绪")


def _serialize_result(result: Any) -> dict:
    """把引擎的 `SyncResult` 转成可 JSON 化的字典（字段缺失也不报错）"""
    if result is None:
        return {}
    out: dict = {}
    for key in (
        "config_id",
        "uploaded",
        "downloaded",
        "deleted_local",
        "deleted_remote",
        "skipped",
        "errors",
        "duration_ms",
    ):
        if hasattr(result, key):
            out[key] = getattr(result, key)
    if hasattr(result, "total_actions"):
        out["total_actions"] = result.total_actions()
    return out


def reload_configs() -> None:
    """把最新规则交给引擎（自动同步的定时器据此重建）"""
    if _engine is None:
        return
    try:
        _engine.reload_configs(sync_service.list_configs())
    except Exception as exc:  # noqa: BLE001 - 重载失败不影响界面展示
        logger.warning("重载同步规则失败：%s", exc)


# ── 规则管理（工作线程） ──────────────────────────


def _h_sync_list(_p: dict) -> dict:
    """列出全部同步规则"""
    return {"configs": sync_service.list_configs()}


def _h_sync_create(p: dict) -> dict:
    """新建同步规则"""
    config_id = sync_service.create_config(
        name=str(p.get("name", "")),
        local_path=str(p.get("local_path", "")),
        disk_id=int(p.get("disk_id", 0)),
        remote_path=str(p.get("remote_path", "")),
        auto_sync=bool(p.get("auto_sync", False)),
        sync_interval=int(p.get("sync_interval", 300)),
    )
    return {"id": config_id}


def _h_sync_update(p: dict) -> dict:
    """更新同步规则"""
    sync_service.update_config(
        config_id=str(p["config_id"]),
        name=str(p.get("name", "")),
        local_path=str(p.get("local_path", "")),
        disk_id=int(p.get("disk_id", 0)),
        remote_path=str(p.get("remote_path", "")),
        auto_sync=bool(p.get("auto_sync", False)),
        sync_interval=int(p.get("sync_interval", 300)),
        enabled=bool(p.get("enabled", True)),
    )
    return {"ok": True}


def _h_sync_delete(p: dict) -> dict:
    """删除同步规则"""
    sync_service.delete_config(str(p["config_id"]))
    return {"ok": True}


def _h_sync_snapshot(p: dict) -> dict:
    """取远端目录快照（用于预览"将同步哪些文件"）"""
    entries = sync_service.get_remote_snapshot(
        int(p.get("disk_id", 0)), str(p.get("remote_path", ""))
    )
    return {"entries": entries}


def _h_sync_touch(p: dict) -> dict:
    """标记某条规则刚刚同步过（健康检查/手动确认用）"""
    sync_service.touch_synced(str(p["config_id"]))
    return {"ok": True}


# ── 引擎控制（GUI 线程） ──────────────────────────


def _h_sync_run(p: dict) -> dict:
    """立即同步一次（引擎内部起线程，不阻塞界面）"""
    if _engine is None:
        raise RuntimeError("同步引擎未初始化")
    started = bool(_engine.trigger_sync(str(p["config_id"])))
    return {"started": started}


def _h_sync_reload(_p: dict) -> dict:
    """规则变更后让引擎重载（自动同步定时器据此更新）"""
    reload_configs()
    return {"ok": True}


def _h_sync_status(_p: dict) -> dict:
    """引擎状态（供界面显示"同步中"）"""
    running = bool(getattr(_engine, "is_running", False)) if _engine else False
    return {"engine_ready": _engine is not None, "running": running}


#: 白名单登记表：{service: {method: 处理函数}}
HANDLERS: dict[str, dict[str, Callable[[dict], Any]]] = {
    "sync": {
        "list": _h_sync_list,
        "create": _h_sync_create,
        "update": _h_sync_update,
        "delete": _h_sync_delete,
        "snapshot": _h_sync_snapshot,
        "touch": _h_sync_touch,
        "run": _h_sync_run,
        "reload": _h_sync_reload,
        "status": _h_sync_status,
    },
}

#: 必须在 **GUI 线程**执行的方法（只碰引擎对象与信号）
MAIN_THREAD_METHODS: set[tuple[str, str]] = {
    ("sync", "run"),
    ("sync", "reload"),
    ("sync", "status"),
}
