import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../config/design_tokens.dart';
import '../../providers/album_backup_provider.dart';
import '../../services/album_backup/album_backup_service.dart';
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

  void _snack(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('相册同步'),
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(12, 8, 12, 24),
        children: [
          const _ActivityCard(),
          const SizedBox(height: 8),
          const AlbumBackupCard(),
          const SizedBox(height: 12),
          SizedBox(
            width: double.infinity,
            child: FilledButton.tonalIcon(
              onPressed: _openRemoteFolder,
              icon: const Icon(Icons.folder_open_outlined, size: 18),
              label: const Text('在文件中查看已上传'),
            ),
          ),
          const SizedBox(height: 4),
          SizedBox(
            width: double.infinity,
            child: TextButton.icon(
              onPressed: () => context.go('/transfer'),
              icon: const Icon(Icons.list_alt_outlined, size: 18),
              label: const Text('去传输任务查看上传明细'),
            ),
          ),
          const SizedBox(height: 4),
          Text(
            '本页只展示同步概况；上传明细与失败记录在「传输任务」中查看。',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 11.5, color: context.tokens.fgSubtle),
          ),
        ],
      ),
    );
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
                    style: TextStyle(fontSize: 11.5, color: context.tokens.fgSubtle),
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
