"""
Linux FUSE 挂载驱动（v1.7.0）

把「本地缓存目录」挂成 FUSE 文件系统，语义与 Windows CfAPI 对齐：
  - 目录里能看到**全部**远端条目（本地缓存的真实文件 + 索引中的在线文件）；
  - 读取未缓存文件时按需下载（`fetch(rel_path) -> bytes`）并落地，再照常读；
  - 删除 / 重命名 / 写入只作用于本地缓存，**不触碰远端**；
  - 停用（`stop`）后卸载，目录恢复普通文件夹，可直接删除。

注意：挂载后**不能**用 `os.path.isfile(sync_root/rel)` 判断「是否已缓存」——
FUSE 的 stat 会命中索引、对所有在线文件都返回文件类型；必须用
`LinuxFuseMount.is_cached()`（走底层目录）。

实现要点（踩过的坑）：
  1. **底层目录不能用挂载点自身访问**：挂载后 `sync_root` 已被 FUSE 接管，
     再 `open(sync_root/x)` 会递归进本文件系统。因此 `start()` 前先
     `os.open(sync_root, O_DIRECTORY|O_PATH|O_CLOEXEC)` 拿到底层 fd，
     之后所有真实 I/O 都走 `/proc/self/fd/<fd>/...`。
  2. `open` 水合完**必须打开真实文件并返回其 fd**；返回 0 会让后续
     `read` 去读 stdin（0 是合法 fd，用 `if fh` 判断还会被当成 None）。
  3. `FUSE(..., foreground=True)` 会阻塞，必须跑在独立线程；不开前台模式
     会让 `fuse_main` 自己 fork，线程化调用方式不成立。
  4. 挂载点可能已有缓存文件，需 `nonempty` 选项，否则挂载直接失败。
  5. 本驱动是**多线程**（libfuse 默认多线程循环）：水合按相对路径加锁，
     同一文件的并发 open 只下载一次。

自动同步（v1.7.0 修复）：
  - 挂载目录内的**新建 / 修改 / 改名**会通过 `on_local_change(rel)` 通知上层，
    由 `mount_sync_service` 排队上传到远端（与远端保持一致）；
  - **删除 / 删目录不通知**：本地删除只代表「释放本地缓存」，绝不回传远端
    （远端删除只能在文件管理页做）。

限制（与 CfAPI 一致，如实登记）：
  - `fetch` 取的是**整文件**，大文件会整体进内存；后续应改为按 4MB 范围拉取。
"""

from __future__ import annotations

import errno
import os
import shutil
import subprocess
import threading
import time
from typing import Callable

from src.utils.logger import get_logger

logger = get_logger(__name__)

try:  # fusepy 在缺少 libfuse 的机器上 import 期即抛错
    from fuse import FUSE as _FUSE
    from fuse import FuseOSError as _FuseOSError
    from fuse import Operations as _FuseOperations
except Exception:  # noqa: BLE001 - 纯 Linux 可选依赖
    _FUSE = None
    _FuseOperations = object

    class _FuseOSError(OSError):  # type: ignore[no-redef]
        """fusepy 缺失时的占位（不会真的被调用）"""

        def __init__(self, errno_: int):
            super().__init__(errno_, os.strerror(errno_))


_FETCH = Callable[[str], bytes]
_LISTING = Callable[[], list[dict]]

#: 索引快照的有效期（秒）：避免每次 getattr/readdir 都查一次 sqlite
_SNAPSHOT_TTL = 1.0

#: 卸载命令候选（按优先级）
_UNMOUNT_CMDS = (
    ("fusermount", "-u"),
    ("fusermount3", "-u"),
    ("umount",),
)


