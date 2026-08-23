import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:video_player/video_player.dart';

import '../../providers/file_provider.dart';
import '../../providers/session_provider.dart';
import '../../utils/stream_proxy.dart';

/// 文件预览页面
///
/// 支持格式（对齐桌面端 preview_dialog.py）：
/// - 图片：png, jpg, jpeg, gif, bmp, webp, tiff, tif, ico, svg
/// - 视频：mp4, mkv, avi, mov, webm, flv, wmv, m4v, mpg, mpeg, ts, 3gp
/// - 音频：mp3, wav, ogg, flac, m4a, aac, wma, opus
/// - Markdown：md, markdown, mdown, mkd
/// - 文本：txt, log, csv, tsv, diff, patch, json, xml, yaml, yml, ini, toml,
///         conf, cfg, env, html, htm, css, less, scss, sass,
///         py, java, kt, scala, groovy, js, mjs, cjs, ts, jsx, tsx,
///         go, rs, c, cpp, cc, cxx, h, hpp, hh, hxx,
///         cs, swift, rb, php, lua, pl, perl, r, m,
///         sql, sh, bash, zsh, fish, bat, cmd, ps1,
///         vue, svelte, astro, gradle, properties, lock, gitignore, dockerignore
///
/// 视频/音频通过本地 StreamProxy 流式播放（边下边播、支持 seek），
/// 文本通过 /api/v1/files/stream 流式加载（首屏≤3s，大文件截断旧内容），
/// 对标桌面端 StreamProxy + StreamTextLoader。
class FilePreviewPage extends ConsumerWidget {
  final int diskId;
  final String path;
  final String name;

  const FilePreviewPage({
    super.key,
    required this.diskId,
    required this.path,
    required this.name,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ext = name.split('.').last.toLowerCase();

    return Scaffold(
      appBar: AppBar(title: Text(name)),
      body: _buildPreview(context, ref, ext),
    );
  }

  Widget _buildPreview(BuildContext context, WidgetRef ref, String ext) {
    // ── 图片 ──
    if ([
      'png',
      'jpg',
      'jpeg',
      'gif',
      'bmp',
      'webp',
      'tiff',
      'tif',
      'ico',
      'svg',
    ].contains(ext)) {
      return _ImagePreview(diskId: diskId, path: path);
    }

    // ── 视频（对齐桌面端 VIDEO_EXTS）──
    if ([
      'mp4',
      'mkv',
      'avi',
      'mov',
      'webm',
      'flv',
      'wmv',
      'm4v',
      'mpg',
      'mpeg',
      'ts',
      '3gp',
    ].contains(ext)) {
      return _MediaPreview(
        diskId: diskId,
        path: path,
        name: name,
        isVideo: true,
      );
    }

    // ── 音频（对齐桌面端 AUDIO_EXTS）──
    if ([
      'mp3',
      'wav',
      'ogg',
      'flac',
      'm4a',
      'aac',
      'wma',
      'opus',
    ].contains(ext)) {
      return _MediaPreview(
        diskId: diskId,
        path: path,
        name: name,
        isVideo: false,
      );
    }

    // ── Markdown（对齐桌面端 MARKDOWN_EXTS）──
    if (['md', 'markdown', 'mdown', 'mkd'].contains(ext)) {
      return _TextPreview(
        diskId: diskId,
        path: path,
        name: name,
        isMarkdown: true,
      );
    }

    // ── 文本/代码（对齐桌面端 TEXT_EXTS）──
    if ([
      'txt',
      'log',
      'csv',
      'tsv',
      'diff',
      'patch',
      'json',
      'xml',
      'yaml',
      'yml',
      'ini',
      'toml',
      'conf',
      'cfg',
      'env',
      'html',
      'htm',
      'css',
      'less',
      'scss',
      'sass',
      'py',
      'java',
      'kt',
      'scala',
      'groovy',
      'js',
      'mjs',
      'cjs',
      'ts',
      'jsx',
      'tsx',
      'go',
      'rs',
      'c',
      'cpp',
      'cc',
      'cxx',
      'h',
      'hpp',
      'hh',
      'hxx',
      'cs',
      'swift',
      'rb',
      'php',
      'lua',
      'pl',
      'perl',
      'r',
      'm',
      'sql',
      'sh',
      'bash',
      'zsh',
      'fish',
      'bat',
      'cmd',
      'ps1',
      'vue',
      'svelte',
      'astro',
      'gradle',
      'properties',
      'lock',
      'gitignore',
      'dockerignore',
    ].contains(ext)) {
      return _TextPreview(
        diskId: diskId,
        path: path,
        name: name,
        isMarkdown: false,
      );
    }

    // 不支持
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.preview, size: 64, color: Colors.grey.shade400),
          const SizedBox(height: 16),
          const Text('不支持预览此文件类型'),
          const SizedBox(height: 8),
          Text('请下载后查看', style: TextStyle(color: Colors.grey.shade600)),
        ],
      ),
    );
  }
}

