"""
路径安全工具（v1.6.0 Phase 5 代码审查 S-1 / S-2 修复）。

**背景（S-1）**：`file_service._safe_join` 曾用
`if not str(target).startswith(str(base_path))` 判定越界。
字符串前缀判定隐含「同名前缀即同一目录」的错误假设：

.. code-block:: text

    base   = D:\\disks\\alice                （用户 alice 的磁盘根）
    rel    = ../alice2/evil.txt
    target = D:\\disks\\alice2\\evil.txt     （用户 alice2 的磁盘）
    str(target).startswith(str(base)) ⇒ True ⇒ 放行

即「同名前缀的兄弟目录」可被穿越。`sync_service` 另有一份同写法实现。
本模块提供**唯一实现**，按路径分量比较是否包含于基准目录内。

**背景（S-2）**：`_validate_filename` 原先只被 `init_upload` 调用。凡接口契约声明
「纯文件名」（上传文件名 / 流式预览文件名 / 重命名的新名称）都必须过同一份校验，
否则越界只能靠 `safe_join` 兜底（错误码从 400 漂到 403/500）。

安全宪法 §9.4.13：本模块抛出的错误信息**不回显**用户输入（只说明非法类别）。
"""

import os
from pathlib import Path
from typing import Iterable

# 校验顺序保证错误信息分类稳定：先空/上级段 → 再分隔符 → 再盘符 → 再平台分隔符
_FILENAME_EMPTY_MSG = "filename 非法：不能为空或上级目录段"
_FILENAME_SEP_MSG = "filename 非法：不能包含路径分隔符"
_FILENAME_DRIVE_MSG = "filename 非法：不能包含盘符前缀"


def is_within_base(base_path: Path, target: Path) -> bool:
    """
    判断 ``target`` 是否落在 ``base_path`` 之内（含二者相等）。

    用 ``os.path.commonpath`` 做**分量级**比较，而不是字符串前缀：
    ``D:\\disks\\alice`` 与 ``D:\\disks\\alice2`` 的公共路径是 ``D:\\disks``，
    因此不再互相"包含"。

    :param base_path: 基准目录（应为已 resolve 的绝对路径）
    :param target: 待判定路径（应为已 resolve 的绝对路径）
    :returns: True 表示包含（Windows 下比较大小写不敏感）
    """
    try:
        common = os.path.commonpath([str(target), str(base_path)])
    except ValueError:
        # 不同驱动器（D: vs E:）或绝对/相对混用 —— 一律视为不包含
        return False
    return os.path.normcase(common) == os.path.normcase(str(base_path))


def safe_join(base: str, rel: str, forbidden_parts: Iterable[str] = ()) -> str:
    """
    安全路径拼接：把相对路径 ``rel`` 限定在 ``base`` 目录内。

    :param base: 根目录绝对路径
    :param rel: 相对路径（可含前置 ``/``；可含合法的子目录分量）
    :param forbidden_parts: 任意路径分量命中即拒绝的名字（如修复隐藏目录）
    :returns: 限定后的绝对路径字符串
    :raises PermissionError: 路径越界 / 含禁止的路径分量
    """
    base_path = Path(base).resolve()
    target = (base_path / rel.lstrip("/")).resolve()
    if not is_within_base(base_path, target):
        raise PermissionError("非法路径访问")
    blocked = {p for p in forbidden_parts if p}
    if blocked:
        rel_norm = rel.replace("\\", "/").lstrip("/")
        if any(part in blocked for part in rel_norm.split("/") if part):
            raise PermissionError("非法路径访问")
    return str(target)


def validate_filename(filename: str) -> str:
    """
    校验「纯文件名」口径，拒绝任何可能逃出目标目录的写法。

    口径（同时挡住 POSIX 与 Windows 语义，**不依赖运行平台**）：
      ① ``strip()`` 后非空，且不是 ``.`` / ``..``；
      ② 不含 ``/``、``\\`` 与 NUL；
      ③ Windows 盘符前缀（``C:evil.txt`` 在 ntpath 下是**驱动器相对**路径，会逃出目标目录）；
      ④ ``os.path.basename(name) == name``（POSIX 侧 ``a/b``、Windows 侧 ``d:\\a`` 一并挡住）；
      ⑤ 不含当前平台的 ``os.path.sep`` / ``os.path.altsep``（平台差异兜底）。

    :param filename: 客户端提交的文件名 / 新名称
    :returns: 去空白后的合法文件名
    :raises ValueError: 文件名为空、含路径分隔符或为上级目录段
    """
    name = (filename or "").strip()
    if not name or name in (".", ".."):
        raise ValueError(_FILENAME_EMPTY_MSG)
    if "/" in name or "\\" in name or "\x00" in name:
        raise ValueError(_FILENAME_SEP_MSG)
    if len(name) >= 2 and name[1] == ":" and name[0].isalpha():
        raise ValueError(_FILENAME_DRIVE_MSG)
    if os.path.basename(name) != name:
        raise ValueError(_FILENAME_SEP_MSG)
    seps = [s for s in (os.path.sep, os.path.altsep) if s]
    if any(s in name for s in seps):
        raise ValueError(_FILENAME_SEP_MSG)
    return name
