"""
通用文件预览对话框

支持的预览类型（按扩展名识别）：
  - 图片：png/jpg/jpeg/gif/bmp/webp/tiff/tif/ico
  - 矢量图：svg
  - 视频：mp4/mkv/avi/mov/webm/flv/wmv/m4v/mpg/mpeg/ts/3gp
  - 音频：mp3/wav/ogg/flac/m4a/aac/wma/opus
  - Markdown：md/markdown/mdown/mkd（渲染为 HTML，支持 fenced code）
  - 文本：txt/log/json/xml/yaml/ini/csv 以及常见编程语言源码

跨平台说明：
  - 视频/音频通过本地 StreamProxy（loopback HTTP）流式传输，无需写临时文件。
  - 文本通过 StreamTextLoader 流式加载，首屏≤3s，大文件超 8MB 截断旧内容。
  - 图片/SVG/Markdown 整文件加载，保持 v1.0 行为（内存缓冲，不写磁盘）。

使用方式：
    dialog = PreviewDialog(disk_id, rel_path, filename, parent=self)
    dialog.exec()
"""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, QUrl, QByteArray, QTimer
from PySide6.QtGui import QPixmap, QFont, QTextCursor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QStackedWidget, QWidget, QLabel,
    QPlainTextEdit, QSlider, QSizePolicy, QScrollArea,
)

# QtMultimedia 与 QtSvgWidgets 在 PySide6 中默认可用，但运行时可能因系统缺
# 少 codec 而失败；此处仅捕获导入级错误，运行级错误另行处理。
try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PySide6.QtMultimediaWidgets import QVideoWidget
    HAS_MEDIA = True
except ImportError:  # pragma: no cover - 极少数精简版 PySide6
    HAS_MEDIA = False

try:
    from PySide6.QtSvgWidgets import QSvgWidget
    HAS_SVG = True
except ImportError:  # pragma: no cover
    HAS_SVG = False

from qfluentwidgets import (
    PushButton, ToolButton, BodyLabel, CaptionLabel, StrongBodyLabel,
    SubtitleLabel, FluentIcon as FIF, ProgressRing,
)

from src.services import file_service
from src.utils.icon import get_app_icon
from src.utils.worker import Worker
from src.utils.logger import get_logger
from src.components.stream_proxy import StreamProxy
from src.components.stream_text_loader import StreamTextLoader

logger = get_logger(__name__)

# ── 文件类型识别 ──────────────────────────────────
IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "tiff", "tif", "ico"}
SVG_EXTS = {"svg"}
VIDEO_EXTS = {"mp4", "mkv", "avi", "mov", "webm", "flv", "wmv",
              "m4v", "mpg", "mpeg", "ts", "3gp"}
AUDIO_EXTS = {"mp3", "wav", "ogg", "flac", "m4a", "aac", "wma", "opus"}
MARKDOWN_EXTS = {"md", "markdown", "mdown", "mkd"}
TEXT_EXTS = {
    # 通用文本
    "txt", "log", "csv", "tsv", "diff", "patch",
    # 标记/配置
    "json", "xml", "yaml", "yml", "ini", "toml", "conf", "cfg", "env",
    "html", "htm", "css", "less", "scss", "sass",
    # 编程语言
    "py", "java", "kt", "scala", "groovy", "js", "mjs", "cjs", "ts", "jsx", "tsx",
    "go", "rs", "c", "cpp", "cc", "cxx", "h", "hpp", "hh", "hxx",
    "cs", "swift", "rb", "php", "lua", "pl", "perl", "r", "m",
    "sql", "sh", "bash", "zsh", "fish", "bat", "cmd", "ps1",
    "vue", "svelte", "astro",
    # 项目文件
    "gradle", "properties", "lock", "gitignore", "dockerignore",
}

# 单次预览最大字节数：超出则提示用户改用下载
MAX_PREVIEW_BYTES = 500 * 1024 * 1024  # 500 MB