/// 图片预览 - 下载到内存后显示
class _ImagePreview extends ConsumerWidget {
  final int diskId;
  final String path;

  const _ImagePreview({required this.diskId, required this.path});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return FutureBuilder<Uint8List>(
      future: _loadImage(ref),
      builder: (ctx, snap) {
        if (snap.connectionState == ConnectionState.waiting) {
          return const Center(child: CircularProgressIndicator());
        }
        if (snap.hasError || snap.data == null) {
          return Center(child: Text('加载失败: ${snap.error}'));
        }
        return InteractiveViewer(
          child: Center(child: Image.memory(snap.data!, fit: BoxFit.contain)),
        );
      },
    );
  }

  Future<Uint8List> _loadImage(WidgetRef ref) async {
    final fs = ref.read(fileServiceProvider);
    final stream = await fs.download(diskId, path);
    final bytes = <int>[];
    await for (final chunk in stream) {
      bytes.addAll(chunk);
    }
    return Uint8List.fromList(bytes);
  }
}

/// 视频/音频预览 - 通过本地 StreamProxy 流式播放，边下边播、支持 seek
/// 对标桌面端 StreamProxy + QMediaPlayer
class _MediaPreview extends ConsumerStatefulWidget {
  final int diskId;
  final String path;
  final String name;
  final bool isVideo;

  const _MediaPreview({
    required this.diskId,
    required this.path,
    required this.name,
    required this.isVideo,
  });

  @override
  ConsumerState<_MediaPreview> createState() => _MediaPreviewState();
}

class _MediaPreviewState extends ConsumerState<_MediaPreview> {
  VideoPlayerController? _controller;
  StreamProxy? _proxy;
  bool _isInitialized = false;
  bool _hasError = false;
  String _errorMessage = '';
  // v1.4.1：完整时长（秒，来自 meta.duration）与当前流起始偏移（seek 后 position 归零补偿）
  double _fullDurationSeconds = 0.0;
  double _seekOffsetSeconds = 0.0;

  /// 文件所在目录（磁盘内相对路径，不含文件名）
  String get _pathDir {
    final p = widget.path;
    if (!p.contains('/')) return '';
    return p.substring(0, p.lastIndexOf('/'));
  }

  @override
  void initState() {
    super.initState();
    _loadAndPlay();
  }

