"""
服务端地址历史服务（本地缓存）

职责：
  - 在 storage/server_history.json 中维护用户用过的服务端地址列表
  - 登录页 / 设置页连接成功后调用 record(url) 把地址写入历史
  - 下次启动时通过 list() / get_default() 提供下拉候选与默认值

设计要点：
  - 只持久化「连接成功过的」地址，避免误输入污染列表
  - 最近用过的排前面（LRU 顺序），便于下拉首项即默认值
  - 上限 _MAX_HISTORY 条，超出按最旧顺序淘汰
  - 文件读写都做异常吞掉处理，不让本地缓存问题阻断登录主流程
  - 不持久化任何 token / session_key / 密码等敏感字段，仅存 URL 字符串
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from src.config.settings import LOCAL_STORAGE_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 历史文件路径
_HISTORY_FILE = Path(LOCAL_STORAGE_DIR) / "server_history.json"

# 最多保留的历史条目数
_MAX_HISTORY = 10

# 兜底地址：当历史为空且没有任何输入时使用
_FALLBACK_URL = "http://localhost:8989"

# 文件读写互斥锁（多线程登录场景防写入冲突）
_lock = threading.Lock()


def _normalize(url: str) -> str:
    """
    规范化 URL：去首尾空白、去末尾斜杠。

    用于 add 时的去重比较以及保存格式统一。
    """
    return (url or "").strip().rstrip("/")


def _load_raw() -> list[str]:
    """从磁盘加载历史列表，文件不存在或损坏时返回空列表"""
    if not _HISTORY_FILE.exists():
        return []
    try:
        with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            # 仅保留字符串元素，过滤异常值
            return [str(x) for x in data if isinstance(x, str) and x.strip()]
        return []
    except Exception as exc:
        logger.warning("读取服务端历史失败，将以空历史继续: %s", exc)
        return []


def _save_raw(items: list[str]) -> None:
    """把历史列表写回磁盘"""
    try:
        os.makedirs(_HISTORY_FILE.parent, exist_ok=True)
        tmp = _HISTORY_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        # 原子替换，避免半写文件污染缓存
        os.replace(tmp, _HISTORY_FILE)
    except Exception as exc:
        logger.warning("保存服务端历史失败（不影响登录）: %s", exc)


def list_history() -> list[str]:
    """返回历史地址列表（最近使用的在前）。无历史时返回空列表。"""
    with _lock:
        return _load_raw()


def get_default() -> str:
    """
    取默认显示地址：

    - 历史非空 → 取第一项（即最近成功连接过的）
    - 历史为空 → 兜底返回 _FALLBACK_URL
    """
    items = list_history()
    return items[0] if items else _FALLBACK_URL


def record(url: str) -> None:
    """
    把一个连接成功的地址写入历史。

    规则：
      - 与历史项规范化后比较去重
      - 写入后置顶，最多保留 _MAX_HISTORY 条
      - 空字符串忽略
    """
    norm = _normalize(url)
    if not norm:
        return
    with _lock:
        items = _load_raw()
        # 去重：把所有等同项移除
        items = [x for x in items if _normalize(x) != norm]
        # 把当前 URL 置顶
        items.insert(0, norm)
        # 截断
        if len(items) > _MAX_HISTORY:
            items = items[:_MAX_HISTORY]
        _save_raw(items)


def delete(url: str) -> None:
    """
    从历史记录中删除指定的地址（v1.1.4 新增）。

    规则：
      - 规范化后匹配，删除所有匹配项
      - 不存在的 URL 静默忽略
      - 空字符串忽略
    """
    norm = _normalize(url)
    if not norm:
        return
    with _lock:
        items = _load_raw()
        before = len(items)
        items = [x for x in items if _normalize(x) != norm]
        if len(items) < before:
            _save_raw(items)
            logger.info("已从历史记录删除: %s", norm)
        else:
            logger.debug("历史记录中不存在: %s，无需删除", norm)
