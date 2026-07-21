import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/disk_permission.dart';
import '../../models/user.dart';
import '../../models/virtual_disk.dart';
import '../../providers/admin_provider.dart';
import '../../widgets/loading_indicator.dart';

/// 权限配置页（admin）
///
/// 移动端全屏单列布局：用户列表 → 点击弹出权限配置底部弹窗。
/// 使用 CheckboxListTile 替代 DataTable，提升触摸操作体验。
class AdminPermissionsPage extends ConsumerStatefulWidget {
  const AdminPermissionsPage({super.key});

  @override
  ConsumerState<AdminPermissionsPage> createState() =>
      _AdminPermissionsPageState();
}

class _AdminPermissionsPageState extends ConsumerState<AdminPermissionsPage> {
  @override
  Widget build(BuildContext context) {
    final userListAsync = ref.watch(userListProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('权限配置'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: '刷新',
            onPressed: () {
              ref.invalidate(userListProvider);
              ref.invalidate(adminDiskListProvider);
            },
          ),
        ],
      ),
      body: userListAsync.when(
        data: (users) {
          final normalUsers = users.where((u) => u.role != 'admin').toList();
          if (normalUsers.isEmpty) {
            return Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    Icons.people_outline,
                    size: 64,
                    color: Colors.grey.shade400,
                  ),
                  const SizedBox(height: 12),
                  Text('暂无普通用户', style: Theme.of(context).textTheme.bodyLarge),
                ],
              ),
            );
          }
          return ListView.separated(
            padding: const EdgeInsets.all(16),
            itemCount: normalUsers.length,
            separatorBuilder: (_, _) => const SizedBox(height: 8),
            itemBuilder: (ctx, i) {
              final user = normalUsers[i];
              return Card(
                child: ListTile(
                  leading: CircleAvatar(
                    backgroundColor: Theme.of(
                      context,
                    ).colorScheme.primaryContainer,
                    child: Text(
                      user.username.isEmpty
                          ? '?'
                          : user.username[0].toUpperCase(),
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.onPrimaryContainer,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                  title: Text(user.username),
                  subtitle: const Text('点击配置磁盘权限'),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => _showPermBottomSheet(context, ref, user),
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

  /// 弹出权限配置底部弹窗
  Future<void> _showPermBottomSheet(
    BuildContext context,
    WidgetRef ref,
    User user,
  ) async {
    // 加载该用户的当前权限
    final permMap = <int, DiskPermission>{};
    try {
      final perms = await ref
          .read(permissionServiceProvider)
          .listPermissions(user.id);
      for (final p in perms) {
        final dp = DiskPermission.fromJson(p);
        permMap[dp.virtualDiskId] = dp;
      }
    } catch (_) {
      // 加载失败不影响弹窗打开，视为无权限
    }

    if (!context.mounted) return;

    // 获取磁盘列表
    List<VirtualDisk> disks;
    try {
      disks = await ref.read(adminDiskListProvider.future);
    } catch (e) {
      if (context.mounted) {
        _showSnackBar(context, '加载磁盘列表失败: $e', isError: true);
      }
      return;
    }

    if (disks.isEmpty || !context.mounted) {
      if (context.mounted) {
        _showSnackBar(context, '暂无可用的磁盘', isError: true);
      }
      return;
    }

    // 本地权限状态（初始值从服务端加载）
    final permState = <int, _PermState>{};
    for (final disk in disks) {
      final existing = permMap[disk.id];
      permState[disk.id] = _PermState(
        canRead: existing?.canRead ?? false,
        canWrite: existing?.canWrite ?? false,
        canDelete: existing?.canDelete ?? false,
      );
    }

    if (!context.mounted) return;

    // 保存状态（跟踪是否正在保存）
    var isSaving = false;

    await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) => _PermBottomSheet(
        user: user,
        disks: disks,
        permState: permState,
        isSaving: isSaving,
        onSave: () async {
          if (isSaving) return; // 防止重复点击
          isSaving = true;
          try {
            final ps = ref.read(permissionServiceProvider);
            var changedCount = 0;
            for (final disk in disks) {
              final state = permState[disk.id]!;
              final existing = permMap[disk.id];

              final hasChanged =
                  (existing == null) ||
                  existing.canRead != state.canRead ||
                  existing.canWrite != state.canWrite ||
                  existing.canDelete != state.canDelete;

              if (hasChanged) {
                changedCount++;
                if (!state.canRead && !state.canWrite && !state.canDelete) {
                  try {
                    await ps.deletePermission(user.id, disk.id);
                  } catch (_) {
                    // 权限不存在时删除可能报错，忽略
                  }
                } else {
                  await ps.setPermission(
                    user.id,
                    disk.id,
                    canRead: state.canRead,
                    canWrite: state.canWrite,
                    canDelete: state.canDelete,
                  );
                }
              }
            }

            if (ctx.mounted) Navigator.pop(ctx);

            // 在父页面显示成功提示
            if (context.mounted) {
              _showSnackBar(
                context,
                changedCount > 0
                    ? '已保存 ${user.username} 的 $changedCount 项权限变更'
                    : '权限未作更改',
              );
            }
          } catch (e) {
            isSaving = false;
            // 刷新 UI 显示恢复后的按钮状态
            if (ctx.mounted) {
              (ctx as Element).markNeedsBuild();
            }
            if (context.mounted) {
              _showSnackBar(context, '保存失败: $e', isError: true);
            }
          }
        },
      ),
    );
  }

  void _showSnackBar(
    BuildContext context,
    String message, {
    bool isError = false,
  }) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Row(
          children: [
            Icon(
              isError ? Icons.error_outline : Icons.check_circle_outline,
              color: Colors.white,
              size: 20,
            ),
            const SizedBox(width: 12),
            Expanded(child: Text(message)),
          ],
        ),
        behavior: SnackBarBehavior.floating,
        margin: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
        duration: const Duration(seconds: 3),
      ),
    );
  }
}

