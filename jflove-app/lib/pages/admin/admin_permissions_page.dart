import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../config/design_tokens.dart';
import '../../models/disk_permission.dart';
import '../../models/user.dart';
import '../../models/virtual_disk.dart';
import '../../providers/admin_provider.dart';
import '../../widgets/app_card.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/error_state.dart';

/// 权限配置页（admin）
///
/// 移动端全屏单列布局：用户列表 → 点击弹出权限配置底部弹窗。
/// 使用 CheckboxListTile 替代 DataTable，提升触摸操作体验。
///
/// v1.5.0：用户列表换 `AppCard` + `AppBadge`，空态 / 加载态 / 错误态换统一组件，
/// 底部弹窗的磁盘权限组改用 `AppCard` + `SectionTitle`，硬编码灰阶换令牌灰阶；
/// **复选框矩阵的功能与布局结构保持不变**，仅颜色 / 圆角 / 间距走令牌。
class AdminPermissionsPage extends ConsumerStatefulWidget {
  const AdminPermissionsPage({super.key});

  @override
  ConsumerState<AdminPermissionsPage> createState() =>
      _AdminPermissionsPageState();
}

class _AdminPermissionsPageState extends ConsumerState<AdminPermissionsPage> {
  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
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
            return const EmptyState(
              icon: Icons.people_outline_rounded,
              title: '暂无普通用户',
              subtitle: '先在「用户管理」中创建普通用户，再回到此处配置磁盘权限',
            );
          }
          return ListView.separated(
            padding: EdgeInsets.all(t.s4),
            itemCount: normalUsers.length,
            separatorBuilder: (_, _) => SizedBox(height: t.s2),
            itemBuilder: (ctx, i) {
              final user = normalUsers[i];
              return AppCard(
                padding: EdgeInsets.all(t.s3),
                onTap: () => _showPermBottomSheet(context, ref, user),
                child: Row(
                  children: [
                    // 首字母头像（品牌淡底 + 品牌字色）
                    Container(
                      width: 40,
                      height: 40,
                      alignment: Alignment.center,
                      decoration: BoxDecoration(
                        color: t.bgActive,
                        shape: BoxShape.circle,
                      ),
                      child: Text(
                        user.username.isEmpty
                            ? '?'
                            : user.username[0].toUpperCase(),
                        style: const TextStyle(
                          fontSize: 13.5,
                          fontWeight: FontWeight.w600,
                          color: AppTokens.brand600,
                        ),
                      ),
                    ),
                    SizedBox(width: t.s3),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            user.username,
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
                            '点击配置磁盘权限',
                            style: TextStyle(fontSize: 11.5, color: t.fgMuted),
                          ),
                        ],
                      ),
                    ),
                    Icon(Icons.chevron_right_rounded, color: t.fgSubtle),
                  ],
                ),
              );
            },
          );
        },
        loading: () => const ListSkeleton(rows: 5),
        error: (e, _) => ErrorState(
          message: '$e',
          onRetry: () => ref.invalidate(userListProvider),
        ),
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
    final t = context.tokens;
    // 提示条底色是深色反相面（见 theme.dart 的 snackBarTheme），
    // 因此图标取 `onPrimary`（亮色）而非页面前景色，避免在深底上看不清。
    final onInverse = Theme.of(context).colorScheme.onPrimary;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Row(
          children: [
            Icon(
              isError ? Icons.error_outline_rounded : Icons.check_circle_outline,
              color: onInverse,
              size: 20,
            ),
            SizedBox(width: t.s3),
            Expanded(child: Text(message)),
          ],
        ),
        behavior: SnackBarBehavior.floating,
        margin: EdgeInsets.fromLTRB(t.s4, 0, t.s4, t.s4),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(t.rMd)),
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
    final t = context.tokens;
    return Padding(
      padding: EdgeInsets.only(
        bottom: MediaQuery.of(context).viewInsets.bottom,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // 拖拽手柄
          Container(
            margin: EdgeInsets.only(top: t.s2, bottom: t.s1),
            width: 32,
            height: 4,
            decoration: BoxDecoration(
              color: t.borderDefault,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          // 标题
          Padding(
            padding: EdgeInsets.fromLTRB(t.s4, t.s2, t.s4, t.s1),
            child: Row(
              children: [
                const Icon(
                  Icons.security_rounded,
                  size: 20,
                  color: AppTokens.brand500,
                ),
                SizedBox(width: t.s2),
                Expanded(
                  child: Text(
                    '${widget.user.username} 的权限',
                    style: Theme.of(context).textTheme.titleMedium,
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
          // 权限列表：每个磁盘一张卡片（分区标题 + 三项权限复选框）
          Flexible(
            child: ListView(
              shrinkWrap: true,
              padding: EdgeInsets.fromLTRB(t.s4, t.s3, t.s4, t.s3),
              children: [
                SectionTitle(
                  '磁盘权限',
                  trailing: AppBadge(
                    '${widget.disks.length} 个磁盘',
                    tone: BadgeTone.neutral,
                  ),
                ),
                SizedBox(height: t.s3),
                ...widget.disks.map((disk) {
                  final state = widget.permState[disk.id]!;
                  return AppCard(
                    margin: EdgeInsets.only(bottom: t.s3),
                    padding: EdgeInsets.zero,
                    child: Column(
                      children: [
                        Padding(
                          padding: EdgeInsets.symmetric(
                            horizontal: t.s3,
                            vertical: t.s2,
                          ),
                          child: Row(
                            children: [
                              Container(
                                width: 30,
                                height: 30,
                                alignment: Alignment.center,
                                decoration: BoxDecoration(
                                  gradient: t.gradBrandSoft,
                                  borderRadius: BorderRadius.circular(t.rMd),
                                  border: Border.all(color: t.borderSubtle),
                                ),
                                child: const Icon(
                                  Icons.folder_rounded,
                                  size: 16,
                                  color: AppTokens.brand600,
                                ),
                              ),
                              SizedBox(width: t.s3),
                              Expanded(
                                child: Text(
                                  disk.name,
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: TextStyle(
                                    fontSize: 13.5,
                                    fontWeight: FontWeight.w600,
                                    color: t.fgDefault,
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                        const Divider(height: 1),
                        // 复选框矩阵：左缩进保持 v1.4.2 的 40（= s6 + s4），只是改为令牌组合
                        Padding(
                          padding: EdgeInsets.fromLTRB(
                            t.s6 + t.s4,
                            t.s1,
                            t.s3,
                            t.s1,
                          ),
                          child: Column(
                            children: [
                              _PermCheckboxRow(
                                label: '读取',
                                icon: Icons.visibility_outlined,
                                value: state.canRead,
                                enabled: !widget.isSaving,
                                onChanged: (v) =>
                                    setState(() => state.canRead = v),
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
                      ],
                    ),
                  );
                }),
              ],
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
    final t = context.tokens;
    return Row(
      children: [
        Icon(icon, size: 16, color: t.fgMuted),
        SizedBox(width: t.s2),
        Expanded(
          child: Text(
            label,
            style: TextStyle(fontSize: 14, color: t.fgDefault),
          ),
        ),
        Checkbox(
          value: value,
          onChanged: enabled ? (v) => onChanged(v ?? false) : null,
          visualDensity: VisualDensity.compact,
          materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
          side: BorderSide(color: t.borderDefault),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(t.rSm),
          ),
        ),
      ],
    );
  }
}
