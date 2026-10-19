"""
Windows CfAPI 挂载驱动（v1.7.0）

> 平台门面在 `src/utils/mount_vfs.py`；本模块只在 Windows 上可 import
> （模块级 `ctypes.WinDLL` 在其它平台不存在），调用方请走门面的 `create_mount`。

把「本地缓存目录」注册成 Windows 云同步根（Cloud Files sync root）：
  - 远端文件以**占位**形式出现在资源管理器，带云状态（仅在线 / 已缓存）；
  - 用户打开占位时触发 `FETCH_DATA` 回调，驱动按需下载并写回内容。

实现路线（已由 `poc/cfapi_probe.py` 三步判据验证，退出码 0）：
  1. `CfRegisterSyncRoot` 注册；
  2. `CfConnectSyncRoot` 连接并登记 `FETCH_DATA` 回调；
  3. 创建占位：先建真实文件（truncate 到目标大小）→ `CfConvertToPlaceholder`
     （`MARK_IN_SYNC | DEHYDRATE`）→ 文件变脱水占位；
  4. 读取占位 → 回调 → `CfExecute(TRANSFER_DATA)` 写回。

限制（如实登记）：
  - 回调里整文件读入内存再切片；超大文件（几百 MB 以上）会占用大量内存，
    后续应改为分块流式（按 `RequiredFileOffset/Length` 拉取范围）。
  - 回调在系统线程执行，内部用同步 HTTP（阻塞该线程，不影响 GUI）。
"""

from __future__ import annotations

import ctypes
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from ctypes import wintypes
from typing import Callable

from src.utils.logger import get_logger

logger = get_logger(__name__)

_cldapi = ctypes.WinDLL("cldapi", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CF_CALLBACK_TYPE_FETCH_DATA = 0
CF_CALLBACK_TYPE_FETCH_PLACEHOLDERS = 2
CF_CALLBACK_TYPE_NOTIFY_DEHYDRATE = 6
CF_CALLBACK_TYPE_NOTIFY_DELETE = 8
CF_CALLBACK_TYPE_NOTIFY_RENAME = 10
CF_CALLBACK_TYPE_NONE = 0xFFFFFFFF
CF_OPERATION_TYPE_TRANSFER_DATA = 0
CF_OPERATION_TYPE_TRANSFER_PLACEHOLDERS = 2
CF_OPERATION_TYPE_ACK_DELETE = 3
CF_OPERATION_TYPE_ACK_RENAME = 4
CF_OPERATION_TYPE_ACK_DEHYDRATE = 5
CF_CONNECT_FLAG_NONE = 0
CF_CREATE_FLAG_NONE = 0
CF_CONVERT_FLAG_MARK_IN_SYNC = 1
CF_CONVERT_FLAG_DEHYDRATE = 2
_GENERIC_READ_WRITE = 0x80000000 | 0x40000000
#: 脱水占位的文件属性标记（Windows 10+）：置位表示「内容不在本地，读取时需回调水合」
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x80
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class _CF_CALLBACK_INFO(ctypes.Structure):
    _fields_ = [
        ("StructSize", wintypes.DWORD),
        ("ConnectionKey", ctypes.c_longlong),
        ("CallbackContext", ctypes.c_void_p),
        ("VolumeGuidName", wintypes.LPCWSTR),
        ("VolumeDosName", wintypes.LPCWSTR),
        ("VolumeSerialNumber", wintypes.DWORD),
        ("SyncRootFileId", ctypes.c_longlong),
        ("SyncRootIdentity", ctypes.c_void_p),
        ("SyncRootIdentityLength", wintypes.DWORD),
        ("FileId", ctypes.c_longlong),
        ("FileSize", ctypes.c_longlong),
        ("FileIdentity", ctypes.c_void_p),
        ("FileIdentityLength", wintypes.DWORD),
        ("NormalizedPath", wintypes.LPCWSTR),
        ("TransferKey", ctypes.c_longlong),
        ("PriorityHint", ctypes.c_ubyte),
        ("CorrelationVector", ctypes.c_void_p),
        ("ProcessInfo", ctypes.c_void_p),
        ("RequestKey", ctypes.c_longlong),
    ]


class _FetchDataParams(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("RequiredFileOffset", ctypes.c_longlong),
        ("RequiredLength", ctypes.c_longlong),
        ("OptionalFileOffset", ctypes.c_longlong),
        ("OptionalLength", ctypes.c_longlong),
        ("LastDehydrationTime", ctypes.c_longlong),
        ("LastDehydrationReason", wintypes.DWORD),
    ]


class _CF_CALLBACK_PARAMETERS(ctypes.Structure):
    _fields_ = [
        ("ParamSize", wintypes.ULONG),
        ("FetchData", _FetchDataParams),
    ]


class _CF_OPERATION_INFO(ctypes.Structure):
    _fields_ = [
        ("StructSize", wintypes.ULONG),
        ("Type", wintypes.ULONG),
        ("ConnectionKey", ctypes.c_longlong),
        ("TransferKey", ctypes.c_longlong),
        ("CorrelationVector", ctypes.c_void_p),
        ("SyncStatus", ctypes.c_void_p),
        ("RequestKey", ctypes.c_longlong),
    ]


class _TransferData(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Buffer", ctypes.c_void_p),
        ("Offset", ctypes.c_longlong),
        ("Length", ctypes.c_longlong),
        ("Completion", ctypes.c_longlong),
    ]


class _AckPayload(ctypes.Structure):
    """ACK_DELETE / ACK_RENAME / ACK_DEHYDRATE 共用的载荷"""
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("CompletionStatus", ctypes.c_longlong),
    ]