def _unescape_mount_field(value: str) -> str:
    """`/proc/self/mounts` 用八进制转义空格等字符（`\\040` = 空格）"""
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _mount_targets() -> set[str]:
    """当前进程可见的全部挂载点（读 `/proc/self/mounts`，不做 stat）"""
    targets: set[str] = set()
    try:
        with open("/proc/self/mounts", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    targets.add(_unescape_mount_field(parts[1]))
    except OSError:
        pass
    return targets


def _is_mountpoint(path: str) -> bool:
    """
    判断路径是否是挂载点。

    **不能只用 `os.path.ismount`**：进程被强杀后残留的 FUSE 挂载点
    stat 会返回 ENOTCONN，`ismount` 吞掉异常后返回 False，于是代码会
    误以为「只是个普通目录」，后续 `makedirs` 直接抛 FileExistsError。
    """
    if os.path.ismount(path):
        return True
    try:
        real = os.path.realpath(path)
    except OSError:
        return False
    return real in _mount_targets()


def is_available() -> bool:
    """
    当前环境是否具备 FUSE 挂载能力。

    三个条件缺一不可：`fusepy` 可 import（= 有 `libfuse.so.2`）、`/dev/fuse` 可访问、
    有 **`fusermount`**（fuse2 的挂载助手；只有 `fusermount3` 时 fuse2 挂不上，
    与其等 15s 超时，不如直接降级为应用内视图）。
    """
    if _FUSE is None:
        return False
    if not os.path.exists("/dev/fuse"):
        return False
    return shutil.which("fusermount") is not None


class _FuseOps(_FuseOperations):
    """
    FUSE 操作实现：底层目录透传 + 在线文件按需水合。

    所有路径都是 FUSE 的绝对路径（以 `/` 开头），内部统一换算成
    「相对挂载点根」的 rel 与底层真实路径。
    """

    #: 时间以纳秒传递（fusepy 默认浮点秒已弃用）
    use_ns = True

    def __init__(self, owner: "LinuxFuseMount"):
        self._owner = owner

    # ── 内部换算 ──────────────────────────────────

    def _rel(self, path: str) -> str:
        return path.strip("/")

    def _real(self, path: str) -> str:
        return self._owner.base_path + path

    # ── 元数据 ────────────────────────────────────

    def getattr(self, path, fh=None):  # noqa: ANN001, ANN201
        real = self._real(path)
        try:
            st = os.stat(real)
            # 必须显式带 uid/gid：fusepy 缺省填 0，否则已缓存文件在 `ls -l` 里显示成 root
            return {
                "st_mode": st.st_mode,
                "st_nlink": st.st_nlink,
                "st_size": st.st_size,
                "st_uid": st.st_uid,
                "st_gid": st.st_gid,
                "st_atime": st.st_atime_ns,
                "st_mtime": st.st_mtime_ns,
                "st_ctime": st.st_ctime_ns,
            }
        except OSError:
            pass

        rel = self._rel(path)
        if not rel:
            return _dir_stat()
        entry = self._owner.entry_of(rel)
        if entry is not None:
            if entry.get("is_dir"):
                return _dir_stat()
            return _file_stat(entry["size"], entry["mtime"])
        if self._owner.has_dir(rel):
            return _dir_stat()
        raise _FuseOSError(errno.ENOENT)

    def readdir(self, path, fh):  # noqa: ANN001, ANN201
        names: dict[str, bool] = {".": True, "..": True}
        real = self._real(path)
        if os.path.isdir(real):
            try:
                for name in os.listdir(real):
                    names[name] = os.path.isdir(os.path.join(real, name))
            except OSError:
                pass
        rel = self._rel(path)
        names.update(self._owner.children_of(rel))
        return list(names)

    def access(self, path, mode):  # noqa: ANN001, ANN201
        if os.path.exists(self._real(path)):
            return 0
        rel = self._rel(path)
        if not rel or self._owner.entry_of(rel) is not None or self._owner.has_dir(rel):
            return 0
        raise _FuseOSError(errno.ENOENT)

    def statfs(self, path):  # noqa: ANN001, ANN201
        try:
            st = os.statvfs(self._owner.base_path)
        except OSError:
            return {}
        return {
            "f_bsize": st.f_frsize,
            "f_frsize": st.f_frsize,
            "f_blocks": st.f_blocks,
            "f_bfree": st.f_bfree,
            "f_bavail": st.f_bavail,
            "f_files": st.f_files,
            "f_ffree": st.f_ffree,
            "f_favail": st.f_favail,
            "f_namemax": 255,
        }

    def utimens(self, path, times=None):  # noqa: ANN001, ANN201
        real = self._real(path)
        if os.path.exists(real):
            try:
                if times is None:
                    os.utime(real)
                else:
                    os.utime(real, ns=times)
            except OSError:
                pass
        return 0

    def chmod(self, path, mode):  # noqa: ANN001, ANN201
        real = self._real(path)
        if os.path.exists(real):
            os.chmod(real, mode)
        return 0

    def chown(self, path, uid, gid):  # noqa: ANN001, ANN201
        return 0

    # ── 读 ────────────────────────────────────────

    def open(self, path, flags):  # noqa: ANN001, ANN201
        real = self._real(path)
        rel = self._rel(path)
        writes = flags & (os.O_WRONLY | os.O_RDWR) != 0
        if not os.path.exists(real):
            if flags & os.O_TRUNC:
                os.makedirs(os.path.dirname(real), exist_ok=True)
                fh = os.open(real, flags, 0o644)
                if writes:
                    self._owner.begin_write(rel)
                return fh
            self._owner.hydrate(rel)
        fh = os.open(real, flags)
        if writes:
            self._owner.begin_write(rel)
        return fh

    def read(self, path, size, offset, fh):  # noqa: ANN001, ANN201
        return os.pread(fh, size, offset)

    # ── 写（只落本地缓存，不回传远端）────────────────

    def create(self, path, mode, fi=None):  # noqa: ANN001, ANN201
        real = self._real(path)
        os.makedirs(os.path.dirname(real), exist_ok=True)
        fh = os.open(real, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        self._owner.begin_write(self._rel(path))
        return fh

    def write(self, path, data, offset, fh):  # noqa: ANN001, ANN201
        return os.pwrite(fh, data, offset)

    def truncate(self, path, length, fh=None):  # noqa: ANN001, ANN201
        if fh is not None:
            os.ftruncate(fh, length)
            return 0
        real = self._real(path)
        if not os.path.exists(real):
            self._owner.hydrate(self._rel(path))
        os.truncate(real, length)
        return 0

    def flush(self, path, fh):  # noqa: ANN001, ANN201
        return 0

    def fsync(self, path, datasync, fh):  # noqa: ANN001, ANN201
        try:
            os.fsync(fh)
        except OSError:
            pass
        return 0

    def release(self, path, fh):  # noqa: ANN001, ANN201
        try:
            os.close(fh)
        except OSError:
            pass
        rel = self._rel(path)
        if self._owner.is_open_for_write(rel):
            self._owner.end_write(rel)
        return 0

    # ── 目录 / 删除 / 重命名 ─────────────────────────

    def mkdir(self, path, mode):  # noqa: ANN001, ANN201
        real = self._real(path)
        os.makedirs(real, mode=mode, exist_ok=True)
        return 0

    def rmdir(self, path):  # noqa: ANN001, ANN201
        real = self._real(path)
        try:
            os.rmdir(real)
        except OSError as e:
            raise _FuseOSError(e.errno or errno.ENOTEMPTY) from e
        return 0

    def unlink(self, path):  # noqa: ANN001, ANN201
        # 本地删除 = 只删本地缓存，**不回传远端**（不做 note_change）
        real = self._real(path)
        try:
            os.unlink(real)
        except OSError as e:
            raise _FuseOSError(e.errno or errno.ENOENT) from e
        return 0

    def rename(self, old, new):  # noqa: ANN001, ANN201
        src = self._real(old)
        dst = self._real(new)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            os.replace(src, dst)
        except OSError as e:
            raise _FuseOSError(e.errno or errno.EIO) from e
        if os.path.isfile(dst):
            self._owner.note_change(self._rel(new))
        return 0


def _owner() -> tuple[int, int]:
    """合成属性用的属主（必须是当前用户，否则 `ls -l` 全是 root）"""
    return os.getuid(), os.getgid()


def _file_stat(size: int, mtime: float) -> dict:
    """未缓存文件的合成属性（大小 / mtime 来自索引；时间单位为纳秒）"""
    now_ns = time.time_ns()
    uid, gid = _owner()
    stamp_ns = int(float(mtime or 0) * 1e9) or now_ns
    return {
        "st_mode": 0o100644,
        "st_nlink": 1,
        "st_size": int(size or 0),
        "st_uid": uid,
        "st_gid": gid,
        "st_atime": stamp_ns,
        "st_mtime": stamp_ns,
        "st_ctime": stamp_ns,
    }


def _dir_stat() -> dict:
    """目录的合成属性（时间为纳秒）"""
    now_ns = time.time_ns()
    uid, gid = _owner()
    return {
        "st_mode": 0o040755,
        "st_nlink": 2,
        "st_size": 0,
        "st_uid": uid,
        "st_gid": gid,
        "st_atime": now_ns,
        "st_mtime": now_ns,
        "st_ctime": now_ns,
    }


class LinuxFuseMount:
    """
    单个挂载点的 FUSE 会话。

    :param mount_id: 挂载点 ID
    :param sync_root: 本地目录（必须可创建；挂载后成为 FUSE 视图）
    :param fetch: `fetch(rel_path) -> bytes`，按相对路径取远端完整内容
    :param listing: `listing() -> list[dict]`，返回索引条目
                    （`rel_path` / `size` / `mtime` / `is_dir`），用于补足在线文件
    """

    def __init__(
        self,
        mount_id: str,
        sync_root: str,
        fetch: _FETCH,
        listing: _LISTING | None = None,
        on_local_change: Callable[[str], None] | None = None,
    ):
        self.mount_id = mount_id
        self.sync_root = os.path.abspath(sync_root)
        self._fetch = fetch
        self._listing = listing
        self._on_local_change = on_local_change
        # 正在被写入的相对路径（写入期间不参与上传，避免把半截文件传上去）
        self._open_writes: dict[str, int] = {}
        self._writes_lock = threading.Lock()

        self._fd = -1
        self._base = ""
        self._thread: threading.Thread | None = None
        self._ready = False
        self._closed = False
        self._error: BaseException | None = None
        self._lifecycle_lock = threading.Lock()

        # 索引快照：(entries{rel: row}, dirs{dir_rel: {name: is_dir}})
        self._snap: tuple[dict[str, dict], dict[str, dict[str, bool]]] = ({}, {"": {}})
        self._snap_at = 0.0
        self._snap_lock = threading.Lock()

        # 同一文件并发水合只下载一次
        self._inflight: dict[str, threading.Lock] = {}
        self._inflight_lock = threading.Lock()

    # ── 生命周期 ──────────────────────────────────

    @property
    def base_path(self) -> str:
        """底层目录的访问前缀（`/proc/self/fd/<fd>`）"""
        return self._base

    def local_root(self) -> str:
        """
        本地缓存的**真实**根路径（不受挂载影响）。

        挂载生效时返回 `/proc/self/fd/<fd>`，否则返回 `sync_root`。
        自动同步扫描必须用它，否则 `os.walk` 会遍历 FUSE 视图本身。
        """
        return self._base or self.sync_root

    def begin_write(self, rel: str) -> None:
        """开始写入某路径（写期间不参与自动上传）"""
        with self._writes_lock:
            self._open_writes[rel] = self._open_writes.get(rel, 0) + 1

    def end_write(self, rel: str) -> None:
        """结束写入：计数归零后通知上层「该文件已可上传」"""
        with self._writes_lock:
            count = self._open_writes.get(rel, 0) - 1
            if count > 0:
                self._open_writes[rel] = count
                return
            self._open_writes.pop(rel, None)
        self.note_change(rel)

    def is_open_for_write(self, rel: str) -> bool:
        """该路径当前是否有未关闭的写句柄"""
        with self._writes_lock:
            return bool(self._open_writes.get(rel))

    def note_change(self, rel: str) -> None:
        """通知上层「本地新增 / 修改了该文件」（删除不通知）"""
        if not rel or self._on_local_change is None:
            return
        try:
            self._on_local_change(rel)
        except Exception as e:  # noqa: BLE001 - 回调异常不能打断文件系统操作
            logger.warning("本地变更回调异常 rel=%s: %s", rel, type(e).__name__)

    def real_path(self, rel: str) -> str:
        """
        相对路径 → 底层真实路径。

        注意：**不能**用 `sync_root` 拼（挂载后那是 FUSE 视图，会递归）；
        必须走挂载前拿到的底层 fd。
        """
        if not self._base:
            raise _FuseOSError(errno.ENODEV)
        if not rel:
            return self._base
        return os.path.join(self._base, rel.replace("/", os.sep))

    def start(self) -> bool:
        """挂载 FUSE 文件系统；成功（已就绪）返回 True"""
        with self._lifecycle_lock:
            if self._ready:
                return True
            if self._closed or not is_available():
                return False

            # 上次异常退出会留下「死」挂载点（访问报 ENOTCONN）：
            # 必须在 makedirs 之前卸掉，否则连 isdir 判定都会失败。
            if _is_mountpoint(self.sync_root):
                logger.info("清理残留挂载点：%s", self.sync_root)
                self._unmount()
            try:
                os.makedirs(self.sync_root, exist_ok=True)
            except OSError as e:
                # 兜底：极少数情况下挂载点检测仍有漏网，卸载后再试一次
                logger.warning("创建挂载点目录失败（尝试清理后重试）%s: %s",
                               self.sync_root, type(e).__name__)
                self._unmount()
                try:
                    os.makedirs(self.sync_root, exist_ok=True)
                except OSError:
                    return False
            try:
                self._fd = os.open(
                    self.sync_root, os.O_DIRECTORY | os.O_PATH | os.O_CLOEXEC,
                )
            except OSError as e:
                logger.warning("打开挂载点底层目录失败 %s: %s",
                               self.sync_root, type(e).__name__)
                return False
            self._base = f"/proc/self/fd/{self._fd}"

            self._thread = threading.Thread(
                target=self._run,
                name=f"jflove-fuse-{self.mount_id}",
                daemon=True,
            )
            self._thread.start()

            if not self._wait_mounted(timeout=15.0):
                logger.warning("FUSE 挂载未就绪 mount=%s: %r",
                               self.mount_id, self._error)
                self._close_fd()
                return False

            self._ready = True
            logger.info("FUSE 挂载就绪 mount=%s root=%s", self.mount_id, self.sync_root)
            return True

    def stop(self) -> None:
        """卸载并释放底层 fd（幂等；退出应用前必须调用）"""
        with self._lifecycle_lock:
            self._closed = True
            if self._ready:
                self._unmount()
                self._ready = False
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._close_fd()

    def _run(self) -> None:
        """挂载线程：`fuse_main` 阻塞到卸载为止"""
        try:
            _FUSE(
                _FuseOps(self),
                self.sync_root,
                foreground=True,
                nonempty=True,
                fsname=f"jflove:{self.mount_id}",
            )
        except BaseException as e:  # noqa: BLE001 - 线程内异常必须记住，不能吞
            self._error = e

    def _wait_mounted(self, timeout: float) -> bool:
        """等待挂载生效（或挂载线程失败退出）"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _is_mountpoint(self.sync_root):
                return True
            if self._thread is not None and not self._thread.is_alive():
                return _is_mountpoint(self.sync_root)
            time.sleep(0.05)
        return _is_mountpoint(self.sync_root)

    def _unmount(self) -> bool:
        """卸载挂载点（多命令 / 多轮重试；最后退回 lazy 卸载）"""
        for cmd in _UNMOUNT_CMDS:
            if shutil.which(cmd[0]) is None:
                continue
            for _ in range(3):
                try:
                    result = subprocess.run(
                        [*cmd, self.sync_root],
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                except (OSError, subprocess.SubprocessError):
                    break
                if result.returncode == 0 or not _is_mountpoint(self.sync_root):
                    return True
                time.sleep(0.2)
        for exe in ("fusermount", "fusermount3"):
            if shutil.which(exe) is None:
                continue
            try:
                subprocess.run(
                    [exe, "-u", "-z", self.sync_root],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
            except (OSError, subprocess.SubprocessError):
                continue
        if _is_mountpoint(self.sync_root):
            logger.warning("FUSE 卸载失败 mount=%s root=%s",
                           self.mount_id, self.sync_root)
            return False
        return True

    def _close_fd(self) -> None:
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError:
                pass
        self._fd = -1
        self._base = ""

    # ── 索引快照 ──────────────────────────────────

    def _snapshot(self) -> tuple[dict[str, dict], dict[str, dict[str, bool]]]:
        """取索引快照（TTL 内复用；索引查询失败时退回上次结果）"""
        now = time.monotonic()
        with self._snap_lock:
            if self._snap_at and now - self._snap_at < _SNAPSHOT_TTL:
                return self._snap
            rows: list[dict] = []
            if self._listing is not None:
                try:
                    rows = self._listing() or []
                except Exception as e:  # noqa: BLE001 - 索引坏了也要让目录可用
                    logger.warning("读取挂载索引失败：%s", type(e).__name__)
                    return self._snap
            entries, dirs = _build_tree(rows)
            self._snap = (entries, dirs)
            self._snap_at = time.monotonic()
            return self._snap

    def entry_of(self, rel: str) -> dict | None:
        """按相对路径取索引条目"""
        return self._snapshot()[0].get(rel)

    def has_dir(self, rel: str) -> bool:
        """该相对路径是否是索引里的目录（含隐式父目录）"""
        return rel in self._snapshot()[1]

    def children_of(self, rel: str) -> dict[str, bool]:
        """目录下的子项：`{name: is_dir}`（含索引推导出的隐式目录）"""
        return dict(self._snapshot()[1].get(rel, {}))

    # ── 水合 ──────────────────────────────────────

    def hydrate(self, rel: str) -> None:
        """
        把在线文件下载到本地缓存（已存在则直接返回）。

        :raises _FuseOSError: 索引里没有该文件（ENOENT）或下载失败（EIO）
        """
        if not rel:
            raise _FuseOSError(errno.EISDIR)
        real = self.real_path(rel)
        if os.path.isfile(real):
            return
        entry = self.entry_of(rel)
        if entry is None:
            raise _FuseOSError(errno.ENOENT)
        if entry.get("is_dir"):
            raise _FuseOSError(errno.EISDIR)

        lock = self._lock_for(rel)
        with lock:
            if os.path.isfile(real):
                return
            try:
                data = self._fetch(rel)
            except Exception as e:  # noqa: BLE001 - 远端失败要让系统调用快速失败
                logger.warning("水合失败 rel=%s: %s", rel, type(e).__name__)
                raise _FuseOSError(errno.EIO) from e

            tmp = f"{real}.jflove-part"
            os.makedirs(os.path.dirname(real), exist_ok=True)
            try:
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
                try:
                    view = memoryview(data)
                    while view:
                        written = os.write(fd, view)
                        view = view[written:]
                finally:
                    os.close(fd)
                mtime = float(entry.get("mtime") or 0)
                if mtime:
                    os.utime(tmp, (mtime, mtime))
                os.replace(tmp, real)
            except OSError as e:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                logger.warning("水合写入失败 rel=%s: %s", rel, type(e).__name__)
                raise _FuseOSError(errno.EIO) from e

    def _lock_for(self, rel: str) -> threading.Lock:
        with self._inflight_lock:
            lock = self._inflight.get(rel)
            if lock is None:
                lock = threading.Lock()
                self._inflight[rel] = lock
            return lock

    def is_cached(self, rel_path: str) -> bool:
        """
        该文件是否已缓存在本地。

        必须查**底层目录**：挂载后 `sync_root/rel` 的 stat 会命中索引，
        永远返回「是个文件」，无法区分已缓存 / 仅在线。
        """
        if not self._base:
            return False
        return os.path.isfile(self.real_path(rel_path))

    # ── 占位接口（FUSE 视图由索引动态生成，无需预建占位）────────

    def add_file(self, rel_path: str, size: int, mtime: float) -> bool:
        """FUSE 下无占位概念：返回 False 表示「未新建占位」（上层仅用于计数）"""
        return False

    def remove_file(self, rel_path: str) -> None:
        """删除本地缓存文件（不影响远端）"""
        if not self._base:
            return
        real = self.real_path(rel_path)
        try:
            if os.path.isfile(real):
                os.remove(real)
        except OSError:
            pass


def _build_tree(
    rows: list[dict],
) -> tuple[dict[str, dict], dict[str, dict[str, bool]]]:
    """
    把索引行整理成「路径 → 条目」与「目录 → 子项」。

    索引里可能缺少父目录行（服务端只登记文件），因此沿途的中间目录
    都要作为隐式目录补出来，否则 `ls` / `cd` 进不去。
    """
    entries: dict[str, dict] = {}
    dirs: dict[str, dict[str, bool]] = {"": {}}
    for row in rows:
        rel = str(row.get("rel_path") or "").strip("/")
        if not rel:
            continue
        is_dir = bool(row.get("is_dir"))
        entries[rel] = {
            "size": int(row.get("size") or 0),
            "mtime": float(row.get("mtime") or 0),
            "is_dir": is_dir,
        }
        parts = rel.split("/")
        dirs.setdefault("", {})
        for i in range(len(parts) - 1):
            parent = "/".join(parts[:i])
            child_dir = "/".join(parts[: i + 1])
            dirs.setdefault(parent, {})[parts[i]] = True
            dirs.setdefault(child_dir, {})
        parent = "/".join(parts[:-1])
        dirs.setdefault(parent, {})[parts[-1]] = is_dir
        if is_dir:
            dirs.setdefault(rel, {})
    return entries, dirs
