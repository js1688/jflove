"""
v1.6.0 Phase 6 · 编辑链路零远端（AC-42 的静态 + 本地半边；v1.6.1 就地改写 AC-47 半边）

判据口径（设计 §4.2.2 / 安全宪法 §9.6 桌面端约束）：
  * 编辑链路（打开 → 编辑 → 保存 / 另存 → 草稿暂存 → 列举 → 丢弃）**只碰本地磁盘**，
    没有任何网络出口 —— Python 侧不走 `http_client`，渲染层也没有 `fetch` / `axios`；
  * **v1.6.1 的反转（原 AC-47 → 新 AC-63）**：编辑链路**要**依赖登录态（未登录时零存在），
    但只准经单一真相 `ui/src/utils/scratch/editor-gate.ts::canUseEditor` 读布尔快照；
    链路仍然**零凭据**（不读 token / session_key / Authorization / jwt，不经 `auth_service`）。

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
  * **v1.6.1 Phase 6 修正（审查 M-3）**：② 节的扫描面原本漏了 `ui/src/layouts/*.tsx` 与
    `ui/src/config/routes.tsx`，而 `GATE_CONSUMER_ALLOWLIST` 却把两者都列了进去 ⇒
    白名单里两条是**死条目**（AC-63 ① 的导航项落点无人守）。现已补入扫描面，
    并把 `routes.tsx` 从"消费点"降级为 `GATE_GUARD_EXEMPT`（守卫层豁免，见该常量注释）。
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
#:
#: ⚠ v1.6.1 审查 **M-3** 修正（扫描面与准入白名单原本不一致 ⇒ 白名单里有两条是**死条目**）：
#:   * `ui/src/layouts/*.tsx` —— AC-63 ① 的**真实落点**（`DesktopLayout.tsx:72` 调
#:     `useEditorGate()` 决定侧栏「文本编辑器」入口的可见性），此前不在扫描面内，
#:     于是"把它改回自己写 `isLoggedIn &&`"这类回归**不会变红**；
#:   * `ui/src/config/routes.tsx` —— 守卫层（`RequireAuth`），此前同样不在扫描面内，
#:     却被列进 `GATE_CONSUMER_ALLOWLIST`；一旦纳入扫描面就会**必然失败**
#:     （它只读 `isLoggedIn`、不调 `canUseEditor`）。现已拆成 `GATE_GUARD_EXEMPT`（见下）。
TS_CHAIN_GLOBS: tuple[str, ...] = (
    "ui/src/stores/scratch-store.ts",
    "ui/src/stores/shortcut-store.ts",
    "ui/src/config/shortcuts.ts",
    "ui/src/config/routes.tsx",
    "ui/src/layouts/*.tsx",
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

#: 登录态 / 鉴权关键字（v1.6.1 起**不再**整表禁扫：准入要读登录态，
#: 改为判「只准经 `editor-gate.ts` 的单一真相读」，见 ② 节；
#: 这里只留**凭据本身** `CREDENTIAL_TOKENS`，`session_key` / `Authorization` / `jwt` 仍零容忍）

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


def _code_text(path: Path) -> str:
    """取某文件的**代码文本**（注释与三引号长串已抹掉，规则见 `tests/code_scan.py`）。

    ⚠ 必须用它、不能直接 `path.read_text()`：本仓的说明文字大量原样引用标识符，
    对原文做 `in` / `index` 判断会被**注释里的字面量**骗过。实测反例（Phase 6 判别力自证）：
    `ui/src/layouts/DesktopLayout.tsx:35` 的注释写着「可见性由 `useEditorGate()` 决定」，
    把 `:72` 的 `useEditorGate()` 换成自己读 `isLoggedIn` 后，原文检索仍能命中
    ⇒ 判据**假绿**。改用去注释文本后该注入立刻变红。
    """
    return "\n".join(stripped for _number, stripped, _original in code_lines(path))


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


# ── ② 静态：只经「准入单一真相」，且整条链路零凭据 ──────────────────

#: 凭据关键字（渲染层严禁持有：AGENTS.md §9.6「文件字节与密钥严禁进入渲染层」）
#: v1.6.1 修正：`isLoggedIn` / `authService` 从本表移除 —— 准入**确实**要读登录态，
#: 改由下面 `TestChainUsesLoginGateWithoutCredentials` 判「只准经单一真相读」。
CREDENTIAL_TOKENS: tuple[str, ...] = (
    "session_key",
    "Authorization",
    "Bearer",
    "jwt",
    "authService",
)

#: 准入单一真相（设计 v1.6.1 §4.1 / DS-16）
GATE_SOURCE = DESKTOP_ROOT / "ui" / "src" / "utils" / "scratch" / "editor-gate.ts"

#: 允许读登录态的渲染层文件 = 单一真相 + **消费点**
#: （消费点必须经 `canUseEditor` / `useEditorGate`，不得自己重写判定；内容区组件一律不许读登录态）
#: 注意：`ui/src/config/routes.tsx` **不在此列** —— 它是**守卫层**，见 `GATE_GUARD_EXEMPT`（M-3 修正）。
GATE_CONSUMER_ALLOWLIST: tuple[str, ...] = (
    "ui/src/utils/scratch/editor-gate.ts",
    "ui/src/utils/scratch/editor-open-paths.ts",
    "ui/src/utils/scratch/shortcut-runtime.ts",
    "ui/src/layouts/DesktopLayout.tsx",
    "ui/src/components/scratch/ScratchWorkspace.tsx",
)

#: **守卫层豁免**（不是消费点）—— 审查 M-3 的修法：把"豁免"与"消费"拆成两张清单，各自有各自判据。
#:
#: 豁免理由（写给下一位维护者，避免又被当成"漏了 canUseEditor"）：
#:   * `ui/src/config/routes.tsx` 的 `RequireAuth` 是 **v1.6.0 以来的既有实现**，
#:     它只读 `isLoggedIn`（外加 `isTokenExpired()`）来决定"把未登录的人送去 `/login`"；
#:   * 这一层的覆盖面**比编辑器更宽**（所有登录后页面共用一个守卫）：把"有没有会话"改写成
#:     编辑器专属的 `canUseEditor`（"编辑器能不能用"）是**层次倒置**，两句话只是恰好在 `/editor` 上同向；
#:   * 设计 §4.1（`文档记录/设计文档/v1.6.1.md:423`）的第 ③ 处消费点原文是
#:     "`/editor` 路由守卫（**`RequireAuth` 已有** + 工作区内部再判一次）"
#:     ⇒ 守卫层属**复用既有实现**，新增的消费点是工作区内部的 `ScratchWorkspace`（已在上面那张清单里）。
#:
#: 因此本清单里的文件：① 必须在 `_iter_chain()` 的扫描面内（否则"豁免"只是注释里的空话 = M-3 老问题）；
#: ② 照常受"零网络 / 零凭据"约束；③ **不**要求出现 `canUseEditor(` / `useEditorGate(`。
GATE_GUARD_EXEMPT: tuple[str, ...] = ("ui/src/config/routes.tsx",)

#: AC-63 ① 的导航项实现文件（侧栏「文本编辑器」入口的可见性来源）—— 必须真的经准入单一真相。
#: 只加 glob 还不够：该文件**本来就不含 `isLoggedIn`**，一旦有人把 `useEditorGate()` 删成
#: "无条件渲染"，通用规则会因"没有 isLoggedIn"而跳过它 ⇒ 这里补一条定点判据。
NAV_GATE_CONSUMER = DESKTOP_ROOT / "ui" / "src" / "layouts" / "DesktopLayout.tsx"


class TestChainUsesLoginGateWithoutCredentials:
    """v1.6.1（AC-63 新增 / AC-47 反转）的静态半边：

      * 准入**依赖**登录态，但只准经单一真相 `editor-gate.ts`（读登录态 ≠ 拿凭据）；
      * 链路仍然零凭据 —— 不读 `session_key` / `Authorization` / `Bearer` / `jwt`，
        也不经 `authService`（凭据只活在 Python 侧与 `stores/auth-store` 的布尔快照里）。

    审查 **M-3** 修正（本类内新增 3 条判据，**不是**削弱）：
      * `TS_CHAIN_GLOBS` 补入 `ui/src/layouts/*.tsx` 与 `ui/src/config/routes.tsx`
        ⇒ AC-63 ① 的导航项真实落点回到扫描面内；
      * `ui/src/config/routes.tsx` 从"消费点"改为 `GATE_GUARD_EXEMPT`（守卫层豁免，理由见该常量注释）
        ⇒ 修掉"白名单把守卫层当消费点"的自相矛盾（原来一旦纳入扫描面就必然失败）；
      * 对 `DesktopLayout.tsx` 增加**定点**判据（该文件不含 `isLoggedIn`，通用规则会跳过它）。
    """

    def test_链路零命中凭据关键字(self):
        hits = _scan(CREDENTIAL_TOKENS)
        assert hits == [], "编辑链路出现凭据关键字：\n" + "\n".join(hits)

    def test_准入判定只有单一真相(self):
        assert GATE_SOURCE.is_file(), "准入单一真身 editor-gate.ts 缺失"
        gate_text = _code_text(GATE_SOURCE)
        assert "export function canUseEditor(" in gate_text, "canUseEditor 必须导出（四处消费）"
        assert "export function useEditorGate(" in gate_text, "useEditorGate 必须导出（React 订阅版）"
        offenders: list[str] = []
        for path in _iter_chain():
            relative = str(path.relative_to(DESKTOP_ROOT)).replace("\\", "/")
            text = _code_text(path)  # 只看代码：注释里的 `isLoggedIn` / `useEditorGate()` 不算
            if "isLoggedIn" not in text:
                continue
            # 守卫层：豁免"必须调 canUseEditor"（理由见 GATE_GUARD_EXEMPT 注释），
            # 但仍受本模块其它判据（零网络 / 零凭据 / 在扫描面内）约束。
            if relative in GATE_GUARD_EXEMPT:
                continue
            if relative not in GATE_CONSUMER_ALLOWLIST:
                offenders.append(f"{relative}：读了登录态，但不在准入面清单内（应经 useEditorGate）")
                continue
            if relative != "ui/src/utils/scratch/editor-gate.ts" and not (
                "canUseEditor(" in text or "useEditorGate(" in text
            ):
                offenders.append(f"{relative}：读了登录态却没经 canUseEditor/useEditorGate")
        assert offenders == [], "准入判定被绕开：\n" + "\n".join(offenders)

    def test_准入清单的每一项都真的在扫描面内(self):
        """审查 M-3 的根因守门：**白名单里的文件必须真的会被扫到**。

        此前 `ui/src/layouts/DesktopLayout.tsx` 与 `ui/src/config/routes.tsx` 都写在白名单里、
        却不在 `TS_CHAIN_GLOBS` 覆盖范围内 ⇒ 它们对上面的用例**毫无约束力**（死条目）。
        这条判据让"glob 又被改窄"这类回归立刻变红，而不是静默退化。
        """
        scanned = {
            str(path.relative_to(DESKTOP_ROOT)).replace("\\", "/") for path in _iter_chain()
        }
        dead = [
            relative
            for relative in (*GATE_CONSUMER_ALLOWLIST, *GATE_GUARD_EXEMPT)
            if relative not in scanned
        ]
        assert dead == [], f"准入清单里存在未被扫描的死条目（M-3 同型问题）：{dead}"

    def test_导航项渲染经准入单一真相(self):
        """AC-63 ①（审查 M-3 点名的真实落点）：侧栏「文本编辑器」入口的可见性。

        `DesktopLayout.tsx` 本来不含 `isLoggedIn`，所以 `test_准入判定只有单一真相` 的通用规则
        会**跳过**它 —— 若有人把 `useEditorGate()` 删掉改成"无条件渲染"，通用规则不会红。
        因此这里对导航项做定点判据：`requiresEditor` 标记仍在，且可见性来自准入单一真相。
        """
        assert NAV_GATE_CONSUMER.is_file(), "导航项实现文件缺失：ui/src/layouts/DesktopLayout.tsx"
        text = _code_text(NAV_GATE_CONSUMER)
        assert "requiresEditor" in text, "导航项必须保留 requiresEditor 标记（否则编辑器入口无法被门控）"
        assert "useEditorGate(" in text or "canUseEditor(" in text, (
            "AC-63 ①：侧栏编辑器入口的可见性必须来自准入单一真相（useEditorGate / canUseEditor），"
            "不得无条件渲染、也不得自己拼条件"
        )

    def test_守卫层豁免只读登录态且仍是既有实现(self):
        """`ui/src/config/routes.tsx` 是**守卫层豁免**，不是消费点（审查 M-3 的第二半）。

        判据：它仍在链路上、仍是那个 `RequireAuth` 既有实现、且**没有**被误改成编辑器专属判据
        （一旦有人给它加 `canUseEditor`，说明层次被倒置，应由本用例提醒回到设计 §4.1 的口径）。
        """
        routes = DESKTOP_ROOT / "ui" / "src" / "config" / "routes.tsx"
        assert routes.is_file(), "守卫层文件缺失：ui/src/config/routes.tsx"
        text = _code_text(routes)
        assert "function RequireAuth(" in text, "守卫层必须是既有的 RequireAuth 实现"
        assert "isLoggedIn" in text, "RequireAuth 的判据是 isLoggedIn（会话有无），不是编辑器可用性"
        assert "canUseEditor(" not in text and "useEditorGate(" not in text, (
            "守卫层不得改用编辑器专属判据（层次倒置）；工作区内部的自判由 ScratchWorkspace 承担"
        )

    def test_快捷键运行时把准入放在接管之前(self):
        """AC-63 ②：未登录时**不响应也不报错** —— 准入必须早于 `preventDefault()`，
        否则会把用户的按键吞掉（设计 §4.7 三道前置，顺序不可换）。

        取**去注释文本**：该文件顶部的说明块里同时提到 `canUseEditor()` 与 `preventDefault()`，
        用原文比较会把注释的先后当成代码的先后（假绿）。
        """
        text = _code_text(
            DESKTOP_ROOT / "ui" / "src" / "utils" / "scratch" / "shortcut-runtime.ts"
        )
        assert "canUseEditor(" in text, "快捷键运行时必须自判准入"
        assert "preventDefault()" in text, "命中后必须接管（否则宿主默认动作与 CM6 双触发）"
        assert text.index("canUseEditor(") < text.index("preventDefault()"), (
            "准入判定晚于 preventDefault ⇒ 未登录也会吞键，违反 AC-63 ②"
        )

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
