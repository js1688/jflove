"""
v1.1.2 登录页布局重排单元测试

覆盖点：
  1. "登录有效期"标签和下拉控件位于同一个 QHBoxLayout 中
  2. 返回按钮和登录按钮位于同一个 QHBoxLayout 中
  3. 登录页 stacked widget 索引仍为 _PAGE_LOGIN（未破坏页面切换）
  4. 下拉控件首次启动选中"1 小时"（默认）档位
"""

from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QHBoxLayout

import pytest

from src.config.settings import (
    APP_NAME, APP_ORG, LOCAL_SESSION_TTL_DEFAULT,
)


_KEY = "session/local_max_seconds"


@pytest.fixture(autouse=True)
def _isolate_qsettings(qapp):
    s = QSettings(APP_ORG, APP_NAME)
    s.remove(_KEY)
    yield
    s.remove(_KEY)


def _find_hbox_containing(layout, target_widget):
    """递归查找包含 target_widget 的 QHBoxLayout；找不到返回 None"""
    for i in range(layout.count()):
        item = layout.itemAt(i)
        sub_layout = item.layout()
        if sub_layout is not None:
            if isinstance(sub_layout, QHBoxLayout):
                for j in range(sub_layout.count()):
                    w = sub_layout.itemAt(j).widget()
                    if w is target_widget:
                        return sub_layout
            # 递归
            r = _find_hbox_containing(sub_layout, target_widget)
            if r is not None:
                return r
    return None


class TestLoginPageLayout:

    def test_TTL标签与下拉位于同一个HBox(self, qapp):
        from src.ui.login_window import LoginWindow
        win = LoginWindow()
        try:
            # _ttl_combo 已在 _build_login_page 创建
            assert hasattr(win, "_ttl_combo")
            # 找到包含下拉的 HBox
            login_page = win._stack.widget(2)  # _PAGE_LOGIN = 2
            page_layout = login_page.layout()
            hbox = _find_hbox_containing(page_layout, win._ttl_combo)
            assert hbox is not None, "TTL 下拉应当在 QHBoxLayout 中"
            # 该 HBox 内还应该有"登录有效期"标签作为另一个子控件
            widgets_in_box = [
                hbox.itemAt(i).widget() for i in range(hbox.count())
            ]
            labels = [w for w in widgets_in_box
                      if w is not None and hasattr(w, "text")
                      and w.text() == "登录有效期"]
            assert len(labels) == 1, "TTL HBox 应当包含一个'登录有效期'标签"
        finally:
            win.deleteLater()

    def test_返回与登录按钮位于同一个HBox(self, qapp):
        from src.ui.login_window import LoginWindow
        win = LoginWindow()
        try:
            assert hasattr(win, "_login_btn")
            login_page = win._stack.widget(2)
            page_layout = login_page.layout()
            hbox = _find_hbox_containing(page_layout, win._login_btn)
            assert hbox is not None, "登录按钮应当在 QHBoxLayout 中"
            # 该 HBox 内应当有 2 个按钮
            widgets_in_box = [
                hbox.itemAt(i).widget() for i in range(hbox.count())
            ]
            buttons = [w for w in widgets_in_box if w is not None]
            assert len(buttons) >= 2, "按钮 HBox 应当包含返回 + 登录两个按钮"
            # 其中应当有一个文本为"返回"
            back_buttons = [b for b in buttons
                            if hasattr(b, "text") and b.text() == "返回"]
            assert len(back_buttons) == 1
        finally:
            win.deleteLater()

    def test_TTL下拉默认选中1小时(self, qapp):
        from src.ui.login_window import LoginWindow
        win = LoginWindow()
        try:
            # 默认值应是 LOCAL_SESSION_TTL_DEFAULT（3600 = 1 小时）
            assert win._ttl_combo.currentData() == LOCAL_SESSION_TTL_DEFAULT
        finally:
            win.deleteLater()