class _TransferPlaceholders(ctypes.Structure):
    """TRANSFER_PLACEHOLDERS：用于回答「目录内容是否齐全」"""
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("CompletionStatus", ctypes.c_longlong),
        ("PlaceholderTotalCount", ctypes.c_longlong),
        ("PlaceholderArray", ctypes.c_void_p),
        ("PlaceholderCount", wintypes.DWORD),
        ("EntriesProcessed", wintypes.DWORD),
    ]


class _OpPayload(ctypes.Union):
    _fields_ = [
        ("TransferData", _TransferData),
        ("Ack", _AckPayload),
        ("TransferPlaceholders", _TransferPlaceholders),
    ]


class _CF_OPERATION_PARAMETERS(ctypes.Structure):
    _fields_ = [
        ("StructSize", wintypes.ULONG),
        ("Type", wintypes.ULONG),
        ("Payload", _OpPayload),
        ("_pad", ctypes.c_byte * 48),
    ]


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _CF_SYNC_REGISTRATION(ctypes.Structure):
    _fields_ = [
        ("StructSize", wintypes.DWORD),
        ("ProviderName", wintypes.LPCWSTR),
        ("ProviderVersion", wintypes.LPCWSTR),
        ("SyncRootIdentity", ctypes.c_void_p),
        ("SyncRootIdentityLength", wintypes.DWORD),
        ("FileIdentity", ctypes.c_void_p),
        ("FileIdentityLength", wintypes.DWORD),
        ("ProviderId", _GUID),
    ]


class _CF_HYDRATION_POLICY(ctypes.Structure):
    _fields_ = [("Primary", ctypes.c_uint32), ("Modifier", ctypes.c_uint32)]


class _CF_POPULATION_POLICY(ctypes.Structure):
    _fields_ = [("Primary", ctypes.c_uint32), ("Modifier", ctypes.c_uint32)]


class _CF_SYNC_POLICIES(ctypes.Structure):
    _fields_ = [
        ("StructSize", wintypes.DWORD),
        ("Hydration", _CF_HYDRATION_POLICY),
        ("Population", _CF_POPULATION_POLICY),
        ("InSync", ctypes.c_uint32),
        ("HardLink", ctypes.c_uint32),
        ("PlaceholderManagement", ctypes.c_uint32),
    ]


_CF_CALLBACK = ctypes.WINFUNCTYPE(
    None, ctypes.POINTER(_CF_CALLBACK_INFO), ctypes.POINTER(_CF_CALLBACK_PARAMETERS),
)


class _CF_CALLBACK_REGISTRATION(ctypes.Structure):
    _fields_ = [("Type", wintypes.DWORD), ("Callback", _CF_CALLBACK)]


_PROVIDER_ID = _GUID(
    0x4A464C56, 0x3137, 0x0001,
    (ctypes.c_ubyte * 8)(0x9A, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77),
)

