"""
Windows CfAPI 可行性探针（v1.7.0 Phase 4a 预研）

目标（设计文档 §11.1 的 3 步判据）：
  1. 用 ctypes 定义 CF_CALLBACK_REGISTRATION / CF_CALLBACK_INFO / CF_OPERATION_INFO；
  2. 注册 sync root → 创建 1 个占位文件；
  3. 读取占位文件触发 FETCH_DATA 回调 → CfExecute(TRANSFER_DATA) 写回内容。

退出码：0 = 三步全部跑通；非 0 = 任一步失败（打印 HRESULT 与原因）。

注意：本脚本只操作临时目录，不触碰仓库数据；可重复运行。
"""

from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import time
from ctypes import wintypes

cldapi = ctypes.WinDLL("cldapi", use_last_error=True)

CF_CALLBACK_TYPE_FETCH_DATA = 0
CF_CALLBACK_TYPE_NONE = 0xFFFFFFFF
CF_OPERATION_TYPE_TRANSFER_DATA = 0
CF_CONNECT_FLAG_NONE = 0
CF_CREATE_FLAG_NONE = 0
CF_PLACEHOLDER_CREATE_FLAG_NONE = 0

CONTENT = b"hello-cfapi-poc"
CALLBACK_HITS: list[str] = []


class FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class CF_CALLBACK_INFO(ctypes.Structure):
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
    """CF_CALLBACK_PARAMETERS.FetchData 分支"""
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("RequiredFileOffset", ctypes.c_longlong),
        ("RequiredLength", ctypes.c_longlong),
        ("OptionalFileOffset", ctypes.c_longlong),
        ("OptionalLength", ctypes.c_longlong),
        ("LastDehydrationTime", ctypes.c_longlong),
        ("LastDehydrationReason", wintypes.DWORD),
    ]


class CF_CALLBACK_PARAMETERS(ctypes.Structure):
    # 官方定义：第一个字段是 ParamSize（ULONG），随后是 callback 专属 union
    _fields_ = [
        ("ParamSize", wintypes.ULONG),
        ("FetchData", _FetchDataParams),
    ]


class CF_OPERATION_INFO(ctypes.Structure):
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


class CF_OPERATION_PARAMETERS(ctypes.Structure):
    # 官方 Remarks：CfExecute 的两个参数都**以 StructSize 开头**
    _fields_ = [
        ("StructSize", wintypes.ULONG),
        ("Type", wintypes.ULONG),
        ("TransferData", _TransferData),
        ("_pad", ctypes.c_byte * 48),
    ]


class CF_FS_METADATA(ctypes.Structure):
    _fields_ = [
        ("FileSize", ctypes.c_longlong),
        ("CreationTime", FILETIME),
        ("LastWriteTime", FILETIME),
        ("LastAccessTime", FILETIME),
        ("ChangeTime", FILETIME),
        ("FileAttributes", wintypes.DWORD),
    ]


class CF_PLACEHOLDER_CREATE_INFO(ctypes.Structure):
    _fields_ = [
        ("RelativeFileName", wintypes.LPCWSTR),
        ("FsMetadata", ctypes.POINTER(CF_FS_METADATA)),
        ("FileIdentityLength", wintypes.DWORD),
        ("FileIdentity", ctypes.c_void_p),
        ("Flags", wintypes.DWORD),
        ("Result", ctypes.c_long),
        ("ReturnedUsn", ctypes.c_longlong),
        ("ReturnedFileId", ctypes.c_longlong),
    ]


CF_CALLBACK = ctypes.WINFUNCTYPE(
    None,
    ctypes.POINTER(CF_CALLBACK_INFO),
    ctypes.POINTER(CF_CALLBACK_PARAMETERS),
)


