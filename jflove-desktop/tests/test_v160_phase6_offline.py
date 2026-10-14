"""
v1.6.0 Phase 6 · 编辑链路零远端 / 不依赖登录（AC-42 / AC-47 的静态 + 本地半边）

判据口径（设计 §4.2.2 / 安全宪法 §9.6 桌面端约束）：
  * 编辑链路（打开 → 编辑 → 保存 / 另存 → 草稿暂存 → 列举 → 丢弃）**只碰本地磁盘**，
    没有任何网络出口 —— Python 侧不走 `http_client`，渲染层也没有 `fetch` / `axios`；
  * 编辑链路**不依赖登录态**：不读 token / session_key，不经过 `auth_service`。

判据的一个前提（**很关键，否则会造出假阳性**）：扫描只看**代码**。
项目里大量中文说明写在 docstring / JSDoc / 行注释里（例如 `scratch_service.py` 顶部
注释提到的 `auth_service.py` 只是**注释里的文件名**），拿文档当代码判会制造一堆假命中。
因此本模块先把注释与三引号长串清掉（保留普通字符串字面量 —— 字符串里写死的
`http://` 远端地址仍然要被抓到），再逐行匹配关键字。

与既有用例的分工：
  * `tests/test_bridge_editor_v1_6_0.py` 已判 `src/bridge/editor.py` **单文件**的
    `/api/v1` / `http_client` / `requests` / `session_key` 0 命中（AC-42 ①）；
    本模块做**扩面**，把服务层与渲染层的整条链路一起锁住。
  * 真机半边（断网 + 服务端未启动 + 未登录下逐项可用）由 P6-5
    `poc/editor-probes/r11-real-gui-drive.py` 实跑判定，不在这里冒充。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator, Sequence

import pytest

from src.bridge import editor as editor_bridge
from src.config import settings
from src.utils import http_client
from tests.code_scan import code_lines, scan

DESKTOP_ROOT = Path(__file__).resolve().parents[1]

#: Python 侧的链路实现（短链路的全部落点，逐个显式列出以免 glob 漂移）
PY_CHAIN: tuple[Path, ...] = (
    DESKTOP_ROOT / "src" / "bridge" / "editor.py",
    DESKTOP_ROOT / "src" / "services" / "editor_service.py",
    DESKTOP_ROOT / "src" / "services" / "scratch_service.py",
    DESKTOP_ROOT / "src" / "services" / "text_detect_service.py",
    DESKTOP_ROOT / "src" / "services" / "file_assoc_service.py",
)

#: 渲染层链路的 glob（`__tests__` 由 `_iter_chain` 排除）
TS_CHAIN_GLOBS: tuple[str, ...] = (
    "ui/src/stores/scratch-store.ts",
    "ui/src/stores/shortcut-store.ts",
    "ui/src/config/shortcuts.ts",
    "ui/src/utils/scratch/*.ts",
    "ui/src/components/scratch/*.ts",
    "ui/src/components/scratch/*.tsx",
    "ui/src/components/scratch/views/*.tsx",
)

#: 网络出口关键字（`WebSocket` / `EventSource` 也算远端通道）
NETWORK_TOKENS: tuple[str, ...] = (
    "/api/v1",
    "http_client",
    "requests",
    "httpx",
    "urllib",
    "urlopen",
    "socket.",
    "aiohttp",
    "WebSocket",
    "XMLHttpRequest",
    "EventSource",
    "fetch(",
    "axios(",
    "http://",
    "https://",
)

#: 登录态 / 鉴权关键字（`Bearer` 大小写敏感，`jwt` 与 `Authorization` 均为强信号）
AUTH_TOKENS: tuple[str, ...] = (
    "session_key",
    "Authorization",
    "Bearer",
    "jwt",
    "auth_service",
    "authService",
    "isLoggedIn",
)

#: Python 链路禁止导入的层（网络 / 鉴权 / 会话）
FORBIDDEN_IMPORTS: tuple[str, ...] = (
    "http_client",
    "api_gateway",
    "auth_service",
    "session_manager",
    "requests",
    "httpx",
    "urllib",
    "aiohttp",
)


def _iter_chain() -> Iterator[Path]:
    """按顺序产出链路文件（渲染层 glob 展开后排除 `__tests__`）"""
    yield from PY_CHAIN
    seen = {path.resolve() for path in PY_CHAIN}
    for pattern in TS_CHAIN_GLOBS:
        for path in sorted(DESKTOP_ROOT.glob(pattern)):
            if not path.is_file() or "__tests__" in path.parts:
                continue
            if path.resolve() in seen:
                continue
            seen.add(path.resolve())
            yield path


def _scan(tokens: Sequence[str]) -> list[str]:
    """扫链路（注释剔除规则集中在 `tests/code_scan.py`），返回命中报告"""
    return scan(list(_iter_chain()), tokens, base=DESKTOP_ROOT)


def _pane(pane_id: str) -> dict[str, Any]:
    """§7.1 #6 的 pane 结构（字段名逐字对齐设计表；草稿形态 `path=None`）"""
    return {
        "id": pane_id,
        "title": "无标题",
        "path": None,
        "language": "plain",
        "encoding": "utf-8",
        "lineEnd": "lf",
        "read_only": False,
        "dirty": True,
        "geometry": {"x": 0, "y": 0, "width": 320, "height": 240},
        "caret": 0,
        "scroll_top": 0,
        "font_size": 14,
        "show_line_numbers": True,
        "word_wrap": True,
    }


