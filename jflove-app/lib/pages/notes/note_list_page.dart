import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../providers/note_provider.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/loading_indicator.dart';

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

  Future<void> _createNote() async {
    final controller = TextEditingController();
    final name = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('新建笔记'),
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
      builder: (ctx) => AlertDialog(
        title: const Text('重命名'),
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
      builder: (ctx) => AlertDialog(
        title: const Text('确认删除'),
        content: Text('确定要删除「$name」吗？'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            style: FilledButton.styleFrom(
              backgroundColor: Theme.of(context).colorScheme.error,
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
    final noteListAsync = ref.watch(noteListProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('笔记管理')),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(12),
            child: TextField(
              controller: _searchController,
              decoration: InputDecoration(
                hintText: '搜索笔记…',
                prefixIcon: const Icon(Icons.search),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                ),
                contentPadding: const EdgeInsets.symmetric(vertical: 0),
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
                    itemCount: filtered.length,
                    itemBuilder: (ctx, i) {
                      final note = filtered[i];
                      return ListTile(
                        leading: const Icon(Icons.description),
                        title: Text(note.name),
                        subtitle: note.mtimeStr.isNotEmpty
                            ? Text(note.mtimeStr)
                            : null,
                        trailing: PopupMenuButton<String>(
                          onSelected: (action) {
                            if (action == 'rename') {
                              _renameNote(note.name);
                            } else if (action == 'delete') {
                              _deleteNote(note.name);
                            }
                          },
                          itemBuilder: (_) => [
                            const PopupMenuItem(
                              value: 'rename',
                              child: ListTile(
                                leading: Icon(Icons.edit),
                                title: Text('重命名'),
                              ),
                            ),
                            const PopupMenuItem(
                              value: 'delete',
                              child: ListTile(
                                leading: Icon(Icons.delete, color: Colors.red),
                                title: Text(
                                  '删除',
                                  style: TextStyle(color: Colors.red),
                                ),
                              ),
                            ),
                          ],
                        ),
                        onTap: () => context.push('/notes/${note.name}'),
                      );
                    },
                  );
                },
                loading: () => const LoadingIndicator(),
                error: (e, _) => Center(child: Text('加载失败: $e')),
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
