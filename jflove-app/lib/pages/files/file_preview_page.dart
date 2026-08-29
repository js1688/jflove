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
  // v1.4.2：修复产物验证播放（>0 时媒体走该任务产物流）
  final int repairTaskId;

  const FilePreviewPage({
    super.key,
    required this.diskId,
    required this.path,
    required this.name,
    this.repairTaskId = 0,
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
        repairTaskId: repairTaskId,
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
        repairTaskId: repairTaskId,
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

/// 视频/音频预览 - 通过本地 StreamProxy 流式播放，边下边播、原生 seek
/// 对标桌面端 StreamProxy + ExoPlayer
/// v1.4.2：纯 byte 模式（服务端无实时修复流）；seek 恢复 ExoPlayer 原生字节
/// range；损坏文件弹「立即修复」引导；repairTaskId>0 时验证播放修复产物。
class _MediaPreview extends ConsumerStatefulWidget {
  final int diskId;
  final String path;
  final String name;
  final bool isVideo;
  // v1.4.2：修复产物验证播放（>0 时经代理拉取修复任务产物流）
  final int repairTaskId;

  const _MediaPreview({
    required this.diskId,
    required this.path,
    required this.name,
    required this.isVideo,
    this.repairTaskId = 0,
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
  // v1.4.2：损坏文件标志（[MEDIA_NEEDS_REPAIR]）——弹「立即修复」引导
  bool _needsRepair = false;

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

      // 2. 启动本地 StreamProxy（v1.4.2 纯 byte 模式）
      final proxy = StreamProxy(
        diskId: widget.diskId,
        path: _pathDir,
        filename: widget.name,
        sessionKey: session.sessionKey!,
        sessionId: session.sessionId,
        serverUrl: session.serverUrl,
        jwtToken: session.token,
        repairTaskId: widget.repairTaskId,
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
      if (mounted) {
        setState(() => _isInitialized = true);
      }
      controller.play();
    } catch (e) {
      // 损坏文件（服务端 415 [MEDIA_NEEDS_REPAIR]）→ 修复引导而非普通错误
      final msg = e.toString();
      if (msg.contains('[MEDIA_NEEDS_REPAIR]') ||
          (_proxy?.lastError.contains('[MEDIA_NEEDS_REPAIR]') ?? false)) {
        _needsRepair = true;
      }
      // 出错时清理 proxy
      _proxy?.close();
      _proxy = null;
      if (mounted) {
        setState(() {
          _hasError = true;
          _errorMessage = msg;
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

  /// v1.4.2：恢复 ExoPlayer 原生 seek（字节 range 直通）。
  /// 修复 v1.4.1 回归：此前无条件重建 controller 重拉流，健康文件
  /// 拖拽后从头播放且时间显示错位。
  Future<void> _seekTo(double targetSeconds) async {
    final controller = _controller;
    if (controller == null || !controller.value.isInitialized) return;
    await controller.seekTo(
      Duration(milliseconds: (targetSeconds * 1000).round()),
    );
  }

  /// 损坏文件「立即修复」：调修复接口创建任务
  Future<void> _repairNow() async {
    try {
      final repair = ref.read(repairServiceProvider);
      await repair.createTask(widget.diskId, _pathDir, widget.name);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('已加入修复队列，可在「修复中心」查看进度')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('发起修复失败：$e')),
        );
      }
    }
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
            Text(_needsRepair ? '该文件已损坏，无法在线播放' : '播放失败'),
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
            if (_needsRepair) ...[
              const SizedBox(height: 16),
              FilledButton.icon(
                onPressed: _repairNow,
                icon: const Icon(Icons.healing),
                label: const Text('立即修复'),
              ),
            ],
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
        // 底部控制栏（v1.4.2：时长与进度回归 ExoPlayer 原生报告）
        _MediaControls(
          controller: controller,
          onSeek: _seekTo,
        ),
      ],
    );
  }
}

/// 媒体播放控制栏（v1.4.2：时长/进度回归 ExoPlayer 原生报告）
class _MediaControls extends StatefulWidget {
  final VideoPlayerController controller;
  final Future<void> Function(double seconds) onSeek;

  const _MediaControls({
    required this.controller,
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
    // v1.4.2：byte 模式容器自带完整 moov，ExoPlayer 直接报告时长与进度
    final duration = controller.value.duration;
    final position = controller.value.position;

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
              // v1.4.2：松手才 seek（onChangeEnd），修复拖动过程连续触发
              // 重建/重拉的体验问题（v1.4.1 遗留）。onChanged 为 Slider 必需
              // 参数，拖动过程中不做任何事（不触发网络请求）。
              onChanged: (_) {},
              onChangeEnd: (v) {
                widget.onSeek(v * duration.inMilliseconds / 1000);
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
