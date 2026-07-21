import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:path_provider/path_provider.dart';
import 'package:video_player/video_player.dart';

import '../../providers/file_provider.dart';

/// 文件预览页面
///
/// 支持格式：
/// - 图片：jpg, jpeg, png, gif, webp, bmp, svg
/// - 文本：md, txt, json, xml, yaml, yml, log, csv, html, css, js, dart, py, java, ts, go, rs, sh
/// - 视频：mp4, mov, avi, mkv, webm, 3gp, flv
/// - 音频：mp3, wav, ogg, flac, aac, m4a, wma
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
    // 图片
    if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg'].contains(ext)) {
      return _ImagePreview(diskId: diskId, path: path);
    }

    // 视频
    if (['mp4', 'mov', 'avi', 'mkv', 'webm', '3gp', 'flv'].contains(ext)) {
      return _MediaPreview(
        diskId: diskId,
        path: path,
        name: name,
        isVideo: true,
      );
    }

    // 音频
    if (['mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a', 'wma'].contains(ext)) {
      return _MediaPreview(
        diskId: diskId,
        path: path,
        name: name,
        isVideo: false,
      );
    }

    // 文本/代码
    if ([
      'md',
      'txt',
      'json',
      'xml',
      'yaml',
      'yml',
      'log',
      'csv',
      'html',
      'css',
      'js',
      'dart',
      'py',
      'java',
      'ts',
      'go',
      'rs',
      'sh',
    ].contains(ext)) {
      return _TextPreview(diskId: diskId, path: path, name: name);
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

/// 视频/音频预览 - 下载到临时文件后使用 VideoPlayer 播放
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
  bool _isInitialized = false;
  bool _hasError = false;
  String _errorMessage = '';

  @override
  void initState() {
    super.initState();
    _loadAndPlay();
  }

  Future<void> _loadAndPlay() async {
    try {
      // 1. 下载加密流到临时文件
      final fs = ref.read(fileServiceProvider);
      final stream = await fs.download(widget.diskId, widget.path);

      final tempDir = await getTemporaryDirectory();
      final tempFile = File('${tempDir.path}/${widget.name}');
      final sink = tempFile.openWrite();

      await for (final chunk in stream) {
        sink.add(chunk);
      }
      await sink.close();

      // 2. 用 VideoPlayer 播放本地文件
      final controller = VideoPlayerController.file(tempFile);
      _controller = controller;

      await controller.initialize();
      if (mounted) {
        setState(() => _isInitialized = true);
      }
      controller.play();
    } catch (e) {
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
    _controller?.dispose();
    super.dispose();
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
        _MediaControls(controller: controller),
      ],
    );
  }
}

/// 媒体播放控制栏
class _MediaControls extends StatefulWidget {
  final VideoPlayerController controller;

  const _MediaControls({required this.controller});

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
              onChanged: (v) {
                final pos = Duration(
                  milliseconds: (v * duration.inMilliseconds).round(),
                );
                controller.seekTo(pos);
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

/// 文本预览 - 下载到内存后显示
class _TextPreview extends ConsumerWidget {
  final int diskId;
  final String path;
  final String name;

  const _TextPreview({
    required this.diskId,
    required this.path,
    required this.name,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return FutureBuilder<String>(
      future: _loadText(ref),
      builder: (ctx, snap) {
        if (snap.connectionState == ConnectionState.waiting) {
          return const Center(child: CircularProgressIndicator());
        }
        if (snap.hasError || snap.data == null) {
          return Center(child: Text('加载失败: ${snap.error}'));
        }

        final content = snap.data!;
        if (name.endsWith('.md')) {
          return Markdown(
            data: content,
            selectable: true,
            padding: const EdgeInsets.all(16),
          );
        }

        return SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: SelectableText(
            content,
            style: const TextStyle(fontFamily: 'monospace', fontSize: 13),
          ),
        );
      },
    );
  }

  Future<String> _loadText(WidgetRef ref) async {
    final fs = ref.read(fileServiceProvider);
    final stream = await fs.download(diskId, path);
    final bytes = <int>[];
    await for (final chunk in stream) {
      bytes.addAll(chunk);
    }
    return utf8.decode(bytes);
  }
}
