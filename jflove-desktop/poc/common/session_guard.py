"""
会话文件护栏（**强制**）：把桌面端会话重定向到临时路径，绝不污染用户真实登录态

## 为什么要有这个模块（事故记录）

2026-09-20，P0-2 的**交互式控制台**（`poc/bridge/main.py`）漏了这层保护。
用户点了一次「一键登录」→ `auth_service.login()` → `_save_session()` →
**临时账号的会话被覆盖写进用户真实的 `session.json`**，用户原本的登录态丢失。

而同一节点的自动化测试（`test_headless.py`）**做对了**这件事，所以测试期没暴露问题 ——
这正是「同一条保护必须只有一个实现、且所有入口强制经过」的原因。

## 事实依据

桌面端状态的**真实**位置在 `%APPDATA%\\JFLove\\storage\\`，不在仓库的
`jflove-desktop/storage/`（后者只有旧版迁移源 `server_history.json`，内容不同）。

## 用法

    guard = SessionGuard(tmp_session_path).activate()   # 必须在任何登录/登出之前
    try:
        ...跑 PoC...
    finally:
        ok, detail = guard.verify_unchanged()           # 断言用户真实文件没被动过
        guard.cleanup()

`:param tmp_session_path:` 临时会话文件路径（放在 PoC 自己的目录里，结束即删）
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

# poc/common/session_guard.py → parents[2] = jflove-desktop
_DESKTOP_ROOT = Path(__file__).resolve().parents[2]
if str(_DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(_DESKTOP_ROOT))


def _sha16(path: Path) -> str:
    """文件内容短哈希（只用于比对，不泄露内容）"""
    if not path.exists():
        return "(不存在)"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


class SessionGuard:
    """
    会话文件重定向护栏。

    :param tmp_path: 临时会话文件路径（会被创建/删除）
    """

    def __init__(self, tmp_path: str | os.PathLike[str]) -> None:
        self.tmp_path = Path(tmp_path)
        self.tmp_history_path = self.tmp_path.with_name(".tmp_server_history.json")
        self.real_path: Path | None = None
        self.real_hash: str = "(未激活)"
        self.real_history_path: Path | None = None
        self.real_history_hash: str = "(未激活)"
        # 同步规则（N9）也存在本地 JSON，同样必须重定向
        self.tmp_sync_path = self.tmp_path.with_name(".tmp_sync_configs.json")
        self.real_sync_path: Path | None = None
        self.real_sync_hash: str = "(未激活)"
        self._activated = False

    # ── 激活 ──────────────────────────────────────

    def activate(self) -> "SessionGuard":
        """
        记录用户真实状态文件，并把两个路径指向临时文件：

        - `auth_service._SESSION_FILE`（会话，含 JWT）
        - `server_history_service._HISTORY_FILE`（服务端地址历史）

        **必须在任何登录 / 登出 / 会话恢复之前调用。**

        :returns: self（便于链式调用）
        """
        if self._activated:
            return self

        from src.services import auth_service, server_history_service, sync_service

        self.real_path = Path(auth_service._SESSION_FILE)
        self.real_hash = _sha16(self.real_path)
        self.real_history_path = Path(server_history_service._HISTORY_FILE)
        self.real_history_hash = _sha16(self.real_history_path)
        self.real_sync_path = Path(sync_service._get_sync_configs_path())
        self.real_sync_hash = _sha16(self.real_sync_path)

        for path in (self.tmp_path, self.tmp_history_path, self.tmp_sync_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                path.unlink()

        auth_service._SESSION_FILE = str(self.tmp_path)
        server_history_service._HISTORY_FILE = self.tmp_history_path
        # 同步规则：把配置读写重定向到临时文件（否则会改到用户真实规则）
        _tmp_sync = self.tmp_sync_path
        sync_service._get_sync_configs_path = lambda: str(_tmp_sync)
        self._activated = True
        return self

    # ── 校验与清理 ────────────────────────────────

    def verify_unchanged(self) -> tuple[bool, str]:
        """
        校验用户真实状态文件未被改动。

        :returns: (是否一致, 描述文本)
        """
        if self.real_path is None or self.real_history_path is None:
            return False, "护栏未激活"
        session_after = _sha16(self.real_path)
        history_after = _sha16(self.real_history_path)
        ok = self.real_hash == session_after and self.real_history_hash == history_after
        detail = (
            f"会话 {self.real_hash} → {session_after}；"
            f"地址历史 {self.real_history_hash} → {history_after}"
        )
        return ok, detail

    def cleanup(self) -> None:
        """删除临时文件，并把两个路径恢复原位"""
        # noqa: F401 - 本处只校验前两个路径
        from src.services import auth_service, server_history_service

        for path in (self.tmp_path, self.tmp_history_path, self.tmp_sync_path):
            if path.exists():
                path.unlink()
        if self.real_path is not None:
            auth_service._SESSION_FILE = str(self.real_path)
        if self.real_history_path is not None:
            server_history_service._HISTORY_FILE = self.real_history_path

    # ── 便利属性 ──────────────────────────────────

    @property
    def summary(self) -> str:
        """一句话描述当前护栏状态（可安全打印）"""
        return (
            f"真实会话={self.real_path}（哈希 {self.real_hash}）"
            f" / 真实地址历史={self.real_history_path}（哈希 {self.real_history_hash}）"
            f" → 临时={self.tmp_path.parent}"
        )
