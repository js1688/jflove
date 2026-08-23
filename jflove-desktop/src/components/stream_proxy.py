"""
本地流式代理模块

StreamProxy 在 127.0.0.1 随机端口启动一个 HTTP 服务器，QMediaPlayer 通过
http://127.0.0.1:{port}/{token} 拉取媒体流，代理负责：
  1. 解析 HTTP Range 请求头（含 suffix range bytes=-N），把字节范围传给
     file_service.stream_range()
  2. 逐帧接收解密后的明文，直接 write 给 QMediaPlayer
  3. 响应 206 Partial Content，含正确的 Content-Range / Content-Length

安全保障：
  - 只绑定 loopback 地址，外部无法访问
  - URL 含一次性随机 token（UUID4），防止其他进程猜测

并发设计：
  - 使用 ThreadingTCPServer：每个连接独立线程，seek 请求无需等待上一流结束
  - 元数据（file_size / content_type）只拉取一次，加锁缓存到 StreamProxy
"""

from __future__ import annotations

import uuid
import threading
import socketserver
import http.server
from typing import Optional

from src.services import file_service
from src.utils.logger import get_logger

logger = get_logger(__name__)


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    """处理 QMediaPlayer 发来的 GET / HEAD 请求"""

    # 使用 HTTP/1.1 提升 FFmpeg 兼容性（206 Partial Content 标准响应）
    protocol_version = "HTTP/1.1"

    def setup(self):
        """setup() 在 handle() 之前执行，此时 self.server 已就绪"""
        super().setup()
        # server 是 _TokenTCPServer，持有 proxy 引用
        self.proxy: "StreamProxy" = self.server.proxy  # type: ignore

    def log_message(self, fmt, *args):
        """禁用默认的 stderr 日志，改为 logger"""
        logger.debug("StreamProxy [%s] " + fmt, self.client_address[0], *args)

    def _ensure_meta(self) -> tuple[int, str]:
        """
        返回 (file_size, content_type)。

        首次调用时向服务端发 range(0, 0) 请求拿元数据帧并缓存；
        后续调用直接返回缓存（线程安全）。
        """
        with self.proxy._meta_lock:
            if self.proxy._meta_fetched:
                return self.proxy._file_size, self.proxy._content_type
            # 在锁内发请求，防止多线程重复拉取。
            # 注意：网络超时（≤35s）期间锁会被持有，但元数据每个 StreamProxy
            # 实例只拉取一次，正常网络条件下影响可忽略；详见 v1.1.0 审查报告 L-3。
            meta, frame_iter = file_service.stream_range(
                self.proxy.disk_id,
                self.proxy.path,
                self.proxy.filename,
                range_start=0,
                range_end=0,
                # v1.4.0：探测请求也声明支持时间 range，服务端对损坏文件
                # 返回 time meta（file_size / duration），代理据此进入 time 模式
                range_start_seconds=0,
            )
            # meta 已在 stream_range 内读取；不消费数据帧，直接关闭生成器
            # （time 模式下服务端会继续输出修复流，及时中断避免浪费转码）
            frame_iter.close()
            self.proxy._file_size = meta.get("file_size", 0)
            self.proxy._content_type = meta.get(
                "content_type", "application/octet-stream"
            )
            # v1.4.0：time 修复流模式与时长（供 GET 线性时间 seek 映射）
            self.proxy._stream_mode = meta.get("stream_mode", "byte")
            self.proxy._duration = float(meta.get("duration", 0.0) or 0.0)
            self.proxy._meta_fetched = True
        return self.proxy._file_size, self.proxy._content_type

    def _parse_range(self, file_size: int) -> tuple[int, int]:
        """
        解析 Range 请求头，返回 (start, end_exclusive)。

        支持三种 RFC 7233 格式：
          bytes=X-Y  → (X, Y+1)          绝对范围
          bytes=X-   → (X, file_size)     从 X 到结尾
          bytes=-N   → (file_size-N, file_size)   最后 N 字节（suffix range）
        无 Range 头时返回整个文件范围。
        """
        raw = self.headers.get("Range", "")
        if raw.startswith("bytes="):
            parts = raw[6:].split("-", 1)
            try:
                if not parts[0]:
                    # Suffix range: bytes=-N，表示文件最后 N 字节
                    suffix = int(parts[1])
                    return max(0, file_size - suffix), file_size
                start = int(parts[0])
                end = int(parts[1]) + 1 if parts[1] else file_size
                return start, end
            except (ValueError, IndexError):
                pass
        return 0, file_size

    def _send_error_response(self, code: int, msg: str) -> None:
        self.send_response(code)
        self.send_header("Connection", "close")
        self.end_headers()
        logger.warning("StreamProxy 错误响应 %d: %s", code, msg)

    def do_HEAD(self) -> None:  # noqa: N802
        """QMediaPlayer 先发 HEAD 探测文件大小和类型"""
        if self.proxy._closed:
            self._send_error_response(503, "proxy closed")
            return
        if not self._check_token():
            return
        try:
            file_size, content_type = self._ensure_meta()
        except Exception as e:
            logger.error("StreamProxy HEAD 元数据失败: %s", e)
            self._send_error_response(500, str(e))
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(file_size))
        # v1.4.0：time 修复流为实时生成、总字节不可预知，不支持字节 seek，
        # 不声明 Accept-Ranges，让播放器按顺序流式播放。
        # v1.4.1：不要对 time 流声明 Accept-Ranges——QMediaPlayer 的 FFmpeg 后端
        # 一旦认为可 seek，会进入随机访问模式并发字节 Range 探测，对 chunked
        # empty_moov fMP4 直接 Demuxing failed（实测）；UI 主动 seek 改由
        # proxy.seek() + 新 URL 重新拉流实现，不依赖字节 Range。
        if self.proxy._stream_mode != "time":
            self.send_header("Accept-Ranges", "bytes")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        """处理媒体流请求（含 Range seek；v1.4.0 兼容 time 修复流）"""
        if self.proxy._closed:
            self._send_error_response(503, "proxy closed")
            return
        if not self._check_token():
            return

        try:
            file_size, content_type = self._ensure_meta()
        except Exception as e:
            logger.error("StreamProxy GET 元数据失败: %s", e)
            self._send_error_response(500, str(e))
            return

        range_start, range_end = self._parse_range(file_size)
        range_end = min(range_end, file_size)

        # ── v1.4.0：time 修复流分支 ──
        # 修复流由服务端 ffmpeg 实时生成（总字节不可预知），响应走 200 + chunked；
        # 播放器若发出字节 Range，用平均码率线性近似映射为时间起点
        # （range_start / file_size * duration），服务端 -ss 重拉。
        # v1.4.1：UI 主动 seek 的 _seek_seconds 优先，一次性消费。
        if self.proxy._stream_mode == "time":
            seconds = self.proxy._seek_seconds
            self.proxy._seek_seconds = 0.0
            if seconds <= 0 and range_start > 0 and self.proxy._duration > 0 and file_size > 0:
                seconds = range_start / file_size * self.proxy._duration
            try:
                _, frame_iter = file_service.stream_range(
                    self.proxy.disk_id,
                    self.proxy.path,
                    self.proxy.filename,
                    range_start=0,
                    range_end=-1,
                    range_start_seconds=seconds,
                )
            except Exception as e:
                logger.error("StreamProxy 修复流请求失败: %s", e)
                self._send_error_response(500, str(e))
                return

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Connection", "close")
            self.end_headers()
            self._pump_frames(frame_iter)
            return

        try:
            _, frame_iter = file_service.stream_range(
                self.proxy.disk_id,
                self.proxy.path,
                self.proxy.filename,
                range_start=range_start,
                range_end=range_end,
            )
        except Exception as e:
            logger.error("StreamProxy stream_range 失败: %s", e)
            self._send_error_response(500, str(e))
            return

        content_length = range_end - range_start
        self.send_response(206)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header(
            "Content-Range",
            f"bytes {range_start}-{range_end - 1}/{file_size}",
        )
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Connection", "close")
        self.end_headers()

        self._pump_frames(frame_iter)

    def _pump_frames(self, frame_iter) -> None:
        """把解密后的帧逐块写给 QMediaPlayer（断开视为正常流程）"""
        try:
            for chunk in frame_iter:
                if self.proxy._closed:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端主动断开（seek / 关闭对话框），属于正常流程
        except Exception as e:
            logger.warning("StreamProxy 写入失败: %s", e)
        finally:
            # 明确关闭生成器，立即触发 resp.close()，不等 GC
            frame_iter.close()

    def _check_token(self) -> bool:
        """验证 URL token，不匹配则返回 404"""
        expected = f"/{self.proxy._token}"
        if not self.path.startswith(expected):
            self._send_error_response(404, "invalid token")
            return False
        return True


