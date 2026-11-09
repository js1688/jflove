"""
临时目录迁移服务（v1.9.0）

职责：服务启动时把旧版分散的临时目录收敛到统一位置：
  - 上传分片：`build/upload_tmp/<uuid>` → `build/tmp/upload/<uuid>`
  - 离线下载：`<磁盘根>/<目标目录>/.jflove-download/<task_id>` → `<磁盘根>/.jflove-tmp/offline/<task_id>`
  - 修复产物：`<磁盘根>/<原文件目录>/.jflove-repair/<output>` → `<磁盘根>/.jflove-tmp/repair/<task_id>/<output>`

约束：
  - 幂等：目标已存在则跳过；迁移失败只记 warning，不阻塞启动/文件访问；
  - 只处理程序自产固定命名内容，不触碰用户正常文件；
  - 旧路径读取回退由 repair/offline 服务负责（迁移失败时仍可工作）。
"""

from __future__ import annotations

import os
import shutil

import aiosqlite

from src.config.settings import (
    LEGACY_UPLOAD_TEMP_DIR,
    OFFLINE_DIR_NAME,
    REPAIR_DIR_NAME,
    TEMP_DIR_NAME,
    TEMP_OFFLINE_SUBDIR,
    TEMP_REPAIR_SUBDIR,
    UPLOAD_TEMP_DIR,
)
from src.repositories import repair_task_repository, virtual_disk_repository
from src.utils.logger import get_logger
from src.utils.path_safety import safe_join

logger = get_logger(__name__)


def _remove_empty_dir(path: str) -> None:
    """删除空目录（仅用于旧临时目录收尾；非空则忽略）。"""
    try:
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
    except OSError:
        pass


def _move_dir(src: str, dst: str) -> bool:
    """移动目录（目标已存在则不覆盖）；失败返回 False。"""
    if not os.path.isdir(src):
        return False
    if os.path.exists(dst):
        return False
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        return True
    except OSError as exc:
        logger.warning("临时目录迁移失败（保留旧目录）: %s", type(exc).__name__)
        return False


async def migrate_legacy_temp_dirs(db: aiosqlite.Connection) -> dict:
    """
    执行一次临时目录迁移（启动时调用；幂等）。

    :param db: 数据库连接
    :returns: 迁移统计（不含路径明文）
    """
    stats = {"upload_dirs": 0, "offline_dirs": 0, "repair_files": 0, "failed": 0}

    # 1) 上传分片：旧 build/upload_tmp -> build/tmp/upload
    if os.path.isdir(LEGACY_UPLOAD_TEMP_DIR):
        os.makedirs(UPLOAD_TEMP_DIR, exist_ok=True)
        try:
            names = os.listdir(LEGACY_UPLOAD_TEMP_DIR)
        except OSError:
            names = []
        for name in names:
            src = os.path.join(LEGACY_UPLOAD_TEMP_DIR, name)
            if not os.path.isdir(src):
                continue
            # 只允许 UUID 目录（程序自产）
            if not (len(name) == 36 and name.count("-") == 4):
                continue
            dst = os.path.join(UPLOAD_TEMP_DIR, name)
            if _move_dir(src, dst):
                stats["upload_dirs"] += 1
            elif os.path.exists(dst):
                # 目标已存在：旧目录可能是重复残留，保留不动（由清理任务按 TTL 处理）
                continue
            else:
                stats["failed"] += 1
        _remove_empty_dir(LEGACY_UPLOAD_TEMP_DIR)

    # 2) 磁盘级：旧 .jflove-download / .jflove-repair -> .jflove-tmp
    try:
        disks = await virtual_disk_repository.list_all(db)
    except Exception as exc:  # noqa: BLE001 - 迁移失败不阻塞启动
        logger.warning("枚举虚拟磁盘失败，跳过临时目录迁移: %s", type(exc).__name__)
        return stats

    repair_rows = []
    try:
        repair_rows = await repair_task_repository.list_all_with_artifacts(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取修复产物记录失败: %s", type(exc).__name__)

    for disk in disks:
        base = os.path.realpath(str(disk["real_path"]))
        if not os.path.isdir(base):
            continue
        tmp_root = os.path.join(base, TEMP_DIR_NAME)

        # 2.1 离线下载旧临时目录
        legacy_offline = os.path.join(base, OFFLINE_DIR_NAME)
        if os.path.isdir(legacy_offline):
            try:
                subs = os.listdir(legacy_offline)
            except OSError:
                subs = []
            for sub in subs:
                if not sub.isdigit():
                    continue
                src = os.path.join(legacy_offline, sub)
                dst = os.path.join(tmp_root, TEMP_OFFLINE_SUBDIR, sub)
                if _move_dir(src, dst):
                    stats["offline_dirs"] += 1
            _remove_empty_dir(legacy_offline)

        # 2.2 修复产物（按任务记录映射到 task_id 子目录）
        for row in repair_rows:
            try:
                row_disk_id = int(row["disk_id"])
                task_id = int(row["id"])
                rel_path = str(row["rel_path"] or "")
                output_name = str(row["output_name"] or "")
            except (TypeError, ValueError, KeyError):
                continue
            if row_disk_id != int(disk["id"]) or not output_name:
                continue
            try:
                old_dir = os.path.join(safe_join(base, rel_path), REPAIR_DIR_NAME)
            except PermissionError:
                continue
            src = os.path.join(old_dir, output_name)
            dst = os.path.join(tmp_root, TEMP_REPAIR_SUBDIR, str(task_id), output_name)
            if not os.path.isfile(src) or os.path.exists(dst):
                continue
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
                stats["repair_files"] += 1
            except OSError as exc:
                logger.warning("修复产物迁移失败（保留旧路径）: %s", type(exc).__name__)
                stats["failed"] += 1
            _remove_empty_dir(old_dir)

    if any(stats.values()):
        logger.info(
            "临时目录迁移完成: upload=%d offline=%d repair=%d failed=%d",
            stats["upload_dirs"], stats["offline_dirs"],
            stats["repair_files"], stats["failed"],
        )
    return stats
