"""
v1.6.1 · F6「IDEA 复制的代码贴不进来」**外壳侧修复**的可失败单测。

## 被测对象（`src/ui/web_shell.py`）

- `normalize_clipboard_for_paste(clipboard)`：坏形态剪贴板 ⇒ 重写成 `CF_HTML` + `CF_UNICODETEXT`；
- `build_paste_safe_mime_data(mime)`：重建的实际动作（只留 html + text）；
- `PasteClipboardNormalizer`：事件过滤器（`Ctrl+V` 前置归一化，**放行**默认粘贴）。
  过滤器**只挂渲染 widget**（`QWebEngineView.focusProxy()`），**绝不挂 `QApplication`**
  —— 应用级 Python 过滤器 + QtWebEngine 在 Linux/X11 上会在
  `PySide::getWrapperForQObject` 段错误（Fedora 启动即崩溃的根因）。

## 真凶与判据（r18 真机单变量对照矩阵，见 `poc/editor-probes/r17-clipboard-cfhtml.py`）

只要剪贴板上有 `UniformResourceLocator`（`CFSTR_INETURL`，Qt 侧 `text/uri-list`），
QtWebEngine 的 `Ctrl+V` 就贴出 **0 字符**；把它剔掉、只留 `CF_HTML` + `CF_UNICODETEXT`
就能贴出来（**已实测**）。因此本文件的判据逐条对应"外科手术"要求：

| 用例 | 判据 |
| --- | --- |
| 含 `text/uri-list` + 文本/HTML | 归一化后**不再含**该格式，且文本与 HTML **仍在** |
| 不含该格式 | **完全不动**剪贴板（写入次数 = 0、`dataChanged` 次数 = 0、Win32 序列号不变） |
| 空剪贴板 / 无剪贴板 | 不动、**不抛** |
| 只有 `text/uri-list`（资源管理器"复制文件"） | **不动** —— 不得把"文件复制"改写成一段文本 |
| **含 `CF_HDROP` 的混合载荷**（**L-11**） | **不动**（理由 `has-file-list`）—— 重建式归一化会连**文件列表**一起丢掉 |
| 只有 `hasUrls()` 为真（无 `CF_HDROP`）| **照常归一化** —— 否则会误伤 AWT 载荷（它同带 `text/uri-list`） |
| 幂等 | 连续两次 == 一次 |
| 重写失败 | **静默降级**（不抛、不改变默认粘贴行为） |
| 事件过滤器 | **永不吞键**（恒 `return False`）；只对"真会发生粘贴的 `Ctrl+V`"动作 |
| 事件过滤器 | 窗口**非活动**时不动剪贴板 |
| 递归 | 重写引发的 `dataChanged` 回环不得再写一次 |
| 接线回归 | 真 `WebShell` 把过滤器装到**渲染 widget**（`view.focusProxy()`）上，且**真的收到**按键事件 |
| 接线回归 | 中立 `QWidget` 收到 `Ctrl+V` **不得**触发 —— 守住"不许再挂 `QApplication`"（Linux 崩溃根因） |
| 接线回归 | 平台关闭（非 Win32）时**不安装**任何过滤器；换目标时**先摘旧**再挂新 |

安全：本文件**不打印任何剪贴板正文**（只用自造短串做长度/布尔断言）；不联网、不读用户文件。
用例全部走**注入的假剪贴板**，只有一条"序列号零写入"用例碰真剪贴板，且**只在它本来就不含
`text/uri-list` 时**执行（否则 skip）——绝不为了测试去改用户剪贴板。
"""
from __future__ import annotations

import ctypes
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QMimeData, QObject, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QWidget

from src.ui import web_shell
from src.ui.web_shell import (
    CLIPBOARD_HDROP_FORMAT,
    CLIPBOARD_URI_LIST_FORMAT,
    PasteClipboardNormalizer,
    WebShell,
    build_paste_safe_mime_data,
    normalize_clipboard_for_paste,
)

