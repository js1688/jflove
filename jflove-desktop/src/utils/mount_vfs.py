"""
系统级云盘挂载驱动门面（v1.7.0）

按平台选择实现，对上层（`mount_sync_service`）暴露同一套契约：

| 平台 | 实现模块 | 机制 |
| --- | --- | --- |
| Windows | `src/utils/mount_cfapi.py` | Cloud Filter API：占位 + 读取时水合 |
| Linux | `src/utils/mount_fuse.py` | FUSE：目录视图 + 读取时按需下载 |
| 其它 | —— | `is_available()` 返回 False，上层降级为应用内视图 |

实例契约（两种实现一致）：
  - `start() -> bool`
  - `stop() -> None`（幂等；退出应用前必须调用）
  - `add_file(rel_path, size, mtime) -> bool`（幂等）
  - `remove_file(rel_path) -> None`
  - `is_cached(rel_path) -> bool`
  - `local_root() -> str` / `real_path(rel) -> str`
  - `begin_write` / `end_write` / `is_open_for_write` / `note_change`（自动同步用）

平台实现**惰性 import**：Windows 专属模块顶层有 `ctypes.WinDLL`，
在 Linux 上 import 会直接抛 `AttributeError`（这也是本门面存在的原因）。
"""

from __future__ import annotations

import os
import sys
from typing import Callable

_FETCH = Callable[[str], bytes]
_LISTING = Callable[[], list[dict]]


def backend() -> str:
    """当前平台的挂载后端名：`cfapi` / `fuse` / `none`"""
    if os.name == "nt":
        return "cfapi"
    if sys.platform.startswith("linux"):
        return "fuse"
    return "none"


def is_available() -> bool:
    """当前平台是否具备系统级挂载能力（不代表某个挂载点一定成功）"""
    name = backend()
    if name == "cfapi":
        return True
    if name == "fuse":
        from src.utils import mount_fuse

        return mount_fuse.is_available()
    return False


def create_mount(
    mount_id: str,
    sync_root: str,
    fetch: _FETCH,
    listing: _LISTING | None = None,
    on_local_change: Callable[[str], None] | None = None,
):
    """
    按平台创建挂载会话。

    :param mount_id: 挂载点 ID（仅用于日志与文件名）
    :param sync_root: 本地目录（FUSE 下即挂载点；CfAPI 下即 sync root）
    :param fetch: `fetch(rel_path) -> bytes`，取远端完整内容
    :param listing: 索引查询回调（FUSE 用来补足未缓存的在线文件；CfAPI 不需要）
    :param on_local_change: 本地新增 / 修改回调（FUSE 用它驱动自动上传）
    :raises RuntimeError: 当前平台没有可用的挂载后端
    """
    name = backend()
    if name == "cfapi":
        from src.utils.mount_cfapi import WinCfapiMount

        return WinCfapiMount(
            mount_id, sync_root, fetch, on_local_change=on_local_change,
        )
    if name == "fuse":
        from src.utils.mount_fuse import LinuxFuseMount

        return LinuxFuseMount(
            mount_id, sync_root, fetch,
            listing=listing, on_local_change=on_local_change,
        )
    raise RuntimeError("当前平台不支持系统级挂载")


def __getattr__(name: str):
    """
    兼容旧引用（`mount_vfs.WinCfapiMount` / `mount_vfs.LinuxFuseMount`）。

    惰性导入保证「在 Linux 上访问 WinCfapiMount」不会连带加载 ctypes.WinDLL。
    """
    if name == "WinCfapiMount":
        from src.utils.mount_cfapi import WinCfapiMount

        return WinCfapiMount
    if name == "LinuxFuseMount":
        from src.utils.mount_fuse import LinuxFuseMount

        return LinuxFuseMount
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
