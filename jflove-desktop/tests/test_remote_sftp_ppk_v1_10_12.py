"""
SFTP 面板不出目录 + 选 `.ppk` 无反应（v1.10.12）

覆盖用户 2026-11-17 实测反馈的两条：

1. SFTP 连上后文件面板常驻「会话尚未建立」——根因是标签在后台建链完成**之前**
   就去要目录，且失败后不重试。前端已按会话状态门控（
   `ui/src/components/remote/__tests__/sftp-view-session-gate.test.tsx`），
   这里守 Python 侧的文案：链路未就绪必须是「正在连接中」的语义，而不是听起来像
   「会话丢了」。

2. 选 `.ppk` 毫无反应——第一层原因是原生文件对话框的**过滤器不含 `*.ppk`**
   （不含的扩展名在对话框里是灰色不可选）。这里守过滤器常量与选中 `.ppk` 后的
   拒绝文案（必须是可执行的转换指引）。
"""

from __future__ import annotations

from typing import Any

import pytest

from src.bridge import remote as remote_bridge
from src.services.ssh_session import SshError, SshSession


def _stub_dialog(monkeypatch: pytest.MonkeyPatch, path: str) -> dict[str, str]:
    """
    把原生文件对话框换成返回固定路径，并记录传进去的过滤器。

    :returns: `{"filter": <过滤器文本>}`（断言用）
    """
    from PySide6.QtWidgets import QFileDialog

    captured: dict[str, str] = {}

    def fake_get_open_file_name(parent: Any = None, caption: str = "", directory: str = "",
                                filter_text: str = "", *args: Any, **kwargs: Any) -> Any:
        captured["filter"] = filter_text
        captured["caption"] = caption
        return path, ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(fake_get_open_file_name))
    return captured


def test_private_key_filter_allows_ppk() -> None:
    """过滤器必须含 *.ppk，否则原生对话框里 .ppk 是灰色不可选（= 用户说的「毫无反应」）"""
    assert "*.ppk" in remote_bridge.PRIVATE_KEY_FILTER
    # 常见私钥扩展名不能被挤掉
    for pattern in ("*.pem", "*.key", "*.rsa", "id_rsa*"):
        assert pattern in remote_bridge.PRIVATE_KEY_FILTER


def test_pick_private_key_uses_ppk_filter(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """真正传给对话框的就是这个常量（防止常量改了、调用点仍用旧字符串）"""
    key_file = tmp_path / "id_ed25519"
    key_file.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n", encoding="utf-8")
    captured = _stub_dialog(monkeypatch, str(key_file))

    # 内容无效会抛错，这里只关心过滤器是否真的传下去
    with pytest.raises(RuntimeError):
        remote_bridge._h_pick_private_key({})
    assert captured["filter"] == remote_bridge.PRIVATE_KEY_FILTER


def test_pick_private_key_ppk_gives_conversion_guide(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """选中 .ppk ⇒ 立刻给出可执行的转换指引（而不是静默 / paramiko 英文原文）"""
    ppk = tmp_path / "win-scp.ppk"
    ppk.write_text(
        "PuTTY-User-Key-File-3: ssh-rsa\n"
        "Encryption: none\n"
        "Comment: imported-openssh-key\n",
        encoding="utf-8",
    )
    _stub_dialog(monkeypatch, str(ppk))

    before = dict(remote_bridge._pending_private_keys)
    with pytest.raises(RuntimeError) as excinfo:
        remote_bridge._h_pick_private_key({})

    message = str(excinfo.value)
    assert "ppk" in message
    assert "PuTTYgen" in message
    assert "Export OpenSSH key" in message
    # 失败时不能留下可用的 token（避免后续保存把半成品当成功）
    assert remote_bridge._pending_private_keys == before


def test_pick_private_key_cancel_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """用户点取消 ⇒ 静默返回 cancelled，不报错（否则也是「有反应但不对」）"""
    _stub_dialog(monkeypatch, "")
    assert remote_bridge._h_pick_private_key({}) == {"cancelled": True}


def test_open_sftp_before_ready_says_connecting() -> None:
    """链路未建好：文案必须是「正在连接中」，前端据此显示等待而不是目录错误"""
    session = SshSession({"id": 1, "host": "127.0.0.1", "port": 22, "username": "u"})
    with pytest.raises(SshError) as excinfo:
        session.open_sftp()
    message = str(excinfo.value)
    assert "正在连接中" in message
    assert "尚未建立" in message