def _detect_kind(filename: str) -> str:
    """根据扩展名识别文件类型，返回 image/svg/video/audio/markdown/text/other"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in IMAGE_EXTS:
        return "image"
    if ext in SVG_EXTS:
        return "svg"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in MARKDOWN_EXTS:
        return "markdown"
    if ext in TEXT_EXTS:
        return "text"
    return "other"


def _format_ms(ms: int) -> str:
    """毫秒数 → mm:ss / hh:mm:ss"""
    if ms <= 0:
        return "00:00"
    total_seconds = ms // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _format_size(size: int) -> str:
    """字节数格式化"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 ** 2:
        return f"{size / 1024:.1f} KB"
    if size < 1024 ** 3:
        return f"{size / 1024 ** 2:.1f} MB"
    return f"{size / 1024 ** 3:.2f} GB"


# ── 各类内置预览控件 ──────────────────────────────

class _ImageView(QScrollArea):
    """图片预览：自适应缩放、超大图可滚动查看"""

    def __init__(self, data: bytes, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setAlignment(Qt.AlignCenter)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignCenter)
        pix = QPixmap()
        pix.loadFromData(QByteArray(data))
        if not pix.isNull():
            # 超过 1920x1080 的图先按比例缩到 1920×1080，再让用户在控件内查看
            if pix.width() > 1920 or pix.height() > 1080:
                pix = pix.scaled(1920, 1080, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._label.setPixmap(pix)
        else:
            self._label.setText("无法解析图片数据")
        self.setWidget(self._label)


class _SvgView(QWidget):
    """SVG 预览（依赖 QtSvgWidgets）"""

    def __init__(self, data: bytes, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if HAS_SVG:
            svg = QSvgWidget()
            svg.load(QByteArray(data))
            layout.addWidget(svg)
        else:  # pragma: no cover
            layout.addWidget(BodyLabel("当前环境未安装 QtSvgWidgets，无法预览 SVG"))


class _TextView(QWidget):
    """纯文本/源代码预览（等宽字体，自动换行可关）"""

    def __init__(self, data: bytes, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QTextBrowser
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        browser = QTextBrowser()
        font = QFont("Consolas, Monaco, Courier New")
        font.setStyleHint(QFont.TypeWriter)
        browser.setFont(font)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = data.decode("gbk")
            except UnicodeDecodeError:
                text = f"[二进制文件，无法以文本方式显示，共 {_format_size(len(data))}]"
        browser.setPlainText(text)
        layout.addWidget(browser)


class _MarkdownView(QWidget):
    """Markdown 预览：复用 MarkdownView 组件（QWebEngineView + mermaid + 高亮）"""

    def __init__(self, data: bytes, parent=None):
        super().__init__(parent)
        from src.components.markdown_view import MarkdownView
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("gbk", errors="replace")
        view = MarkdownView()
        view.set_markdown(text)
        layout.addWidget(view)


class _MediaView(QWidget):
    """
    视频 / 音频播放控件。

    通过 StreamProxy 本地代理 URL 流式拉取，无需写临时文件。
    """

    def __init__(self, media_url: str, kind: str,
                 display_name: str = "", proxy: Optional[StreamProxy] = None, parent=None):
        super().__init__(parent)
        self._kind = kind  # "video" or "audio"
        self._media_url = media_url
        self._display_name = display_name
        # v1.4.1：StreamProxy 引用，供时长兑底与主动 seek
        self._proxy = proxy
        # 当前流起始偏移（ms）：time 修复流 seek 后服务端 -ss 重拉、时间戳归零，
        # QMediaPlayer 的 position 从 0 开始，用偏移把显示/滑块映射回绝对时间轴
        self._seek_offset_ms = 0
        self._setup_ui()
        self._setup_player()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        if self._kind == "video":
            self._video_widget = QVideoWidget()
            self._video_widget.setMinimumHeight(360)
            self._video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            layout.addWidget(self._video_widget, stretch=1)
        else:
            # 音频专用：展示一个圆形指示 + 文件名
            audio_card = QWidget()
            audio_layout = QVBoxLayout(audio_card)
            audio_layout.setAlignment(Qt.AlignCenter)
            audio_layout.setSpacing(12)
            ring = ProgressRing()
            ring.setRange(0, 0)  # 不确定模式：作为播放视觉指示
            ring.setFixedSize(96, 96)
            self._audio_ring = ring
            audio_layout.addWidget(ring, alignment=Qt.AlignCenter)
            audio_layout.addWidget(
                StrongBodyLabel(self._display_name or self._media_url),
                alignment=Qt.AlignCenter,
            )
            audio_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            layout.addWidget(audio_card, stretch=1)

        # 进度条 + 时间标签
        time_row = QHBoxLayout()
        time_row.setSpacing(8)
        self._position_label = CaptionLabel("00:00")
        time_row.addWidget(self._position_label)
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.sliderMoved.connect(self._on_slider_moved)
        self._slider.sliderReleased.connect(self._on_slider_released)
        time_row.addWidget(self._slider, stretch=1)
        self._duration_label = CaptionLabel("00:00")
        time_row.addWidget(self._duration_label)
        layout.addLayout(time_row)

        # 控制按钮
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)
        self._play_btn = ToolButton(FIF.PLAY)
        self._play_btn.setToolTip("播放/暂停")
        self._play_btn.clicked.connect(self._toggle_play)
        ctrl_row.addWidget(self._play_btn)

        stop_btn = ToolButton(FIF.PAUSE)
        stop_btn.setToolTip("停止")
        stop_btn.clicked.connect(self._on_stop)
        ctrl_row.addWidget(stop_btn)

        ctrl_row.addStretch()
        ctrl_row.addWidget(BodyLabel("音量"))
        self._volume_slider = QSlider(Qt.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(70)
        self._volume_slider.setFixedWidth(120)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        ctrl_row.addWidget(self._volume_slider)
        layout.addLayout(ctrl_row)

    def _setup_player(self) -> None:
        """创建并配置 QMediaPlayer"""
        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._audio_output.setVolume(0.7)
        self._player.setAudioOutput(self._audio_output)
        if self._kind == "video":
            self._player.setVideoOutput(self._video_widget)

        # 信号连接
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_state_changed)
        self._player.errorOccurred.connect(self._on_error)

        self._player.setSource(QUrl(self._media_url))
        # 自动播放
        self._player.play()

        # v1.4.1：QMediaPlayer 的 FFmpeg 后端对空 moov 流式 fMP4 拿不到总时长，
        # 用 QTimer 轮询 StreamProxy 的 meta.duration 兑底设置进度条范围。
        self._hint_timer = QTimer(self)
        self._hint_timer.setInterval(500)
        self._hint_timer.timeout.connect(self._update_duration_hint)
        self._hint_timer.start()

    # ── 控制回调 ───────────────────────────────────

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_stop(self) -> None:
        self._player.stop()

    def _on_volume_changed(self, value: int) -> None:
        self._audio_output.setVolume(value / 100.0)

    def _on_slider_moved(self, value: int) -> None:
        self._position_label.setText(_format_ms(value))

    def _on_slider_released(self) -> None:
        # v1.4.1：time 修复流 seek 依赖重新拉流（服务端 -ss），而非 QMediaPlayer
        # 内部字节 seek。目标为绝对时间轴位置：记录偏移 → proxy.seek → 重新 setSource。
        target_ms = self._slider.value()
        self._seek_offset_ms = target_ms
        new_url = self._media_url
        if self._proxy is not None:
            self._proxy.seek(target_ms / 1000.0)
            # seek 后 URL 带新版本号，强制 QMediaPlayer 重新 GET（同 URL 会复用缓存）
            new_url = self._proxy.url
        self._player.stop()
        self._player.setSource(QUrl(new_url))
        self._player.play()

    def _on_position_changed(self, ms: int) -> None:
        # 拖动进度条时不要被自动更新覆盖；position 加偏移映射回绝对时间轴
        absolute = ms + self._seek_offset_ms
        if not self._slider.isSliderDown():
            self._slider.setValue(absolute)
        self._position_label.setText(_format_ms(absolute))

    def _on_duration_changed(self, ms: int) -> None:
        # QMediaPlayer 报告的 duration 可能为 0（流式拿不到），统一走兑底逻辑
        self._update_duration_hint()

    def _update_duration_hint(self) -> None:
        """
        用 StreamProxy 的 meta.duration 兑底设置进度条范围与总时长标签。

        v1.4.1：QMediaPlayer 的 duration 只读且对空 moov 流式 fMP4 不可靠，故
        优先用 meta 的完整时长（恒定），QMediaPlayer 的 duration 仅作兑底。
        """
        hint_ms = int(self._proxy.duration * 1000) if self._proxy else 0
        player_ms = self._player.duration()
        full_ms = hint_ms if hint_ms > 0 else player_ms
        if full_ms > 0 and self._slider.maximum() != full_ms:
            self._slider.setRange(0, full_ms)
            self._duration_label.setText(_format_ms(full_ms))

    def _on_state_changed(self, state) -> None:
        # 切换播放/暂停图标
        if state == QMediaPlayer.PlayingState:
            self._play_btn.setIcon(FIF.PAUSE)
        else:
            self._play_btn.setIcon(FIF.PLAY)

    def _on_error(self, error, msg: str = "") -> None:
        logger.error("媒体播放错误：%s %s", error, msg)

    def stop_and_release(self) -> None:
        """对话框关闭时调用：停止播放并释放 QMediaPlayer，使 StreamProxy 能及时关闭连接"""
        try:
            self._player.stop()
            self._player.setSource(QUrl())
        except Exception:
            pass


# 文本流式预览：缓冲超过此值时截断旧内容
_TEXT_MAX_BYTES = 8 * 1024 * 1024   # 8 MB
_TEXT_KEEP_BYTES = 6 * 1024 * 1024  # 截断后保留 6 MB


class _StreamTextView(QWidget):
    """
    文本流式预览控件。

    通过 StreamTextLoader 逐帧 append 解密内容，
    超出 8MB 后截断旧内容，保留最新 6MB。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._edit = QPlainTextEdit()
        font = QFont("Consolas, Monaco, Courier New")
        font.setStyleHint(QFont.TypeWriter)
        self._edit.setFont(font)
        self._edit.setReadOnly(True)
        layout.addWidget(self._edit)
        self._buf_bytes = 0

    def append_bytes(self, data: bytes) -> None:
        """
        追加一块解密字节到文本框。

        :param data: 明文字节（UTF-8 优先，GBK 兜底）
        """
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("gbk", errors="replace")

        self._buf_bytes += len(data)
        if self._buf_bytes > _TEXT_MAX_BYTES:
            # 截断：保留最新 6MB 的内容
            full = self._edit.toPlainText()
            keep_chars = _TEXT_KEEP_BYTES  # 字节近似字符数（ASCII 主导时相同）
            self._edit.setPlainText(full[-keep_chars:])
            self._buf_bytes = _TEXT_KEEP_BYTES

        self._edit.moveCursor(QTextCursor.End)
        self._edit.insertPlainText(text)


# ── 主对话框 ──────────────────────────────────────

class PreviewDialog(QDialog):
    """
    通用文件预览对话框。

    根据扩展名自动选择最合适的预览方式。数据获取异步进行，加载阶段显示
    旋转加载指示；完成后切换到具体预览控件。

    :param disk_id: 虚拟磁盘 ID
    :param rel_path: 服务端文件相对路径
    :param filename: 显示用文件名
    """

    def __init__(self, disk_id: int, rel_path: str, filename: str, parent=None):
        super().__init__(parent)
        self._disk_id = disk_id
        self._rel_path = rel_path
        self._filename = filename
        self._kind = _detect_kind(filename)
        self._media_view: Optional[_MediaView] = None
        self._worker: Optional[Worker] = None
        self._proxy: Optional[StreamProxy] = None
        self._text_loader: Optional[StreamTextLoader] = None

        self.setWindowTitle(f"预览：{filename}")
        self.setWindowIcon(get_app_icon())
        self.resize(960, 660)

        self._setup_ui()
        self._start_loading()

    # ── UI 构建 ────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # 顶部标题
        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(SubtitleLabel(self._filename))
        header.addStretch()
        kind_text = {
            "image": "图片", "svg": "矢量图", "video": "视频", "audio": "音频",
            "markdown": "Markdown", "text": "文本",
        }.get(self._kind, "其他")
        header.addWidget(CaptionLabel(f"类型：{kind_text}"))
        layout.addLayout(header)

        # 主体堆栈：加载页 + 内容页
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, stretch=1)

        # 加载页
        loading = QWidget()
        l_layout = QVBoxLayout(loading)
        l_layout.setAlignment(Qt.AlignCenter)
        ring = ProgressRing()
        ring.setRange(0, 0)
        ring.setFixedSize(64, 64)
        l_layout.addWidget(ring, alignment=Qt.AlignCenter)
        l_layout.addSpacing(12)
        l_layout.addWidget(BodyLabel("正在加载预览…"), alignment=Qt.AlignCenter)
        self._stack.addWidget(loading)  # index 0

        # 内容页占位（加载完成后替换）
        self._content_holder = QWidget()
        self._content_layout = QVBoxLayout(self._content_holder)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self._content_holder)  # index 1

        # 底部关闭按钮
        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = PushButton("关闭")
        close_btn.setMinimumWidth(100)
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    # ── 数据加载 ────────────────────────────────────

    def _start_loading(self) -> None:
        """根据文件类型选择加载策略"""
        if self._kind in ("video", "audio"):
            # 媒体类：StreamProxy 本地代理，QMediaPlayer 直接 HTTP 拉取
            path_dir = os.path.dirname(self._rel_path)
            self._proxy = StreamProxy(self._disk_id, path_dir, self._filename)
            self._proxy.start()
            if not HAS_MEDIA:
                self._show_message(
                    "当前环境未安装 QtMultimedia 模块，无法预览音视频。"
                    "请安装 PySide6 完整版及系统 codec（Linux 需 GStreamer 插件）。"
                )
                return
            self._media_view = _MediaView(
                self._proxy.url, self._kind,
                display_name=self._filename, proxy=self._proxy, parent=self,
            )
            self._content_layout.addWidget(self._media_view)
            self._stack.setCurrentIndex(1)

        elif self._kind == "text":
            # 文本类：StreamTextLoader 流式 append
            path_dir = os.path.dirname(self._rel_path)
            text_view = _StreamTextView(self)
            self._content_layout.addWidget(text_view)
            self._stack.setCurrentIndex(1)

            self._text_loader = StreamTextLoader(self)
            self._text_loader.chunk_ready.connect(text_view.append_bytes)
            self._text_loader.error.connect(self._on_load_error)
            self._text_loader.start_loading(
                self._disk_id, path_dir, self._filename,
                range_start=0, range_end=-1,
            )

        else:
            # 图片 / SVG / Markdown / 其他：整文件加载（内存，不写磁盘）
            self._worker = Worker(
                file_service.get_preview_bytes, self._disk_id, self._rel_path
            )
            self._worker.finished.connect(self._on_bytes_ready)
            self._worker.error.connect(self._on_load_error)
            self._worker.start()

    # ── 加载回调 ────────────────────────────────────

    def _on_bytes_ready(self, data: bytes) -> None:
        """字节数据加载完成"""
        if len(data) > MAX_PREVIEW_BYTES:
            self._show_message(
                f"文件过大（{_format_size(len(data))}），超出预览大小上限"
                f"（{_format_size(MAX_PREVIEW_BYTES)}）。请改用下载查看。"
            )
            return

        if self._kind == "image":
            view = _ImageView(data)
        elif self._kind == "svg":
            view = _SvgView(data)
        elif self._kind == "markdown":
            view = _MarkdownView(data)
        elif self._kind == "text":
            view = _TextView(data)
        else:
            # 兜底：尝试当文本显示，否则提示无法预览
            try:
                data.decode("utf-8")
                view = _TextView(data)
            except UnicodeDecodeError:
                self._show_message(
                    f"文件类型「{self._filename}」暂不支持预览，"
                    f"大小 {_format_size(len(data))}。请下载到本地后查看。"
                )
                return

        self._content_layout.addWidget(view)
        self._stack.setCurrentIndex(1)

    def _on_load_error(self, msg: str) -> None:
        """加载失败"""
        logger.error("预览加载失败：%s", msg)
        self._show_message(f"预览加载失败：{msg}")

    def _show_message(self, msg: str) -> None:
        """在内容区显示一条普通提示文字（替代实际预览控件）"""
        # 清空之前可能添加的控件
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        wrapper = QWidget()
        wl = QVBoxLayout(wrapper)
        wl.setAlignment(Qt.AlignCenter)
        wl.setSpacing(6)
        wl.addWidget(BodyLabel(msg, wrapper), alignment=Qt.AlignCenter)
        self._content_layout.addWidget(wrapper)
        self._stack.setCurrentIndex(1)

    # ── 关闭清理 ────────────────────────────────────

    def closeEvent(self, event) -> None:
        """关闭时停止播放、关闭代理、取消文本加载"""
        if self._media_view is not None:
            self._media_view.stop_and_release()
        if self._proxy is not None:
            self._proxy.close()
            self._proxy = None
        if self._text_loader is not None:
            self._text_loader.cancel()
            self._text_loader = None
        super().closeEvent(event)