class _TokenTCPServer(socketserver.ThreadingTCPServer):
    """
    多线程 TCP 服务器。

    每个 HTTP 连接在独立线程处理，FFmpeg 并发 Range 请求（seek）无需排队等待。
    所有工作线程均设为 daemon，进程退出时自动回收。
    """

    allow_reuse_address = True
    daemon_threads = True  # 工作线程随主进程退出，无需手动 join

    def __init__(self, server_address, handler_class, proxy: "StreamProxy"):
        self.proxy = proxy
        super().__init__(server_address, handler_class)

    # proxy 注入由 _ProxyHandler.setup() 负责


class StreamProxy:
    """
    本地流式 HTTP 代理，供 QMediaPlayer 拉取加密媒体文件。

    用法：
        proxy = StreamProxy(disk_id, path, filename)
        proxy.start()
        player.setSource(QUrl(proxy.url))
        # 关闭时：
        proxy.close()
    """

    def __init__(self, disk_id: int, path: str, filename: str):
        self.disk_id = disk_id
        self.path = path
        self.filename = filename

        self._token: str = uuid.uuid4().hex
        self._server: Optional[_TokenTCPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._closed: bool = False
        self._port: int = 0

        # 元数据缓存（首次请求后填充，避免每次 GET 多一次元数据往返）
        self._file_size: int = 0
        self._content_type: str = "application/octet-stream"
        # v1.4.0：time 修复流模式与媒体时长（秒），供 GET 线性时间 seek 映射
        self._stream_mode: str = "byte"
        self._duration: float = 0.0
        self._meta_fetched: bool = False
        self._meta_lock = threading.Lock()
        # v1.4.1：UI 主动 seek 的目标秒（一次性，GET time 分支消费后归零）
        self._seek_seconds: float = 0.0
        # v1.4.1：seek 版本号，拼进 URL query 强制 QMediaPlayer 重新拉流
        self._seek_version: int = 0

    @property
    def url(self) -> str:
        """本地代理 URL（含一次性 token 与 seek 版本号）"""
        return f"http://127.0.0.1:{self._port}/{self._token}?v={self._seek_version}"

    @property
    def duration(self) -> float:
        """
        媒体时长（秒），供 UI 显示总时长与 seek 映射。

        v1.4.1：QMediaPlayer 的 FFmpeg 后端对空 moov 的流式 fMP4 无法从 moof 提前
        推算出总时长（边下边播时 duration 一直为 0 或只有已下载时长），故 UI 层
        改用 meta 的 duration 直接设置进度条，不再依赖 QMediaPlayer 的 duration。
        首次 HEAD/GET 拉取 meta 后可用；未拉取时为 0。
        """
        return self._duration

    def seek(self, seconds: float) -> None:
        """
        UI 主动 seek：设置下次 GET 的时间起点（仅 time 修复流生效）。

        调用后播放器需重新 setSource / 重新 GET，本方法把目标秒缓存为一次性值，
        GET 的 time 分支消费后归零；之后播放器内部的字节 Range 仍走线性映射。
        """
        self._seek_seconds = max(0.0, seconds)
        # 递增版本号：QMediaPlayer 对相同 URL 会复用缓存不再发 GET，版本号变化
        # 强制其重新请求，从而消费 _seek_seconds 触发服务端 -ss 重拉
        self._seek_version += 1

    def start(self) -> None:
        """启动代理服务器（在独立守护线程中运行）"""
        self._server = _TokenTCPServer(
            ("127.0.0.1", 0),
            _ProxyHandler,
            self,
        )
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "StreamProxy 已启动: port=%d filename=%s",
            self._port,
            self.filename[:20],
        )

    def close(self) -> None:
        """停止代理，不再接受新连接；活跃流会在下一次写入时因 _closed=True 退出"""
        self._closed = True
        if self._server is not None:
            threading.Thread(
                target=self._server.shutdown,
                daemon=True,
            ).start()
            self._server = None
        logger.info("StreamProxy 已关闭: filename=%s", self.filename[:20])