_cldapi.CfRegisterSyncRoot.argtypes = [
    wintypes.LPCWSTR, ctypes.POINTER(_CF_SYNC_REGISTRATION),
    ctypes.POINTER(_CF_SYNC_POLICIES), wintypes.DWORD, ctypes.c_void_p,
]
_cldapi.CfRegisterSyncRoot.restype = ctypes.c_long
_cldapi.CfUnregisterSyncRoot.argtypes = [wintypes.LPCWSTR]
_cldapi.CfUnregisterSyncRoot.restype = ctypes.c_long
_cldapi.CfConnectSyncRoot.argtypes = [
    wintypes.LPCWSTR, ctypes.POINTER(_CF_CALLBACK_REGISTRATION),
    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_longlong),
]
_cldapi.CfConnectSyncRoot.restype = ctypes.c_long
_cldapi.CfDisconnectSyncRoot.argtypes = [ctypes.c_longlong]
_cldapi.CfDisconnectSyncRoot.restype = ctypes.c_long
_cldapi.CfConvertToPlaceholder.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
    ctypes.POINTER(ctypes.c_longlong), ctypes.c_void_p,
]
_cldapi.CfConvertToPlaceholder.restype = ctypes.c_long
_cldapi.CfExecute.argtypes = [
    ctypes.POINTER(_CF_OPERATION_INFO), ctypes.POINTER(_CF_OPERATION_PARAMETERS),
]
_cldapi.CfExecute.restype = ctypes.c_long
_kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


def is_available() -> bool:
    """当前平台是否支持 CfAPI（仅 Windows）"""
    return os.name == "nt"


