import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../config/design_tokens.dart';
import '../../models/transfer_task.dart';
import '../../providers/transfer_provider.dart';
import '../../widgets/app_card.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/error_state.dart';
import '../repair/repair_center_page.dart';

/// 传输页（v1.5.0：底部导航 6 → 5，合并「修复中心」为第二个页签）
///
/// 对标桌面端 transfer_page.py。两个页签：
///   1. 传输任务：本机上传 / 下载任务流（流式进度）
///   2. 修复中心：全平台共享的媒体修复任务（原 `/repair` 路由仍保留）
class TransferPage extends ConsumerStatefulWidget {
  const TransferPage({super.key, this.initialTab = 0});

  /// 初始页签（`/repair` 旧入口会带 1 进来）
  final int initialTab;

  @override
  ConsumerState<TransferPage> createState() => _TransferPageState();
}

class _TransferPageState extends ConsumerState<TransferPage>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs;

  @override
  void initState() {
    super.initState();
    _tabs = TabController(
      length: 2,
      vsync: this,
      initialIndex: widget.initialTab.clamp(0, 1),
    );
    _tabs.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return Scaffold(
      appBar: AppBar(
        title: const Text('传输任务'),
        actions: [
          // 只在前两个页签（传输任务）上提供「清除已完成」
          if (_tabs.index == 0)
            IconButton(
              icon: const Icon(Icons.clear_all_rounded),
              tooltip: '清除已完成',
              onPressed: _clearFinished,
            ),
          IconButton(
            icon: const Icon(Icons.refresh_rounded),
            tooltip: '刷新',
            onPressed: () => setState(() {}),
          ),
        ],
        bottom: TabBar(
          controller: _tabs,
          labelColor: t.fgDefault,
          unselectedLabelColor: t.fgMuted,
          indicatorColor: AppTokens.brand500,
          indicatorSize: TabBarIndicatorSize.label,
          dividerColor: t.borderSubtle,
          labelStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
          unselectedLabelStyle: const TextStyle(fontSize: 14),
          tabs: const [
            Tab(text: '传输任务'),
            Tab(text: '修复中心'),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabs,
        children: const [
          _TransferTaskList(),
          RepairCenterContent(),
        ],
      ),
    );
  }

  void _clearFinished() {
    final removed = ref.read(transferServiceProvider).clearFinished();
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(removed > 0 ? '已清除 $removed 个已结束任务' : '没有可清除的任务'),
        duration: const Duration(seconds: 2),
      ),
    );
    setState(() {});
  }
}

/// 传输任务列表（页签一）
class _TransferTaskList extends ConsumerWidget {
  const _TransferTaskList();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final taskAsync = ref.watch(transferTaskStreamProvider);

    return taskAsync.when(
      data: (tasks) {
        if (tasks.isEmpty) {
          return const EmptyState(
            icon: Icons.cloud_download_outlined,
            title: '暂无传输任务',
            subtitle: '在「文件」页面上传或下载文件',
          );
        }

        final stats = ref.watch(transferStatsProvider);
        return Column(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
              child: Row(
                children: [
                  _StatPill(
                    label: '共 ${stats['total'] ?? 0}',
                    tone: BadgeTone.brand,
                  ),
                  const SizedBox(width: 6),
                  _StatPill(
                    label: '进行中 ${stats['running'] ?? 0}',
                    tone: BadgeTone.warning,
                  ),
                  const SizedBox(width: 6),
                  _StatPill(
                    label: '完成 ${stats['completed'] ?? 0}',
                    tone: BadgeTone.success,
                  ),
                  const SizedBox(width: 6),
                  _StatPill(
                    label: '失败 ${stats['failed'] ?? 0}',
                    tone: BadgeTone.danger,
                  ),
                ],
              ),
            ),
            Expanded(
              child: ListView.builder(
                padding: const EdgeInsets.fromLTRB(12, 8, 12, 24),
                itemCount: tasks.length,
                itemBuilder: (ctx, i) => _TransferTaskCard(task: tasks[i]),
              ),
            ),
          ],
        );
      },
      loading: () => const ListSkeleton(),
      error: (e, _) => ErrorState(
        title: '传输任务加载失败',
        message: '$e',
        onRetry: () => ref.invalidate(transferTaskStreamProvider),
      ),
    );
  }
}

/// 统计小胶囊
class _StatPill extends StatelessWidget {
  const _StatPill({required this.label, required this.tone});

  final String label;
  final BadgeTone tone;

  @override
  Widget build(BuildContext context) => AppBadge(label, tone: tone);
}

/// 传输任务卡片
///
/// 对标桌面端 TransferTaskRow。
class _TransferTaskCard extends ConsumerWidget {
  final TransferTask task;

  const _TransferTaskCard({required this.task});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.tokens;
    final isUpload = task.kind == TaskKind.upload;
    final isRunning =
        task.status == TaskStatus.running ||
        task.status == TaskStatus.pending ||
        task.status == TaskStatus.hashing;

    return AppCard(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                isUpload ? Icons.upload_rounded : Icons.download_rounded,
                size: 18,
                color: AppTokens.brand600,
              ),
              SizedBox(width: t.s2),
              Expanded(
                child: Text(
                  task.filename,
                  style: TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                    color: t.fgDefault,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              SizedBox(width: t.s2),
              AppBadge(_statusText(task.status), tone: _statusTone(task.status)),
              if (isRunning) ...[
                SizedBox(width: t.s1),
                SizedBox(
                  width: 26,
                  height: 26,
                  child: IconButton(
                    icon: const Icon(Icons.close_rounded, size: 15),
                    tooltip: '取消任务',
                    color: t.fgMuted,
                    onPressed: () {
                      ref.read(transferServiceProvider).cancel(task.id);
                    },
                    padding: EdgeInsets.zero,
                    visualDensity: VisualDensity.compact,
                  ),
                ),
              ],
            ],
          ),
          SizedBox(height: t.s3),
          ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: LinearProgressIndicator(
              value: task.fileSize > 0 ? task.percent / 100.0 : null,
              minHeight: 6,
              backgroundColor: t.bgSunken,
              valueColor: AlwaysStoppedAnimation<Color>(AppTokens.brand500),
            ),
          ),
          SizedBox(height: t.s2),
          Text(
            '${_formatSize(task.transferred)} / ${_formatSize(task.fileSize)}'
            '（${task.percent}%）',
            style: TextStyle(fontSize: 12, color: t.fgMuted),
          ),
          if (task.error != null) ...[
            SizedBox(height: t.s1),
            Text(
              '错误：${task.error}',
              style: const TextStyle(
                color: AppTokens.danger500,
                fontSize: 12,
                height: 1.5,
              ),
            ),
          ],
          // 下载完成且非空路径时，显示保存位置
          if (task.kind == TaskKind.download &&
              task.status == TaskStatus.completed &&
              task.localPath.isNotEmpty) ...[
            SizedBox(height: t.s1),
            Text(
              '保存至：${task.localPath}',
              style: const TextStyle(
                color: AppTokens.success500,
                fontSize: 11.5,
                height: 1.5,
              ),
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
          ],
        ],
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

  BadgeTone _statusTone(TaskStatus status) {
    switch (status) {
      case TaskStatus.completed:
        return BadgeTone.success;
      case TaskStatus.failed:
        return BadgeTone.danger;
      case TaskStatus.cancelled:
        return BadgeTone.neutral;
      case TaskStatus.pending:
      case TaskStatus.hashing:
      case TaskStatus.running:
        return BadgeTone.brand;
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