  Future<void> _loadAndPlay() async {
    try {
      // 1. 读取会话信息
      final session = ref.read(sessionManagerProvider);
      if (session.sessionKey == null) {
        throw Exception('会话密钥未就绪，请重新登录');
      }

      // 2. 启动本地 StreamProxy（对标桌面端 StreamProxy）
      final proxy = StreamProxy(
        diskId: widget.diskId,
        path: _pathDir,
        filename: widget.name,
        sessionKey: session.sessionKey!,
        sessionId: session.sessionId,
        serverUrl: session.serverUrl,
        jwtToken: session.token,
      );
      _proxy = proxy;
      await proxy.start();

      // 3. 用本地代理 URL 创建网络播放器（ExoPlayer 自动发 Range 请求）
      final controller = VideoPlayerController.networkUrl(
        Uri.parse(proxy.url),
        videoPlayerOptions: VideoPlayerOptions(
          mixWithOthers: true, // 音频不独占
          allowBackgroundPlayback: true, // 允许后台播放
        ),
      );
      _controller = controller;

      await controller.initialize();
      // v1.4.1：ExoPlayer 对空 moov 流式 fMP4 拿不到完整时长，用 meta.duration 兑底
      _fullDurationSeconds = proxy.duration;
      if (mounted) {
        setState(() => _isInitialized = true);
      }
      controller.play();
    } catch (e) {
      // 出错时清理 proxy
      _proxy?.close();
      _proxy = null;
      if (mounted) {
        setState(() {
          _hasError = true;
          _errorMessage = e.toString();
        });
      }
    }
  }

  @override
  void dispose() {
    _proxy?.close();
    _controller?.dispose();
    super.dispose();
  }

  /// v1.4.1：time 修复流 seek 依赖重新拉流（服务端 -ss），而非 ExoPlayer 内部 seek。
  /// 目标为绝对时间轴位置：记录偏移 → proxy.seek → 重建 controller 重新 initialize。
  Future<void> _seekTo(double targetSeconds) async {
    final proxy = _proxy;
    if (proxy == null) return;
    _seekOffsetSeconds = targetSeconds;
    proxy.seek(targetSeconds);
    final old = _controller;
    _controller = null;
    old?.dispose();
    final controller = VideoPlayerController.networkUrl(
      Uri.parse(proxy.url),
      videoPlayerOptions: VideoPlayerOptions(
        mixWithOthers: true,
        allowBackgroundPlayback: true,
      ),
    );
    _controller = controller;
    try {
      await controller.initialize();
      controller.play();
    } catch (_) {
      // seek 失败：保持现状，不打断播放
    }
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    if (_hasError) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.error_outline, size: 48, color: Colors.red.shade300),
            const SizedBox(height: 12),
            const Text('播放失败'),
            const SizedBox(height: 4),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 24),
              child: Text(
                _errorMessage,
                style: TextStyle(color: Colors.grey.shade600, fontSize: 12),
                textAlign: TextAlign.center,
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ),
      );
    }

    if (!_isInitialized) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const CircularProgressIndicator(),
            const SizedBox(height: 16),
            Text(
              widget.isVideo ? '正在加载视频…' : '正在加载音频…',
              style: TextStyle(color: Colors.grey.shade600),
            ),
          ],
        ),
      );
    }

    final controller = _controller!;
    final aspectRatio = widget.isVideo
        ? controller.value.aspectRatio
        : 1.0; // 音频固定 1:1

    return Column(
      children: [
        if (widget.isVideo)
          Expanded(
            child: Center(
              child: GestureDetector(
                onTap: () {
                  setState(() {
                    controller.value.isPlaying
                        ? controller.pause()
                        : controller.play();
                  });
                },
                child: AspectRatio(
                  aspectRatio: aspectRatio,
                  child: VideoPlayer(controller),
                ),
              ),
            ),
          )
        else
          // 音频模式 - 显示波形占位 + 播放控制
          Expanded(
            child: Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    Icons.audiotrack,
                    size: 80,
                    color: Theme.of(context).colorScheme.primary,
                  ),
                  const SizedBox(height: 16),
                  Text(
                    '正在播放音频',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 8),
                  Text(
                    widget.name,
                    style: TextStyle(color: Colors.grey.shade600),
                  ),
                ],
              ),
            ),
          ),
        // 底部控制栏
        _MediaControls(
          controller: controller,
          fullDurationSeconds: _fullDurationSeconds,
          seekOffsetSeconds: _seekOffsetSeconds,
          onSeek: _seekTo,
        ),
      ],
    );
  }
}

