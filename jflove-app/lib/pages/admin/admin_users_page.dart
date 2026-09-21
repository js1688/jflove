import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../config/design_tokens.dart';
import '../../models/user.dart';
import '../../providers/admin_provider.dart';
import '../../widgets/app_card.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/error_state.dart';

/// 用户管理页（admin）
///
/// v1.5.0：列表项换 `AppCard`、角色/启用状态换 `AppBadge`、
/// 空态 / 加载态 / 错误态换统一组件，硬编码红色换 `AppTokens.danger500`。
/// 业务逻辑（列表隐藏管理员行的设计意图、启用/禁用、改密、删除）与 v1.4.2 完全一致。
class AdminUsersPage extends ConsumerWidget {
  const AdminUsersPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.tokens;
    final userListAsync = ref.watch(userListProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('用户管理'),
        actions: [
          IconButton(
            icon: const Icon(Icons.add),
            tooltip: '添加用户',
            onPressed: () => _addUser(context, ref),
          ),
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: '刷新',
            onPressed: () => ref.invalidate(userListProvider),
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
              subtitle: '点击右上角「添加用户」创建第一个普通用户',
            );
          }
          return ListView.builder(
            padding: EdgeInsets.all(t.s3),
            itemCount: normalUsers.length,
            itemBuilder: (ctx, i) => _buildUserCard(context, ref, normalUsers[i]),
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

  /// 单个用户卡片（首字母头像 + 用户名 + 角色/状态徽标 + 三点菜单）
  Widget _buildUserCard(BuildContext context, WidgetRef ref, User user) {
    final t = context.tokens;
    return AppCard(
      margin: EdgeInsets.only(bottom: t.s2),
      padding: EdgeInsets.all(t.s3),
      child: Row(
        children: [
          // 首字母头像（品牌淡底 + 品牌字色）
          Container(
            width: 40,
            height: 40,
            alignment: Alignment.center,
            decoration: BoxDecoration(color: t.bgActive, shape: BoxShape.circle),
            child: Text(
              user.username.isEmpty ? '?' : user.username[0].toUpperCase(),
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
                // 角色与启用状态用徽标表达（替代原先的彩色/纯文本拼接）
                Row(
                  children: [
                    AppBadge(
                      user.role == 'admin' ? '管理员' : '普通用户',
                      tone: user.role == 'admin'
                          ? BadgeTone.brand
                          : BadgeTone.neutral,
                    ),
                    SizedBox(width: t.s2),
                    AppBadge(
                      user.enabled ? '启用' : '禁用',
                      tone: user.enabled ? BadgeTone.success : BadgeTone.neutral,
                      icon: user.enabled
                          ? Icons.check_circle_outline_rounded
                          : Icons.block_rounded,
                    ),
                  ],
                ),
              ],
            ),
          ),
          PopupMenuButton<String>(
            tooltip: '操作菜单',
            onSelected: (action) {
              if (action == 'password') {
                _changePassword(context, ref, user);
              } else if (action == 'toggle') {
                _toggleEnabled(context, ref, user);
              } else if (action == 'delete') {
                _deleteUser(context, ref, user);
              }
            },
            itemBuilder: (_) => [
              const PopupMenuItem(
                value: 'password',
                child: ListTile(
                  leading: Icon(Icons.lock_outline_rounded),
                  title: Text('修改密码'),
                ),
              ),
              PopupMenuItem(
                value: 'toggle',
                child: ListTile(
                  leading: Icon(
                    user.enabled ? Icons.block_rounded : Icons.check_circle,
                  ),
                  title: Text(user.enabled ? '禁用' : '启用'),
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

  void _addUser(BuildContext context, WidgetRef ref) {
    final userCtrl = TextEditingController();
    final pwdCtrl = TextEditingController();
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('添加用户'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: userCtrl,
              decoration: const InputDecoration(labelText: '用户名'),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: pwdCtrl,
              obscureText: true,
              decoration: const InputDecoration(labelText: '密码'),
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
                    .read(userServiceProvider)
                    .createUser(userCtrl.text.trim(), pwdCtrl.text);
                ref.invalidate(userListProvider);
              } catch (e) {
                if (context.mounted) {
                  ScaffoldMessenger.of(
                    context,
                  ).showSnackBar(SnackBar(content: Text('创建失败: $e')));
                }
              }
            },
            child: const Text('创建'),
          ),
        ],
      ),
    );
  }

  void _changePassword(BuildContext context, WidgetRef ref, User user) {
    final pwdCtrl = TextEditingController();
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('修改密码'),
        content: TextField(
          controller: pwdCtrl,
          obscureText: true,
          decoration: const InputDecoration(labelText: '新密码'),
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
                    .read(userServiceProvider)
                    .changePassword(user.id, pwdCtrl.text);
                if (context.mounted) {
                  ScaffoldMessenger.of(
                    context,
                  ).showSnackBar(const SnackBar(content: Text('密码已更新')));
                }
              } catch (e) {
                if (context.mounted) {
                  ScaffoldMessenger.of(
                    context,
                  ).showSnackBar(SnackBar(content: Text('修改失败: $e')));
                }
              }
            },
            child: const Text('确认'),
          ),
        ],
      ),
    );
  }

  void _toggleEnabled(BuildContext context, WidgetRef ref, User user) {
    ref
        .read(userServiceProvider)
        .setEnabled(user.id, !user.enabled)
        .then((_) {
          ref.invalidate(userListProvider);
        })
        .catchError((e) {
          if (context.mounted) {
            ScaffoldMessenger.of(
              context,
            ).showSnackBar(SnackBar(content: Text('操作失败: $e')));
          }
        });
  }

  void _deleteUser(BuildContext context, WidgetRef ref, User user) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('确认删除'),
        content: Text('确定要删除用户「${user.username}」吗？'),
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
                await ref.read(userServiceProvider).deleteUser(user.id);
                ref.invalidate(userListProvider);
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
