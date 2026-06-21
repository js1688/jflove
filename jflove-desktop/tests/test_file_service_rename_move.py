"""
桌面端文件服务层 rename_file / move_file 单元测试（v1.1.3）

通过 mock http_client.post 验证服务层传参正确性。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.services import file_service


@pytest.fixture()
def mock_post():
    """mock http_client.post，默认返回空 dict"""
    with patch("src.services.file_service.http_client") as m:
        m.post.return_value = {}
        yield m


class TestRenameFile:
    """rename_file 服务方法测试"""

    def test_calls_correct_endpoint(self, mock_post):
        """验证调用了正确的 API 路径"""
        file_service.rename_file(1, "docs/old.txt", "new.txt")
        mock_post.post.assert_called_once_with(
            "/api/v1/files/rename",
            {"disk_id": 1, "path": "docs/old.txt", "new_name": "new.txt"},
        )

    def test_passes_disk_id(self, mock_post):
        """disk_id 参数透传"""
        file_service.rename_file(42, "a.txt", "b.txt")
        call_args = mock_post.post.call_args
        assert call_args[0][1]["disk_id"] == 42

    def test_passes_path(self, mock_post):
        """path 参数透传"""
        file_service.rename_file(1, "dir/sub/file.txt", "renamed.txt")
        call_args = mock_post.post.call_args
        assert call_args[0][1]["path"] == "dir/sub/file.txt"

    def test_passes_new_name(self, mock_post):
        """new_name 参数透传"""
        file_service.rename_file(1, "old.txt", "shiny_new.txt")
        call_args = mock_post.post.call_args
        assert call_args[0][1]["new_name"] == "shiny_new.txt"

    def test_rename_directory(self, mock_post):
        """目录重命名与文件一致走同一接口"""
        file_service.rename_file(3, "my_dir", "new_dir")
        mock_post.post.assert_called_once()
        payload = mock_post.post.call_args[0][1]
        assert payload["path"] == "my_dir"
        assert payload["new_name"] == "new_dir"

    def test_api_error_propagates(self, mock_post):
        """后端返回异常时应向上抛出"""
        mock_post.post.side_effect = RuntimeError("API Error")
        with pytest.raises(RuntimeError, match="API Error"):
            file_service.rename_file(1, "a.txt", "b.txt")

    def test_404_error_propagates_as_api_error(self, mock_post):
        """v1.1.4: 服务端 rename 返回 404 时，http_client 抛 ApiError，
           服务层不应吞掉，直接向上层抛出供 file_page._on_rename_or_move_error 处理"""
        from src.utils.http_client import ApiError
        mock_post.post.side_effect = ApiError(404, "路径不存在")
        with pytest.raises(ApiError) as exc_info:
            file_service.rename_file(1, "gone.txt", "new.txt")
        assert exc_info.value.status_code == 404
        assert "路径不存在" in str(exc_info.value)


class TestMoveFile:
    """move_file 服务方法测试"""

    def test_calls_correct_endpoint(self, mock_post):
        """验证调用了正确的 API 路径"""
        file_service.move_file(1, "docs/report.txt", "archive")
        mock_post.post.assert_called_once_with(
            "/api/v1/files/move",
            {"disk_id": 1, "src_path": "docs/report.txt", "dst_dir_path": "archive"},
        )

    def test_passes_disk_id(self, mock_post):
        """disk_id 参数透传"""
        file_service.move_file(7, "a.txt", "b")
        call_args = mock_post.post.call_args
        assert call_args[0][1]["disk_id"] == 7

    def test_passes_src_path(self, mock_post):
        """src_path 参数透传"""
        file_service.move_file(1, "sub/deep/file.txt", "target")
        call_args = mock_post.post.call_args
        assert call_args[0][1]["src_path"] == "sub/deep/file.txt"

    def test_passes_dst_dir_path(self, mock_post):
        """dst_dir_path 参数透传"""
        file_service.move_file(1, "file.txt", "new/location")
        call_args = mock_post.post.call_args
        assert call_args[0][1]["dst_dir_path"] == "new/location"

    def test_move_to_root(self, mock_post):
        """移动到根目录，dst_dir_path 为空字符串"""
        file_service.move_file(1, "nested/file.txt", "")
        payload = mock_post.post.call_args[0][1]
        assert payload["dst_dir_path"] == ""

    def test_move_directory(self, mock_post):
        """移动目录本身走同一接口"""
        file_service.move_file(2, "old_dir", "new_parent")
        payload = mock_post.post.call_args[0][1]
        assert payload["src_path"] == "old_dir"
        assert payload["dst_dir_path"] == "new_parent"

    def test_api_error_propagates(self, mock_post):
        """后端返回异常时应向上抛出"""
        mock_post.post.side_effect = RuntimeError("Move failed")
        with pytest.raises(RuntimeError, match="Move failed"):
            file_service.move_file(1, "a.txt", "b")

    def test_404_error_propagates_as_api_error(self, mock_post):
        """v1.1.4: 服务端 move 返回 404 时，http_client 抛 ApiError"""
        from src.utils.http_client import ApiError
        mock_post.post.side_effect = ApiError(404, "源路径不存在")
        with pytest.raises(ApiError) as exc_info:
            file_service.move_file(1, "ghost.txt", "dst")
        assert exc_info.value.status_code == 404
        assert "源路径不存在" in str(exc_info.value)
