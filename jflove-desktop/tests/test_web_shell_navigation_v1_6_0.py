"""
v1.6.0 桌面端 Web 外壳：**导航边界（S-3）**

依据：

- 安全宪法 §9.6（桌面端）：渲染层"无网络出口"，文件字节与密钥**严禁**进入渲染层；
  而本版桥新增 `editor.readTextOnly` / `editor.detectAndRead` / `editor.writeText`
  ⇒ 渲染层具备**任意路径本地文件读写**能力 ⇒ "只加载自家产物"成为安全边界。
- 探针 `poc/editor-probes/r7-webengine-channel-origin.py`
  （产物 `poc/editor-probes/out/r7-offscreen.json`）实测：`qt.webChannelTransport`
  对**同一 profile 上的任意文档**都可见，与文档来源无关；Qt 默认**没有**任何
  导航策略（全仓 `acceptNavigationRequest` / `createWindow` 零命中）。
- Phase 5 审查报告 `文档记录/代码审查报告/v1.6.0.md` **S-3** 的三处小改：
  ① 导航白名单只放行自家产物；② `createWindow` 显式拒绝；③ 外链交系统浏览器。

覆盖点：`is_internal_url` 白名单口径（含**共享前缀兄弟目录**注入与 `..` 穿越）、
`ShellPage.acceptNavigationRequest` 的放行/拒绝/外链转交、`createWindow` 拒绝、
真实 `WebShell` 装的确实是 `ShellPage`（接线回归）、日志 URL 截断。

测试手法：`QT_QPA_PLATFORM=offscreen`（`conftest.py` 已设）；整个模块复用同一个
`WebShell`（Chromium 视图构造较重）；外链转交用 `web_shell._open_external_url`
接缝替换（**不** patch `QDesktopServices`——patch PySide6 类属性会在 `QApplication`
析构期引发进程级崩溃，见 BACKLOG BL-16 / LESSONS L-1）。
"""
from __future__ import annotations

import os

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEnginePage

from src.ui import web_shell as web_shell_module
from src.ui.web_shell import (
    ShellPage,
    WebShell,
    _url_for_log,
    is_internal_url,
    resolve_index_html,
    resolve_web_root,
)

_NAV = QWebEnginePage.NavigationType
_WINDOW = QWebEnginePage.WebWindowType


def _norm(path: str) -> str:
    """比对路径时统一分隔符与大小写（Qt 把 Windows 路径输出成正斜杠）"""
    return os.path.normcase(os.path.normpath(path))


def _local(url: QUrl) -> str:
    """`QUrl` → 规范化后的本地路径（供"确实是共享前缀"这类前置断言用）"""
    return _norm(url.toLocalFile())


# ── is_internal_url：白名单口径 ─────────────────────────────────────


class TestIsInternalUrl:
    """`is_internal_url()` 是导航裁决的唯一判据，先把它钉死"""

    def test_about_blank_allowed(self) -> None:
        assert is_internal_url(QUrl("about:blank")) is True

    @pytest.mark.parametrize(
        "raw",
        [
            "http://example.com/",
            "https://example.com/x.html",
            "data:text/html,<h1>x</h1>",
            "javascript:alert(1)",
            "ftp://example.com/x",
            "about:config",
        ],
    )
    def test_non_internal_target_rejected(self, raw: str) -> None:
        assert is_internal_url(QUrl(raw)) is False

    def test_own_index_allowed(self) -> None:
        assert is_internal_url(QUrl.fromLocalFile(resolve_index_html())) is True

    def test_own_asset_allowed(self) -> None:
        url = QUrl.fromLocalFile(os.path.join(resolve_web_root(), "assets", "index-x.js"))
        assert is_internal_url(url) is True

    def test_external_file_url_rejected(self, tmp_path) -> None:
        """用户目录里的任意 html 同样能拿到桥 ⇒ 必须拒绝"""
        html = tmp_path / "evil.html"
        html.write_text("<h1>x</h1>", encoding="utf-8")
        assert is_internal_url(QUrl.fromLocalFile(str(html))) is False

    def test_sibling_dir_with_shared_prefix_rejected(self) -> None:
        """
        核心回归：`ui/dist-evil/` 与 `ui/dist/` 共享前缀，
        旧的字符串 `startswith` 写法会放行（同 S-1 的 `_safe_join` 缺陷）。
        """
        root = resolve_web_root()
        sibling = os.path.join(os.path.dirname(root), os.path.basename(root) + "-evil")
        url = QUrl.fromLocalFile(os.path.join(sibling, "evil.html"))
        assert _local(url).startswith(_norm(root))  # 前置：确实是共享前缀
        assert is_internal_url(url) is False

    def test_parent_traversal_rejected(self) -> None:
        url = QUrl.fromLocalFile(
            os.path.join(resolve_web_root(), "..", "outside", "evil.html")
        )
        assert is_internal_url(url) is False


