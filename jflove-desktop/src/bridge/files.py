"""
桥的「文件操作 + 原生对话框」处理函数（N7-a）

## 为什么上传/下载必须在 Python

Web 端的 `file-service` 在**客户端**做分片、算 SHA256、加解密（`utils/crypto` +
`stream-frame`）。桌面端的加密链路只有一份实现（`http_client.py`），
且原桌面端 `file_service` 已经实现好分片上传/断点下载/进度回调 —— 直接复用服务层，
**不让渲染层碰字节流**。

## 对话框为什么必须走 GUI 线程

`QFileDialog` 只能在 GUI 线程创建。桥的默认分发是工作线程（`Worker`），
因此 `dialogs.*` 被登记进 `bridge.MAIN_THREAD_METHODS`，由桥在 GUI 线程内联执行
（模态对话框本来就会阻塞界面，语义正确）。
"""

from __future__ import annotations

import base64
from typing import Any, Callable

from PySide6.QtWidgets import QFileDialog

from src.services import file_service
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: 主窗口引用（对话框需要父窗口；由 `main.py` 注入）
_main_window = None


def set_main_window(window: Any) -> None:
    """注入主窗口（供原生对话框用作 parent）"""
    global _main_window
    _main_window = window


def _publish(topic: str, payload: Any) -> None:
    """向渲染层推事件（惰性导入避免与 bridge 形成循环依赖）"""
    from src.bridge import bridge as bridge_module

    bridge_module.publish_event(topic, payload)


# ── 文件操作（工作线程） ──────────────────────────


def _h_files_list(p: dict) -> list[dict]:
    """列出目录内容"""
    return file_service.list_files(int(p["disk_id"]), str(p.get("rel_path", "")))


def _h_files_mkdir(p: dict) -> dict:
    """新建目录"""
    file_service.make_dir(int(p["disk_id"]), str(p["rel_path"]))
    return {"ok": True}


def _h_files_rename(p: dict) -> dict:
    """重命名"""
    file_service.rename_file(int(p["disk_id"]), str(p["path"]), str(p["new_name"]))
    return {"ok": True}


def _h_files_move(p: dict) -> dict:
    """移动到目标目录"""
    file_service.move_file(int(p["disk_id"]), str(p["src_path"]), str(p["dst_dir_path"]))
    return {"ok": True}


def _h_files_delete(p: dict) -> dict:
    """删除全部文件（服务层按 -f 全删语义处理）"""
    file_service.delete_file(int(p["disk_id"]), str(p["rel_path"]))
    return {"ok": True}


def _h_files_upload(p: dict) -> dict:
    """
    上传本地文件（分片 + 哈希 + 加密全部在 Python 完成）。

    :param p: `{disk_id, rel_path, local_path}`
    """
    disk_id = int(p["disk_id"])
    rel_path = str(p.get("rel_path", ""))
    local_path = str(p["local_path"])
    name = str(p.get("name") or local_path.replace("\\", "/").rsplit("/", 1)[-1])

    def _progress(*args: object) -> None:
        """
        进度回调。

        **注意**：服务层的 `ProgressCallback` 实际会带更多参数（如速度），
        早先写成 `(sent, total)` 会直接抛 `TypeError` —— 表现为"上传静默失败"。
        这里只取前两个数值参数，对签名变化保持容错。
        """
        sent = int(args[0]) if args and isinstance(args[0], (int, float)) else 0
        total = int(args[1]) if len(args) > 1 and isinstance(args[1], (int, float)) else 0
        _publish(
            "files.upload_progress",
            {"rel_path": rel_path, "name": name, "sent": sent, "total": total},
        )

    file_service.upload_file(disk_id, rel_path, local_path, progress_callback=_progress)
    _publish("files.upload_done", {"rel_path": rel_path, "name": name})
    return {"ok": True, "name": name}


def _h_files_download(p: dict) -> dict:
    """
    下载到本地路径（解密 + 落盘全部在 Python 完成）。

    :param p: `{disk_id, rel_path, save_path}`
    """
    disk_id = int(p["disk_id"])
    rel_path = str(p["rel_path"])
    save_path = str(p["save_path"])

    def _progress(*args: object) -> None:
        """进度回调（签名容错，理由同 `_h_files_upload`）"""
        received = int(args[0]) if args and isinstance(args[0], (int, float)) else 0
        total = int(args[1]) if len(args) > 1 and isinstance(args[1], (int, float)) else 0
        _publish(
            "files.download_progress",
            {"rel_path": rel_path, "received": received, "total": total},
        )

    written = file_service.download_file(
        disk_id, rel_path, save_path, progress_callback=_progress
    )
    _publish("files.download_done", {"rel_path": rel_path, "save_path": save_path})
    return {"ok": True, "save_path": save_path, "bytes": written}


