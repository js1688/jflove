import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/transfer_task.dart';
import '../../providers/transfer_provider.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/loading_indicator.dart';

/// 传输任务列表页
///
/// 对标桌面端 transfer_page.py。
class TransferPage extends ConsumerWidget {
  const TransferPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final taskAsync = ref.watch(transferTaskStreamProvider);
    final theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(
        title: const Text('传输任务'),
        actions: [
          IconButton(
            icon: const Icon(Icons.clear_all),
            tooltip: '清除已完成',
            onPressed: () {
              ref.read(transferServiceProvider).clearFinished();
            },
          ),
        ],
      ),
      body: taskAsync.when(
        data: (tasks) {
          if (tasks.isEmpty) {
            return const EmptyState(
              icon: Icons.cloud_download,
              title: '暂无传输任务',
              subtitle: '在「文件」页面上传或下载文件',
            );
          }

          final stats = ref.read(transferServiceProvider).stats();
          return Column(
            children: [
              // 统计行
              Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 8,
                ),
                decoration: BoxDecoration(
                  color: theme.colorScheme.surfaceContainerHighest.withAlpha(
                    60,
                  ),
                ),
                child: Row(
                  children: [
                    _statChip(
                      '共 ${stats['total'] ?? 0}',
                      theme.colorScheme.primary,
                    ),
                    const SizedBox(width: 8),
                    _statChip('进行中 ${stats['running'] ?? 0}', Colors.orange),
                    const SizedBox(width: 8),
                    _statChip('完成 ${stats['completed'] ?? 0}', Colors.green),
                    const SizedBox(width: 8),
                    _statChip('失败 ${stats['failed'] ?? 0}', Colors.red),
                  ],
                ),
              ),
              // 任务列表
              Expanded(
                child: ListView.builder(
                  padding: const EdgeInsets.all(8),
                  itemCount: tasks.length,
                  itemBuilder: (ctx, i) => _TransferTaskCard(task: tasks[i]),
                ),
              ),
            ],
          );
        },
        loading: () => const LoadingIndicator(),
        error: (e, _) => Center(child: Text('加载失败: $e')),
      ),
    );
  }

  Widget _statChip(String label, Color color) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: color.withAlpha(30),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(label, style: TextStyle(fontSize: 12, color: color)),
    );
  }
}

/// 传输任务卡片
///
/// 对标桌面端 TransferTaskRow。
class _TransferTaskCard extends StatelessWidget {
  final TransferTask task;

  const _TransferTaskCard({required this.task});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isUpload = task.kind == TaskKind.upload;
    final isRunning =
        task.status == TaskStatus.running ||
        task.status == TaskStatus.pending ||
        task.status == TaskStatus.hashing;

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  isUpload ? Icons.upload : Icons.download,
                  size: 20,
                  color: theme.colorScheme.primary,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    task.filename,
                    style: theme.textTheme.titleSmall,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                Text(
                  _statusText(task.status),
                  style: TextStyle(
                    fontSize: 12,
                    color: _statusColor(task.status),
                  ),
                ),
                if (isRunning) ...[
                  const SizedBox(width: 4),
                  SizedBox(
                    width: 20,
                    height: 20,
                    child: IconButton(
                      icon: const Icon(Icons.close, size: 16),
                      onPressed: () {
                        // TODO: cancel task
                      },
                      padding: EdgeInsets.zero,
                      visualDensity: VisualDensity.compact,
                    ),
                  ),
                ],
              ],
            ),
            const SizedBox(height: 8),
            LinearProgressIndicator(
              value: task.fileSize > 0 ? task.percent / 100.0 : null,
              backgroundColor: theme.colorScheme.surfaceContainerHighest,
            ),
            const SizedBox(height: 4),
            Text(
              '${_formatSize(task.transferred)} / ${_formatSize(task.fileSize)}（${task.percent}%）',
              style: theme.textTheme.bodySmall,
            ),
            if (task.error != null)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  '错误: ${task.error}',
                  style: TextStyle(color: Colors.red.shade700, fontSize: 12),
                ),
              ),
          ],
        ),
      ),
    );
  }

  String _statusText(TaskStatus status) {
    switch (status) {
      case TaskStatus.pending:
        return '等待中';
      case TaskStatus.hashing:
        return '校验中';
      case TaskStatus.running:
        return '传输中';
      case TaskStatus.completed:
        return '已完成';
      case TaskStatus.failed:
        return '失败';
      case TaskStatus.cancelled:
        return '已取消';
    }
  }

  Color _statusColor(TaskStatus status) {
    switch (status) {
      case TaskStatus.completed:
        return Colors.green;
      case TaskStatus.failed:
        return Colors.red;
      case TaskStatus.cancelled:
        return Colors.grey;
      default:
        return Colors.orange;
    }
  }

  String _formatSize(int size) {
    if (size < 1024) return '$size B';
    if (size < 1024 * 1024) return '${(size / 1024).toStringAsFixed(1)} KB';
    if (size < 1024 * 1024 * 1024) {
      return '${(size / (1024 * 1024)).toStringAsFixed(1)} MB';
    }
    return '${(size / (1024 * 1024 * 1024)).toStringAsFixed(1)} GB';
  }
}
