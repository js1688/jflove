"""
桥的「传输任务」处理函数（N10 尾巴）

## 为什么要有这一层

N10 交付时，任务列表活在**渲染层**（zustand store，由进度事件喂），
好处是简单，但代价是：

- 应用重启 → 任务列表清空（无持久化）
- **无法取消**（上传/下载是在 Python 侧的一次阻塞调用，渲染层没有句柄）

原桌面端本来就有 `transfer_manager.py`（任务对象 + QThread + 取消 + 重试 + 信号），
本模块把它经桥暴露出来，**让"任务真相"回到 Python**：

    渲染层：提交任务（transfer.upload / transfer.download）
              ↓
    TransferManager：持有任务、跑 QThread、支持 cancel
              ↓ 信号 → 桥事件
    transfer.added / transfer.updated / transfer.removed → 渲染层只做展示

> 本模块是**加法**：不改变 `files.upload` / `files.download` 的行为，
> 渲染层可以逐步切过来（切换点集中在 `ui/src/hooks/use-files.ts`）。
"""

from __future__ import annotations

from typing import Any, Callable

from src.utils.logger import get_logger

logger = get_logger(__name__)

#: 传输管理器（由 `setup()` 创建）
_manager: Any = None


def setup(publish_event: Callable[[str, Any], None]) -> None:
    """创建传输管理器并把它的信号转成桥事件"""
    global _manager
    from src.utils.transfer_manager import TransferManager

    _manager = TransferManager()
    _manager.task_added.connect(
        lambda task: publish_event("transfer.added", _serialize_task(task))
    )
    _manager.task_updated.connect(
        lambda task_id: publish_event("transfer.updated", _find_task(task_id))
    )
    _manager.task_removed.connect(
        lambda task_id: publish_event("transfer.removed", {"id": str(task_id)})
    )
    logger.info("传输管理器已就绪")


def _serialize_task(task: Any) -> dict:
    """把 `TransferTask` 转成可 JSON 化的字典（字段缺失也不报错）"""
    if task is None:
        return {}
    kind = getattr(task, "kind", "")
    status = getattr(task, "status", "")
    return {
        "id": str(getattr(task, "id", "")),
        "kind": getattr(kind, "value", str(kind)),
        "filename": str(getattr(task, "filename", "")),
        "diskId": int(getattr(task, "disk_id", 0) or 0),
        "remotePath": str(getattr(task, "rel_path", "")),
        "localPath": str(getattr(task, "local_path", "")),
        "fileSize": int(getattr(task, "file_size", 0) or 0),
        "transferred": int(getattr(task, "transferred", 0) or 0),
        "percent": int(getattr(task, "percent", 0) or 0),
        "status": getattr(status, "value", str(status)),
        "error": str(getattr(task, "error", "") or ""),
    }


def _find_task(task_id: str) -> dict:
    """按 id 取任务快照（供 updated 事件携带完整状态）"""
    if _manager is None:
        return {"id": str(task_id)}
    for task in _manager.tasks:
        if str(getattr(task, "id", "")) == str(task_id):
            return _serialize_task(task)
    return {"id": str(task_id)}


# ── 桥方法 ────────────────────────────────────────


def _h_transfer_list(_p: dict) -> dict:
    """列出全部传输任务（供页面初始化时补齐状态）"""
    if _manager is None:
        return {"tasks": []}
    return {"tasks": [_serialize_task(t) for t in _manager.tasks]}


def _h_transfer_upload(p: dict) -> dict:
    """提交上传任务（Python 侧分片 + 加密 + 进度）"""
    if _manager is None:
        raise RuntimeError("传输管理器未初始化")
    task_id = _manager.submit_upload(
        int(p["disk_id"]), str(p.get("rel_dir", "")), str(p["local_path"])
    )
    return {"id": task_id}


def _h_transfer_download(p: dict) -> dict:
    """提交下载任务（Python 侧解密后落盘）"""
    if _manager is None:
        raise RuntimeError("传输管理器未初始化")
    task_id = _manager.submit_download(
        int(p["disk_id"]),
        str(p["rel_path"]),
        str(p["save_path"]),
        filename=str(p.get("filename", "")),
        file_size=int(p.get("file_size", 0) or 0),
        remote_mtime=float(p.get("remote_mtime", 0.0) or 0.0),
    )
    return {"id": task_id}


def _h_transfer_cancel(p: dict) -> dict:
    """取消任务（正在跑的工作线程会在下一次取消检查点退出）"""
    if _manager is None:
        raise RuntimeError("传输管理器未初始化")
    _manager.cancel(str(p["task_id"]))
    return {"ok": True}


def _h_transfer_clear(_p: dict) -> dict:
    """清空已结束的任务（已完成/失败/已取消）"""
    if _manager is None:
        return {"ok": True, "removed": 0}
    remover = getattr(_manager, "clear_finished", None)
    if remover is None:
        return {"ok": True, "removed": 0}
    return {"ok": True, "removed": int(remover() or 0)}


#: 白名单登记表：{service: {method: 处理函数}}
HANDLERS: dict[str, dict[str, Callable[[dict], Any]]] = {
    "transfer": {
        "list": _h_transfer_list,
        "upload": _h_transfer_upload,
        "download": _h_transfer_download,
        "cancel": _h_transfer_cancel,
        "clear": _h_transfer_clear,
    },
}

#: 必须在 **GUI 线程**执行的方法（管理器持有 QThread，信号连接要求主线程亲和）
MAIN_THREAD_METHODS: set[tuple[str, str]] = {
    ("transfer", "upload"),
    ("transfer", "download"),
    ("transfer", "cancel"),
    ("transfer", "clear"),
}