/// 权限状态（本地可变，用于复选框勾选）
class _PermState {
  bool canRead;
  bool canWrite;
  bool canDelete;

  _PermState({
    required this.canRead,
    required this.canWrite,
    required this.canDelete,
  });
}

/// 权限配置底部弹窗
class _PermBottomSheet extends StatefulWidget {
  final User user;
  final List<VirtualDisk> disks;
  final Map<int, _PermState> permState;
  final bool isSaving;
  final VoidCallback onSave;

  const _PermBottomSheet({
    required this.user,
    required this.disks,
    required this.permState,
    required this.isSaving,
    required this.onSave,
  });

  @override
  State<_PermBottomSheet> createState() => _PermBottomSheetState();
}

class _PermBottomSheetState extends State<_PermBottomSheet> {
  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: EdgeInsets.only(
        bottom: MediaQuery.of(context).viewInsets.bottom,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // 拖拽手柄
          Container(
            margin: const EdgeInsets.only(top: 8, bottom: 4),
            width: 32,
            height: 4,
            decoration: BoxDecoration(
              color: Colors.grey.shade300,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          // 标题
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
            child: Row(
              children: [
                Icon(Icons.security, color: theme.colorScheme.primary),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    '${widget.user.username} 的权限',
                    style: theme.textTheme.titleMedium?.copyWith(
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
                TextButton(
                  onPressed: widget.isSaving ? null : widget.onSave,
                  child: widget.isSaving
                      ? const SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Text('保存'),
                ),
              ],
            ),
          ),
          const Divider(height: 1),
          // 权限列表
          Flexible(
            child: ListView(
              shrinkWrap: true,
              padding: const EdgeInsets.symmetric(vertical: 8),
              children: widget.disks.map((disk) {
                final state = widget.permState[disk.id]!;
                return Column(
                  children: [
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      child: Row(
                        children: [
                          Icon(Icons.folder, color: theme.colorScheme.primary),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              disk.name,
                              style: theme.textTheme.bodyMedium?.copyWith(
                                fontWeight: FontWeight.w500,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                    Padding(
                      padding: const EdgeInsets.only(left: 40),
                      child: Column(
                        children: [
                          _PermCheckboxRow(
                            label: '读取',
                            icon: Icons.visibility_outlined,
                            value: state.canRead,
                            enabled: !widget.isSaving,
                            onChanged: (v) => setState(() => state.canRead = v),
                          ),
                          _PermCheckboxRow(
                            label: '写入',
                            icon: Icons.edit_outlined,
                            value: state.canWrite,
                            enabled: !widget.isSaving,
                            onChanged: (v) =>
                                setState(() => state.canWrite = v),
                          ),
                          _PermCheckboxRow(
                            label: '删除',
                            icon: Icons.delete_outline,
                            value: state.canDelete,
                            enabled: !widget.isSaving,
                            onChanged: (v) =>
                                setState(() => state.canDelete = v),
                          ),
                        ],
                      ),
                    ),
                    const Divider(height: 1, indent: 40),
                  ],
                );
              }).toList(),
            ),
          ),
        ],
      ),
    );
  }
}

/// 单行权限复选框（图标 + 标签 + Checkbox 居右）
class _PermCheckboxRow extends StatelessWidget {
  final String label;
  final IconData icon;
  final bool value;
  final bool enabled;
  final ValueChanged<bool> onChanged;

  const _PermCheckboxRow({
    required this.label,
    required this.icon,
    required this.value,
    required this.enabled,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Icon(icon, size: 16, color: Colors.grey.shade600),
        const SizedBox(width: 8),
        Expanded(child: Text(label, style: const TextStyle(fontSize: 14))),
        Checkbox(
          value: value,
          onChanged: enabled ? (v) => onChanged(v ?? false) : null,
          visualDensity: VisualDensity.compact,
          materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
        ),
      ],
    );
  }
}
