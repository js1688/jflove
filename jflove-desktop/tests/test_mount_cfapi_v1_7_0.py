"""
v1.7.0 CfAPI 常量与结构体布局回归测试（Windows）

这些**不是**风格测试：`mount_cfapi.py` 用 ctypes 直接调 cfapi.h，枚举值或
结构体偏移错一个字节，表现就是「资源管理器进不去目录 / 云操作 60s 超时
（0x800701AA）/ 删除挂起」，而且本地单测全绿也发现不了。

因此本文件把「与 cfapi.h 逐值一致」写成断言（依据：MS Learn
CF_CALLBACK_TYPE / CF_OPERATION_TYPE / CF_OPERATION_PARAMETERS）。
"""

from __future__ import annotations

import ctypes
import os
import shutil
import tempfile

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="CfAPI 仅 Windows 支持",
)

if os.name == "nt":
    from src.utils import mount_cfapi as mc
else:  # pragma: no cover - 仅为了让非 Windows 下的 import 不炸
    mc = None  # type: ignore[assignment]


# ── 回调类型：必须与 cfapi.h 的枚举顺序一致 ──────────────────────

def test_回调类型枚举值():
    """cfapi.h: FETCH_DATA=0 … FETCH_PLACEHOLDERS=3 … NOTIFY_DELETE=9 / RENAME=11"""
    assert mc.CF_CALLBACK_TYPE_FETCH_DATA == 0
    assert mc.CF_CALLBACK_TYPE_VALIDATE_DATA == 1
    assert mc.CF_CALLBACK_TYPE_CANCEL_FETCH_DATA == 2
    assert mc.CF_CALLBACK_TYPE_FETCH_PLACEHOLDERS == 3
    assert mc.CF_CALLBACK_TYPE_CANCEL_FETCH_PLACEHOLDERS == 4
    assert mc.CF_CALLBACK_TYPE_NOTIFY_DEHYDRATE == 7
    assert mc.CF_CALLBACK_TYPE_NOTIFY_DELETE == 9
    assert mc.CF_CALLBACK_TYPE_NOTIFY_RENAME == 11
    assert mc.CF_CALLBACK_TYPE_NONE == 0xFFFFFFFF


def test_操作类型枚举值():
    """cfapi.h: TRANSFER_DATA=0 / TRANSFER_PLACEHOLDERS=4 / ACK_DEHYDRATE=5

    ACK_DELETE=6 / ACK_RENAME=7（顺序即取值，错一个就会 ACK 成别的操作）
    """
    assert mc.CF_OPERATION_TYPE_TRANSFER_DATA == 0
    assert mc.CF_OPERATION_TYPE_RETRIEVE_DATA == 1
    assert mc.CF_OPERATION_TYPE_ACK_DATA == 2
    assert mc.CF_OPERATION_TYPE_RESTART_HYDRATION == 3
    assert mc.CF_OPERATION_TYPE_TRANSFER_PLACEHOLDERS == 4
    assert mc.CF_OPERATION_TYPE_ACK_DEHYDRATE == 5
    assert mc.CF_OPERATION_TYPE_ACK_DELETE == 6
    assert mc.CF_OPERATION_TYPE_ACK_RENAME == 7


# ── 结构体布局：字段偏移必须与 cfapi.h 一致 ────────────────────

def test_操作参数结构体布局():
    """ParamSize 在 0，union 在 8；各操作结构内字段偏移与官方一致"""
    params = mc._CF_OPERATION_PARAMETERS
    assert params.ParamSize.offset == 0
    assert mc._OP_PAYLOAD_OFFSET == 8
    assert ctypes.sizeof(params) == 48, "union 最大成员（RetrieveData）应为 40B"

    td = mc._TransferData
    assert (td.Flags.offset, td.CompletionStatus.offset, td.Buffer.offset,
            td.Offset.offset, td.Length.offset) == (0, 4, 8, 16, 24)

    tp = mc._TransferPlaceholders
    assert (tp.Flags.offset, tp.CompletionStatus.offset,
            tp.PlaceholderTotalCount.offset, tp.PlaceholderArray.offset,
            tp.PlaceholderCount.offset, tp.EntriesProcessed.offset) == (0, 4, 8, 16, 24, 28)

    ack_dehydrate = mc._AckDehydrate
    assert (ack_dehydrate.Flags.offset, ack_dehydrate.CompletionStatus.offset,
            ack_dehydrate.FileIdentity.offset,
            ack_dehydrate.FileIdentityLength.offset) == (0, 4, 8, 16)

    assert ctypes.sizeof(mc._AckDelete) == 8
    assert ctypes.sizeof(mc._AckRename) == 8


def test_ParamSize_按操作精确取值():
    """文档要求 ParamSize = union 偏移 + 该操作结构大小（传整包会被判 E_INVALIDARG）"""
    sizes = mc._OP_PARAM_SIZES
    assert sizes[mc.CF_OPERATION_TYPE_TRANSFER_DATA] == 8 + 32
    assert sizes[mc.CF_OPERATION_TYPE_TRANSFER_PLACEHOLDERS] == 8 + 32
    assert sizes[mc.CF_OPERATION_TYPE_ACK_DEHYDRATE] == 8 + 24
    assert sizes[mc.CF_OPERATION_TYPE_ACK_DELETE] == 8 + 8
    assert sizes[mc.CF_OPERATION_TYPE_ACK_RENAME] == 8 + 8


def test_new_params_使用精确大小():
    root = tempfile.mkdtemp(prefix="jflove_cfapi_layout_")
    mount = mc.WinCfapiMount("test-layout", root, lambda rel: b"")
    try:
        for op_type, expected in mc._OP_PARAM_SIZES.items():
            assert mount._new_params(op_type).ParamSize == expected
    finally:
        mount._pool.shutdown(wait=False)
        shutil.rmtree(root, ignore_errors=True)


def test_注册表包含目录枚举报文类型():
    """FETCH_PLACEHOLDERS(3) 必须在注册表里，否则目录打不开（平台等满 60s）"""
    root = tempfile.mkdtemp(prefix="jflove_cfapi_reg_")
    mount = mc.WinCfapiMount("test-reg", root, lambda rel: b"")
    try:
        types = [reg.Type for reg in mount._registrations]
        assert mc.CF_CALLBACK_TYPE_FETCH_PLACEHOLDERS in types
        assert mc.CF_CALLBACK_TYPE_FETCH_DATA in types
        assert mc.CF_CALLBACK_TYPE_NOTIFY_DELETE in types
        assert mc.CF_CALLBACK_TYPE_NOTIFY_RENAME in types
        assert mc.CF_CALLBACK_TYPE_NOTIFY_DEHYDRATE in types
        assert mc.CF_CALLBACK_TYPE_NONE == types[-1], "NONE 必须是表尾终止符"
        assert len(types) == len(set(types)), "回调类型不能重复注册"
    finally:
        mount._pool.shutdown(wait=False)
        shutil.rmtree(root, ignore_errors=True)


def test_状态码():
    """STATUS_SUCCESS=0、STATUS_UNSUCCESSFUL=0xC0000001（平台会转成 CLOUD_FILE_UNSUCCESSFUL）"""
    assert mc._STATUS_SUCCESS == 0
    assert mc._STATUS_UNSUCCESSFUL & 0xFFFFFFFF == 0xC0000001
