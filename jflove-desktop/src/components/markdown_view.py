"""
基于 QTextBrowser 的 Markdown 预览组件（稳定版）

为什么不用 QWebEngineView：
  - PySide6 6.11 + Qt 6.10 + Wayland 下 QWebEngineView 内嵌的 QQuickWidget
    在收到鼠标 hover 事件时会触发 PySide6 包装对象查找崩溃（PYSIDE-2700 系列），
    实际表现为打开笔记 → 鼠标移过预览 → segfault
  - 因此本组件改用 Qt 原生 QTextBrowser，配合 pygments（纯 Python 代码高亮库），
    彻底脱离 Chromium 内核，跨平台稳定，无任何 JS 资产依赖

特性：
  - GitHub 风格排版（QTextBrowser 支持的 CSS 子集）
  - 代码块语法高亮（pygments，纯 Python，BSD-3）
  - Markdown 表格、引用、列表、标题
  - 锚点跳转（与大纲面板对齐）
  - Mermaid 块降级展示：以等宽代码框 + "📊 Mermaid 图表"标签呈现，
    用户可复制源码到 mermaid.live 等工具可视化（QTextBrowser 不支持 JS，
    无法在原地渲染图表）

完全自包含：仅依赖 markdown 和 pygments 两个 Python 包，无任何 JS / CSS 网络资源。
"""

from __future__ import annotations

import re
from html import escape

import markdown as md_lib

from PySide6.QtWidgets import QTextBrowser, QSizePolicy

try:
    from pygments import highlight
    from pygments.lexers import get_lexer_by_name, guess_lexer
    from pygments.formatters import HtmlFormatter
    from pygments.util import ClassNotFound
    HAS_PYGMENTS = True
except ImportError:  # pragma: no cover
    HAS_PYGMENTS = False

from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── QTextBrowser 兼容的 CSS（QTextDocument 仅支持 CSS 2.1 子集） ──
# 不支持的属性（border-radius / flex / 媒体查询等）在这里都避开了，
# 只用 QTextBrowser 文档明确支持的 color / background / font / margin /
# padding / border / text-align 等基本属性。
_CSS = """
body {
    font-family: 'PingFang SC', 'Microsoft YaHei', 'Segoe UI', Arial, sans-serif;
    font-size: 14pt;
    color: #24292f;
    background-color: #ffffff;
    line-height: 160%;
}
h1 {
    font-size: 22pt;
    font-weight: bold;
    color: #1f2328;
    margin-top: 22px;
    margin-bottom: 12px;
    padding-bottom: 6px;
    border-bottom: 1px solid #d0d7de;
}
h2 {
    font-size: 18pt;
    font-weight: bold;
    color: #1f2328;
    margin-top: 20px;
    margin-bottom: 10px;
    padding-bottom: 4px;
    border-bottom: 1px solid #d0d7de;
}
h3 {
    font-size: 16pt;
    font-weight: bold;
    color: #1f2328;
    margin-top: 18px;
    margin-bottom: 8px;
}
h4 {
    font-size: 14pt;
    font-weight: bold;
    color: #1f2328;
    margin-top: 16px;
    margin-bottom: 6px;
}
h5, h6 { font-size: 13pt; font-weight: bold; color: #57606a; margin-top: 14px; }
p { margin-top: 8px; margin-bottom: 8px; }
strong, b { font-weight: bold; }
em, i { font-style: italic; }
a { color: #0969da; text-decoration: none; }

code {
    font-family: 'Cascadia Code', Consolas, Menlo, monospace;
    background-color: #f6f8fa;
    color: #cf222e;
    padding: 2px 6px;
    font-size: 12pt;
}
pre {
    background-color: #f6f8fa;
    color: #1f2328;
    border: 1px solid #d0d7de;
    padding: 12px;
    margin-top: 10px;
    margin-bottom: 10px;
    font-family: 'Cascadia Code', Consolas, Menlo, monospace;
    font-size: 12pt;
}
blockquote {
    margin-left: 0px;
    margin-right: 0px;
    padding-left: 14px;
    color: #57606a;
    border-left: 4px solid #d0d7de;
    background-color: #f6f8fa;
}
table {
    border-collapse: collapse;
    margin-top: 10px;
    margin-bottom: 10px;
    border: 1px solid #d0d7de;
}
th {
    background-color: #f6f8fa;
    border: 1px solid #d0d7de;
    padding: 6px 12px;
    font-weight: bold;
    text-align: left;
}
td {
    border: 1px solid #d0d7de;
    padding: 6px 12px;
}
ul, ol { margin-top: 6px; margin-bottom: 6px; }
li { margin-top: 2px; margin-bottom: 2px; }
hr { border: 1px solid #d0d7de; }

/* Mermaid 降级展示样式 */
.mermaid-label {
    background-color: #fff8c5;
    color: #9a6700;
    border-left: 4px solid #d4a72c;
    padding: 6px 12px;
    font-size: 12pt;
}
.mermaid-block {
    background-color: #fbfbf6;
    border: 1px solid #d4a72c;
    padding: 12px;
    font-family: 'Cascadia Code', Consolas, Menlo, monospace;
    font-size: 12pt;
    color: #1f2328;
}
"""


# ── 公开 API ───────────────────────────────────────