def _h_files_preview_text(p: dict) -> dict:
    """
    取文本类预览内容（txt / md / 代码等）。

    字节由服务层经加密流取回并解密，**渲染层只拿到文本**。
    """
    disk_id = int(p["disk_id"])
    rel_path = str(p["rel_path"])
    raw = file_service.get_preview_bytes(disk_id, rel_path)
    limit = int(p.get("limit", 2 * 1024 * 1024))
    truncated = len(raw) > limit
    text = raw[:limit].decode("utf-8", errors="replace")
    return {"text": text, "truncated": truncated, "size": len(raw)}


def _h_files_preview_base64(p: dict) -> dict:
    """取二进制预览内容（图片/SVG 等交给 Qt 原生解码时用 base64 传路径）"""
    disk_id = int(p["disk_id"])
    rel_path = str(p["rel_path"])
    raw = file_service.get_preview_bytes(disk_id, rel_path)
    return {"base64": base64.b64encode(raw).decode("ascii"), "size": len(raw)}


def _h_files_write_temp(p: dict) -> dict:
    """
    把服务端文件解密后写到本地临时文件，返回**本地路径**。

    用途：图片走 **Qt 原生解码**（OS 解码器），`QImageReader` 需要一个本地路径 /
    设备；把它落到临时目录再交给原生浮层最稳妥（也支持 tiff/heic 等 Chromium 不支持
    却在 Qt 侧有插件的格式）。
    """
    import os
    import tempfile

    disk_id = int(p["disk_id"])
    rel_path = str(p["rel_path"])
    name = str(p.get("name") or rel_path.replace("\\", "/").rsplit("/", 1)[-1])
    raw = file_service.get_preview_bytes(disk_id, rel_path)

    temp_dir = os.path.join(tempfile.gettempdir(), "jflove-preview")
    os.makedirs(temp_dir, exist_ok=True)
    local_path = os.path.join(temp_dir, name)
    with open(local_path, "wb") as fp:
        fp.write(raw)
    return {"local_path": local_path, "size": len(raw), "name": name}


# ── 原生对话框（GUI 线程） ────────────────────────


def _h_dialogs_open_files(_p: dict) -> dict:
    """选择本地文件（用于上传）"""
    paths, _filter = QFileDialog.getOpenFileNames(_main_window, "选择要上传的文件")
    files = []
    import os

    for path in paths:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        files.append({"path": path, "name": os.path.basename(path), "size": size})
    return {"files": files}


def _h_dialogs_save_file(p: dict) -> dict:
    """选择保存位置（用于下载）"""
    default_name = str(p.get("default_name") or "")
    path, _filter = QFileDialog.getSaveFileName(_main_window, "保存到", default_name)
    return {"path": path}


def _h_dialogs_select_directory(p: dict) -> dict:
    """选择本地目录（同步目录 / 笔记目录等）"""
    path = QFileDialog.getExistingDirectory(_main_window, str(p.get("title") or "选择目录"))
    return {"path": path}


def _h_dialogs_message(p: dict) -> dict:
    """原生确认框（渲染层的兜底确认，可选）"""
    from PySide6.QtWidgets import QMessageBox

    title = str(p.get("title") or "JFLove")
    text = str(p.get("text") or "")
    box = QMessageBox(_main_window)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Question)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    return {"confirmed": box.exec() == QMessageBox.StandardButton.Yes}


#: 白名单登记表：{service: {method: 处理函数}}
HANDLERS: dict[str, dict[str, Callable[[dict], Any]]] = {
    "files": {
        "list": _h_files_list,
        "mkdir": _h_files_mkdir,
        "rename": _h_files_rename,
        "move": _h_files_move,
        "delete": _h_files_delete,
        "upload": _h_files_upload,
        "download": _h_files_download,
        "preview_text": _h_files_preview_text,
        "preview_base64": _h_files_preview_base64,
        "write_temp": _h_files_write_temp,
    },
    "dialogs": {
        "open_files": _h_dialogs_open_files,
        "save_file": _h_dialogs_save_file,
        "select_directory": _h_dialogs_select_directory,
        "message": _h_dialogs_message,
    },
}