def _boom(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("编辑链路不得发起网络调用（AC-42 / 安全宪法 §9.6）")


# ── ① 静态：整条链路零网络出口 ──────────────────────────────────────

class TestChainHasNoRemoteEgress:
    """AC-42 ①（扩面）：链路内任一文件都不得出现网络出口关键字"""

    def test_链路文件齐全(self):
        chain = list(_iter_chain())
        assert len(chain) >= 20, (
            f"链路文件数异常（{len(chain)}），glob 可能已漂移：{[p.name for p in chain]}"
        )
        missing = [str(path) for path in PY_CHAIN if not path.exists()]
        assert missing == [], f"Python 链路文件缺失：{missing}"

    def test_链路零命中网络关键字(self):
        hits = _scan(NETWORK_TOKENS)
        assert hits == [], "编辑链路出现网络出口：\n" + "\n".join(hits)


# ── ② 静态：整条链路不依赖登录态 ────────────────────────────────────

class TestChainHasNoLoginDependency:
    """AC-47 的静态半边：链路不读登录态、不经鉴权与网络层"""

    def test_链路零命中鉴权关键字(self):
        hits = _scan(AUTH_TOKENS)
        assert hits == [], "编辑链路出现鉴权关键字：\n" + "\n".join(hits)

    @pytest.mark.parametrize("path", PY_CHAIN, ids=lambda path: path.name)
    def test_python_链路不导入网络或鉴权层(self, path: Path):
        offenders: list[str] = []
        for number, stripped, _original in code_lines(path):
            line = stripped.strip()
            if not line.startswith(("import ", "from ")):
                continue
            for module in FORBIDDEN_IMPORTS:
                if module in line:
                    offenders.append(f"{path.name}:{number}: {line}")
        assert offenders == [], f"链路导入了被禁层：{offenders}"


# ── ③ 行为：哨兵之下本地全流程可用 ──────────────────────────────────

class TestOfflineFlowBehaviour:
    """AC-42 ②的本地半边：把 HTTP 客户端换成「一调用就炸」的哨兵后，全流程仍可用"""

    def test_哨兵之下_打开到丢弃全流程可用(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(settings, "SCRATCH_DIR", str(tmp_path / "scratch"))
        # 哨兵：任何真正的网络调用都会把用例炸掉（而不是悄悄联网）
        monkeypatch.setattr(http_client.HttpClient, "get", _boom)
        monkeypatch.setattr(http_client.HttpClient, "post", _boom)
        monkeypatch.setattr(http_client.requests, "request", _boom)
        monkeypatch.setattr(http_client.requests, "get", _boom)
        monkeypatch.setattr(http_client.requests, "post", _boom)

        handlers = editor_bridge.HANDLERS["editor"]
        target = tmp_path / "note.txt"
        # 用字节写：`Path.write_text` 在 Windows 上会把 \\n 翻译成 CRLF，LF 语义会被破坏
        target.write_bytes(b"hello\n")

        # ① 打开（含类型校验与编码判定）
        item = handlers["detectAndRead"]({"paths": [str(target)]})["items"][0]
        assert item["ok"] is True, item
        assert item["text"] == "hello\n"
        assert item["encoding"] and item["lineEnding"] == "lf", "链路必须给出可回写的编码与换行符"

        # ② 写回（按原编码 + 原换行符原子写）
        written = handlers["writeText"](
            {
                "path": str(target),
                "text": "hello world\n",
                "encoding": item["encoding"],
                "lineEnding": item["lineEnding"],
            }
        )
        assert written["ok"] is True, written
        assert target.read_bytes() == b"hello world\n", "写回不得改动换行符形态"

        # ③ 新窗格（无路径）→ 草稿暂存 → 列举 → 丢弃
        pane = _pane("offline1")
        assert handlers["draftSave"]({"pane": pane, "content": "草稿正文\n"})["ok"] is True
        listed = handlers["draftList"]({})
        assert any(str(entry.get("id")) == "offline1" for entry in listed["entries"]), listed
        assert handlers["draftDiscard"]({"paneId": "offline1"})["ok"] is True

        # ④ 全流程走完，用户目录命名集合不变（无副本、无旁路文件）
        assert sorted(p.name for p in tmp_path.iterdir()) == ["note.txt", "scratch"]
