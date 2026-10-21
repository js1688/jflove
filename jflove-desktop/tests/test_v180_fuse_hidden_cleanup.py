"""
v1.8.0 issue #5 回归：FUSE 卸载后清理 `.fuse_hidden*`。

背景：libfuse 在"文件仍被打开时被 unlink"会把底层文件改名为 `.fuse_hidden*`；
若卸载后不清理，用户看到目录删不干净，且必须重启服务端才消失。
本用例直接验证卸载后的清理函数（不需要真实 FUSE 挂载）。
"""

from __future__ import annotations

from pathlib import Path

from src.utils.mount_fuse import LinuxFuseMount


def _mount_for(base: Path) -> LinuxFuseMount:
    """构造一个只带清理所需字段的挂载对象（不启动 FUSE）。"""
    mount = object.__new__(LinuxFuseMount)
    mount._base = str(base)  # type: ignore[attr-defined]
    mount.mount_id = 1  # type: ignore[attr-defined]
    return mount


def test_cleanup_fuse_hidden_removes_residue_recursively(tmp_path: Path) -> None:
    """根目录与子目录中的 `.fuse_hidden*` 都要删除，正常文件保留。"""
    (tmp_path / "sub").mkdir()
    (tmp_path / ".fuse_hidden000001").write_bytes(b"x")
    (tmp_path / "sub" / ".fuse_hidden000002").write_bytes(b"y")
    (tmp_path / "keep.txt").write_bytes(b"keep")

    _mount_for(tmp_path)._cleanup_fuse_hidden()

    assert not (tmp_path / ".fuse_hidden000001").exists()
    assert not (tmp_path / "sub" / ".fuse_hidden000002").exists()
    assert (tmp_path / "keep.txt").read_bytes() == b"keep"


def test_cleanup_fuse_hidden_is_idempotent(tmp_path: Path) -> None:
    """没有残留时不得抛异常（重复卸载/解除挂载 3 次仍成立）。"""
    mount = _mount_for(tmp_path)
    mount._cleanup_fuse_hidden()
    mount._cleanup_fuse_hidden()
    assert list(tmp_path.iterdir()) == []