def render_markdown_html(text: str) -> str:
    """
    把 Markdown 文本渲染为完整 HTML 文档（QTextBrowser 可直接 setHtml）。

    流程：
      1. 抽取 ``` ```mermaid 块 → 占位符
      2. 抽取其他代码块（含语言标签）→ 占位符
      3. 标题前注入 <a id="hN"> 锚点
      4. markdown 库转换主体
      5. 还原 mermaid 占位符为带标签的代码框
      6. 还原代码块占位符（pygments 高亮 → inline-style HTML）

    :param text: Markdown 源
    :returns: 完整 HTML 字符串
    """
    body = _md_to_html(text)
    return f"<html><head><style>{_CSS}</style></head><body>{body}</body></html>"


def _md_to_html(text: str) -> str:
    """Markdown → HTML，含 mermaid / 代码块特殊处理 + 锚点注入"""

    # Step 1: 抽取 mermaid 块
    mermaid_blocks: list[str] = []

    def _stash_mermaid(m: re.Match) -> str:
        mermaid_blocks.append(m.group(1))
        return f"\n@@JFLOVE_MERMAID_{len(mermaid_blocks) - 1}@@\n"

    text_clean = re.sub(
        r"```mermaid\s*\n(.*?)```",
        _stash_mermaid, text, flags=re.DOTALL,
    )

    # Step 2: 抽取其他带语言标识的代码块
    code_blocks: list[tuple[str, str]] = []

    def _stash_code(m: re.Match) -> str:
        lang = (m.group(1) or "").strip()
        code = m.group(2)
        code_blocks.append((lang, code))
        return f"\n@@JFLOVE_CODE_{len(code_blocks) - 1}@@\n"

    text_clean = re.sub(
        r"```([^\n`]*)\n(.*?)```",
        _stash_code, text_clean, flags=re.DOTALL,
    )

    # Step 3: 注入标题锚点
    anchor_idx = 0
    out_lines: list[str] = []
    for line in text_clean.split("\n"):
        if re.match(r"^#{1,6}\s+\S", line):
            out_lines.append(f'<a id="h{anchor_idx}"></a>')
            anchor_idx += 1
        out_lines.append(line)
    text_with_anchors = "\n".join(out_lines)

    # Step 4: markdown → HTML（不再用 fenced_code，因为代码块已被我们抽走单独处理）
    html = md_lib.markdown(
        text_with_anchors,
        extensions=["tables", "nl2br", "sane_lists"],
    )

    # Step 5: 还原 mermaid 占位符为带醒目标签的代码框
    for i, src in enumerate(mermaid_blocks):
        placeholder = f"<p>@@JFLOVE_MERMAID_{i}@@</p>"
        rendered = (
            '<p class="mermaid-label">📊 Mermaid 图表'
            '（QTextBrowser 不支持 JS，下方显示源码，可复制到 mermaid.live 可视化）'
            "</p>"
            f'<pre class="mermaid-block">{escape(src)}</pre>'
        )
        html = html.replace(placeholder, rendered)

    # Step 6: 还原代码块（pygments 高亮）
    for i, (lang, code) in enumerate(code_blocks):
        placeholder = f"<p>@@JFLOVE_CODE_{i}@@</p>"
        html = html.replace(placeholder, _highlight_code(lang, code))

    return html


def _highlight_code(lang: str, code: str) -> str:
    """
    用 pygments 高亮代码并返回 HTML（inline style，QTextBrowser 兼容）。

    无 pygments 或语言识别失败时降级为带 escape 的 <pre> 块。
    """
    if not HAS_PYGMENTS:
        return f"<pre>{escape(code)}</pre>"
    try:
        if lang:
            lexer = get_lexer_by_name(lang)
        else:
            try:
                lexer = guess_lexer(code)
            except ClassNotFound:
                return f"<pre>{escape(code)}</pre>"
        # noclasses=True 把所有样式以 inline style 写到 <span> 里，
        # QTextBrowser 不支持 <style> 选择 .pygments-class，必须 inline
        formatter = HtmlFormatter(style="default", noclasses=True, nowrap=False)
        return highlight(code, lexer, formatter)
    except Exception as e:  # pragma: no cover - pygments 极端异常
        logger.debug("pygments 高亮失败（%s）：%s", lang, e)
        return f"<pre>{escape(code)}</pre>"


# ── Qt 控件 ────────────────────────────────────────

class MarkdownView(QTextBrowser):
    """
    Markdown 预览控件（基于 QTextBrowser，零崩溃）。

    典型用法：
        view = MarkdownView()
        view.set_markdown("# Hello\\n\\n```python\\nprint('hi')\\n```")
        ...
        view.scroll_to_anchor("h0")
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # 微调字体（QTextBrowser 默认字号偏小）
        f = self.font()
        f.setPointSize(max(11, f.pointSize()))
        self.setFont(f)
        self._last_text = ""

    def set_markdown(self, text: str) -> None:
        """渲染 Markdown 文本"""
        self._last_text = text
        html = render_markdown_html(text)
        self.setHtml(html)

    def scroll_to_anchor(self, anchor_id: str) -> None:
        """滚动到指定 id 锚点"""
        self.scrollToAnchor(anchor_id)