class CF_CALLBACK_REGISTRATION(ctypes.Structure):
    _fields_ = [
        ("Type", wintypes.DWORD),
        ("Callback", CF_CALLBACK),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class CF_SYNC_REGISTRATION(ctypes.Structure):
    _fields_ = [
        ("StructSize", wintypes.DWORD),
        ("ProviderName", wintypes.LPCWSTR),
        ("ProviderVersion", wintypes.LPCWSTR),
        ("SyncRootIdentity", ctypes.c_void_p),
        ("SyncRootIdentityLength", wintypes.DWORD),
        ("FileIdentity", ctypes.c_void_p),
        ("FileIdentityLength", wintypes.DWORD),
        ("ProviderId", GUID),
    ]


class CF_HYDRATION_POLICY(ctypes.Structure):
    _fields_ = [
        ("Primary", ctypes.c_uint32),
        ("Modifier", ctypes.c_uint32),
    ]


class CF_POPULATION_POLICY(ctypes.Structure):
    _fields_ = [
        ("Primary", ctypes.c_uint32),
        ("Modifier", ctypes.c_uint32),
    ]


class CF_SYNC_POLICIES(ctypes.Structure):
    _fields_ = [
        ("StructSize", wintypes.DWORD),
        ("Hydration", CF_HYDRATION_POLICY),
        ("Population", CF_POPULATION_POLICY),
        ("InSync", ctypes.c_uint32),
        ("HardLink", ctypes.c_uint32),
        ("PlaceholderManagement", ctypes.c_uint32),
    ]


#: 固定 ProviderId（同一 provider 的所有 sync root 共用）
_PROVIDER_ID = GUID(
    0x4A464C56, 0x3137, 0x0001,
    (ctypes.c_ubyte * 8)(0x9A, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77),
)


def _on_fetch_data(info_ptr, params_ptr) -> None:  # noqa: ANN001
    """FETCH_DATA 回调：按请求范围写回内容"""
    info = info_ptr.contents
    params = params_ptr.contents
    CALLBACK_HITS.append(str(info.NormalizedPath))

    offset = int(params.FetchData.RequiredFileOffset)
    length = int(params.FetchData.RequiredLength)
    chunk = CONTENT[offset: offset + length]
    buf = ctypes.create_string_buffer(chunk, len(chunk))

    op_info = CF_OPERATION_INFO()
    op_info.StructSize = ctypes.sizeof(CF_OPERATION_INFO)
    op_info.Type = CF_OPERATION_TYPE_TRANSFER_DATA
    op_info.ConnectionKey = info.ConnectionKey
    op_info.TransferKey = info.TransferKey
    op_info.RequestKey = info.RequestKey

    op_params = CF_OPERATION_PARAMETERS()
    op_params.StructSize = ctypes.sizeof(CF_OPERATION_PARAMETERS)
    op_params.Type = CF_OPERATION_TYPE_TRANSFER_DATA
    op_params.TransferData.Flags = 0
    op_params.TransferData.Buffer = ctypes.cast(buf, ctypes.c_void_p)
    op_params.TransferData.Offset = offset
    op_params.TransferData.Length = length
    op_params.TransferData.Completion = 0

    hr = cldapi.CfExecute(ctypes.byref(op_info), ctypes.byref(op_params))
    print(f"  [callback] CfExecute hr=0x{hr & 0xFFFFFFFF:08X} "
          f"offset={offset} length={length}")


cldapi.CfRegisterSyncRoot.argtypes = [
    wintypes.LPCWSTR,
    ctypes.POINTER(CF_SYNC_REGISTRATION),
    ctypes.POINTER(CF_SYNC_POLICIES),
    wintypes.DWORD,
    ctypes.c_void_p,
]
cldapi.CfRegisterSyncRoot.restype = ctypes.c_long

cldapi.CfUnregisterSyncRoot.argtypes = [wintypes.LPCWSTR]
cldapi.CfUnregisterSyncRoot.restype = ctypes.c_long

cldapi.CfConnectSyncRoot.argtypes = [
    wintypes.LPCWSTR,
    ctypes.POINTER(CF_CALLBACK_REGISTRATION),
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_longlong),
]
cldapi.CfConnectSyncRoot.restype = ctypes.c_long

