"""
磁盘容量与文件系统类型探测（v1.10.26，R1）

用途：给「文件管理磁盘列表」与「磁盘设置」提供 R1 要求的四项信息：
  - `total_size`：最大空间（字节）
  - `used_size` ：已用空间（字节）
  - `free_size` ：可用空间（字节）
  - `disk_type` ：文件系统类型（ext4 / xfs / btrfs / ntfs / apfs / fuse…）

设计要点：
  1. **绝不抛异常**：路径不可读、被卸载、权限不足都只返回 `None`，
     由客户端渲染成 `—`。磁盘列表是一条高频接口，不能因为某个挂载点掉了就整页 500。
  2. 分平台取类型：
     - Linux：解析 `/proc/mounts`（`mount` 命令的输出格式与它一致），
       取**最长前缀匹配**的挂载点 —— 否则 `/` 会抢走 `/home` 的子目录。
     - Windows：`GetVolumeInformationW`（ctypes 调 kernel32），
       取 `lpFileSystemNameBuffer`（NTFS / FAT32 / exFAT）。
     - macOS / 其它 Unix：`stat -f %T`（HFS+ / APFS）。
  3. `shutil.disk_usage` 本身跨平台，失败时返回全 `None`。

⚠ 不要把这里的结果缓存到数据库 —— 容量是**实时**数据，
   落库会立刻过期（并且和 `df -h` 对不上，违反 R1 的验收判据）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from src.utils.logger import get_logger

logger = get_logger(__name__)

#: `/proc/mounts` 的读取上限。真实机器上通常 < 10KB，这里给足余量但防失控。
_PROC_MOUNTS_MAX_BYTES = 1024 * 1024


def _read_mounts_linux() -> list[tuple[str, str]]:
    """
    读取 `/proc/mounts`，返回 [(挂载点, 文件系统类型)] 列表。

    格式：`<device> <mountpoint> <fstype> <options> <dump> <pass>`
    挂载点里的空格等字符以八进制转义（`\\040`），这里做还原。

    :returns: 挂载点与文件系统类型列表；读取失败返回空列表
    """
    try:
        with open("/proc/mounts", "r", encoding="utf-8", errors="replace") as f:
            raw = f.read(_PROC_MOUNTS_MAX_BYTES)
    except OSError:
        return []

    result: list[tuple[str, str]] = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mountpoint, fstype = parts[1], parts[2]
        # 还原八进制转义（\\040 = 空格，\\011 = tab，\\134 = 反斜杠）
        for esc, ch in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"),
                        ("\\134", "\\")):
            mountpoint = mountpoint.replace(esc, ch)
        result.append((mountpoint, fstype))
    return result


def _disk_type_linux(path: str) -> str | None:
    """
    取 Linux 上 `path` 所在文件系统的类型（最长前缀匹配挂载点）。

    :param path: 目标目录绝对路径
    :returns: 文件系统类型（如 `ext4` / `xfs` / `fuse.sshfs`）；取不到返回 None
    """
    mounts = _read_mounts_linux()
    if not mounts:
        return None

    # realpath 解析符号链接，否则 /home -> /mnt/data 会匹配错挂载点
    try:
        target = os.path.realpath(path)
    except OSError:
        target = path

    best_mount = ""
    best_type: str | None = None
    for mountpoint, fstype in mounts:
        # 去掉挂载点末尾的 /（根挂载点 "/" 除外），避免 "//" 前缀问题
        mp = mountpoint.rstrip("/") or "/"
        if mp == target or target.startswith(mp + os.sep) or mp == "/":
            if len(mp) >= len(best_mount):
                best_mount, best_type = mp, fstype
    return best_type


def _disk_type_windows(path: str) -> str | None:
    """
    取 Windows 上 `path` 所在卷的文件系统类型（NTFS / FAT32 / exFAT）。

    :param path: 目标目录绝对路径
    :returns: 文件系统名；非 Windows 或调用失败返回 None
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        drive = os.path.splitdrive(os.path.abspath(path))[0]
        if not drive:
            return None
        root = drive + "\\"

        fs_name_buf = ctypes.create_unicode_buffer(261)
        volume_name_buf = ctypes.create_unicode_buffer(261)
        serial = wintypes.DWORD()
        max_component = wintypes.DWORD()
        fs_flags = wintypes.DWORD()

        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root),
            volume_name_buf, len(volume_name_buf),
            ctypes.byref(serial),
            ctypes.byref(max_component),
            ctypes.byref(fs_flags),
            fs_name_buf, len(fs_name_buf),
        )
        if not ok:
            return None
        name = fs_name_buf.value.strip()
        return name or None
    except Exception as e:  # noqa: BLE001
        logger.debug("读取 Windows 卷类型失败：%s", e)
        return None


def _disk_type_macos(path: str) -> str | None:
    """
    取 macOS 上 `path` 所在卷的文件系统类型（`stat -f %T`）。

    :param path: 目标目录绝对路径
    :returns: 文件系统类型（如 `apfs` / `hfs`）；失败返回 None
    """
    try:
        out = subprocess.run(
            ["stat", "-f", "%T", path],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode != 0:
            return None
        name = out.stdout.strip()
        return name or None
    except (OSError, subprocess.SubprocessError):
        return None


def get_disk_type(path: str) -> str | None:
    """
    按当前平台取 `path` 所在文件系统的类型。

    :param path: 目标目录绝对路径
    :returns: 文件系统类型字符串；取不到返回 None（客户端渲染为 `—`）
    """
    if not path:
        return None
    try:
        if sys.platform == "win32":
            return _disk_type_windows(path)
        if sys.platform == "darwin":
            return _disk_type_macos(path)
        return _disk_type_linux(path)
    except Exception as e:  # noqa: BLE001
        logger.debug("读取磁盘类型失败 path=%r：%s", path, e)
        return None


def get_usage(path: str) -> dict:
    """
    取某路径所在文件系统的容量信息（R1 四项中的三项 + 类型）。

    任何失败都**不抛异常**，对应字段为 None：
      - 路径不存在 / 无权限 / 已卸载 → 全部 None
      - 只能读到容量但读不到类型 → `disk_type` 为 None

    :param path: 目标目录绝对路径
    :returns: {"total_size": int|None, "used_size": int|None,
               "free_size": int|None, "disk_type": str|None}
    """
    result: dict = {
        "total_size": None,
        "used_size": None,
        "free_size": None,
        "disk_type": None,
    }
    if not path:
        return result

    try:
        usage = shutil.disk_usage(path)
        result["total_size"] = int(usage.total)
        result["used_size"] = int(usage.used)
        result["free_size"] = int(usage.free)
    except (OSError, ValueError) as e:
        # 如实记录，但不让整条磁盘列表接口失败（R1 要求「不可读兜底不崩」）
        logger.debug("读取磁盘容量失败 path=%r：%s", path, e)

    result["disk_type"] = get_disk_type(path)
    return result


def get_usage_for_disks(paths: list[str]) -> dict[str, dict]:
    """
    批量取多个磁盘路径的容量信息（同一路径只算一次）。

    :param paths: 磁盘真实路径列表
    :returns: {路径: get_usage(路径)} 映射
    """
    cache: dict[str, dict] = {}
    for p in paths:
        if p not in cache:
            cache[p] = get_usage(p)
    return cache
