"""
同步引擎（v1.1.6 适配版）

v1.1.6 变更：
  - 配置 ID 从服务端 int 改为本地 str（uuid 短 ID）
  - _build_remote_index 改为传 disk_id + remote_path，不再依赖 config_id 调 snapshot
  - touch_synced 改为本地写入（不再调服务端）

同步规则（**重要**）：
  1. 本地有 / 远端无 → 上传
  2. 本地无 / 远端有 → 下载（注意：即使是用户在本地"删除"了文件，
     这条规则也会让远端文件原封不动重新出现在本地。删除远端文件
     必须通过文档管理页面）
  3. 本地有 / 远端有（按以下顺序判断，命中即停）：
     a. **size 一致** + mtime 差距 ≤ 容差(2 秒) → 跳过
     b. **size 一致** + mtime 显著不同 → 按 mtime 取新者
     c. **size 不同** → 必然是修改过的文件，按 mtime 取新者
  4. **任何方向都不会主动删除文件**

实现要点：
  - 单例 SyncEngine（QObject），通过 sync_started / sync_finished / sync_error
    信号给 UI 反馈
  - 每个开启 auto_sync 的配置维护一个 QTimer，到期触发 trigger_sync()
  - 所有耗时操作（本地扫描、API 调用）都跑在 QThread 中，不阻塞 UI
  - 上传/下载提交到全局 transfer_manager 后立即返回，引擎不阻塞等待传输完成
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from src.services import sync_service
from src.utils.transfer_manager import transfer_manager
from src.utils.logger import get_logger

logger = get_logger(__name__)


# 文件 mtime 比较容差（秒）：FAT32/网络盘等会有 1~2 秒精度差，
# 加容差避免反复来回同步同一个文件
_MTIME_TOLERANCE = 2.0


@dataclass
class SyncResult:
    """单次同步轮次的统计结果，供 UI 展示"""

    config_id: str  # v1.1.6：改为 str 类型
    uploaded: int = 0
    downloaded: int = 0
    skipped: int = 0
    error: str = ""

    @property
    def total_actions(self) -> int:
        return self.uploaded + self.downloaded


# ── 工作线程：执行单个配置的一次同步 ─────────────────

class _SyncRunWorker(QThread):
    """
    单次同步执行线程：扫描本地 + 拉远端快照 + 计算 diff + 提交 transfer 任务。
    """

    finished_with_result = Signal(object)  # SyncResult
    error = Signal(str, str)               # config_id (str), msg

    def __init__(self, config: dict):
        super().__init__()
        self._config = config

    def run(self) -> None:
        cfg = self._config
        cfg_id = cfg["id"]
        local_path = cfg["local_path"]
        disk_id = cfg["disk_id"]
        remote_path = cfg["remote_path"]

        result = SyncResult(config_id=cfg_id)
        try:
            if not os.path.isdir(local_path):
                # 本地目录缺失：尝试创建（首次配置时常见）
                os.makedirs(local_path, exist_ok=True)

            local_index = _build_local_index(local_path)
            # v1.1.6：改用 disk_id + remote_path 调 snapshot
            remote_index = _build_remote_index(disk_id, remote_path)

            for rel in sorted(set(local_index.keys()) | set(remote_index.keys())):
                local_meta = local_index.get(rel)
                remote_meta = remote_index.get(rel)

                if local_meta and not remote_meta:
                    # 本地独有 → 上传
                    self._submit_upload(cfg, rel, local_meta)
                    result.uploaded += 1
                elif remote_meta and not local_meta:
                    # 远端独有 → 下载（包含"本地被用户删除"的场景，会重新拉回）
                    self._submit_download(cfg, rel, remote_meta)
                    result.downloaded += 1
                else:
                    # 两边都有：先比 size，size 一致 + mtime 容差内直接跳过（快路径）
                    local_size, local_mtime = local_meta
                    remote_size = remote_meta.get("size", 0)
                    remote_mtime = remote_meta.get("modified_at", 0)

                    if (
                        local_size == remote_size
                        and abs(local_mtime - remote_mtime) <= _MTIME_TOLERANCE
                    ):
                        # 完全一致：不提交任何任务，零开销
                        result.skipped += 1
                    elif local_mtime > remote_mtime:
                        self._submit_upload(cfg, rel, local_meta)
                        result.uploaded += 1
                    else:
                        self._submit_download(cfg, rel, remote_meta)
                        result.downloaded += 1

            # v1.1.6：touch_synced 改为本地写入
            try:
                sync_service.touch_synced(cfg_id)
            except Exception as e:
                logger.warning("touch_synced 失败（不影响已提交的传输任务）：%s", e)

            self.finished_with_result.emit(result)
        except Exception as e:
            logger.error("同步任务失败 cfg=%s: %s", cfg_id, e)
            self.error.emit(cfg_id, str(e))

    # ── 提交传输任务 ──────────────────────────────

    def _submit_upload(
        self, cfg: dict, rel: str, local_meta: tuple[int, float]
    ) -> None:
        """构造上传任务参数并提交到 transfer_manager"""
        local_full = os.path.join(cfg["local_path"], rel.replace("/", os.sep))
        # 计算远端目标父目录（remote_path + 文件相对父目录）
        rel_parent = "/".join(rel.split("/")[:-1])
        remote_dir = "/".join(p for p in [cfg["remote_path"] or "", rel_parent] if p)
        transfer_manager.submit_upload(
            cfg["disk_id"], remote_dir, local_full,
        )

    def _submit_download(
        self, cfg: dict, rel: str, remote_meta: dict
    ) -> None:
        """构造下载任务参数并提交到 transfer_manager"""
        local_full = os.path.join(cfg["local_path"], rel.replace("/", os.sep))
        # 确保本地父目录存在（transfer_manager 内部 download_to_file 也会处理，
        # 但这里提前创建可避免任务真正执行时再失败）
        parent = os.path.dirname(local_full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # 远端完整相对路径
        remote_rel = "/".join(p for p in [cfg["remote_path"] or "", rel] if p)
        filename = os.path.basename(rel)
        # 把远端 mtime 一并传下去，下载完成后客户端 os.utime 还原本地 mtime
        # 这样下次同步该文件就走 size+mtime 快路径直接跳过，不再重复下载
        transfer_manager.submit_download(
            cfg["disk_id"], remote_rel, local_full,
            filename=filename,
            file_size=remote_meta.get("size", 0),
            remote_mtime=remote_meta.get("modified_at", 0.0),
        )


# ── 索引构建 ──────────────────────────────────────

def _build_local_index(root: str) -> dict[str, tuple[int, float]]:
    """
    递归扫描本地目录，返回 {相对路径: (size, mtime)} 字典。
    路径分隔符统一为 /，与远端快照保持一致。
    """
    result: dict[str, tuple[int, float]] = {}
    root_norm = os.path.normpath(root)
    base_len = len(root_norm) + 1
    for dirpath, _dirnames, filenames in os.walk(root_norm):
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                stat = os.stat(full)
            except OSError as e:
                logger.warning("本地索引跳过 %s: %s", full, e)
                continue
            rel = full[base_len:].replace(os.sep, "/")
            result[rel] = (stat.st_size, stat.st_mtime)
    return result


def _build_remote_index(disk_id: int, remote_path: str) -> dict[str, dict]:
    """
    通过 API 获取远端快照，转换为 {相对路径: 元数据} 字典。

    v1.1.6：签名从 config_id 改为 disk_id + remote_path。
    """
    files = sync_service.get_remote_snapshot(disk_id, remote_path)
    return {f["path"]: f for f in files}


# ── 单例引擎 ─────────────────────────────────────

class SyncEngine(QObject):
    """
    同步引擎单例。

    职责：
      - 维护每个开启 auto_sync 的配置的 QTimer
      - 接收手动同步触发请求（`trigger_sync(config_id)`）
      - 防止同一配置并发同步（`_running_ids` 集合）

    :signal sync_started(str): 某配置开始同步（v1.1.6：ID 为 str）
    :signal sync_finished(object): 某配置结束（携带 SyncResult）
    :signal sync_error(str, str): 某配置同步失败
    """

    sync_started = Signal(str)       # v1.1.6：str
    sync_finished = Signal(object)
    sync_error = Signal(str, str)    # config_id (str), msg

    def __init__(self):
        super().__init__()
        self._timers: dict[str, QTimer] = {}
        self._workers: dict[str, _SyncRunWorker] = {}
        self._running_ids: set[str] = set()
        # 缓存最后一次拿到的配置（用于决定是否重新部署 timer）
        self._configs_by_id: dict[str, dict] = {}

    # ── 配置变更同步到引擎 ────────────────────────

    def reload_configs(self, configs: list[dict]) -> None:
        """
        同步引擎应当感知到的配置列表（通常由 sync_page 在拉到列表后调用）。
        会重建定时器：新增 enabled+auto_sync 的配置会启动 timer，
        关闭/删除的配置会停止 timer。
        """
        new_ids = {c["id"] for c in configs}
        # 停止已不存在或已禁用的 timer
        for cid in list(self._timers.keys()):
            if cid not in new_ids:
                self._stop_timer(cid)
        # 应用新的配置列表
        self._configs_by_id = {c["id"]: c for c in configs}
        for cfg in configs:
            cid = cfg["id"]
            if cfg.get("enabled") and cfg.get("auto_sync"):
                self._ensure_timer(cfg)
            else:
                self._stop_timer(cid)

    def _ensure_timer(self, cfg: dict) -> None:
        """根据配置启动/调整 QTimer"""
        cid = cfg["id"]
        interval_ms = max(30, int(cfg.get("sync_interval") or 300)) * 1000
        timer = self._timers.get(cid)
        if timer is None:
            timer = QTimer(self)
            timer.timeout.connect(lambda c=cid: self.trigger_sync(c))
            self._timers[cid] = timer
        timer.setInterval(interval_ms)
        if not timer.isActive():
            timer.start()

    def _stop_timer(self, config_id: str) -> None:
        """停止并移除某配置的 timer"""
        timer = self._timers.pop(config_id, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    # ── 触发同步 ──────────────────────────────────

    def trigger_sync(self, config_id: str) -> bool:
        """
        触发一次同步。

        - 已在执行中：直接返回 False（防并发）
        - 配置不在缓存：返回 False（应先 reload_configs）

        :returns: 是否成功启动一次同步任务
        """
        if config_id in self._running_ids:
            logger.info("同步已在执行中，忽略重复触发：cfg=%s", config_id)
            return False
        cfg = self._configs_by_id.get(config_id)
        if not cfg:
            logger.warning("trigger_sync 找不到配置：cfg=%s", config_id)
            return False

        self._running_ids.add(config_id)
        self.sync_started.emit(config_id)

        worker = _SyncRunWorker(cfg)
        worker.finished_with_result.connect(self._on_sync_finished)
        worker.error.connect(self._on_sync_error)
        self._workers[config_id] = worker
        worker.start()
        return True

    # ── worker 回调 ───────────────────────────────

    def _on_sync_finished(self, result: SyncResult) -> None:
        cid = result.config_id
        self._running_ids.discard(cid)
        worker = self._workers.pop(cid, None)
        if worker is not None:
            worker.deleteLater()
        logger.info(
            "同步完成 cfg=%s: 上传 %d / 下载 %d / 跳过 %d",
            cid, result.uploaded, result.downloaded, result.skipped,
        )
        self.sync_finished.emit(result)

    def _on_sync_error(self, config_id: str, msg: str) -> None:
        self._running_ids.discard(config_id)
        worker = self._workers.pop(config_id, None)
        if worker is not None:
            worker.deleteLater()
        self.sync_error.emit(config_id, msg)


# 模块级单例
sync_engine = SyncEngine()
