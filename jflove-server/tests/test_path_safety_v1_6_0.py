"""
v1.6.0 Phase 5 审查 S-1 / S-2：路径安全判据与文件名校验口径

**对应审查结论**：`文档记录/代码审查报告/v1.6.0.md` S-1（严重）、S-2（严重）

- **S-1**：`_safe_join` 原用 `str(target).startswith(str(base_path))` 判定越界 ⇒
  `base=D:\\disks\\alice` + `rel=../alice2/evil.txt` 解析到 **同名前缀的兄弟目录**
  `D:\\disks\\alice2\\evil.txt`，字符串前缀成立即被放行。`sync_service` 另有一份同写法副本。
- **S-2**：`_validate_filename` 原先只被 `init_upload` 调用；接口契约声明"纯文件名"的
  `get_stream_range.filename` 与 `rename_file.new_name` 都只靠 `_safe_join` 兜底。

**验收判据**：
  ① 同名前缀兄弟目录一律拒绝（S-1 核心回归）；
  ② 名字以基准目录名开头的**合法子目录**不得被误伤（防过度收紧）；
  ③ 文件名校验对所有"纯文件名"入口生效，越界写法走 `ValueError`（⇒ 400）而非 403；
  ④ 负向对照：把 `_safe_join` 还原成 `startswith` 写法 → 本文件①组用例必须失败
     （证明用例拦的是真漏洞）。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from src.services import file_service, sync_service
from src.utils import path_safety


# --------------------------------------------------------------------------- #
# S-1：safe_join 的越界判定
# --------------------------------------------------------------------------- #

def _make_base_and_sibling(tmp_path: Path) -> tuple[Path, Path]:
    """构造 `alice`（基准）与 `alice2`（同名前缀的兄弟目录）两个真实目录"""
    base = tmp_path / "alice"
    sibling = tmp_path / "alice2"
    base.mkdir()
    sibling.mkdir()
    return base, sibling


def test_is_within_base_rejects_shared_prefix_sibling(tmp_path: Path) -> None:
    """分量级包含判定：`...\\alice2\\x` 不属于 `...\\alice`"""
    base, sibling = _make_base_and_sibling(tmp_path)
    target = (sibling / "evil.txt").resolve()
    # 先文档化"这确实是一个越界目标"（字符串前缀成立，分量包含不成立）
    assert str(target).startswith(str(base.resolve()))
    assert path_safety.is_within_base(base.resolve(), target) is False


def test_is_within_base_accepts_self_and_children(tmp_path: Path) -> None:
    """基准目录自身与真实子目录属于包含关系"""
    base, _ = _make_base_and_sibling(tmp_path)
    assert path_safety.is_within_base(base.resolve(), base.resolve()) is True
    assert path_safety.is_within_base(base.resolve(), (base / "sub" / "a.txt").resolve()) is True


def test_safe_join_rejects_sibling_with_shared_prefix(tmp_path: Path) -> None:
    """S-1 核心回归：`../alice2/evil.txt` 必须被拒（原实现会放行）"""
    base, _ = _make_base_and_sibling(tmp_path)
    with pytest.raises(PermissionError):
        file_service._safe_join(str(base), "../alice2/evil.txt")


def test_sync_service_safe_join_rejects_sibling_with_shared_prefix(tmp_path: Path) -> None:
    """S-1 第二处副本（sync_service）同样必须被拒"""
    base, _ = _make_base_and_sibling(tmp_path)
    with pytest.raises(PermissionError):
        sync_service._safe_join(str(base), "../alice2/evil.txt")


def test_safe_join_rejects_other_sibling(tmp_path: Path) -> None:
    """负向对照面：不同前缀的兄弟目录（原实现已能拦，回归不得退化）"""
    base = tmp_path / "alice"
    base.mkdir()
    (tmp_path / "bob").mkdir()
    with pytest.raises(PermissionError):
        file_service._safe_join(str(base), "../bob/evil.txt")


def test_safe_join_rejects_deep_traversal(tmp_path: Path) -> None:
    """多级 `../` 越出磁盘根同样拒绝"""
    base = tmp_path / "alice"
    base.mkdir()
    with pytest.raises(PermissionError):
        file_service._safe_join(str(base), "../../outside.txt")


def test_safe_join_accepts_legit_children(tmp_path: Path) -> None:
    """防过度收紧：合法子目录 / 名字以基准目录名开头的子目录必须放行"""
    base = tmp_path / "alice"
    base.mkdir()
    assert file_service._safe_join(str(base), "sub/ok.txt") == str(
        (base / "sub" / "ok.txt").resolve()
    )
    assert file_service._safe_join(str(base), "") == str(base.resolve())
    # `alice_notes` 以 base 名开头，但确实是 base 内部的子目录
    assert file_service._safe_join(str(base), "alice_notes/a.txt") == str(
        (base / "alice_notes" / "a.txt").resolve()
    )
    # 返回父级再回到基准目录自身：仍属包含范围
    assert file_service._safe_join(str(base), "../alice/ok.txt") == str(
        (base / "ok.txt").resolve()
    )


@pytest.mark.skipif(os.name != "nt", reason="盘符语义仅 Windows")
def test_safe_join_rejects_drive_prefixed_rel(tmp_path: Path) -> None:
    """Windows：`E:` 之类的驱动器前缀视为越界（不同驱动器 ⇒ commonpath 抛 ValueError）"""
    base = tmp_path / "alice"
    base.mkdir()
    with pytest.raises(PermissionError):
        file_service._safe_join(str(base), "E:/evil.txt")


@pytest.mark.parametrize(
    "rel",
    [
        ".jflove-repair/x.txt",
        "sub/.jflove-repair/x.txt",
        ".jflove-repair",
    ],
)
def test_safe_join_rejects_repair_dir_segments(tmp_path: Path, rel: str) -> None:
    """v1.4.2 既有约束不得退化：修复隐藏目录段一律拒绝"""
    base = tmp_path / "alice"
    base.mkdir()
    with pytest.raises(PermissionError):
        file_service._safe_join(str(base), rel)


def test_sync_service_safe_join_keeps_repair_dir_behavior(tmp_path: Path) -> None:
    """sync_service 从不特判修复目录（口径保持不变，仅共享越界判定）"""
    base = tmp_path / "alice"
    base.mkdir()
    got = sync_service._safe_join(str(base), ".jflove-repair/x.txt")
    assert got == str((base / ".jflove-repair" / "x.txt").resolve())


# --------------------------------------------------------------------------- #
# S-2：文件名校验（路径安全工具层 + 服务层入口）
# --------------------------------------------------------------------------- #

MALICIOUS_NAMES = [
    "../../evil.txt",
    "..\\..\\evil.txt",
    "sub/evil.txt",
    "sub\\evil.txt",
    "/etc/passwd",
    "C:evil.txt",
    "..",
    ".",
    "   ",
    "evil\x00.txt",
]

LEGAL_NAMES = [
    "中文 文件名.txt",
    "a.b.c.txt",
    ".hidden",
    "with space.txt",
    "semi;colon&amp.txt",
]


@pytest.mark.parametrize("name", MALICIOUS_NAMES)
def test_validate_filename_rejects(name: str) -> None:
    """工具层：越界写法一律 `ValueError`（不回显用户输入）"""
    with pytest.raises(ValueError) as ei:
        path_safety.validate_filename(name)
    detail = str(ei.value)
    assert "filename 非法" in detail
    assert "evil" not in detail and "../" not in detail


@pytest.mark.parametrize("name", LEGAL_NAMES)
def test_validate_filename_accepts(name: str) -> None:
    """工具层：合法文件名不得误伤（含前后空白 trim）"""
    assert path_safety.validate_filename(name) == name
    assert path_safety.validate_filename(f"  {name} \n") == name


def test_file_service_validate_filename_is_shared_impl() -> None:
    """`file_service._validate_filename` 与工具层同源（口径唯一）"""
    assert file_service._validate_filename("ok.txt") == "ok.txt"
    with pytest.raises(ValueError):
        file_service._validate_filename("../alice2/evil.txt")


def _patch_disk_path(monkeypatch: pytest.MonkeyPatch, base: Path) -> None:
    """绕过 DB：把 `_get_disk_path` 指到临时目录（role=admin 已跳过权限校验）"""

    async def _fake(db, disk_id):  # noqa: ANN001, ANN202 - 测试替身
        return str(base)

    monkeypatch.setattr(file_service, "_get_disk_path", _fake)


def test_get_stream_range_rejects_traversal_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """接口契约：流式预览的 `filename` 是纯文件名 ⇒ 越界写法 `ValueError`（控制器 ⇒ 400）"""
    _patch_disk_path(monkeypatch, tmp_path)
    with pytest.raises(ValueError) as ei:
        asyncio.run(
            file_service.get_stream_range(
                None, 1, "admin", 1, "", "../alice2/evil.txt", 0, -1
            )
        )
    assert "filename 非法" in str(ei.value)


def test_get_stream_range_accepts_legal_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正例：合法目录 + 合法文件名仍返回真实路径与 MIME"""
    _patch_disk_path(monkeypatch, tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "ok.txt").write_bytes(b"hello")
    path, start, end, size, content_type = asyncio.run(
        file_service.get_stream_range(None, 1, "admin", 1, "sub", "ok.txt", 0, -1)
    )
    assert Path(path) == (tmp_path / "sub" / "ok.txt").resolve()
    assert (start, end, size) == (0, 5, 5)
    assert content_type.startswith("text/plain")


def test_rename_file_rejects_nul_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """接口契约：`rename_file` 的新名称走同一份口径（NUL 属非法字符）"""
    _patch_disk_path(monkeypatch, tmp_path)
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError) as ei:
        asyncio.run(
            file_service.rename_file(None, 1, "admin", 1, "a.txt", "b\x00c.txt")
        )
    assert "名称包含非法字符" in str(ei.value)
    assert (tmp_path / "a.txt").exists()


def test_rename_file_accepts_legal_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """正例：合法新名称照常重命名（防过度收紧）"""
    _patch_disk_path(monkeypatch, tmp_path)
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    asyncio.run(file_service.rename_file(None, 1, "admin", 1, "a.txt", "b.txt"))
    assert (tmp_path / "b.txt").exists()
    assert not (tmp_path / "a.txt").exists()
