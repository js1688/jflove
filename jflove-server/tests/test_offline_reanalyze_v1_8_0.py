"""
离线下载「签名过期自愈」（AC-225 / 设计 §2.5.5）回归用例。

覆盖：
  - `url_expired` → 自动重分析一次 → 刷新地址 → 从断点继续 → 成功；
  - 重分析后仍过期 → 只重试一次，最终 failed(url_expired)；
  - 分析队列满 → 不自动重分析，直接 failed(url_expired)；
  - 非 url_expired 错误 → 不触发重分析；
  - 已成功项在重试轮中不重复下载（断点续传）；
  - 重分析匹配优先 `variant_id`，避免站点改版时把 A 的地址写到 B 上。

说明：只调内部 worker，不启动真实队列；分析器/下载器用假实现，
真实数据库用 conftest 的临时库（不触碰开发库）。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import src.models.database as database
from src.repositories import offline_download_repository as repo
from src.services import config_service, offline_download_service as service
from src.services.media_analyzer_service import AnalysisError, AnalysisResult, AnalyzedItem
from src.services.media_fetcher_service import FetchError, FetchOutcome


@asynccontextmanager
async def _db():
    """独立数据库连接（row_factory=Row）。"""
    db = await aiosqlite.connect(database.DB_PATH)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


async def _users(db):
    from src.repositories import user_repository

    alice = await user_repository.find_by_username(db, "alice")
    return dict(alice)


@pytest.fixture()
def offline_env(env, monkeypatch):
    """打开离线下载 + 清空任务表 + 建 alice 的默认目录。"""
    config_service.invalidate_cache()

    async def setup():
        async with _db() as db:
            await config_service.update(db, "offline_download_enabled", "1")
            await db.execute("DELETE FROM offline_download_items")
            await db.execute("DELETE FROM offline_download_tasks")
            await db.execute("DELETE FROM offline_download_settings")
            await db.commit()
            alice = await _users(db)
            await repo.upsert_setting(db, alice["id"], env["disk_id"], "alice_dl")
            (Path(env["disk_root"]) / "alice_dl").mkdir(exist_ok=True)
        return alice

    alice = asyncio.run(setup())
    monkeypatch.setattr(
        service.BROWSER_MANAGER, "available", AsyncMock(return_value=False)
    )
    yield {
        **env,
        "alice_id": alice["id"],
        "alice_user": {"id": alice["id"], "role": "user", "username": "alice"},
    }


async def make_ready_task(db, user_id: int, items: list[dict] | None = None) -> int:
    """造一个 `ready` 任务 + 清单项。"""
    task_id = await repo.insert_task(
        db,
        user_id=user_id,
        username="alice",
        source_url="https://example.com/page",
        source_domain="example.com",
        page_title="Demo",
        status="ready",
    )
    rows = items or [
        {
            "kind": "video",
            "display_name": "240P",
            "ext": "mp4",
            "source_url": "https://cdn.example.com/old.mp4",
            "protocol": "http",
            "est_size": 100,
        }
    ]
    await repo.replace_items(db, task_id, rows)
    await repo.set_analysis_result(
        db,
        task_id,
        level="l2",
        expire_at="2999-01-01T00:00:00+00:00",
        item_total=len(rows),
        page_title="Demo",
    )
    return task_id


def _item(source_url: str, name: str = "240P", variant_id: str = "") -> AnalyzedItem:
    """构造一条分析结果项。"""
    return AnalyzedItem(
        kind="video",
        display_name=name,
        ext="mp4",
        source_url=source_url,
        protocol="http",
        variant_id=variant_id,
        est_size=100,
    )


class _FakeAnalyzer:
    """记录调用次数/地址的假分析器。"""

    def __init__(self, items=None, error: AnalysisError | None = None):
        self.items = items or [_item("https://cdn.example.com/new.mp4")]
        self.error = error
        self.calls: list[str] = []

    async def analyze(self, url, **kwargs):
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return AnalysisResult(page_title="Demo", items=self.items, level="l2")


class _FakeFetcher:
    """按 URL 决定成功/过期的假下载器。"""

    def __init__(self, expired_urls: set[str] | None = None):
        self.expired_urls = expired_urls or set()
        self.calls: list[str] = []

    async def fetch_item(self, target_dir, task_id, item, **kwargs):
        url = item["source_url"]
        self.calls.append(url)
        if url in self.expired_urls:
            raise FetchError("url_expired", "模拟签名过期")
        name = f"{item['display_name']}.mp4"
        return FetchOutcome(
            output_name=name,
            output_path=str(Path(target_dir) / name),
            bytes_written=100,
        )

    async def fetch_images(self, *args, **kwargs):
        return []


class _FakeOutbound:
    def __init__(self, *args, **kwargs):  # v1.8.2：构造参数（超时）忽略
        pass

    async def aclose(self):
        pass


def _install(monkeypatch, analyzer, fetcher):
    monkeypatch.setattr(service, "MediaAnalyzerService", lambda *a, **k: analyzer)
    monkeypatch.setattr(service, "MediaFetcherService", lambda *a, **k: fetcher)
    monkeypatch.setattr(service, "OutboundClient", _FakeOutbound)


def _select_and_run(task_id: int, user: dict, item_ids: list[int], disk_id: int):
    async def run():
        async with _db() as db:
            await service.select_items(db, user, task_id, item_ids, disk_id, "alice_dl")
            await service._run_download(db, task_id)
            row = await repo.find_task(db, task_id, user["id"])
            items = [dict(entry) for entry in await repo.list_items(db, task_id)]
            return dict(row), items

    return asyncio.run(run())


def test_url_expired_auto_reanalyze_then_success(offline_env, monkeypatch) -> None:
    """地址过期 → 自动重分析 1 次 → 用新地址续传成功。"""
    analyzer = _FakeAnalyzer()
    fetcher = _FakeFetcher({"https://cdn.example.com/old.mp4"})
    _install(monkeypatch, analyzer, fetcher)

    async def prepare():
        async with _db() as db:
            task_id = await make_ready_task(db, offline_env["alice_id"])
            item = (await repo.list_items(db, task_id))[0]
            return task_id, int(item["id"])

    task_id, item_id = asyncio.run(prepare())
    row, items = _select_and_run(
        task_id, offline_env["alice_user"], [item_id], offline_env["disk_id"]
    )

    assert row["status"] == "success"
    assert items[0]["source_url"] == "https://cdn.example.com/new.mp4"
    assert items[0]["status"] == "success"
    assert analyzer.calls == ["https://example.com/page"]  # 重分析的是页面，不是媒体地址
    assert fetcher.calls == [
        "https://cdn.example.com/old.mp4",
        "https://cdn.example.com/new.mp4",
    ]


def test_reanalyze_still_expired_fails_once(offline_env, monkeypatch) -> None:
    """重分析后的新地址仍过期 ⇒ 只重试 1 次，最终 failed + url_expired。"""
    analyzer = _FakeAnalyzer()
    fetcher = _FakeFetcher(
        {"https://cdn.example.com/old.mp4", "https://cdn.example.com/new.mp4"}
    )
    _install(monkeypatch, analyzer, fetcher)

    async def prepare():
        async with _db() as db:
            task_id = await make_ready_task(db, offline_env["alice_id"])
            item = (await repo.list_items(db, task_id))[0]
            return task_id, int(item["id"])

    task_id, item_id = asyncio.run(prepare())
    row, items = _select_and_run(
        task_id, offline_env["alice_user"], [item_id], offline_env["disk_id"]
    )

    assert row["status"] == "failed" and row["error_code"] == "url_expired"
    assert len(analyzer.calls) == 1
    assert fetcher.calls == [
        "https://cdn.example.com/old.mp4",
        "https://cdn.example.com/new.mp4",
    ]


def test_queue_full_skips_auto_reanalyze(offline_env, monkeypatch) -> None:
    """分析队列已满（20 条排队/分析中）时不自动重分析。"""
    analyzer = _FakeAnalyzer()
    fetcher = _FakeFetcher({"https://cdn.example.com/old.mp4"})
    _install(monkeypatch, analyzer, fetcher)

    async def prepare():
        async with _db() as db:
            for _ in range(20):
                await repo.insert_task(
                    db,
                    user_id=offline_env["alice_id"],
                    username="alice",
                    source_url="https://example.com/q",
                    source_domain="example.com",
                    status="queued",
                )
            task_id = await make_ready_task(db, offline_env["alice_id"])
            item = (await repo.list_items(db, task_id))[0]
            return task_id, int(item["id"])

    task_id, item_id = asyncio.run(prepare())
    row, _items = _select_and_run(
        task_id, offline_env["alice_user"], [item_id], offline_env["disk_id"]
    )

    assert row["status"] == "failed" and row["error_code"] == "url_expired"
    assert analyzer.calls == []
    assert fetcher.calls == ["https://cdn.example.com/old.mp4"]


def test_other_fetch_error_does_not_reanalyze(offline_env, monkeypatch) -> None:
    """防盗链等非过期错误不触发重分析。"""
    analyzer = _FakeAnalyzer()
    fetcher = _FakeFetcher()
    fetcher.fetch_item = AsyncMock(side_effect=FetchError("referer_denied", "防盗链"))
    _install(monkeypatch, analyzer, fetcher)

    async def prepare():
        async with _db() as db:
            task_id = await make_ready_task(db, offline_env["alice_id"])
            item = (await repo.list_items(db, task_id))[0]
            return task_id, int(item["id"])

    task_id, item_id = asyncio.run(prepare())
    row, _items = _select_and_run(
        task_id, offline_env["alice_user"], [item_id], offline_env["disk_id"]
    )

    assert row["status"] == "failed" and row["error_code"] == "referer_denied"
    assert analyzer.calls == []


def test_success_items_not_redownloaded_after_reanalyze(offline_env, monkeypatch) -> None:
    """两项任务：第一项成功后第二项过期 ⇒ 重试只下载第二项（断点续传）。"""
    items = [
        {
            "kind": "video",
            "display_name": "A",
            "ext": "mp4",
            "source_url": "https://cdn.example.com/a.mp4",
            "protocol": "http",
            "est_size": 100,
        },
        {
            "kind": "video",
            "display_name": "B",
            "ext": "mp4",
            "source_url": "https://cdn.example.com/b-old.mp4",
            "protocol": "http",
            "est_size": 100,
        },
    ]
    analyzer = _FakeAnalyzer(
        items=[
            _item("https://cdn.example.com/a.mp4", "A"),
            _item("https://cdn.example.com/b-new.mp4", "B"),
        ]
    )
    fetcher = _FakeFetcher({"https://cdn.example.com/b-old.mp4"})
    _install(monkeypatch, analyzer, fetcher)

    async def prepare():
        async with _db() as db:
            task_id = await make_ready_task(db, offline_env["alice_id"], items=items)
            rows = await repo.list_items(db, task_id)
            return task_id, [int(row["id"]) for row in rows]

    task_id, item_ids = asyncio.run(prepare())
    row, rows = _select_and_run(
        task_id, offline_env["alice_user"], item_ids, offline_env["disk_id"]
    )

    assert row["status"] == "success"
    assert fetcher.calls == [
        "https://cdn.example.com/a.mp4",
        "https://cdn.example.com/b-old.mp4",
        "https://cdn.example.com/b-new.mp4",
    ]
    assert all(entry["status"] == "success" for entry in rows)


def test_reanalyze_failure_keeps_url_expired(offline_env, monkeypatch) -> None:
    """重分析本身失败（如无媒体）⇒ 最终仍是 url_expired，不把分析错误抛给用户。"""
    analyzer = _FakeAnalyzer(error=AnalysisError("no_media", "没有找到媒体"))
    fetcher = _FakeFetcher({"https://cdn.example.com/old.mp4"})
    _install(monkeypatch, analyzer, fetcher)

    async def prepare():
        async with _db() as db:
            task_id = await make_ready_task(db, offline_env["alice_id"])
            item = (await repo.list_items(db, task_id))[0]
            return task_id, int(item["id"])

    task_id, item_id = asyncio.run(prepare())
    row, _items = _select_and_run(
        task_id, offline_env["alice_user"], [item_id], offline_env["disk_id"]
    )

    assert row["status"] == "failed" and row["error_code"] == "url_expired"
    assert len(analyzer.calls) == 1


def test_progress_not_double_counted(offline_env, monkeypatch) -> None:
    """进度 = 文件实际字节，不应把“回调值 + 结果字节”重复累加（防 >100%）。"""
    class _ProgressFetcher(_FakeFetcher):
        async def fetch_item(self, target_dir, task_id, item, **kwargs):
            progress = kwargs.get("progress")
            if progress is not None:
                progress(50, 100)
                progress(100, 100)
            return FetchOutcome(
                output_name="240P.mp4",
                output_path=str(Path(target_dir) / "240P.mp4"),
                bytes_written=100,
            )

    analyzer = _FakeAnalyzer()
    fetcher = _ProgressFetcher()
    _install(monkeypatch, analyzer, fetcher)

    async def prepare():
        async with _db() as db:
            task_id = await make_ready_task(db, offline_env["alice_id"])
            item = (await repo.list_items(db, task_id))[0]
            return task_id, int(item["id"])

    task_id, item_id = asyncio.run(prepare())
    row, _items = _select_and_run(
        task_id, offline_env["alice_user"], [item_id], offline_env["disk_id"]
    )

    assert row["status"] == "success"
    assert row["bytes_done"] == 100
    assert row["bytes_total"] == 100


def test_match_prefers_variant_id() -> None:
    """重分析匹配优先 variant_id，避免同名不同变体错配。"""
    old = {
        "kind": "video",
        "variant_id": "v-720",
        "display_name": "720P",
        "ext": "mp4",
        "width": 1280,
        "height": 720,
    }
    wrong = _item("https://cdn.example.com/wrong.mp4", name="720P")
    right = _item("https://cdn.example.com/right.mp4", name="720P", variant_id="v-720")
    index = service._match_reanalysis_item(old, [wrong, right], set())
    assert index == 1
