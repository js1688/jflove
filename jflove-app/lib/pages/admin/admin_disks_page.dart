import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../providers/admin_provider.dart';
import '../../providers/file_provider.dart';
import '../../widgets/loading_indicator.dart';

/// 磁盘管理页（admin）
///
/// 对标桌面端 disk_page.py。
class AdminDisksPage extends ConsumerWidget {
  const AdminDisksPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final diskListAsync = ref.watch(adminDiskListProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('磁盘管理'),
        actions: [
          IconButton(
            icon: const Icon(Icons.add),
            tooltip: '添加磁盘',
            onPressed: () => _addDisk(context, ref),
          ),
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: '刷新',
            onPressed: () => ref.invalidate(adminDiskListProvider),
          ),
        ],
      ),
      body: diskListAsync.when(
        data: (disks) => ListView.builder(
          padding: const EdgeInsets.all(12),
          itemCount: disks.length,
          itemBuilder: (ctx, i) {
            final disk = disks[i];
            return Card(
              margin: const EdgeInsets.only(bottom: 8),
              child: ListTile(
                title: Text('${disk.id}. ${disk.name}'),
                subtitle: Text('路径: ${disk.path}'),
                trailing: PopupMenuButton<String>(
                  onSelected: (action) {
                    if (action == 'edit') {
                      _editDisk(context, ref, disk);
                    } else if (action == 'delete') {
                      _deleteDisk(context, ref, disk);
                    }
                  },
                  itemBuilder: (_) => [
                    const PopupMenuItem(
                      value: 'edit',
                      child: ListTile(
                        leading: Icon(Icons.edit),
                        title: Text('编辑'),
                      ),
                    ),
                    const PopupMenuItem(
                      value: 'delete',
                      child: ListTile(
                        leading: Icon(Icons.delete, color: Colors.red),
                        title: Text('删除', style: TextStyle(color: Colors.red)),
                      ),
                    ),
                  ],
                ),
              ),
            );
          },
        ),
        loading: () => const LoadingIndicator(),
        error: (e, _) => Center(child: Text('加载失败: $e')),
      ),
    );
  }

  void _addDisk(BuildContext context, WidgetRef ref) {
    final nameCtrl = TextEditingController();
    final pathCtrl = TextEditingController();
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('添加虚拟磁盘'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: nameCtrl,
              decoration: const InputDecoration(labelText: '磁盘名称'),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: pathCtrl,
              decoration: const InputDecoration(
                labelText: '真实路径',
                hintText: '/data/docs',
              ),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () async {
              Navigator.pop(ctx);
              try {
                await ref
                    .read(diskServiceProvider)
                    .createDisk(nameCtrl.text.trim(), pathCtrl.text.trim());
                ref.invalidate(adminDiskListProvider);
              } catch (e) {
                if (context.mounted) {
                  ScaffoldMessenger.of(
                    context,
                  ).showSnackBar(SnackBar(content: Text('添加失败: $e')));
                }
              }
            },
            child: const Text('创建'),
          ),
        ],
      ),
    );
  }

  void _editDisk(BuildContext context, WidgetRef ref, disk) {
    final nameCtrl = TextEditingController(text: disk.name);
    final pathCtrl = TextEditingController(text: disk.path);
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('编辑虚拟磁盘'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: nameCtrl,
              decoration: const InputDecoration(labelText: '磁盘名称'),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: pathCtrl,
              decoration: const InputDecoration(labelText: '真实路径'),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () async {
              Navigator.pop(ctx);
              try {
                await ref
                    .read(diskServiceProvider)
                    .updateDisk(
                      disk.id,
                      nameCtrl.text.trim(),
                      pathCtrl.text.trim(),
                    );
                ref.invalidate(adminDiskListProvider);
              } catch (e) {
                if (context.mounted) {
                  ScaffoldMessenger.of(
                    context,
                  ).showSnackBar(SnackBar(content: Text('更新失败: $e')));
                }
              }
            },
            child: const Text('保存'),
          ),
        ],
      ),
    );
  }

  void _deleteDisk(BuildContext context, WidgetRef ref, disk) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('确认删除'),
        content: Text('确定要删除磁盘「${disk.name}」吗？\n相关权限配置将同步清除。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () async {
              Navigator.pop(ctx);
              try {
                await ref.read(diskServiceProvider).deleteDisk(disk.id);
                ref.invalidate(adminDiskListProvider);
              } catch (e) {
                if (context.mounted) {
                  ScaffoldMessenger.of(
                    context,
                  ).showSnackBar(SnackBar(content: Text('删除失败: $e')));
                }
              }
            },
            child: const Text('删除'),
          ),
        ],
      ),
    );
  }
}
