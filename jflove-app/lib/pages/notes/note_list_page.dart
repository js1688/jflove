import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../config/design_tokens.dart';
import '../../providers/note_provider.dart';
import '../../widgets/app_card.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/error_state.dart';

/// 笔记管理（v1.5.0 设计令牌化）
///
/// 与「传输任务」「修复中心」保持同一套视觉：
///   - 列表项从裸 `ListTile` 换成 `AppCard`（1px 描边 + 极浅阴影）；
///   - 加载中 → `ListSkeleton`，加载失败 → `ErrorState`，空列表 → `EmptyState`；
///   - 弹窗外壳同样由 `AppCard` 承载，圆角 / 间距 / 语义色全部取自 `AppTokens`。
class NoteListPage extends ConsumerStatefulWidget {
  const NoteListPage({super.key});

  @override
  ConsumerState<NoteListPage> createState() => _NoteListPageState();
}

class _NoteListPageState extends ConsumerState<NoteListPage> {
  final _searchController = TextEditingController();
  String _searchKeyword = '';

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  /// 统一弹窗外壳
  ///
  /// `Dialog` 只负责提供 Material 层与遮罩，描边 / 圆角 / 阴影交给 `AppCard`，
  /// 这样弹窗与列表卡片是同一套视觉令牌（v1.4.2 的 AlertDialog 是 M3 默认
  /// 28 圆角，与卡片不一致）。
  Widget _dialog({
    required String title,
    required Widget content,
    required List<Widget> actions,
  }) {
    final t = context.tokens;
    return Dialog(
      backgroundColor: t.bgSurface,
      elevation: 0,
      insetPadding: EdgeInsets.symmetric(horizontal: t.s6, vertical: t.s6),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(t.rLg),
      ),
      child: AppCard(
        padding: EdgeInsets.all(t.s5),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              title,
              style: TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.w600,
                color: t.fgDefault,
              ),
            ),
            SizedBox(height: t.s4),
            content,
            SizedBox(height: t.s5),
            Row(mainAxisAlignment: MainAxisAlignment.end, children: actions),
          ],
        ),
      ),
    );
  }

  Future<void> _createNote() async {
    final controller = TextEditingController();
    final name = await showDialog<String>(
      context: context,
      builder: (ctx) => _dialog(
        title: '新建笔记',
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(
            labelText: '文件名（.md 结尾）',
            hintText: '我的笔记',
          ),
          autofocus: true,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim()),
            child: const Text('创建'),
          ),
        ],
      ),
    );

    if (name != null && name.isNotEmpty) {
      final filename = name.endsWith('.md') ? name : '$name.md';
      try {
        final noteService = ref.read(noteServiceProvider);
        await noteService.createNote(filename);
        ref.invalidate(noteListProvider);
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(
            context,
          ).showSnackBar(SnackBar(content: Text('创建失败: $e')));
        }
      }
    }
  }

  void _renameNote(String oldName) {
    final controller = TextEditingController(text: oldName);
    showDialog(
      context: context,
      builder: (ctx) => _dialog(
        title: '重命名',
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(labelText: '新文件名'),
          autofocus: true,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () async {
              Navigator.pop(ctx);
              final newName = controller.text.trim();
              if (newName.isEmpty || newName == oldName) return;
              final filename = newName.endsWith('.md')
                  ? newName
                  : '$newName.md';
              try {
                final noteService = ref.read(noteServiceProvider);
                await noteService.renameNote(oldName, filename);
                ref.invalidate(noteListProvider);
              } catch (e) {
                if (mounted) {
                  ScaffoldMessenger.of(
                    context,
                  ).showSnackBar(SnackBar(content: Text('重命名失败: $e')));
                }
              }
            },
            child: const Text('确认'),
          ),
        ],
      ),
    );
  }

  Future<void> _deleteNote(String name) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => _dialog(
        title: '确认删除',
        content: Text(
          '确定要删除「$name」吗？',
          style: TextStyle(
            fontSize: 14,
            height: 1.5,
            color: context.tokens.fgMuted,
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            // 破坏性操作用语义危险色，替代原先的 colorScheme.error
            style: FilledButton.styleFrom(
              backgroundColor: AppTokens.danger500,
            ),
            child: const Text('删除'),
          ),
        ],
      ),
    );

    if (confirmed == true) {
      try {
        final noteService = ref.read(noteServiceProvider);
        await noteService.deleteNote(name);
        ref.invalidate(noteListProvider);
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(
            context,
          ).showSnackBar(SnackBar(content: Text('删除失败: $e')));
        }
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    final noteListAsync = ref.watch(noteListProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('笔记管理')),
      body: Column(
        children: [
          Padding(
            padding: EdgeInsets.all(t.s3),
            child: TextField(
              controller: _searchController,
              decoration: InputDecoration(
                hintText: '搜索笔记…',
                prefixIcon: const Icon(Icons.search),
                suffixIcon: _searchKeyword.isNotEmpty
                    ? IconButton(
                        icon: const Icon(Icons.clear),
                        onPressed: () {
                          _searchController.clear();
                          setState(() => _searchKeyword = '');
                        },
                      )
                    : null,
              ),
              onChanged: (v) =>
                  setState(() => _searchKeyword = v.toLowerCase()),
            ),
          ),
          Expanded(
            child: RefreshIndicator(
              onRefresh: () async => ref.invalidate(noteListProvider),
              child: noteListAsync.when(
                data: (notes) {
                  final filtered = _searchKeyword.isEmpty
                      ? notes
                      : notes
                            .where(
                              (n) =>
                                  n.name.toLowerCase().contains(_searchKeyword),
                            )
                            .toList();

                  if (filtered.isEmpty) {
                    return const EmptyState(
                      icon: Icons.note_outlined,
                      title: '暂无笔记',
                      subtitle: '点击右下角按钮新建笔记',
                    );
                  }

                  return ListView.builder(
                    physics: const AlwaysScrollableScrollPhysics(),
                    padding: EdgeInsets.fromLTRB(t.s3, 0, t.s3, t.s5),
                    itemCount: filtered.length,
                    itemBuilder: (ctx, i) {
                      final note = filtered[i];
                      return AppCard(
                        margin: EdgeInsets.only(bottom: t.s2),
                        padding: EdgeInsets.all(t.s3),
                        onTap: () => context.push('/notes/${note.name}'),
                        child: Row(
                          children: [
                            // 图标底盘：与 StatCard 的图标块同款（品牌淡底 + 圆角）
                            Container(
                              width: 36,
                              height: 36,
                              decoration: BoxDecoration(
                                color: t.bgActive,
                                borderRadius: BorderRadius.circular(t.rMd),
                              ),
                              child: const Icon(
                                Icons.description_outlined,
                                size: 18,
                                color: AppTokens.brand600,
                              ),
                            ),
                            SizedBox(width: t.s3),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    note.name,
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    style: TextStyle(
                                      fontSize: 14,
                                      fontWeight: FontWeight.w600,
                                      color: t.fgDefault,
                                    ),
                                  ),
                                  if (note.mtimeStr.isNotEmpty) ...[
                                    SizedBox(height: t.s1),
                                    Text(
                                      note.mtimeStr,
                                      style: TextStyle(
                                        fontSize: 12,
                                        color: t.fgMuted,
                                      ),
                                    ),
                                  ],
                                ],
                              ),
                            ),
                            SizedBox(width: t.s1),
                            PopupMenuButton<String>(
                              icon: Icon(Icons.more_vert, color: t.fgMuted),
                              tooltip: '更多操作',
                              onSelected: (action) {
                                if (action == 'rename') {
                                  _renameNote(note.name);
                                } else if (action == 'delete') {
                                  _deleteNote(note.name);
                                }
                              },
                              itemBuilder: (_) => [
                                PopupMenuItem(
                                  value: 'rename',
                                  child: Row(
                                    children: [
                                      Icon(
                                        Icons.edit_outlined,
                                        size: 18,
                                        color: t.fgMuted,
                                      ),
                                      SizedBox(width: t.s2),
                                      Text(
                                        '重命名',
                                        style: TextStyle(
                                          fontSize: 14,
                                          color: t.fgDefault,
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                                PopupMenuItem(
                                  value: 'delete',
                                  child: Row(
                                    children: [
                                      // 危险操作统一用语义危险色，替代原先的硬编码红色
                                      const Icon(
                                        Icons.delete_outline,
                                        size: 18,
                                        color: AppTokens.danger500,
                                      ),
                                      SizedBox(width: t.s2),
                                      const Text(
                                        '删除',
                                        style: TextStyle(
                                          fontSize: 14,
                                          color: AppTokens.danger500,
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                              ],
                            ),
                          ],
                        ),
                      );
                    },
                  );
                },
                loading: () => const ListSkeleton(),
                error: (e, _) => ErrorState(
                  message: '$e',
                  onRetry: () => ref.invalidate(noteListProvider),
                ),
              ),
            ),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: _createNote,
        tooltip: '新建笔记',
        child: const Icon(Icons.add),
      ),
    );
  }
}
