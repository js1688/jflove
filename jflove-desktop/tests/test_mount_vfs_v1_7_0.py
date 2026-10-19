"""
v1.7.0 CfAPI 系统级挂载集成测试（Windows）

验证 `src/utils/mount_cfapi.py` 的完整链路（Linux FUSE 用例见
`tests/test_mount_fuse_v1_7_0.py`）：
  注册 sync root → 创建脱水占位 → 读取触发 FETCH_DATA → 内容与远端一致。

对应需求 AC-102 / AC-103 / AC-104（系统挂载部分）。
非 Windows 环境自动跳过。
"""

from __future__ import annotations

import os
import tempfile

import pytest

from src.utils import mount_vfs

pytestmark = pytest.mark.skipif(
    mount_vfs.backend() != "cfapi", reason="CfAPI 仅 Windows 支持",
)


def test_cfapi_挂载并水合_内容一致():
    """注册 → 占位 → 读取水合，内容必须逐字节一致"""
    root = tempfile.mkdtemp(prefix="jflove_vfs_test_")
    content = b"vfs-integration-content"
    vfs = mount_vfs.WinCfapiMount("test-mount", root, lambda rel: content)
    try:
        assert vfs.start() is True, "CfRegisterSyncRoot / CfConnectSyncRoot 失败"
        assert vfs.add_file("a.txt", len(content), 0.0) is True

        path = os.path.join(root, "a.txt")
        assert os.path.exists(path)
        with open(path, "rb") as f:
            assert f.read() == content
    finally:
        vfs.stop()


def test_占位重复创建幂等():
    """同一路径重复 add_file 不报错（已存在直接返回 True）"""
    root = tempfile.mkdtemp(prefix="jflove_vfs_test_")
    content = b"idempotent"
    vfs = mount_vfs.WinCfapiMount("test-idem", root, lambda rel: content)
    try:
        assert vfs.start() is True
        assert vfs.add_file("b.txt", len(content), 0.0) is True
        assert vfs.add_file("b.txt", len(content), 0.0) is True
    finally:
        vfs.stop()
