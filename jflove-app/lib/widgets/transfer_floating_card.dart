import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../models/transfer_task.dart';
import '../providers/transfer_provider.dart';

/// 传输任务浮动卡片
///
/// 桌面端无等效组件（桌面端传输任务在独立页面）。
/// 显示在首页底部，展示进行中的传输任务进度。
class TransferFloatingCard extends ConsumerWidget {
  const TransferFloatingCard({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final taskAsync = ref.watch(transferTaskStreamProvider);

    return taskAsync.when(
      data: (tasks) {
        final running = tasks
            .where(
              (t) =>
                  t.status == TaskStatus.pending ||
                  t.status == TaskStatus.running ||
                  t.status == TaskStatus.hashing,
            )
            .toList();
        if (running.isEmpty) return const SizedBox.shrink();

        final totalProgress = running.fold<int>(0, (sum, t) => sum + t.percent);
        final avgProgress = running.isNotEmpty
            ? (totalProgress / running.length).round()
            : 0;

        return SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Material(
              elevation: 4,
              borderRadius: BorderRadius.circular(12),
              color: Theme.of(context).colorScheme.primaryContainer,
              child: InkWell(
                borderRadius: BorderRadius.circular(12),
                onTap: () => context.push('/transfer'),
                child: Padding(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 12,
                  ),
                  child: Row(
                    children: [
                      Icon(
                        Icons.cloud_download,
                        color: Theme.of(context).colorScheme.onPrimaryContainer,
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text(
                              '${running.length} 个任务进行中',
                              style: TextStyle(
                                color: Theme.of(
                                  context,
                                ).colorScheme.onPrimaryContainer,
                                fontWeight: FontWeight.w500,
                              ),
                            ),
                            const SizedBox(height: 4),
                            LinearProgressIndicator(
                              value: avgProgress / 100.0,
                              backgroundColor: Theme.of(
                                context,
                              ).colorScheme.surface,
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(width: 8),
                      Icon(
                        Icons.chevron_right,
                        color: Theme.of(context).colorScheme.onPrimaryContainer,
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        );
      },
      loading: () => const SizedBox.shrink(),
      error: (e, _) => const SizedBox.shrink(),
    );
  }
}