HTML_SAMPLE = "<HTML><BODY>第一行 &amp; 第二行</BODY></HTML>"
TEXT_SAMPLE = "第一行 & 第二行\n"


# ── 测试替身 ───────────────────────────────────────────────────


def make_mime(*, html: str | None, text: str | None, uri: str | None) -> QMimeData:
    """造一份"当前剪贴板"的 `QMimeData`（按需带 `text/uri-list`）"""
    mime = QMimeData()
    if html is not None:
        mime.setHtml(html)
    if text is not None:
        mime.setText(text)
    if uri is not None:
        mime.setData(CLIPBOARD_URI_LIST_FORMAT, uri.encode("utf-8"))
    return mime


class FakeClipboard:
    """
    假剪贴板：`setMimeData` **写入次数**与 **`dataChanged` 次数**就是"零写入"的判据
    （等价于 Windows 剪贴板序列号：任何写入都会 +1）。

    `on_change` 用来模拟"有人连了 `dataChanged` 并在里面回环调用归一化"的场景。
    """

    def __init__(self, mime: QMimeData | None, *, fail_write: bool = False) -> None:
        self._mime = mime
        self.fail_write = fail_write
        self.writes: list[QMimeData] = []
        self.data_changed_count = 0
        self.on_change = None

    def mimeData(self) -> QMimeData | None:
        return self._mime

    def setMimeData(self, mime: QMimeData) -> None:
        if self.fail_write:
            raise RuntimeError("模拟剪贴板写入失败")
        self.writes.append(mime)
        self._mime = mime  # 先落盘再发信号（与 Qt 的语义一致）
        self.data_changed_count += 1
        if self.on_change is not None:
            self.on_change()


class FakeWindow(QObject):
    """只提供 `isActiveWindow()` 的假窗口（过滤器只需要这一个能力）"""

    def __init__(self, active: bool = True) -> None:
        super().__init__()
        self._active = active

    def isActiveWindow(self) -> bool:  # noqa: N802 - 与被模拟的 Qt API 同名
        return self._active


class FakeAppWithClipboard:
    """替换 `web_shell.QGuiApplication`（**模块属性**，不是 PySide6 类属性，避免析构崩溃）"""

    def __init__(self, clipboard) -> None:
        self._clipboard = clipboard

    def clipboard(self):
        return self._clipboard


def bad_clipboard(uri: str = "file:///D:/x.txt") -> FakeClipboard:
    """造一个"坏形态"假剪贴板：真实 AWT 载荷 = html + text + `text/uri-list`"""
    return FakeClipboard(make_mime(html=HTML_SAMPLE, text=TEXT_SAMPLE, uri=uri))


def win32_clipboard_sequence() -> int | None:
    """Win32 剪贴板序列号（非 Windows 返回 None）；任何写入都会让它 +1"""
    if sys.platform != "win32":
        return None
    try:
        return int(ctypes.WinDLL("user32").GetClipboardSequenceNumber())
    except Exception:  # noqa: BLE001 - 拿不到就不判（该用例会 skip）
        return None


def key_press(key: Qt.Key, mods: Qt.KeyboardModifier, *, auto_repeat: bool = False) -> QKeyEvent:
    """造一个按键事件（`auto_repeat` 用于验证"长按重复不算新的粘贴意图"）"""
    return QKeyEvent(
        QEvent.Type.KeyPress, key, mods, "", bool(auto_repeat), 1
    )


# ── 归一化本体 ─────────────────────────────────────────────────