/// 媒体播放控制栏
class _MediaControls extends StatefulWidget {
  final VideoPlayerController controller;
  // v1.4.1：完整时长（秒，meta.duration 兑底）与当前流起始偏移
  final double fullDurationSeconds;
  final double seekOffsetSeconds;
  final Future<void> Function(double seconds) onSeek;

  const _MediaControls({
    required this.controller,
    required this.fullDurationSeconds,
    required this.seekOffsetSeconds,
    required this.onSeek,
  });

  @override
  State<_MediaControls> createState() => _MediaControlsState();
}

class _MediaControlsState extends State<_MediaControls> {
  void _onStateChanged() {
    if (mounted) setState(() {});
  }

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onStateChanged);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onStateChanged);
    super.dispose();
  }

  String _formatDuration(Duration d) {
    String twoDigits(int n) => n.toString().padLeft(2, '0');
    final min = twoDigits(d.inMinutes.remainder(60));
    final sec = twoDigits(d.inSeconds.remainder(60));
    return '$min:$sec';
  }

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    final isPlaying = controller.value.isPlaying;
    // v1.4.1：总时长用 meta.duration 兑底（ExoPlayer 对空 moov 流式拿不到）；
    // position 加偏移映射回绝对时间轴（time seek 后 position 归零的补偿）。
    final duration = Duration(
      milliseconds: (widget.fullDurationSeconds * 1000).round(),
    );
    final position = controller.value.position + Duration(
      milliseconds: (widget.seekOffsetSeconds * 1000).round(),
    );

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      decoration: BoxDecoration(
        color: Theme.of(
          context,
        ).colorScheme.surfaceContainerHighest.withAlpha(80),
      ),
      child: Row(
        children: [
          IconButton(
            icon: Icon(isPlaying ? Icons.pause : Icons.play_arrow),
            onPressed: () {
              isPlaying ? controller.pause() : controller.play();
            },
          ),
          Text(_formatDuration(position)),
          Expanded(
            child: Slider(
              value: duration.inMilliseconds > 0
                  ? (position.inMilliseconds / duration.inMilliseconds).clamp(
                      0.0,
                      1.0,
                    )
                  : 0,
              onChanged: (v) {
                final seconds = v * widget.fullDurationSeconds;
                widget.onSeek(seconds);
              },
            ),
          ),
          Text(_formatDuration(duration)),
          IconButton(
            icon: Icon(
              controller.value.volume > 0 ? Icons.volume_up : Icons.volume_off,
            ),
            onPressed: () {
              controller.setVolume(controller.value.volume > 0 ? 0 : 1);
            },
          ),
        ],
      ),
    );
  }
}

/// 文本预览 - 流式加载，对大文件截断旧内容避免内存溢出
/// 对标桌面端 StreamTextLoader
class _TextPreview extends ConsumerStatefulWidget {
  final int diskId;
  final String path;
  final String name;
  final bool isMarkdown;

  const _TextPreview({
    required this.diskId,
    required this.path,
    required this.name,
    required this.isMarkdown,
  });

  @override
  ConsumerState<_TextPreview> createState() => _TextPreviewState();
}

class _TextPreviewState extends ConsumerState<_TextPreview> {
  String _content = '';
  bool _loading = true;
  String? _error;
  bool _truncated = false;
  StreamSubscription<Uint8List>? _sub;

  /// 大文本截断阈值（对齐桌面端：单次最多保留 8MB）
  static const int _maxTextBytes = 8 * 1024 * 1024;

  /// 文件所在目录（磁盘内相对路径，不含文件名）
  String get _pathDir {
    final p = widget.path;
    if (!p.contains('/')) return '';
    return p.substring(0, p.lastIndexOf('/'));
  }

  @override
  void initState() {
    super.initState();
    _loadTextStream();
  }

