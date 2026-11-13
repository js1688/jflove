import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../config/design_tokens.dart';
import '../../config/routes.dart';
import '../../providers/remote_provider.dart';
import '../../services/remote_ssh_service.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/error_state.dart';

/// 远程连接列表（v1.10.0）
///
/// 分组横向筛选 + 连接卡片；点卡片直接建连进终端页（长按出编辑/删除菜单）。
class RemoteListPage extends ConsumerStatefulWidget {
  const RemoteListPage({super.key});

  @override
  ConsumerState<RemoteListPage> createState() => _RemoteListPageState();
}

class _RemoteListPageState extends ConsumerState<RemoteListPage> {
  /// -1 = 全部
  int _selectedGroup = -1;

  Future<void> _open(RemoteConnection item) async {
    final api = ref.read(remoteApiProvider);
    try {
      final full = await api.getConnection(item.id);
      if (!mounted) return;
      await context.push(AppRoutes.remoteTerminal, extra: full);
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('打开失败：$error')),
      );
    }
  }

  Future<void> _remove(RemoteConnection item) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('删除连接'),
        content: Text('确定删除「${item.name}」？'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('删除')),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      await ref.read(remoteApiProvider).deleteConnection(item.id);
      ref.invalidate(remoteOverviewProvider);
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('删除失败：$error')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    final overview = ref.watch(remoteOverviewProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('远程连接'),
        actions: [
          IconButton(
            icon: const Icon(Icons.add_rounded),
            tooltip: '新建连接',
            onPressed: () => context.push(AppRoutes.remoteEdit),
          ),
        ],
      ),
      body: overview.when(
        data: (data) {
          final items = _selectedGroup == -1
              ? data.connections
              : data.connections.where((c) => c.groupId == _selectedGroup).toList();
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(remoteOverviewProvider),
            child: ListView(
              padding: EdgeInsets.fromLTRB(t.s3, t.s2, t.s3, t.s6),
              children: [
                if (data.groups.isNotEmpty) ...[
                  SizedBox(
                    height: 38,
                    child: ListView(
                      scrollDirection: Axis.horizontal,
                      children: [
                        _GroupChip(
                          label: '全部',
                          selected: _selectedGroup == -1,
                          onTap: () => setState(() => _selectedGroup = -1),
                        ),
                        for (final group in data.groups)
                          _GroupChip(
                            label: group.name,
                            count: group.connectionCount,
                            selected: _selectedGroup == group.id,
                            onTap: () => setState(() => _selectedGroup = group.id),
                          ),
                      ],
                    ),
                  ),
                  SizedBox(height: t.s2),
                ],
                if (items.isEmpty)
                  const EmptyState(
                    icon: Icons.terminal_rounded,
                    title: '还没有连接',
                    subtitle: '新建一条 SSH 连接，配置会存到服务端',
                  ),
                for (final item in items)
                  Padding(
                    padding: EdgeInsets.only(bottom: t.s2),
                    child: _ConnectionCard(
                      connection: item,
                      jumpName: data.connections
                          .where((c) => c.id == item.jumpConnectionId)
                          .map((c) => c.name)
                          .firstOrNull,
                      onTap: () => _open(item),
                      onEdit: () => context.push(AppRoutes.remoteEdit, extra: item),
                      onDelete: () => _remove(item),
                    ),
                  ),
              ],
            ),
          );
        },
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) => ErrorState(
          message: '$error',
          onRetry: () => ref.invalidate(remoteOverviewProvider),
        ),
      ),
    );
  }
}

class _GroupChip extends StatelessWidget {
  const _GroupChip({
    required this.label,
    required this.selected,
    required this.onTap,
    this.count,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;
  final int? count;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return Padding(
      padding: EdgeInsets.only(right: t.s2),
      child: ChoiceChip(
        label: Text(count == null ? label : '$label $count'),
        selected: selected,
        onSelected: (_) => onTap(),
        showCheckmark: false,
      ),
    );
  }
}

class _ConnectionCard extends StatelessWidget {
  const _ConnectionCard({
    required this.connection,
    required this.onTap,
    required this.onEdit,
    required this.onDelete,
    this.jumpName,
  });

  final RemoteConnection connection;
  final String? jumpName;
  final VoidCallback onTap;
  final VoidCallback onEdit;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    final authLabel = switch (connection.authType) {
      'publickey' => '密钥',
      'password_keyboard' => '密码 + OTP',
      _ => '密码',
    };
    return Card(
      margin: EdgeInsets.zero,
      child: InkWell(
        borderRadius: BorderRadius.circular(t.rLg),
        onTap: onTap,
        onLongPress: () => showModalBottomSheet<void>(
          context: context,
          builder: (ctx) => SafeArea(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                ListTile(
                  leading: const Icon(Icons.edit_outlined),
                  title: const Text('编辑'),
                  onTap: () {
                    Navigator.pop(ctx);
                    onEdit();
                  },
                ),
                ListTile(
                  leading: const Icon(Icons.delete_outline),
                  title: const Text('删除'),
                  onTap: () {
                    Navigator.pop(ctx);
                    onDelete();
                  },
                ),
              ],
            ),
          ),
        ),
        child: Padding(
          padding: EdgeInsets.all(t.s3),
          child: Row(
            children: [
              Container(
                width: 42,
                height: 42,
                decoration: BoxDecoration(
                  color: t.bgActive,
                  borderRadius: BorderRadius.circular(t.rMd),
                ),
                child: Icon(Icons.terminal_rounded, color: AppTokens.brand600),
              ),
              SizedBox(width: t.s3),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      connection.name,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
                    ),
                    SizedBox(height: 2),
                    Text(
                      '${connection.host}:${connection.port}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 12,
                        color: t.fgMuted,
                        fontFamily: 'monospace',
                      ),
                    ),
                    SizedBox(height: 6),
                    Wrap(
                      spacing: 6,
                      runSpacing: 4,
                      children: [
                        _Tag(text: authLabel),
                        if (connection.username.isNotEmpty) _Tag(text: connection.username),
                        if (jumpName != null) _Tag(text: '↳ 跳板 $jumpName'),
                      ],
                    ),
                  ],
                ),
              ),
              Icon(Icons.chevron_right_rounded, color: t.fgSubtle),
            ],
          ),
        ),
      ),
    );
  }
}

class _Tag extends StatelessWidget {
  const _Tag({required this.text});
  final String text;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: t.bgSunken,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(text, style: TextStyle(fontSize: 11, color: t.fgMuted)),
    );
  }
}
