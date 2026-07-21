import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../providers/file_provider.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/loading_indicator.dart';

class FileListPage extends ConsumerWidget {
  const FileListPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final diskListAsync = ref.watch(accessibleDiskListProvider);

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
            padding: const EdgeInsets.all(12),
            itemCount: disks.length,
            itemBuilder: (ctx, i) {
              final disk = disks[i];
              return Card(
                margin: const EdgeInsets.only(bottom: 12),
                child: ListTile(
                  leading: CircleAvatar(
                    backgroundColor: Colors.blue.shade100,
                    child: Icon(Icons.cloud, color: Colors.blue.shade700),
                  ),
                  title: Text(disk.name),
                  subtitle: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      if (disk.path.isNotEmpty) Text('路径: ${disk.path}'),
                      Row(
                        children: [
                          if (disk.canWrite)
                            const Chip(
                              label: Text('可写', style: TextStyle(fontSize: 11)),
                              visualDensity: VisualDensity.compact,
                              padding: EdgeInsets.zero,
                              materialTapTargetSize:
                                  MaterialTapTargetSize.shrinkWrap,
                            )
                          else
                            Chip(
                              label: Text('只读', style: TextStyle(fontSize: 11)),
                              visualDensity: VisualDensity.compact,
                              padding: EdgeInsets.zero,
                              materialTapTargetSize:
                                  MaterialTapTargetSize.shrinkWrap,
                              backgroundColor: Colors.grey.shade200,
                            ),
                        ],
                      ),
                    ],
                  ),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => context.push('/files/${disk.id}'),
                ),
              );
            },
          );
        },
        loading: () => const LoadingIndicator(),
        error: (e, _) => Center(child: Text('加载失败: $e')),
      ),
    );
  }
}