class WinCfapiMount:
    """
    单个挂载点的 CfAPI 会话。

    :param sync_root: 本地缓存目录（必须已存在）
    :param fetch: `fetch(rel_path) -> bytes`，按相对路径取远端完整内容
    """

    def __init__(
        self,
        mount_id: str,
        sync_root: str,
        fetch: Callable[[str], bytes],
        on_local_change: Callable[[str], None] | None = None,
    ):
        # on_local_change 在 CfAPI 下不使用（没有写入回调），保留参数以统一工厂契约
        self.mount_id = mount_id
        self.sync_root = os.path.abspath(sync_root)
        self._fetch = fetch
        self._connection_key = 0
        self._registered = False
        self._connected = False
        self._closed = False
        # 回调在系统线程执行：下载走线程池 + 超时，避免资源管理器被慢请求卡死
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="jflove-vfs")
        self._lifecycle_lock = threading.Lock()
        # 必须持有回调引用，否则会被 GC 回收（系统线程回调崩溃）
        self._cb_fetch = _CF_CALLBACK(self._on_fetch_data)
        self._cb_placeholders = _CF_CALLBACK(self._on_fetch_placeholders)
        self._cb_delete = _CF_CALLBACK(self._on_delete)
        self._cb_rename = _CF_CALLBACK(self._on_rename)
        self._cb_dehydrate = _CF_CALLBACK(self._on_dehydrate)
        self._registrations = (_CF_CALLBACK_REGISTRATION * 6)(
            _CF_CALLBACK_REGISTRATION(CF_CALLBACK_TYPE_FETCH_DATA, self._cb_fetch),
            _CF_CALLBACK_REGISTRATION(
                CF_CALLBACK_TYPE_FETCH_PLACEHOLDERS, self._cb_placeholders,
            ),
            _CF_CALLBACK_REGISTRATION(CF_CALLBACK_TYPE_NOTIFY_DELETE, self._cb_delete),
            _CF_CALLBACK_REGISTRATION(CF_CALLBACK_TYPE_NOTIFY_RENAME, self._cb_rename),
            _CF_CALLBACK_REGISTRATION(CF_CALLBACK_TYPE_NOTIFY_DEHYDRATE, self._cb_dehydrate),
            _CF_CALLBACK_REGISTRATION(CF_CALLBACK_TYPE_NONE, _CF_CALLBACK()),
        )

    # ── 生命周期 ──────────────────────────────────

    def start(self) -> bool:
        """注册并连接 sync root；成功返回 True"""
        if not is_available():
            return False
        os.makedirs(self.sync_root, exist_ok=True)

        reg = _CF_SYNC_REGISTRATION()
        reg.StructSize = ctypes.sizeof(_CF_SYNC_REGISTRATION)
        reg.ProviderName = "JFLove"
        reg.ProviderVersion = "1.7.0"
        root_id = self.mount_id.encode("utf-8")
        root_buf = ctypes.create_string_buffer(root_id, len(root_id))
        reg.SyncRootIdentity = ctypes.cast(root_buf, ctypes.c_void_p)
        reg.SyncRootIdentityLength = len(root_id)
        reg.ProviderId = _PROVIDER_ID
        self._root_buf = root_buf

        policies = _CF_SYNC_POLICIES()
        policies.StructSize = ctypes.sizeof(_CF_SYNC_POLICIES)
        policies.Hydration.Primary = 1  # PROGRESSIVE
        policies.Population.Primary = 1  # FULL

        hr = _cldapi.CfRegisterSyncRoot(
            self.sync_root, ctypes.byref(reg), ctypes.byref(policies), 0, None,
        )
        if hr < 0:
            # 上一次异常退出会留下仍然有效的注册（进程被杀时来不及注销）。
            # 这种情况下**不要直接放弃**：直接连上去，否则整个挂载点会降级成
            # 应用内视图，直到用户手动清理注册表项。
            logger.warning(
                "CfRegisterSyncRoot 未成功 mount=%s hr=0x%08X（尝试直接连接已存在的注册）",
                self.mount_id, hr & 0xFFFFFFFF,
            )
        else:
            self._registered = True

        key = ctypes.c_longlong(0)
        hr = _cldapi.CfConnectSyncRoot(
            self.sync_root, self._registrations, None,
            CF_CONNECT_FLAG_NONE, ctypes.byref(key),
        )
        if hr < 0:
            logger.warning("CfConnectSyncRoot 失败 mount=%s hr=0x%08X",
                           self.mount_id, hr & 0xFFFFFFFF)
            self._unregister()
            return False
        self._connection_key = key.value
        self._connected = True
        # 连接成功即视为「本进程负责注销」：哪怕是上一次运行遗留的注册，
        # 退出时也要清掉，否则下次启动又会撞上同一条陈旧注册。
        self._registered = True
        logger.info("CfAPI 挂载就绪 mount=%s root=%s", self.mount_id, self.sync_root)
        return True

    def stop(self) -> None:
        """断开连接并注销（幂等；退出应用前必须调用，否则目录会一直被接管）"""
        with self._lifecycle_lock:
            self._closed = True
            if self._connected:
                _cldapi.CfDisconnectSyncRoot(self._connection_key)
                self._connected = False
            self._unregister()
        self._pool.shutdown(wait=False)

    def _unregister(self) -> None:
        if self._registered:
            _cldapi.CfUnregisterSyncRoot(self.sync_root)
            self._registered = False

    # ── 占位管理 ──────────────────────────────────

    def add_file(self, rel_path: str, size: int, mtime: float) -> bool:
        """
        在 sync root 下创建一个**脱水占位**。

        :param rel_path: 相对路径（`/` 分隔）
        :param size: 远端文件大小
        :param mtime: 远端 mtime（Unix 秒）
        :returns: 是否成功
        """
        local = os.path.join(self.sync_root, rel_path.replace("/", os.sep))
        parent = os.path.dirname(local)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if os.path.exists(local):
            return True

        try:
            # 先建真实文件并 truncate 到目标大小（内容随后被脱水清空）
            with open(local, "wb") as f:
                f.truncate(max(0, int(size)))
            if mtime:
                os.utime(local, (mtime, mtime))
        except OSError as e:
            logger.warning("创建占位文件失败（本地）: %s", type(e).__name__)
            return False

        handle = _kernel32.CreateFileW(
            local, _GENERIC_READ_WRITE, 0, None,
            _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None,
        )
        if handle == _INVALID_HANDLE:
            logger.warning("打开占位文件失败（error=%s）", ctypes.get_last_error())
            return False
        try:
            identity = rel_path.encode("utf-8")
            id_buf = ctypes.create_string_buffer(identity, len(identity))
            usn = ctypes.c_longlong(0)
            hr = _cldapi.CfConvertToPlaceholder(
                handle,
                ctypes.cast(id_buf, ctypes.c_void_p),
                len(identity),
                CF_CONVERT_FLAG_MARK_IN_SYNC | CF_CONVERT_FLAG_DEHYDRATE,
                ctypes.byref(usn),
                None,
            )
        finally:
            _kernel32.CloseHandle(handle)

        if hr < 0:
            logger.warning("CfConvertToPlaceholder 失败 rel=%s hr=0x%08X",
                           rel_path, hr & 0xFFFFFFFF)
            # 失败时删掉残留的真实文件，避免用户看到一堆 0 内容的普通文件
            try:
                os.remove(local)
            except OSError:
                pass
            return False
        return True

    def local_root(self) -> str:
        """本地缓存的真实根路径（CfAPI 下就是 sync root 本身）"""
        return self.sync_root

    def real_path(self, rel: str) -> str:
        """相对路径 → 本地真实路径（与 FUSE 实现同名同义）"""
        if not rel:
            return self.sync_root
        return os.path.join(self.sync_root, rel.replace("/", os.sep))

    def note_change(self, rel_path: str) -> None:
        """CfAPI 没有写入回调：本地变更由上层定时扫描发现"""

    def begin_write(self, rel_path: str) -> None:
        """CfAPI 无写句柄跟踪（占位以保持接口一致）"""

    def end_write(self, rel_path: str) -> None:
        """CfAPI 无写句柄跟踪（占位以保持接口一致）"""

    def is_open_for_write(self, rel_path: str) -> bool:
        """CfAPI 无法判断写入状态，恒为 False（依靠 mtime 稳定性判断）"""
        return False

    def is_cached(self, rel_path: str) -> bool:
        """
        该占位的内容是否**已在本机**（已水合）。

        不能只看「文件存在」：CfAPI 的脱水占位同样是普通文件（只是内容不在本地）。
        用 `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS` 判定 —— 置位 = 仅在云端。
        """
        local = os.path.join(self.sync_root, rel_path.replace("/", os.sep))
        try:
            info = os.stat(local)
        except OSError:
            return False
        if not (info.st_mode & 0o170000) == 0o100000:  # 非普通文件
            return False
        attrs = getattr(info, "st_file_attributes", 0)
        return not (attrs & _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)

    def remove_file(self, rel_path: str) -> None:
        """删除占位（本地文件）"""
        local = os.path.join(self.sync_root, rel_path.replace("/", os.sep))
        try:
            if os.path.isfile(local):
                os.remove(local)
        except OSError:
            pass

    # ── 系统回调 ──────────────────────────────────

    def _on_fetch_data(self, info_ptr, params_ptr) -> None:  # noqa: ANN001
        """
        FETCH_DATA：按需取远端内容并写回。

        超时/失败时写回 0 字节，让系统**立即失败**而不是挂起资源管理器。
        """
        try:
            info = info_ptr.contents
            params = params_ptr.contents
            rel = self._identity_of(info)
            if not rel or self._closed:
                return

            offset = int(params.FetchData.RequiredFileOffset)
            length = int(params.FetchData.RequiredLength)

            chunk = b""
            try:
                data = self._fetch_with_timeout(rel)
                chunk = data[offset: offset + length]
            except Exception as e:  # noqa: BLE001 - 远端不可达必须快速失败
                logger.warning("水合失败 rel=%s: %s", rel, type(e).__name__)

            buf = ctypes.create_string_buffer(chunk, len(chunk)) if chunk else None
            op_info = self._op_info(info, CF_OPERATION_TYPE_TRANSFER_DATA)
            op_params = _CF_OPERATION_PARAMETERS()
            op_params.StructSize = ctypes.sizeof(_CF_OPERATION_PARAMETERS)
            op_params.Type = CF_OPERATION_TYPE_TRANSFER_DATA
            op_params.Payload.TransferData.Flags = 0
            op_params.Payload.TransferData.Buffer = (
                ctypes.cast(buf, ctypes.c_void_p) if buf is not None else None
            )
            op_params.Payload.TransferData.Offset = offset
            op_params.Payload.TransferData.Length = len(chunk)
            op_params.Payload.TransferData.Completion = 0

            hr = _cldapi.CfExecute(
                ctypes.byref(op_info), ctypes.byref(op_params),
            )
            if hr < 0:
                logger.warning("CfExecute 失败 rel=%s hr=0x%08X",
                               rel, hr & 0xFFFFFFFF)
        except Exception as e:  # noqa: BLE001 - 回调不能抛异常到系统线程
            logger.warning("FETCH_DATA 回调异常: %s", type(e).__name__)

    def _on_fetch_placeholders(self, info_ptr, params_ptr) -> None:  # noqa: ANN001
        """
        目录枚举通知：告诉平台「该目录内容已齐全」。

        Population=FULL 下通常不触发；但被当作"待填充"的普通目录会触发，
        不回应会让资源管理器**进入文件夹时卡住**。
        """
        try:
            info = info_ptr.contents
            op_info = self._op_info(info, CF_OPERATION_TYPE_TRANSFER_PLACEHOLDERS)
            op_params = _CF_OPERATION_PARAMETERS()
            op_params.StructSize = ctypes.sizeof(_CF_OPERATION_PARAMETERS)
            op_params.Type = CF_OPERATION_TYPE_TRANSFER_PLACEHOLDERS
            tp = op_params.Payload.TransferPlaceholders
            tp.Flags = 0
            tp.CompletionStatus = 0
            tp.PlaceholderTotalCount = 0
            tp.PlaceholderArray = None
            tp.PlaceholderCount = 0
            tp.EntriesProcessed = 0
            hr = _cldapi.CfExecute(
                ctypes.byref(op_info), ctypes.byref(op_params),
            )
            if hr < 0:
                logger.warning("TRANSFER_PLACEHOLDERS 失败 hr=0x%08X", hr & 0xFFFFFFFF)
        except Exception as e:  # noqa: BLE001
            logger.warning("FETCH_PLACEHOLDERS 回调异常: %s", type(e).__name__)

    def _on_delete(self, info_ptr, params_ptr) -> None:  # noqa: ANN001
        """删除通知：必须 ACK，否则资源管理器会挂起（本版只删本地，不动远端）"""
        self._ack(info_ptr, CF_OPERATION_TYPE_ACK_DELETE)

    def _on_rename(self, info_ptr, params_ptr) -> None:  # noqa: ANN001
        """重命名通知：ACK 让操作立即完成"""
        self._ack(info_ptr, CF_OPERATION_TYPE_ACK_RENAME)

    def _on_dehydrate(self, info_ptr, params_ptr) -> None:  # noqa: ANN001
        """脱水通知：ACK 让「释放空间」立即完成"""
        self._ack(info_ptr, CF_OPERATION_TYPE_ACK_DEHYDRATE)

    def _ack(self, info_ptr, op_type: int) -> None:  # noqa: ANN001
        """对删除 / 重命名 / 脱水操作发 ACK"""
        try:
            info = info_ptr.contents
            op_info = self._op_info(info, op_type)
            op_params = _CF_OPERATION_PARAMETERS()
            op_params.StructSize = ctypes.sizeof(_CF_OPERATION_PARAMETERS)
            op_params.Type = op_type
            op_params.Payload.Ack.Flags = 0
            op_params.Payload.Ack.CompletionStatus = 0
            hr = _cldapi.CfExecute(
                ctypes.byref(op_info), ctypes.byref(op_params),
            )
            if hr < 0:
                logger.warning("ACK 失败 type=%s hr=0x%08X",
                               op_type, hr & 0xFFFFFFFF)
        except Exception as e:  # noqa: BLE001
            logger.warning("ACK 回调异常 type=%s: %s", op_type, type(e).__name__)

    def _op_info(self, info: _CF_CALLBACK_INFO, op_type: int) -> _CF_OPERATION_INFO:
        """构造 CF_OPERATION_INFO（连接键 / 传输键 / 请求键均来自回调）"""
        op = _CF_OPERATION_INFO()
        op.StructSize = ctypes.sizeof(_CF_OPERATION_INFO)
        op.Type = op_type
        op.ConnectionKey = info.ConnectionKey
        op.TransferKey = info.TransferKey
        op.RequestKey = info.RequestKey
        return op

    def _fetch_with_timeout(self, rel: str, timeout: float = 8.0) -> bytes:
        """在线程池里取内容并限时，避免系统线程被慢请求长期占用"""
        future: Future = self._pool.submit(self._fetch, rel)
        return future.result(timeout=timeout)

    def _identity_of(self, info: _CF_CALLBACK_INFO) -> str:
        """从 FileIdentity 读回相对路径（创建占位时写入的 UTF-8 路径）"""
        if info.FileIdentity and info.FileIdentityLength:
            raw = ctypes.string_at(info.FileIdentity, info.FileIdentityLength)
            return raw.decode("utf-8", "replace")
        # 兜底：用绝对路径相对 sync root 计算
        path = info.NormalizedPath or ""
        try:
            return os.path.relpath(path, self.sync_root).replace(os.sep, "/")
        except ValueError:
            return ""
