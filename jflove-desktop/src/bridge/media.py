"""
桥的「媒体预览」处理函数（N7-b，全部在 GUI 线程执行）

链路（用户要求：**吊起本地解码**，底层沿用原桌面端那套）：

    视频/音频：StreamProxy（127.0.0.1 + 一次性 token + Range 206）
               → QMediaPlayer（Windows: Media Foundation / Linux: GStreamer）
    图片：     files.write_temp 落临时文件 → QImageReader（Qt 原生解码）
               → QLabel 显示 QPixmap

两者都渲染在**独立顶层原生浮层**上，由渲染层上报的矩形定位（P0-4 已实测贴合精度 1px）。

> 为什么不让渲染层直接拿字节流：桌面端加密链路只有一份实现（`http_client.py`），
> `StreamProxy` 负责逐帧解密后喂给 OS 解码器，渲染层只拿到一个本地 URL / 本地文件路径。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from src.components.stream_proxy import StreamProxy
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: 当前活动的本地流代理（同一时刻只允许一个预览在播）
_proxy: StreamProxy | None = None
#: 原生浮层控制器（由 main.py 注入）
_overlay: Any = None
#: 监听浮层事件的窗口（用于把进度推给渲染层）
_window: Any = None


def setup(overlay: Any) -> None:
    """注入原生浮层控制器（`src/ui/media_overlay.py::MediaOverlay`）"""
    global _overlay
    _overlay = overlay


def _close_proxy() -> None:
    """关闭本地流代理（释放 loopback 连接）"""
    global _proxy
    if _proxy is not None:
        try:
            _proxy.close()
        except Exception as exc:  # noqa: BLE001 - 关闭失败不影响后续预览
            logger.warning("关闭 StreamProxy 失败: %s", exc)
        _proxy = None


def _h_media_show_image(p: dict) -> dict:
    """
    显示图片（**Qt 原生解码**，支持 Chromium 不支持的格式）。

    :param p: `{local_path}` —— 由 `files.write_temp` 先落到本地临时文件
    """
    local_path = str(p.get("local_path") or "")
    if not local_path:
        raise ValueError("缺少 local_path")
    _close_proxy()
    if _overlay is None:
        raise RuntimeError("原生浮层未初始化")
    return json.loads(_overlay.open(json.dumps({"kind": "image", "local_path": local_path})))


def _h_media_open_stream(p: dict) -> dict:
    """
    打开视频/音频：起本地流代理并把地址交给 QMediaPlayer（OS 解码器）。

    :param p: `{disk_id, rel_path, name, repair_task_id?}`
    """
    global _proxy
    _close_proxy()

    disk_id = int(p["disk_id"])
    rel_path = str(p["rel_path"]).replace("\\", "/")
    # `stream_range(disk_id, path, filename)` 的 `path` 语义是**文件所在目录**
    # （服务端会自己拼 `<path>/<filename>`）。这里传的是文件自身的相对路径，
    # 必须先拆开 —— 否则拼成 `<file>/<file>`，服务端直接 404「文件不存在」，
    # 表现为 QMediaPlayer `InvalidMedia: Could not open file`（实测踩过）。
    name = str(p.get("name") or rel_path.rsplit("/", 1)[-1])
    if rel_path.endswith(name):
        dir_path = rel_path[: -len(name)].rstrip("/")
    elif "/" in rel_path:
        dir_path = rel_path.rsplit("/", 1)[0]
    else:
        dir_path = ""

    repair_task_id = p.get("repair_task_id")
    if repair_task_id is not None:
        repair_task_id = int(repair_task_id)

    _proxy = StreamProxy(disk_id, dir_path, name, repair_task_id=repair_task_id)
    # 必须显式 start()：端口在 start() 里才绑定，否则 url 里是 :0
    _proxy.start()
    url = _proxy.url
    kind = str(p.get("kind") or "video")
    if _overlay is None:
        raise RuntimeError("原生浮层未初始化")
    result = json.loads(
        _overlay.open(json.dumps({"kind": kind, "url": url, "name": name}))
    )
    result["url"] = url
    return result


def _h_media_set_rect(p: dict) -> dict:
    """渲染层上报预留矩形（相对视口，CSS 像素）"""
    if _overlay is not None:
        _overlay.setRect(json.dumps(p))
    return {"ok": True}


def _h_media_set_visible(p: dict) -> dict:
    """渲染层要求显示/隐藏浮层"""
    if _overlay is not None:
        _overlay.setVisible(bool(p.get("visible")))
    return {"ok": True}


def _h_media_play(_p: dict) -> dict:
    if _overlay is not None:
        _overlay.play()
    return {"ok": True}


def _h_media_pause(_p: dict) -> dict:
    if _overlay is not None:
        _overlay.pause()
    return {"ok": True}


def _h_media_seek(p: dict) -> dict:
    if _overlay is not None:
        _overlay.seek(int(p.get("position", 0)))
    return {"ok": True}


def _h_media_set_fullscreen(p: dict) -> dict:
    """切换全屏（原生窗口铺满屏幕）"""
    if _overlay is not None:
        _overlay.setFullScreen(bool(p.get("fullscreen")))
    return {"ok": True, "fullscreen": bool(p.get("fullscreen"))}


def _h_media_set_volume(p: dict) -> dict:
    if _overlay is not None:
        _overlay.setVolume(int(p.get("volume", 100)))
    return {"ok": True}


def _h_media_state(_p: dict) -> dict:
    if _overlay is None:
        return {"position": 0, "duration": 0, "playing": False, "kind": ""}
    return json.loads(_overlay.state())


def _h_media_close(_p: dict) -> dict:
    """关闭预览：停播 + 释放本地流代理"""
    if _overlay is not None:
        _overlay.close()
    _close_proxy()
    return {"ok": True}


#: 白名单登记表：{service: {method: 处理函数}}
HANDLERS: dict[str, dict[str, Callable[[dict], Any]]] = {
    "media": {
        "show_image": _h_media_show_image,
        "open_stream": _h_media_open_stream,
        "set_rect": _h_media_set_rect,
        "set_visible": _h_media_set_visible,
        "play": _h_media_play,
        "pause": _h_media_pause,
        "seek": _h_media_seek,
        "set_volume": _h_media_set_volume,
        "set_fullscreen": _h_media_set_fullscreen,
        "state": _h_media_state,
        "close": _h_media_close,
    },
}

#: 必须在 **GUI 线程**执行的方法（原生窗口/播放器/对话框只能主线程碰）
MAIN_THREAD_METHODS: set[tuple[str, str]] = {
    *(("media", name) for name in HANDLERS["media"]),
    ("dialogs", "open_files"),
    ("dialogs", "save_file"),
    ("dialogs", "select_directory"),
    ("dialogs", "message"),
}
