"""
笔记管理页面

布局：
  - 顶部工具栏：新建/重命名/删除/刷新 | 搜索 | 视图切换（编辑/预览/分栏）| 保存状态+保存
  - 左侧（200px）：笔记文件列表
  - 中央（弹性）：Markdown 编辑器 或 HTML 预览，或两者分栏显示
  - 右侧（180px）：大纲导航（解析标题层级，点击跳转）

快捷键：Ctrl+S 保存
"""

import re

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QTextEdit,
    QInputDialog, QAbstractItemView, QTreeWidget, QTreeWidgetItem,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor, QFont

from qfluentwidgets import (
    ToolButton, PrimaryPushButton, SearchLineEdit,
    BodyLabel, InfoBar, InfoBarPosition, MessageBox, FluentIcon as FIF,
)

from src.components.markdown_view import MarkdownView
from src.services import note_service
from src.utils.worker import Worker
from src.utils.logger import get_logger

logger = get_logger(__name__)

_MODE_EDIT = 0
_MODE_PREVIEW = 1
_MODE_SPLIT = 2


class NotePage(QWidget):
    """笔记管理页面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("notePage")
        self._current_filename: str | None = None
        self._is_modified = False
        self._notes: list[dict] = []
        self._worker = None
        self._view_mode = _MODE_PREVIEW
        self._outline_items: list[tuple[int, str, int]] = []

        self._preview_timer = QTimer()
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(400)
        self._preview_timer.timeout.connect(self._render_preview)

        self._setup_ui()
        self._set_view_mode(_MODE_PREVIEW)

    # ── UI 构建 ────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        root.addLayout(self._build_toolbar())

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.setHandleWidth(3)

        main_splitter.addWidget(self._build_list_panel())

        # 编辑器与预览共用一个水平 QSplitter，通过 show/hide 切换视图模式
        self._editor_splitter = QSplitter(Qt.Horizontal)
        self._editor_splitter.setHandleWidth(2)

        self._editor = QTextEdit()
        self._editor.setPlaceholderText(
            "使用 Markdown 语法编写笔记…\n\n"
            "支持代码块（```python ...）和 mermaid 图表（```mermaid ...）"
        )
        self._editor.textChanged.connect(self._on_content_changed)
        self._editor.setAcceptRichText(False)
        # 等宽字体 + 略宽行距，提升 Markdown / 代码 / mermaid 编辑体验
        editor_font = QFont("Cascadia Code, JetBrains Mono, Menlo, Consolas, monospace")
        editor_font.setStyleHint(QFont.TypeWriter)
        editor_font.setPointSize(11)
        self._editor.setFont(editor_font)
        self._editor.setTabStopDistance(28)
        self._editor_splitter.addWidget(self._editor)

        # 预览：QWebEngineView，支持现代 CSS / 代码高亮 / mermaid 图表
        self._preview = MarkdownView()
        self._editor_splitter.addWidget(self._preview)

        main_splitter.addWidget(self._editor_splitter)
        main_splitter.addWidget(self._build_outline_panel())

        main_splitter.setSizes([200, 900, 180])
        main_splitter.setStretchFactor(1, 1)

        root.addWidget(main_splitter)

        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.activated.connect(self._on_save)

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(6)

        new_btn = ToolButton(FIF.ADD)
        new_btn.setToolTip("新建笔记")
        new_btn.clicked.connect(self._on_new_note)
        bar.addWidget(new_btn)

        rename_btn = ToolButton(FIF.EDIT)
        rename_btn.setToolTip("重命名")
        rename_btn.clicked.connect(self._on_rename_note)
        bar.addWidget(rename_btn)

        bar.addSpacing(4)

        delete_btn = ToolButton(FIF.DELETE)
        delete_btn.setToolTip("删除笔记")
        delete_btn.clicked.connect(self._on_delete_note)
        bar.addWidget(delete_btn)

        refresh_btn = ToolButton(FIF.SYNC)
        refresh_btn.setToolTip("刷新列表")
        refresh_btn.clicked.connect(self.load_notes)
        bar.addWidget(refresh_btn)

        bar.addSpacing(16)

        self._search_input = SearchLineEdit()
        self._search_input.setPlaceholderText("搜索笔记…")
        self._search_input.textChanged.connect(self._on_search)
        self._search_input.setMaximumWidth(240)
        bar.addWidget(self._search_input)

        bar.addStretch()

        # 视图模式按钮（互斥）
        self._btn_edit = ToolButton(FIF.EDIT)
        self._btn_edit.setToolTip("仅编辑")
        self._btn_edit.setCheckable(True)
        self._btn_edit.clicked.connect(lambda: self._set_view_mode(_MODE_EDIT))
        bar.addWidget(self._btn_edit)

        self._btn_preview = ToolButton(FIF.VIEW)
        self._btn_preview.setToolTip("仅预览")
        self._btn_preview.setCheckable(True)
        self._btn_preview.clicked.connect(lambda: self._set_view_mode(_MODE_PREVIEW))
        bar.addWidget(self._btn_preview)

        self._btn_split = ToolButton(FIF.LAYOUT)
        self._btn_split.setToolTip("分栏：编辑 + 预览")
        self._btn_split.setCheckable(True)
        self._btn_split.clicked.connect(lambda: self._set_view_mode(_MODE_SPLIT))
        bar.addWidget(self._btn_split)

        bar.addSpacing(16)

        self._save_status_label = BodyLabel("")
        bar.addWidget(self._save_status_label)

        self._save_btn = PrimaryPushButton(FIF.SAVE, "保存")
        self._save_btn.clicked.connect(self._on_save)
        bar.addWidget(self._save_btn)

        return bar

    def _build_list_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(160)
        panel.setMaximumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(0)

        self._note_list = QListWidget()
        self._note_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._note_list.currentItemChanged.connect(self._on_note_selected)
        layout.addWidget(self._note_list)

        return panel

    def _build_outline_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(140)
        panel.setMaximumWidth(260)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 0, 0, 0)
        layout.setSpacing(4)

        layout.addWidget(BodyLabel("大纲"))

        self._outline_tree = QTreeWidget()
        self._outline_tree.setHeaderHidden(True)
        self._outline_tree.setRootIsDecorated(True)
        self._outline_tree.itemClicked.connect(self._on_outline_clicked)
        layout.addWidget(self._outline_tree)

        return panel

    # ── 视图模式 ───────────────────────────────────────

    def _set_view_mode(self, mode: int) -> None:
        self._view_mode = mode
        self._btn_edit.setChecked(mode == _MODE_EDIT)
        self._btn_preview.setChecked(mode == _MODE_PREVIEW)
        self._btn_split.setChecked(mode == _MODE_SPLIT)

        if mode == _MODE_EDIT:
            self._editor.show()
            self._preview.hide()
        elif mode == _MODE_PREVIEW:
            self._editor.hide()
            self._preview.show()
            self._render_preview()
        else:
            self._editor.show()
            self._preview.show()
            self._editor_splitter.setSizes([500, 500])
            self._render_preview()

    # ── 数据加载 ───────────────────────────────────────

    def load_notes(self) -> None:
        """加载笔记列表"""
        self._worker = Worker(note_service.list_notes)
        self._worker.finished.connect(self._on_notes_loaded)
        self._worker.error.connect(lambda e: self._show_error(f"加载笔记失败：{e}"))
        self._worker.start()

    def _on_notes_loaded(self, notes: list) -> None:
        self._notes = notes
        self._populate_list(notes)

    def _populate_list(self, notes: list) -> None:
        current_name = self._current_filename
        self._note_list.clear()
        for note in notes:
            item = QListWidgetItem(note["filename"])
            item.setData(Qt.UserRole, note)
            self._note_list.addItem(item)
            if note["filename"] == current_name:
                self._note_list.setCurrentItem(item)

    def _on_search(self, text: str) -> None:
        keyword = text.strip().lower()
        filtered = (
            [n for n in self._notes if keyword in n["filename"].lower()]
            if keyword else self._notes
        )
        self._populate_list(filtered)

    def _on_note_selected(self, current: QListWidgetItem, previous: QListWidgetItem) -> None:
        if current is None:
            return
        if self._is_modified:
            box = MessageBox("未保存", "当前笔记有未保存的修改，是否放弃？", self)
            if not box.exec():
                self._note_list.setCurrentItem(previous)
                return
        self._load_note(current.data(Qt.UserRole)["filename"])

    def _load_note(self, filename: str) -> None:
        self._worker = Worker(note_service.read_note, filename)
        self._worker.finished.connect(lambda content: self._on_note_loaded(filename, content))
        self._worker.error.connect(lambda e: self._show_error(f"读取笔记失败：{e}"))
        self._worker.start()

    def _on_note_loaded(self, filename: str, content: str) -> None:
        self._current_filename = filename
        self._editor.blockSignals(True)
        self._editor.setPlainText(content)
        self._editor.blockSignals(False)
        self._is_modified = False
        self._save_status_label.setText("")
        self._set_view_mode(_MODE_PREVIEW)
        self._update_outline()

    # ── 编辑与预览 ─────────────────────────────────────

    def _on_content_changed(self) -> None:
        if not self._is_modified:
            self._is_modified = True
            self._save_status_label.setText("● 未保存")
        self._preview_timer.start()

    def _render_preview(self) -> None:
        if self._view_mode == _MODE_EDIT:
            return
        text = self._editor.toPlainText()
        # MarkdownView 内部处理：mermaid / 代码高亮 / 锚点注入 / 现代 CSS
        self._preview.set_markdown(text)
        self._update_outline()

    def _on_save(self) -> None:
        if not self._current_filename:
            self._show_error("请先选择或新建一个笔记")
            return
        self._worker = Worker(note_service.write_note, self._current_filename,
                              self._editor.toPlainText())
        self._worker.finished.connect(self._on_save_done)
        self._worker.error.connect(lambda e: self._show_error(f"保存失败：{e}"))
        self._worker.start()

    def _on_save_done(self, _) -> None:
        self._is_modified = False
        self._save_status_label.setText("")
        self._show_success("已保存")

    # ── 大纲导航 ───────────────────────────────────────

    def _update_outline(self) -> None:
        """解析当前笔记的 Markdown 标题，重建大纲树"""
        text = self._editor.toPlainText()
        headings: list[tuple[int, str, int]] = []
        for i, line in enumerate(text.split('\n')):
            m = re.match(r'^(#{1,6}) (.+)', line)
            if m:
                headings.append((len(m.group(1)), m.group(2).strip(), i))
        self._outline_items = headings

        self._outline_tree.clear()
        stack: list[tuple[int, QTreeWidgetItem]] = []
        for anchor_idx, (level, title, line_no) in enumerate(headings):
            item = QTreeWidgetItem([title])
            item.setData(0, Qt.UserRole, (anchor_idx, line_no))
            while stack and stack[-1][0] >= level:
                stack.pop()
            if stack:
                stack[-1][1].addChild(item)
            else:
                self._outline_tree.addTopLevelItem(item)
            stack.append((level, item))
        self._outline_tree.expandAll()

    def _on_outline_clicked(self, item: QTreeWidgetItem, _: int) -> None:
        """点击大纲：预览模式滚动到锚点，编辑模式光标跳到对应行"""
        data = item.data(0, Qt.UserRole)
        if data is None:
            return
        anchor_idx, line_no = data
        if self._view_mode == _MODE_PREVIEW:
            self._preview.scroll_to_anchor(f"h{anchor_idx}")
        elif self._view_mode == _MODE_SPLIT:
            # 分栏模式：同时让预览滚动 + 编辑器跳行
            self._preview.scroll_to_anchor(f"h{anchor_idx}")
            self._cursor_to_line(line_no)
        else:
            self._cursor_to_line(line_no)

    def _cursor_to_line(self, line_no: int) -> None:
        """将编辑器光标跳转到指定行"""
        block = self._editor.document().findBlockByLineNumber(line_no)
        if block.isValid():
            cursor = QTextCursor(block)
            self._editor.setTextCursor(cursor)
            self._editor.setFocus()

    # ── 笔记管理操作 ───────────────────────────────────

    def _on_new_note(self) -> None:
        name, ok = QInputDialog.getText(self, "新建笔记", "请输入笔记文件名（.md 结尾）：")
        if not ok or not name.strip():
            return
        filename = name.strip()
        if not filename.endswith(".md"):
            filename += ".md"
        self._worker = Worker(note_service.write_note, filename, "")

        def _on_created(_):
            self._current_filename = filename
            self._editor.blockSignals(True)
            self._editor.setPlainText("")
            self._editor.blockSignals(False)
            self._is_modified = False
            self._save_status_label.setText("")
            self._outline_tree.clear()
            self._set_view_mode(_MODE_EDIT)
            self.load_notes()
            self._show_success(f"笔记 {filename} 已创建")

        self._worker.finished.connect(_on_created)
        self._worker.error.connect(lambda e: self._show_error(f"创建失败：{e}"))
        self._worker.start()

    def _on_rename_note(self) -> None:
        if not self._current_filename:
            self._show_error("请先选择要重命名的笔记")
            return
        new_name, ok = QInputDialog.getText(
            self, "重命名笔记", "请输入新文件名：", text=self._current_filename,
        )
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        if not new_name.endswith(".md"):
            new_name += ".md"
        old_name = self._current_filename
        self._worker = Worker(note_service.rename_note, old_name, new_name)

        def _done(_):
            self._current_filename = new_name
            self.load_notes()
            self._show_success("已重命名")

        self._worker.finished.connect(_done)
        self._worker.error.connect(lambda e: self._show_error(f"重命名失败：{e}"))
        self._worker.start()

    def _on_delete_note(self) -> None:
        if not self._current_filename:
            self._show_error("请先选择要删除的笔记")
            return
        box = MessageBox("确认删除", f"确定要删除笔记「{self._current_filename}」吗？", self)
        if not box.exec():
            return
        filename = self._current_filename
        self._worker = Worker(note_service.delete_note, filename)

        def _done(_):
            self._current_filename = None
            self._editor.clear()
            self._preview.set_markdown("")
            self._outline_tree.clear()
            self._is_modified = False
            self._save_status_label.setText("")
            self.load_notes()
            self._show_success("已删除")

        self._worker.finished.connect(_done)
        self._worker.error.connect(lambda e: self._show_error(f"删除失败：{e}"))
        self._worker.start()

    # ── 辅助方法 ───────────────────────────────────────

    def _show_error(self, msg: str) -> None:
        InfoBar.error("错误", msg, parent=self, duration=4000,
                      position=InfoBarPosition.TOP_RIGHT)

    def _show_success(self, msg: str) -> None:
        InfoBar.success("成功", msg, parent=self, duration=3000,
                        position=InfoBarPosition.TOP_RIGHT)