  Future<void> _loadTextStream() async {
    try {
      final fs = ref.read(fileServiceProvider);
      // 使用 /api/v1/files/stream 流式拉取（对标桌面端 stream_range）
      final stream = await fs.streamRange(
        widget.diskId,
        _pathDir,
        widget.name,
        rangeStart: 0,
        rangeEnd: -1,
      );

      final bytesBuffer = BytesBuilder();
      bool firstChunk = true;
      DateTime? firstChunkTime;

      _sub = stream.listen(
        (chunk) {
          if (firstChunk) {
            // 第一帧可能是元数据 JSON（file_size / content_type）
            if (chunk.length < 2048) {
              try {
                final json =
                    jsonDecode(utf8.decode(chunk)) as Map<String, dynamic>;
                if (json.containsKey('file_size') || json['type'] == 'error') {
                  return; // 元数据帧或错误帧，跳过
                }
              } catch (_) {
                // 非 JSON，正常文本数据
              }
            }
            firstChunk = false;
            firstChunkTime = DateTime.now();
          }

          bytesBuffer.add(chunk);

          // 超过 8MB 截断旧内容（对齐桌面端行为）
          if (bytesBuffer.length > _maxTextBytes) {
            final all = bytesBuffer.takeBytes();
            // 保留最后 4MB
            final keepStart = all.length - (_maxTextBytes ~/ 2);
            bytesBuffer.add(all.sublist(keepStart));
            _truncated = true;
          }

          // 首屏渲染：收到第一批数据后立即显示
          if (firstChunkTime != null && mounted) {
            _updateContent(bytesBuffer.toBytes());
          }
        },
        onDone: () {
          if (mounted) {
            _content = _bytesToString(bytesBuffer.takeBytes());
            setState(() => _loading = false);
          }
        },
        onError: (e) {
          if (mounted) {
            setState(() {
              _error = e.toString();
              _loading = false;
            });
          }
        },
      );

      // 首屏超时兜底：3s 后如果还没收到任何数据，强制显示已有内容
      Future.delayed(const Duration(seconds: 3), () {
        if (mounted && _loading && bytesBuffer.length > 0) {
          _content = _bytesToString(bytesBuffer.takeBytes());
          setState(() => _loading = false);
        }
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = e.toString();
          _loading = false;
        });
      }
    }
  }

  String _bytesToString(Uint8List bytes) {
    try {
      return utf8.decode(bytes);
    } catch (_) {
      try {
        return latin1.decode(bytes);
      } catch (_) {
        return '[二进制文件，无法以文本方式显示]';
      }
    }
  }

  void _updateContent(Uint8List bytes) {
    _content = _bytesToString(bytes);
    if (_loading) {
      setState(() => _loading = false);
    } else {
      setState(() {}); // 更新 UI 追加内容
    }
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_loading && _content.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null && _content.isEmpty) {
      return Center(child: Text('加载失败: $_error'));
    }

    if (widget.isMarkdown) {
      return Column(
        children: [
          if (_truncated)
            Container(
              padding: const EdgeInsets.all(8),
              color: Colors.orange.shade50,
              child: const Text(
                '⚠ 文件过大，已截断旧内容',
                style: TextStyle(fontSize: 12),
              ),
            ),
          Expanded(
            child: Markdown(
              data: _content,
              selectable: true,
              padding: const EdgeInsets.all(16),
            ),
          ),
        ],
      );
    }

    return Column(
      children: [
        if (_truncated)
          Container(
            padding: const EdgeInsets.all(8),
            color: Colors.orange.shade50,
            child: const Text('⚠ 文件过大，已截断旧内容', style: TextStyle(fontSize: 12)),
          ),
        Expanded(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(16),
            child: SelectableText(
              _content,
              style: const TextStyle(fontFamily: 'monospace', fontSize: 13),
            ),
          ),
        ),
      ],
    );
  }
}