class TestNormalizeClipboard:
    """`normalize_clipboard_for_paste()` 的四条硬判据"""

    def test_含uri_list时重写并剔除该格式且html与文本仍在(self, qapp) -> None:
        """判据①：含 `text/uri-list` + 文本 + HTML ⇒ 归一化后不再含该格式，文本与 HTML 仍在"""
        clipboard = bad_clipboard()
        assert clipboard.mimeData().hasFormat(CLIPBOARD_URI_LIST_FORMAT) is True  # 前置

        result = normalize_clipboard_for_paste(clipboard)

        assert result["action"] == "rewritten"
        assert result["reason"] == "uri-list-removed"
        assert len(clipboard.writes) == 1  # 恰好写入一次
        written = clipboard.writes[0]
        assert written.hasFormat(CLIPBOARD_URI_LIST_FORMAT) is False  # 真凶格式已剔除
        assert written.hasHtml() is True
        assert written.html() == HTML_SAMPLE  # HTML 逐字符保留
        assert written.text() == TEXT_SAMPLE  # 文本逐字符保留
        assert result["hasHtml"] is True
        assert result["textChars"] == len(TEXT_SAMPLE)

    def test_只有纯文本加uri_list时也重写(self, qapp) -> None:
        """真机矩阵里"真实 AWT 只写纯文本 ⇒ 0 字符"这一格：没有 HTML 也必须归一化"""
        mime = make_mime(html=None, text=TEXT_SAMPLE, uri="https://example.invalid/")
        assert mime.hasHtml() is False and mime.hasFormat("text/plain") is True  # 前置
        clipboard = FakeClipboard(mime)
        result = normalize_clipboard_for_paste(clipboard)

        assert result["action"] == "rewritten"
        written = clipboard.writes[0]
        assert written.hasFormat(CLIPBOARD_URI_LIST_FORMAT) is False
        assert written.text() == TEXT_SAMPLE
        assert written.hasHtml() is False

    def test_不含uri_list时完全不动剪贴板(self, qapp) -> None:
        """判据②：**正常文本复制粘贴**绝不触碰系统剪贴板（写入 0 次、`dataChanged` 0 次）"""
        clipboard = FakeClipboard(make_mime(html=HTML_SAMPLE, text=TEXT_SAMPLE, uri=None))

        result = normalize_clipboard_for_paste(clipboard)

        assert result["action"] == "skipped"
        assert result["reason"] == "no-uri-list"
        assert clipboard.writes == []  # 零写入
        assert clipboard.data_changed_count == 0  # 序列号口径：一次都没变

    def test_真实剪贴板不含uri_list时序列号不变(self, qapp) -> None:
        """
        零写入的**真机口径**复核：Win32 `GetClipboardSequenceNumber` 在调用前后必须相同。

        ⚠ 该用例**不写**任何剪贴板内容：若当前真实剪贴板恰好含 `text/uri-list`，直接 skip，
        绝不为了测试去改写用户剪贴板。
        """
        seq_before = win32_clipboard_sequence()
        if seq_before is None:
            pytest.skip("非 Windows / 拿不到剪贴板序列号")
        from PySide6.QtGui import QGuiApplication

        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is not None and mime.hasFormat(CLIPBOARD_URI_LIST_FORMAT):
            pytest.skip("当前真实剪贴板含 text/uri-list（避免测试破坏用户剪贴板）")

        result = normalize_clipboard_for_paste(clipboard)

        assert result["action"] == "skipped"
        assert win32_clipboard_sequence() == seq_before  # 序列号不变 ⇒ 确实一个字节都没写

    def test_空剪贴板不动且不抛(self, qapp) -> None:
        """判据③：空剪贴板 ⇒ 不动、不抛（没有任何可粘贴内容时**不得**重写，否则等于清空剪贴板）"""
        empty = FakeClipboard(QMimeData())
        result = normalize_clipboard_for_paste(empty)

        assert result["action"] == "skipped"
        assert result["reason"] == "no-uri-list"
        assert empty.writes == []

    def test_只有uri_list时不动_资源管理器复制文件场景(self, qapp) -> None:
        """
        边界（**安全红线**）：剪贴板上只有 `text/uri-list` ⇒ 不重写。

        这一形态就是资源管理器"复制文件"（Win32 `CF_HDROP`）：本机实测 Qt 眼里
        **只有** `text/uri-list`（`hasHtml()=False`、`hasFormat("text/plain")=False`），
        但 `QMimeData.text()` 会**回落到** `text/uri-list` 回一段 `file:///…`。
        若照 `text()` 重写，就会把用户的"文件复制"变成一段纯文本。
        """
        mime = make_mime(html=None, text=None, uri="file:///D:/README.md")
        # 前置自证：这个陷阱是真的 —— `text()` 非空，但**没有** `text/plain` 格式
        assert mime.hasFormat("text/plain") is False
        assert mime.text() == "file:///D:/README.md"
        clipboard = FakeClipboard(mime)

        result = normalize_clipboard_for_paste(clipboard)

        assert result["action"] == "skipped"
        assert result["reason"] == "no-payload"
        assert clipboard.writes == []  # 文件复制必须原样保留

    def test_有uri_list但html与text都为空时不动(self, qapp) -> None:
        """边界：两个真实文本格式都在、但内容都是空串 ⇒ 不重写（重写等于把剪贴板清空）"""
        clipboard = FakeClipboard(make_mime(html="", text="", uri="file:///D:/x.txt"))
        result = normalize_clipboard_for_paste(clipboard)

        assert result["action"] == "skipped"
        assert result["reason"] == "no-payload"
        assert clipboard.writes == []

    def test_CF_HDROP与文本混合载荷时不动_不得丢掉文件列表(self, qapp) -> None:
        """
        **L-11**（审查报告 v1.6.1 §15.4）：含 `CF_HDROP`（复制文件）**且**含文本 + uri-list
        的混合载荷 ⇒ 归一化必须**直接跳过**。

        判据来源：设计 §15.6.7 的安全边界 #3 字面是"**只**做『删掉会打空粘贴的那一项格式』"，
        而本实现是"**重建成**只有 `CF_HTML` + `CF_UNICODETEXT`" —— 后者在混合载荷上会把
        **文件列表一起丢掉**，把"复制文件"降级成纯文本（用户裁决接受的副作用**只**限于 URL 格式）。

        零写入三口径：写入次数 0 / `dataChanged` 0 /（Windows 上另有真机序列号口径用例）。
        """
        mime = make_mime(html=HTML_SAMPLE, text=TEXT_SAMPLE, uri="file:///D:/x.txt")
        # Qt 在 Windows 上把原生 `CF_HDROP` 暴露成这个格式名
        mime.setData(CLIPBOARD_HDROP_FORMAT, b"CF_HDROP-FAKE")
        # 前置自证：三个"会被重建吃掉"的格式**同时在场**（否则下面的断言没有判别力）
        assert mime.hasFormat(CLIPBOARD_HDROP_FORMAT) is True
        assert mime.hasFormat(CLIPBOARD_URI_LIST_FORMAT) is True
        assert mime.hasFormat("text/plain") is True
        clipboard = FakeClipboard(mime)

        result = normalize_clipboard_for_paste(clipboard)

        assert result["action"] == "skipped"
        assert result["reason"] == "has-file-list"
        assert clipboard.writes == []  # 剪贴板未被改写 ⇒ 文件列表完好
        assert clipboard.data_changed_count == 0
        assert clipboard.mimeData() is mime  # 连对象都没被替换

    def test_hasUrls为真但无CF_HDROP格式时仍须归一化_防误伤AWT载荷(self, qapp) -> None:
        """
        **L-11 守卫的"不许扩大"半边**（本批实测踩坑，必须留一条守门用例）：

        Qt 的 `hasUrls()` 只要**有 `text/uri-list` 就为真**（本机实测：连
        `https://example.invalid/` 这种纯 URL 字符串也是 `True`），而**真实 AWT 载荷**
        （IDEA 复制文本，恰恰是 F6 的修复对象）**也带** `text/uri-list`。
        ⇒ 若把 `hasUrls()` 直接当"含文件列表"的判据，**F6 的修复会被整体跳过**
        （本批实测：那样写会让本文件的 9 条既有用例全红）。

        因此这条用例锁死：`hasUrls()` 为真、但**没有** `CF_HDROP` 原生格式时应**照常归一化**。
        """
        mime = make_mime(html=HTML_SAMPLE, text=TEXT_SAMPLE, uri="file:///D:/x.txt")
        # 前置自证：本环境下"有 uri-list ⇒ hasUrls() 为真"这一陷阱**确实成立**
        assert mime.hasUrls() is True
        assert mime.hasFormat(CLIPBOARD_HDROP_FORMAT) is False
        clipboard = FakeClipboard(mime)

        result = normalize_clipboard_for_paste(clipboard)

        assert result["action"] == "rewritten"  # AWT 载荷必须照旧被修复（AC-78 不被破坏）
        assert result["reason"] == "uri-list-removed"
        written = clipboard.writes[0]
        assert written.hasFormat(CLIPBOARD_URI_LIST_FORMAT) is False
        assert written.text() == TEXT_SAMPLE

    def test_判定文件列表失败时按有处理_宁可不动(self, qapp) -> None:
        """守卫的失败方向必须是"不动"（数据安全优先）：格式判定抛错 ⇒ 跳过归一化"""

        class ExplodingFormats(QMimeData):
            def hasFormat(self, mime_type: str) -> bool:  # type: ignore[override]
                if mime_type == CLIPBOARD_HDROP_FORMAT:
                    raise RuntimeError("boom")
                return super().hasFormat(mime_type)

        mime = ExplodingFormats()
        mime.setHtml(HTML_SAMPLE)
        mime.setText(TEXT_SAMPLE)
        mime.setData(CLIPBOARD_URI_LIST_FORMAT, b"file:///D:/x.txt")
        clipboard = FakeClipboard(mime)

        result = normalize_clipboard_for_paste(clipboard)

        assert result["action"] == "skipped"
        assert result["reason"] == "has-file-list"
        assert clipboard.writes == []

    def test_无剪贴板对象时不动且不抛(self, qapp) -> None:
        """边界：拿不到 `QClipboard`（顶层窗口销毁期等）⇒ 静默跳过"""
        result = normalize_clipboard_for_paste(None)

        assert result["action"] == "skipped"
        assert result["reason"] == "no-clipboard"

    def test_幂等_连续两次等于一次(self, qapp) -> None:
        """判据④：幂等 —— 第二次调用必须**看不到** `text/uri-list` 而直接跳过"""
        clipboard = bad_clipboard()

        first = normalize_clipboard_for_paste(clipboard)
        second = normalize_clipboard_for_paste(clipboard)

        assert first["action"] == "rewritten"
        assert second["action"] == "skipped"
        assert second["reason"] == "no-uri-list"
        assert len(clipboard.writes) == 1  # 两次调用 = 一次写入
        assert clipboard.data_changed_count == 1

    def test_重写不得引发递归回环(self, qapp) -> None:
        """
        重写会触发 `dataChanged`；即使有人在回调里**再调一次**归一化，也只能写一次
        （本实现的递归安全来自"重写后不再含 `text/uri-list`"这一不变式）。
        """
        clipboard = bad_clipboard()
        reentrant: list[dict] = []

        def nested() -> None:
            if not reentrant:  # 只回环一层，避免测试自身无限递归
                reentrant.append(normalize_clipboard_for_paste(clipboard))

        clipboard.on_change = nested
        result = normalize_clipboard_for_paste(clipboard)

        assert result["action"] == "rewritten"
        assert len(reentrant) == 1
        assert reentrant[0]["action"] == "skipped"
        assert len(clipboard.writes) == 1  # 回环没有产生第二次写入

    def test_写入失败时静默降级(self, qapp) -> None:
        """边界：`setMimeData` 抛异常 ⇒ 只回报 failed，**不抛**、不改默认粘贴行为"""
        clipboard = FakeClipboard(
            make_mime(html=HTML_SAMPLE, text=TEXT_SAMPLE, uri="file:///D:/x.txt"), fail_write=True
        )

        result = normalize_clipboard_for_paste(clipboard)

        assert result["action"] == "failed"
        assert result["reason"] == "write-failed"
        assert clipboard.writes == []

    def test_读取内容失败时静默降级(self, qapp) -> None:
        """边界：`html()/text()` 抛异常 ⇒ 静默跳过（宁可粘贴失败，也不能把剪贴板写坏）"""

        class ExplodingMime(QMimeData):
            def html(self):  # type: ignore[override]
                raise RuntimeError("boom")

        mime = ExplodingMime()
        mime.setData(CLIPBOARD_URI_LIST_FORMAT, b"file:///D:/x.txt")
        clipboard = FakeClipboard(mime)

        result = normalize_clipboard_for_paste(clipboard)

        assert result["action"] == "skipped"
        assert result["reason"] == "no-payload"
        assert clipboard.writes == []

    def test_build_paste_safe_mime_data_只保留html与text(self, qapp) -> None:
        """`build_paste_safe_mime_data()` 是外科手术本体：输出**只有** html + text 两个格式"""
        fresh = build_paste_safe_mime_data(
            make_mime(html=HTML_SAMPLE, text=TEXT_SAMPLE, uri="file:///D:/x.txt")
        )
        assert fresh is not None
        assert sorted(fresh.formats()) == ["text/html", "text/plain"]
        assert build_paste_safe_mime_data(QMimeData()) is None


