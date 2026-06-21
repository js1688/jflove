"""
v1.1.6：同步配置存储已完全迁移到客户端本地。

此文件被移除——sync_configs 表已从服务端删除，不再需要服务端存储层。
服务端仅保留 `POST /api/v1/sync/snapshot` 接口（在 sync_controller.py 中实现），
该接口直接使用 virtual_disk_repository 查询磁盘信息，无需独立的 repository 层。
"""
