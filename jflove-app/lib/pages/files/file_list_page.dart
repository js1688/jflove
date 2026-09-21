import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../config/design_tokens.dart';
import '../../models/virtual_disk.dart';
import '../../providers/file_provider.dart';
import '../../widgets/app_card.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/error_state.dart';

/// 虚拟磁盘列表页（文件管理入口）
///
/// v1.5.0：清除原先的蓝色图标底盘、蓝色云图标与灰色「只读」标签等硬编码色，
/// 统一走设计令牌；卡片换 `AppCard`，加载态换 `ListSkeleton`（避免布局跳动），
/// 错误态换 `ErrorState` 并给出重试入口，视觉与 Web / 桌面端对齐（需求 AC-4/AC-6）。
class FileListPage extends ConsumerWidget {
  const FileListPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final diskListAsync = ref.watch(accessibleDiskListProvider);
    final t = context.tokens;

    return Scaffold(
      appBar: AppBar(title: const Text('文件管理')),
      body: diskListAsync.when(
        data: (disks) {
          if (disks.isEmpty) {
            return const EmptyState(
              icon: Icons.folder_off,
              title: '暂无可用磁盘',
              subtitle: '请联系管理员分配磁盘权限',
            );
          }
          return ListView.builder(
            padding: EdgeInsets.fromLTRB(t.s3, t.s3, t.s3, t.s6),
            itemCount: disks.length,
            itemBuilder: (ctx, i) => _DiskCard(disk: disks[i]),
          );
        },
        loading: () => const ListSkeleton(),
        error: (e, _) => ErrorState(
          message: '$e',
          onRetry: () => ref.invalidate(accessibleDiskListProvider),
        ),
      ),
    );
  }
}

/// 单个磁盘卡片：品牌色图标底盘 + 名称 + 路径 + 读写徽标 + 前进箭头
class _DiskCard extends StatelessWidget {
  const _DiskCard({required this.disk});

  final VirtualDisk disk;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return AppCard(
      margin: EdgeInsets.only(bottom: t.s2),
      padding: EdgeInsets.all(t.s3),
      onTap: () => context.push('/files/${disk.id}'),
      child: Row(
        children: [
          // 图标底盘对齐统一组件的「品牌色调」：bgActive 底 + brand600 前景
          Container(
            width: 36,
            height: 36,
            decoration: BoxDecoration(
              color: t.bgActive,
              borderRadius: BorderRadius.circular(t.rMd),
            ),
            child: const Icon(Icons.cloud, size: 18, color: AppTokens.brand600),
          ),
          SizedBox(width: t.s3),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  disk.name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 13.5,
                    fontWeight: FontWeight.w600,
                    color: t.fgDefault,
                  ),
                ),
                if (disk.path.isNotEmpty) ...[
                  SizedBox(height: t.s1),
                  Text(
                    '路径: ${disk.path}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 11.5, color: t.fgMuted),
                  ),
                ],
              ],
            ),
          ),
          SizedBox(width: t.s2),
          // 读写权限徽标：可写 = 成功色，只读 = 中性色
          AppBadge(
            disk.canWrite ? '可写' : '只读',
            tone: disk.canWrite ? BadgeTone.success : BadgeTone.neutral,
          ),
          SizedBox(width: t.s1),
          Icon(Icons.chevron_right, size: 18, color: t.fgSubtle),
        ],
      ),
    );
  }
}