# ── 事件过滤器（挂钩点行为） ────────────────────────────────────


class TestPasteClipboardNormalizer:
    """挂钩点必须"只在该动的时候动、且永不吞键" """

    def test_永不吞键(self, qapp, monkeypatch) -> None:
        """判据：`eventFilter` 恒返回 `False` —— 默认粘贴必须照常发生（不自己插文本）"""
        clipboard = bad_clipboard()
        monkeypatch.setattr(web_shell, "QGuiApplication", FakeAppWithClipboard(clipboard))
        normalizer = PasteClipboardNormalizer(FakeWindow(active=True))

        handled = normalizer.eventFilter(
            None, key_press(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        )

        assert handled is False  # 永不吞键
        assert len(clipboard.writes) == 1  # 但确实做了归一化
        assert normalizer.rewrite_count == 1
        assert normalizer.paste_key_count == 1

    @pytest.mark.parametrize(
        "event",
        [
            pytest.param(
                key_press(Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier), id="ctrl+c"
            ),
            pytest.param(key_press(Qt.Key.Key_V, Qt.KeyboardModifier.NoModifier), id="bare-v"),
            pytest.param(
                key_press(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier, auto_repeat=True),
                id="auto-repeat",
            ),
            pytest.param(QEvent(QEvent.Type.KeyRelease), id="key-release"),
            pytest.param(QEvent(QEvent.Type.MouseButtonPress), id="mouse"),
        ],
    )
    def test_非粘贴按键不触发归一化(self, qapp, monkeypatch, event) -> None:
        """判据：只有"真会发生粘贴的 `Ctrl+V`"才算触发（`Ctrl+C` / 自动重复 / 其它事件都不算）"""
        clipboard = bad_clipboard()
        monkeypatch.setattr(web_shell, "QGuiApplication", FakeAppWithClipboard(clipboard))
        normalizer = PasteClipboardNormalizer(FakeWindow(active=True))

        assert normalizer.eventFilter(None, event) is False
        assert normalizer.paste_key_count == 0
        assert clipboard.writes == []

    def test_窗口非活动时绝不动剪贴板(self, qapp, monkeypatch) -> None:
        """判据：**本应用窗口不在活动态**时，剪贴板一个字节都不许动"""
        clipboard = bad_clipboard()
        monkeypatch.setattr(web_shell, "QGuiApplication", FakeAppWithClipboard(clipboard))
        normalizer = PasteClipboardNormalizer(FakeWindow(active=False))

        assert normalizer.eventFilter(
            None, key_press(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        ) is False

        assert normalizer.paste_key_count == 1  # 按键确实到达过（证明过滤器被调用）
        assert clipboard.writes == []  # 但剪贴板没被动
        assert normalizer.rewrite_count == 0

    def test_平台未启用时不动剪贴板(self, qapp, monkeypatch) -> None:
        """判据：非 Win32 平台上一律不动（真凶 `CFSTR_INETURL` 是 Win32 专有格式）"""
        clipboard = bad_clipboard()
        monkeypatch.setattr(web_shell, "QGuiApplication", FakeAppWithClipboard(clipboard))
        monkeypatch.setattr(web_shell, "CLIPBOARD_NORMALIZE_ENABLED", False)
        normalizer = PasteClipboardNormalizer(FakeWindow(active=True))

        assert normalizer.eventFilter(
            None, key_press(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        ) is False
        assert clipboard.writes == []
        assert normalizer.paste_key_count == 0

    def test_过滤器内部异常不得外泄(self, qapp, monkeypatch) -> None:
        """判据：过滤器抛异常会波及整个事件分发 ⇒ 必须自己吞掉并放行"""

        class ExplodingClipboard(FakeClipboard):
            def mimeData(self):
                raise RuntimeError("boom")

        monkeypatch.setattr(
            web_shell, "QGuiApplication", FakeAppWithClipboard(ExplodingClipboard(None))
        )
        normalizer = PasteClipboardNormalizer(FakeWindow(active=True))

        assert normalizer.eventFilter(
            None, key_press(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        ) is False

    def test_标准键判定与_Qt_一致(self, qapp) -> None:
        """前置自证：`Ctrl+V` 确实 `matches(Paste)`，否则上面几条用例会"恒绿"而无判别力"""
        assert key_press(
            Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier
        ).matches(QKeySequence.StandardKey.Paste) is True
        assert key_press(
            Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier
        ).matches(QKeySequence.StandardKey.Paste) is False


# ── 接线回归：过滤器只挂渲染 widget（绝不挂 QApplication） ────────


@pytest.fixture(scope="module")
def shell(qapp):
    """真 `WebShell`（Chromium 视图构造较重 ⇒ 模块级复用）"""
    patches = pytest.MonkeyPatch()
    # 用例要在任何平台验证"挂载点"本身（真机只在 Win32 启用）⇒ 在测试内放开平台门禁。
    # 真剪贴板不会被碰：`PasteClipboardNormalizer._on_event` 里的 `QGuiApplication`
    # 在下面各用例中已被换成假剪贴板（见 `test_渲染widget能收到按键事件`）。
    patches.setattr(web_shell, "CLIPBOARD_NORMALIZE_ENABLED", True)
    instance = WebShell(objects={}, frameless=True, publish_event=None)
    # 渲染 widget（focusProxy）在首次 show 时才创建 ⇒ 必须先 show 才有挂载点
    instance.show()
    qapp.processEvents()
    instance._ensure_paste_filter()
    yield instance
    instance.hide()
    patches.undo()


class TestWiring:
    """挂载点回归守门：只挂渲染 widget（BL-51「配了没反应」+ Fedora 启动崩溃双守卫）"""

    def test_中立控件收不到过滤(self, shell, qapp) -> None:
        """
        判据：往一个中立 `QWidget` 投 `Ctrl+V` ⇒ 计数**不变**。

        这条守的是崩溃根因：过滤器一旦挂到 `QApplication`，任何对象的按键都会被计数；
        而 Linux/X11 上应用级 Python 过滤器会让 PySide6 在 `getWrapperForQObject` 段错误。
        """
        before = shell.paste_normalizer.paste_key_count
        target = QWidget()
        QApplication.sendEvent(
            target, key_press(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        )
        target.deleteLater()

        assert shell.paste_normalizer.paste_key_count == before
        assert shell._paste_filter_target is not None

    def test_渲染widget能收到按键事件(self, shell, qapp, monkeypatch) -> None:
        """
        判据：往渲染 widget（`view.focusProxy()`，按键真正的落点）投 `Ctrl+V` ⇒ 计数 +1。

        与上一条合起来：**"该触发的触发、不该触发的绝不触发"**。
        `QGuiApplication` 换成假剪贴板，避免真机（窗口恰好是活动窗口时）改写系统剪贴板。
        """
        monkeypatch.setattr(
            web_shell, "QGuiApplication", FakeAppWithClipboard(bad_clipboard())
        )
        proxy = shell.view.focusProxy()
        assert proxy is not None, "focusProxy() 为空 ⇒ 无法验证挂载点"
        before = shell.paste_normalizer.paste_key_count
        QApplication.sendEvent(
            proxy, key_press(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        )

        assert shell.paste_normalizer.paste_key_count == before + 1
        assert shell._paste_filter_target is proxy

    def test_过滤器绝不吞键(self, shell, qapp) -> None:
        """判据：真外壳的过滤器同样恒返回 `False`（默认粘贴不被阻断）"""
        assert shell.paste_normalizer.eventFilter(
            None, key_press(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        ) is False


class TestEnsurePasteFilter:
    """`_ensure_paste_filter()` 的平台门禁与幂等（纯逻辑，不需要 Chromium 视图）"""

    def test_平台关闭时一个过滤器都不挂(self, qapp, monkeypatch) -> None:
        """判据：`CLIPBOARD_NORMALIZE_ENABLED=False`（Linux）⇒ 不安装任何过滤器"""
        monkeypatch.setattr(web_shell, "CLIPBOARD_NORMALIZE_ENABLED", False)
        target = QWidget()
        normalizer = PasteClipboardNormalizer(FakeWindow(active=True))
        fake = SimpleNamespace(
            view=SimpleNamespace(focusProxy=lambda: target),
            paste_normalizer=normalizer,
            _paste_filter_target=None,
            _paste_filter_warned=False,
        )

        assert WebShell._ensure_paste_filter(fake) is False
        assert fake._paste_filter_target is None
        before = normalizer.paste_key_count
        QApplication.sendEvent(
            target, key_press(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        )
        assert normalizer.paste_key_count == before  # 真的没挂上

    def test_换目标时先摘旧再挂新(self, qapp, monkeypatch) -> None:
        """判据：`focusProxy()` 换 widget（页面重建）⇒ 旧目标失效、新目标生效"""
        monkeypatch.setattr(web_shell, "CLIPBOARD_NORMALIZE_ENABLED", True)
        old = QWidget()
        new = QWidget()
        current = {"widget": old}
        normalizer = PasteClipboardNormalizer(FakeWindow(active=False))  # 非活动 ⇒ 不碰剪贴板
        fake = SimpleNamespace(
            view=SimpleNamespace(focusProxy=lambda: current["widget"]),
            paste_normalizer=normalizer,
            _paste_filter_target=None,
            _paste_filter_warned=False,
        )

        assert WebShell._ensure_paste_filter(fake) is True
        assert fake._paste_filter_target is old
        current["widget"] = new
        assert WebShell._ensure_paste_filter(fake) is True
        assert fake._paste_filter_target is new

        before = normalizer.paste_key_count
        QApplication.sendEvent(
            old, key_press(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        )
        assert normalizer.paste_key_count == before  # 旧目标已摘
        QApplication.sendEvent(
            new, key_press(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        )
        assert normalizer.paste_key_count == before + 1  # 新目标生效

    def test_关键常量与Qt侧映射名一致(self) -> None:
        """判据：触发格式名必须是 Qt 对 Win32 `CFSTR_INETURL` 的映射名（写错就等于永不触发）"""
        assert CLIPBOARD_URI_LIST_FORMAT == "text/uri-list"
        assert web_shell.CLIPBOARD_URI_LIST_FORMAT == CLIPBOARD_URI_LIST_FORMAT
