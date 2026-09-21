import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../config/design_tokens.dart';
import '../../models/virtual_disk.dart';
import '../../providers/admin_provider.dart';
import '../../providers/file_provider.dart';
import '../../widgets/app_card.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/error_state.dart';

/// 磁盘管理页（admin）
///
/// 对标桌面端 disk_page.py。
///
/// v1.5.0：列表项换 `AppCard`（磁盘图标 + 名称 + 路径 + 三点菜单）、
/// 空态 / 加载态 / 错误态换统一组件，硬编码红色换 `AppTokens.danger500`。
/// 业务逻辑（创建 / 编辑 / 删除磁盘）与 v1.4.2 完全一致。
class AdminDisksPage extends ConsumerWidget {
  const AdminDisksPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.tokens;
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
        data: (disks) {
          if (disks.isEmpty) {
            return const EmptyState(
              icon: Icons.storage_outlined,
              title: '暂无磁盘',
              subtitle: '点击右上角「添加磁盘」创建第一个虚拟磁盘',
            );
          }
          return ListView.builder(
            padding: EdgeInsets.all(t.s3),
            itemCount: disks.length,
            itemBuilder: (ctx, i) => _buildDiskCard(context, ref, disks[i]),
          );
        },
        loading: () => const ListSkeleton(rows: 4),
        error: (e, _) => ErrorState(
          message: '$e',
          onRetry: () => ref.invalidate(adminDiskListProvider),
        ),
      ),
    );
  }

  /// 单个磁盘卡片（品牌淡底图标 + 名称 + 路径 + 三点菜单）
  Widget _buildDiskCard(BuildContext context, WidgetRef ref, VirtualDisk disk) {
    final t = context.tokens;
    return AppCard(
      margin: EdgeInsets.only(bottom: t.s2),
      padding: EdgeInsets.all(t.s3),
      child: Row(
        children: [
          Container(
            width: 40,
            height: 40,
            alignment: Alignment.center,
            decoration: BoxDecoration(color: t.bgActive, shape: BoxShape.circle),
            child: const Icon(
              Icons.dns_outlined,
              size: 20,
              color: AppTokens.brand600,
            ),
          ),
          SizedBox(width: t.s3),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${disk.id}. ${disk.name}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 13.5,
                    fontWeight: FontWeight.w600,
                    color: t.fgDefault,
                  ),
                ),
                SizedBox(height: t.s1),
                Text(
                  disk.path,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 11.5,
                    fontFamily: 'monospace',
                    color: t.fgSubtle,
                  ),
                ),
              ],
            ),
          ),
          PopupMenuButton<String>(
            tooltip: '操作菜单',
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
                  leading: Icon(Icons.edit_outlined),
                  title: Text('编辑'),
                ),
              ),
              const PopupMenuItem(
                value: 'delete',
                child: ListTile(
                  leading: Icon(
                    Icons.delete_outline_rounded,
                    color: AppTokens.danger500,
                  ),
                  title: Text(
                    '删除',
                    style: TextStyle(color: AppTokens.danger500),
                  ),
                ),
              ),
            ],
          ),
        ],
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

  void _editDisk(BuildContext context, WidgetRef ref, VirtualDisk disk) {
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

  void _deleteDisk(BuildContext context, WidgetRef ref, VirtualDisk disk) {
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
            // 危险操作：主按钮用语义色，与 Web 端 ConfirmDialog 的 danger 一致
            style: FilledButton.styleFrom(
              backgroundColor: AppTokens.danger500,
            ),
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
