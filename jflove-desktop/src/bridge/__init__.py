"""
JS ↔ Python 桥（桌面端 Web UI 的唯一能力出口）

- `bridge.Bridge`      ：QWebChannel 对象，注册名 `bridge`
- `api_gateway`         ：`api.request` 的路径白名单与 HTTP 转发
"""

from src.bridge.bridge import WHITELIST, Bridge

__all__ = ["Bridge", "WHITELIST"]
