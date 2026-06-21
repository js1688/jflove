"""
MoveTargetDialog 单元测试（v1.1.3）

覆盖：
  - 对话框初始状态（根节点已加载、确认按钮禁用）
  - 根节点展开后懒加载子目录
  - 选中节点后确认按钮可用，getSelectedPath 返回正确值
  - 过滤掉非目录项（is_dir=False 的文件不显示）
  - 取消时 getSelectedPath 返回 None
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt
from src.ui.pages.file_page import MoveTargetDialog


@pytest.fixture()
def dialog(qapp):
    """创建 MoveTargetDialog 实例（offscreen）"""
    with patch("src.ui.pages.file_page.file_service") as mock_fs:
        mock_fs.list_files.return_value = []
        dlg = MoveTargetDialog(disk_id=1, src_rel_path="docs/old.txt")
        dlg.show()
        qapp.processEvents()
        yield dlg, mock_fs
        dlg.close()


class TestMoveTargetDialogInit:
    """对话框初始状态"""

    def test_root_node_exists(self, dialog):
        """打开时应有一个根节点"""
        dlg, _ = dialog
        assert dlg._tree.topLevelItemCount() == 1

    def test_root_node_label(self, dialog):
        """根节点标签包含「根目录」"""
        dlg, _ = dialog
        root = dlg._tree.topLevelItem(0)
        assert "根目录" in root.text(0)

    def test_root_node_path_is_empty(self, dialog):
        """根节点 UserRole 数据为空字符串（磁盘根目录）"""
        dlg, _ = dialog
        root = dlg._tree.topLevelItem(0)
        assert root.data(0, Qt.UserRole) == ""

    def test_root_node_not_loaded_initially(self, dialog):
        """根节点初始状态为「未加载」，展开时才触发懒加载"""
        dlg, _ = dialog
        root = dlg._tree.topLevelItem(0)
        assert root.data(0, Qt.UserRole + 1) is False

    def test_root_has_placeholder_child(self, dialog):
        """根节点有占位子项，使展开箭头可见"""
        dlg, _ = dialog
        root = dlg._tree.topLevelItem(0)
        assert root.childCount() == 1

    def test_confirm_button_disabled_before_show(self, qapp):
        """创建后、show 之前，确认按钮禁用（show 后 Qt 会自动选中首项）"""
        with patch("src.ui.pages.file_page.file_service") as mock_fs:
            mock_fs.list_files.return_value = []
            dlg = MoveTargetDialog(disk_id=1, src_rel_path="x.txt")
            assert not dlg._confirm_btn.isEnabled()
            dlg.close()

    def test_cancel_returns_none(self, qapp):
        """点击取消后 getSelectedPath 返回 None"""
        with patch("src.ui.pages.file_page.file_service") as mock_fs:
            mock_fs.list_files.return_value = []
            dlg = MoveTargetDialog(disk_id=1, src_rel_path="x.txt")
            dlg.reject()
            assert dlg.getSelectedPath() is None


class TestMoveTargetDialogExpand:
    """懒加载展开逻辑"""

    def test_expand_marks_root_as_loaded(self, qapp):
        """展开根节点后，根节点被标记为已加载"""
        with patch("src.ui.pages.file_page.file_service") as mock_fs:
            mock_fs.list_files.return_value = []
            dlg = MoveTargetDialog(disk_id=5, src_rel_path="a.txt")
            dlg.show()
            qapp.processEvents()

            root = dlg._tree.topLevelItem(0)
            # 未展开时为 False
            assert root.data(0, Qt.UserRole + 1) is False

            # 模拟展开（直接调用处理方法，避免依赖 UI 事件）
            dlg._on_item_expanded(root)
            qapp.processEvents()

            # 展开后标记为已加载
            assert root.data(0, Qt.UserRole + 1) is True
            dlg.close()

    def test_only_directories_shown(self, qapp):
        """list_files 返回混合结果时，仅目录子项被添加"""
        mixed_files = [
            {"name": "subdir", "is_dir": True},
            {"name": "file.txt", "is_dir": False},
            {"name": "another_dir", "is_dir": True},
        ]
        with patch("src.ui.pages.file_page.file_service") as mock_fs:
            mock_fs.list_files.return_value = []
            dlg = MoveTargetDialog(disk_id=1, src_rel_path="x.txt")
            dlg.show()
            qapp.processEvents()

            root = dlg._tree.topLevelItem(0)
            # 先触发展开，移除占位子项并标记为已加载
            dlg._on_item_expanded(root)
            qapp.processEvents()

            # 模拟懒加载数据返回
            dlg._on_subdir_loaded(root, mixed_files)
            qapp.processEvents()

            # 只有 2 个目录被添加（file.txt 被过滤）
            assert root.childCount() == 2
            names = [root.child(i).text(0) for i in range(root.childCount())]
            assert "subdir" in names
            assert "another_dir" in names
            assert "file.txt" not in names
            dlg.close()


class TestMoveTargetDialogSelection:
    """选择与确认逻辑"""

    def test_select_root_enables_confirm(self, dialog):
        """选中根节点后确认按钮可用"""
        dlg, _ = dialog
        root = dlg._tree.topLevelItem(0)
        dlg._tree.setCurrentItem(root)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        assert dlg._confirm_btn.isEnabled()

    def test_get_selected_path_root(self, dialog):
        """选中根节点后 getSelectedPath 返回空字符串"""
        dlg, _ = dialog
        root = dlg._tree.topLevelItem(0)
        dlg._tree.setCurrentItem(root)
        dlg._on_confirm()
        assert dlg.getSelectedPath() == ""

    def test_get_selected_path_subdir(self, qapp):
        """选中子目录节点后 getSelectedPath 返回正确路径"""
        with patch("src.ui.pages.file_page.file_service") as mock_fs:
            mock_fs.list_files.return_value = []
            dlg = MoveTargetDialog(disk_id=1, src_rel_path="x.txt")
            dlg.show()
            qapp.processEvents()

            root = dlg._tree.topLevelItem(0)
            # 先展开根节点（清除占位子项）
            dlg._on_item_expanded(root)
            qapp.processEvents()

            # 模拟懒加载返回一个子目录
            dlg._on_subdir_loaded(root, [{"name": "archive", "is_dir": True}])
            qapp.processEvents()

            child = root.child(0)
            assert child is not None
            assert child.text(0) == "archive"

            # 选中子目录并确认
            dlg._tree.setCurrentItem(child)
            qapp.processEvents()
            dlg._on_confirm()
            assert dlg.getSelectedPath() == "archive"
            dlg.close()