cldapi.CfCreatePlaceholders.argtypes = [
    wintypes.LPCWSTR,
    ctypes.POINTER(CF_PLACEHOLDER_CREATE_INFO),
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
cldapi.CfCreatePlaceholders.restype = ctypes.c_long

cldapi.CfExecute.argtypes = [
    ctypes.POINTER(CF_OPERATION_INFO),
    ctypes.POINTER(CF_OPERATION_PARAMETERS),
]
cldapi.CfExecute.restype = ctypes.c_long

cldapi.CfDisconnectSyncRoot.argtypes = [ctypes.c_longlong]
cldapi.CfDisconnectSyncRoot.restype = ctypes.c_long


def _hr(hr: int) -> str:
    return f"0x{hr & 0xFFFFFFFF:08X}"


def _filetime_now() -> FILETIME:
    """当前时间转 FILETIME（100ns 自 1601-01-01）"""
    ft = int((time.time() + 11644473600) * 10_000_000)
    return FILETIME(ft & 0xFFFFFFFF, (ft >> 32) & 0xFFFFFFFF)


def main() -> int:
    root = tempfile.mkdtemp(prefix="jflove_cfapi_poc_")
    print(f"sync root: {root}")

    registered = False
    connected = False
    key = ctypes.c_longlong()

    # 0) 先注册 sync root（CfConnectSyncRoot 要求必须先注册）
    reg = CF_SYNC_REGISTRATION()
    reg.StructSize = ctypes.sizeof(CF_SYNC_REGISTRATION)
    reg.ProviderName = "JFLove"
    reg.ProviderVersion = "1.7.0"
    reg.SyncRootIdentity = None
    reg.SyncRootIdentityLength = 0
    reg.FileIdentity = None
    reg.FileIdentityLength = 0
    reg.ProviderId = _PROVIDER_ID

    policies = CF_SYNC_POLICIES()
    policies.StructSize = ctypes.sizeof(CF_SYNC_POLICIES)
    policies.Hydration.Primary = 1       # PROGRESSIVE：按需水合
    policies.Hydration.Modifier = 0
    policies.Population.Primary = 0      # PARTIAL：占位由 provider 主动创建
    policies.Population.Modifier = 0
    policies.InSync = 0
    policies.HardLink = 0
    policies.PlaceholderManagement = 0

    hr = cldapi.CfRegisterSyncRoot(
        root, ctypes.byref(reg), ctypes.byref(policies), 0, None,
    )
    print(f"CfRegisterSyncRoot hr={_hr(hr)}")
    if hr < 0:
        print("[FAIL] 注册 sync root 失败")
        return 1
    registered = True

    try:
        # 1) 注册回调表（FETCH_DATA + 结尾 NONE）
        cb = CF_CALLBACK(_on_fetch_data)
        registrations = (CF_CALLBACK_REGISTRATION * 2)(
            CF_CALLBACK_REGISTRATION(CF_CALLBACK_TYPE_FETCH_DATA, cb),
            CF_CALLBACK_REGISTRATION(CF_CALLBACK_TYPE_NONE, CF_CALLBACK()),
        )
        hr = cldapi.CfConnectSyncRoot(
            root, registrations, None, CF_CONNECT_FLAG_NONE, ctypes.byref(key),
        )
        print(f"CfConnectSyncRoot hr={_hr(hr)} key={key.value}")
        if hr < 0:
            print("[FAIL] 连接 sync root 失败")
            return 1
        connected = True

        # 2) 创建一个占位文件（大小 = CONTENT 长度）
        name = "hello.txt"
        meta = CF_FS_METADATA()
        meta.FileSize = len(CONTENT)
        # 必须给一个合法属性；0 会被平台视为无效元数据
        meta.FileAttributes = 0x20  # FILE_ATTRIBUTE_ARCHIVE
        # 时间全 0 也会被判无效：四个时间都填当前时刻
        now_ft = _filetime_now()
        meta.CreationTime = now_ft
        meta.LastWriteTime = now_ft
        meta.LastAccessTime = now_ft
        meta.ChangeTime = now_ft
        # FileIdentity 对文件是**强制**字段（官方文档：mandatory for files）
        identity = b"poc-hello"
        identity_buf = ctypes.create_string_buffer(identity, len(identity))
        create = (CF_PLACEHOLDER_CREATE_INFO * 1)(
            CF_PLACEHOLDER_CREATE_INFO(
                RelativeFileName=name,
                FsMetadata=ctypes.pointer(meta),
                FileIdentityLength=len(identity),
                FileIdentity=ctypes.cast(identity_buf, ctypes.c_void_p),
                Flags=CF_PLACEHOLDER_CREATE_FLAG_NONE,
                Result=0,
                ReturnedUsn=0,
                ReturnedFileId=0,
            )
        )
        processed = wintypes.DWORD(0)
        hr = cldapi.CfCreatePlaceholders(
            root, create, 1, CF_CREATE_FLAG_NONE, ctypes.byref(processed),
        )
        print(f"CfCreatePlaceholders hr={_hr(hr)} processed={processed.value} "
              f"item_result={_hr(create[0].Result)}")
        if hr < 0 or create[0].Result < 0:
            print("[FAIL] 创建占位失败")
            return 1

        path = os.path.join(root, name)
        print(f"placeholder exists={os.path.exists(path)} "
              f"size={os.path.getsize(path)}")

        # 3) 读取占位 → 触发 FETCH_DATA → 校验内容
        with open(path, "rb") as f:
            got = f.read()
        print(f"read {len(got)} bytes: {got!r}")
        print(f"callbacks={len(CALLBACK_HITS)}")
        if got == CONTENT:
            print("[OK] CfAPI 三步判据全部跑通")
            return 0
        print("[FAIL] 内容不一致（hydration 未生效）")
        return 1
    finally:
        if connected:
            hr = cldapi.CfDisconnectSyncRoot(key.value)
            print(f"CfDisconnectSyncRoot hr={_hr(hr)}")
        if registered:
            hr = cldapi.CfUnregisterSyncRoot(root)
            print(f"CfUnregisterSyncRoot hr={_hr(hr)}")


if __name__ == "__main__":
    sys.exit(main())