# ── ShellPage：导航裁决 ─────────────────────────────────────────────


@pytest.fixture(scope="module")
def shell(qapp):
    """整个模块复用一个真实 `WebShell`（Chromium 视图构造较重）"""
    instance = WebShell(objects={}, frameless=True, publish_event=None)
    yield instance
    instance.hide()


@pytest.fixture
def opened(monkeypatch) -> list:
    """把"交系统浏览器"换成记录器（**不**真开浏览器）"""
    calls: list = []
    monkeypatch.setattr(web_shell_module, "_open_external_url", calls.append)
    return calls


class TestShellPageWiring:
    """接线回归：外壳必须真的用上 `ShellPage`"""

    def test_view_page_is_shell_page(self, shell) -> None:
        assert isinstance(shell.view.page(), ShellPage)

    def test_own_index_navigation_allowed(self, shell) -> None:
        ok = shell.view.page().acceptNavigationRequest(
            QUrl.fromLocalFile(resolve_index_html()), _NAV.NavigationTypeTyped, True
        )
        assert ok is True

    def test_external_file_navigation_rejected(self, shell, opened, tmp_path) -> None:
        html = tmp_path / "evil.html"
        html.write_text("<h1>x</h1>", encoding="utf-8")
        ok = shell.view.page().acceptNavigationRequest(
            QUrl.fromLocalFile(str(html)), _NAV.NavigationTypeTyped, True
        )
        assert ok is False
        assert opened == []

    def test_remote_navigation_rejected_without_browser(self, shell, opened) -> None:
        """非点击型远程导航（脚本/表单跳转）只拒绝，**不**弹浏览器"""
        ok = shell.view.page().acceptNavigationRequest(
            QUrl("https://evil.example/x"), _NAV.NavigationTypeTyped, True
        )
        assert ok is False
        assert opened == []

    @pytest.mark.parametrize(
        "raw",
        ["https://example.com/a?b=1", "http://example.com/b", "mailto:dev@example.com"],
    )
    def test_clicked_link_goes_to_system_browser(self, shell, opened, raw: str) -> None:
        ok = shell.view.page().acceptNavigationRequest(
            QUrl(raw), _NAV.NavigationTypeLinkClicked, True
        )
        assert ok is False
        assert [str(u.toString()) for u in opened] == [str(QUrl(raw).toString())]

    def test_clicked_data_url_not_forwarded(self, shell, opened) -> None:
        """`data:` 外链既不放行也不外发（它同样能拿到桥）"""
        ok = shell.view.page().acceptNavigationRequest(
            QUrl("data:text/html,<h1>evil</h1>"), _NAV.NavigationTypeLinkClicked, True
        )
        assert ok is False
        assert opened == []

    def test_create_window_returns_none(self, shell) -> None:
        assert shell.view.page().createWindow(_WINDOW.WebBrowserWindow) is None


# ── 日志口径 ────────────────────────────────────────────────────────


class TestUrlForLog:
    """日志里的 URL 要截断（安全宪法 §9.4 的克制口径）"""

    def test_short_url_unchanged(self) -> None:
        url = QUrl("https://example.com/a")
        assert _url_for_log(url) == str(url.toString())

    def test_long_url_truncated(self) -> None:
        url = QUrl("https://example.com/" + "x" * 400)
        text = _url_for_log(url)
        assert text.endswith("…")
        assert len(text) == 121
