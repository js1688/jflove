import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../config/design_tokens.dart';
import '../../providers/album_backup_provider.dart';
import '../../services/album_backup/album_backup_service.dart';
import '../../services/album_backup/album_backup_store.dart';
import '../files/disk_browser_page.dart';
import '../settings/album_backup_card.dart';

/// 相册同步一级页（v1.8.3 / F43）
///
/// 结构：活动卡（阶段/进度/停止）→ 同步设置卡片（复用 `AlbumBackupCard`）→
/// 最近上传（最多 20 条）→ 诊断入口。
class AlbumBackupPage extends ConsumerStatefulWidget {
  const AlbumBackupPage({super.key});

  @override
  ConsumerState<AlbumBackupPage> createState() => _AlbumBackupPageState();
}

class _AlbumBackupPageState extends ConsumerState<AlbumBackupPage> {
  StreamSubscription<AlbumBackupActivity>? _sub;
  Future<List<AlbumBackupRecord>>? _recent;

  @override
  void initState() {
    super.initState();
    final service = ref.read(albumBackupServiceProvider);
    _recent = service.recentUploads();
    _sub = service.activityStream.listen((activity) {
      if (!mounted) return;
      if (activity.phase == AlbumBackupPhase.idle) {
        setState(() => _recent = service.recentUploads());
      }
    });
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }

  Future<void> _openRemoteFolder() async {
    final service = ref.read(albumBackupServiceProvider);
    final config = service.config;
    if (!config.isConfigured) {
      _snack('请先选择远端目标目录');
      return;
    }
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => DiskBrowserPage(
          diskId: config.diskId,
          initialPath: config.remotePath,
        ),
      ),
    );
  }

  Future<void> _copyDiagnostics() async {
    final service = ref.read(albumBackupServiceProvider);
    final a = service.activity;
    final p = service.progress;
    final failures = await service.failedEntries();
    final buffer = StringBuffer()
      ..writeln('JFLove 1.8.3 (Android)')
      ..writeln('阶段：${a.phase.name} · ${a.message}')
      ..writeln('计数：总数 ${p.total} / 已备份 ${p.done} / 上传中 ${p.uploading} '
          '/ 失败 ${p.failed} / 不在范围 ${p.outOfScope} / 远端跳过 ${p.skippedExisting}')
      ..writeln('本轮：${a.processed}/${a.total}');
    if (failures.isNotEmpty) {
      buffer.writeln('失败样例：');
      for (final f in failures.take(10)) {
        buffer.writeln('- ${f.assetId}：${f.reason}（${f.triedAt}）');
      }
    }
    await Clipboard.setData(ClipboardData(text: buffer.toString()));
    _snack('诊断信息已复制（不含 token/路径）');
  }

  void _snack(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('相册同步'),
        actions: [
          IconButton(
            tooltip: '复制诊断信息',
            onPressed: _copyDiagnostics,
            icon: const Icon(Icons.bug_report_outlined),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(12, 8, 12, 24),
        children: [
          const _ActivityCard(),
          const SizedBox(height: 8),
          const AlbumBackupCard(),
          const SizedBox(height: 12),
          Row(
            children: [
              FilledButton.tonalIcon(
                onPressed: _openRemoteFolder,
                icon: const Icon(Icons.folder_open_outlined, size: 18),
                label: const Text('在文件中查看'),
              ),
              const SizedBox(width: 8),
              OutlinedButton.icon(
                onPressed: () => setState(() {
                  _recent = ref.read(albumBackupServiceProvider).recentUploads();
                }),
                icon: const Icon(Icons.refresh, size: 18),
                label: const Text('刷新'),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Text(
            '最近上传',
            style: TextStyle(
              fontSize: 13,
              fontWeight: FontWeight.w600,
              color: context.tokens.fgDefault,
            ),
          ),
          const SizedBox(height: 6),
          FutureBuilder<List<AlbumBackupRecord>>(
            future: _recent,
            builder: (context, snapshot) {
              if (snapshot.connectionState == ConnectionState.waiting) {
                return const Padding(
                  padding: EdgeInsets.symmetric(vertical: 16),
                  child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
                );
              }
              final rows = snapshot.data ?? const <AlbumBackupRecord>[];
              if (rows.isEmpty) {
                return Text(
                  '还没有上传记录',
                  style: TextStyle(fontSize: 12.5, color: AppTokens.light.fgSubtle),
                );
              }
              return Column(
                children: [
                  for (final row in rows)
                    ListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(Icons.check_circle_outline, size: 18),
                      title: Text(
                        row.remotePath.split('/').last,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontSize: 13),
                      ),
                      subtitle: Text(
                        '${_sizeText(row.size)} · ${row.backedAt}',
                        style: const TextStyle(fontSize: 11.5),
                      ),
                    ),
                ],
              );
            },
          ),
        ],
      ),
    );
  }

  String _sizeText(int size) {
    if (size >= 1 << 30) return '${(size / (1 << 30)).toStringAsFixed(1)} GB';
    if (size >= 1 << 20) return '${(size / (1 << 20)).toStringAsFixed(1)} MB';
    if (size >= 1 << 10) return '${(size / (1 << 10)).toStringAsFixed(1)} KB';
    return '$size B';
  }
}

/// 活动卡：阶段/进度/停止（v1.8.3）
class _ActivityCard extends ConsumerWidget {
  const _ActivityCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final service = ref.read(albumBackupServiceProvider);
    return StreamBuilder<AlbumBackupActivity>(
      stream: service.activityStream,
      initialData: service.activity,
      builder: (context, snapshot) {
        final a = snapshot.data ?? AlbumBackupActivity.idle;
        final busy = a.phase != AlbumBackupPhase.idle;
        final value = a.total > 0 ? (a.processed / a.total).clamp(0.0, 1.0) : null;
        return Card(
          margin: EdgeInsets.zero,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(14, 12, 10, 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(
                      busy ? Icons.sync : Icons.check_circle_outline,
                      size: 18,
                      color: busy ? AppTokens.brand500 : AppTokens.light.fgMuted,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        a.message,
                        style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
                      ),
                    ),
                    if (a.cancellable)
                      TextButton(
                        onPressed: () => service.cancelScan(),
                        child: const Text('停止'),
                      ),
                  ],
                ),
                if (busy) ...[
                  const SizedBox(height: 6),
                  LinearProgressIndicator(value: value),
                  const SizedBox(height: 6),
                  Text(
                    a.total > 0 ? '${a.processed} / ${a.total}' : '正在处理…',
                    style: TextStyle(fontSize: 11.5, color: AppTokens.light.fgSubtle),
                  ),
                ],
              ],
            ),
          ),
        );
      },
    );
  }
}
