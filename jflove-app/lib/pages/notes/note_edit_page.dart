import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_markdown/flutter_markdown.dart';

import '../../providers/note_provider.dart';
import '../../utils/exception.dart';

class NoteEditPage extends ConsumerStatefulWidget {
  final String noteId;

  const NoteEditPage({super.key, required this.noteId});

  @override
  ConsumerState<NoteEditPage> createState() => _NoteEditPageState();
}

class _NoteEditPageState extends ConsumerState<NoteEditPage> {
  late TextEditingController _editorController;
  bool _isPreview = true;
  bool _isModified = false;
  bool _isSaving = false;
  bool _isLoading = true;
  String? _loadError;

  @override
  void initState() {
    super.initState();
    _editorController = TextEditingController();
    _loadContent();
  }

  Future<void> _loadContent() async {
    try {
      final noteService = ref.read(noteServiceProvider);
      final note = await noteService.getNote(widget.noteId);
      _editorController.text = note.content ?? '';
      setState(() {
        _isLoading = false;
        _loadError = null;
      });
    } on AppException catch (e) {
      // 404 表示笔记不存在，可能是新笔记（允许继续编辑）
      if (e.code == 404) {
        setState(() {
          _isLoading = false;
          _loadError = null;
        });
      } else {
        setState(() {
          _isLoading = false;
          _loadError = '加载失败 (${e.code}): ${e.message}';
        });
      }
    } catch (e) {
      setState(() {
        _isLoading = false;
        _loadError = '加载失败: $e';
      });
    }
  }

  @override
  void dispose() {
    _editorController.dispose();
    super.dispose();
  }

  Future<bool> _onWillPop() async {
    if (!_isModified) return true;
    final result = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('未保存'),
        content: const Text('当前笔记有未保存的修改，是否放弃？'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('放弃'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('继续编辑'),
          ),
        ],
      ),
    );
    return result ?? true;
  }

  Future<void> _save() async {
    setState(() => _isSaving = true);
    try {
      final noteService = ref.read(noteServiceProvider);
      await noteService.saveNote(widget.noteId, _editorController.text);
      setState(() => _isModified = false);
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('已保存')));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('保存失败: $e')));
      }
    } finally {
      if (mounted) setState(() => _isSaving = false);
    }
  }

  void _insertMarkdown(String prefix, String suffix) {
    final text = _editorController.text;
    final sel = _editorController.selection;
    final start = sel.start;
    final end = sel.end;

    final newText =
        '${text.substring(0, start)}$prefix${text.substring(start, end)}$suffix${text.substring(end)}';
    _editorController.text = newText;
    _editorController.selection = TextSelection.collapsed(
      offset: start + prefix.length + (end - start),
    );
    setState(() => _isModified = true);
  }

  Widget _buildBody() {
    // 加载中
    if (_isLoading) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            CircularProgressIndicator(),
            SizedBox(height: 16),
            Text('正在加载笔记…'),
          ],
        ),
      );
    }

    // 加载失败
    if (_loadError != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.error_outline, size: 48, color: Colors.red.shade300),
            const SizedBox(height: 12),
            Text(
              _loadError!,
              textAlign: TextAlign.center,
              style: TextStyle(color: Colors.grey.shade600),
            ),
            const SizedBox(height: 16),
            OutlinedButton.icon(
              icon: const Icon(Icons.refresh),
              label: const Text('重试'),
              onPressed: () {
                setState(() {
                  _isLoading = true;
                  _loadError = null;
                });
                _loadContent();
              },
            ),
          ],
        ),
      );
    }

    // 正常显示
    if (_isPreview) {
      return Markdown(
        data: _editorController.text.isEmpty
            ? '*暂无内容*'
            : _editorController.text,
        selectable: true,
        padding: const EdgeInsets.all(16),
      );
    }

    return TextField(
      controller: _editorController,
      maxLines: null,
      expands: true,
      textAlignVertical: TextAlignVertical.top,
      style: const TextStyle(
        fontFamily: 'monospace',
        fontSize: 14,
        height: 1.5,
      ),
      decoration: const InputDecoration(
        border: InputBorder.none,
        contentPadding: EdgeInsets.all(16),
        hintText: '使用 Markdown 语法编写笔记…',
      ),
      onChanged: (_) {
        if (!_isModified) setState(() => _isModified = true);
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) async {
        if (didPop) return;
        final canPop = await _onWillPop();
        if (canPop && context.mounted) {
          Navigator.pop(context);
        }
      },
      child: Scaffold(
        appBar: AppBar(
          title: Text(widget.noteId),
          actions: [
            IconButton(
              icon: Icon(_isPreview ? Icons.edit : Icons.visibility),
              tooltip: _isPreview ? '编辑' : '预览',
              onPressed: () => setState(() => _isPreview = !_isPreview),
            ),
            IconButton(
              icon: _isSaving
                  ? const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : Icon(Icons.save, color: _isModified ? Colors.orange : null),
              tooltip: '保存',
              onPressed: _isModified ? _save : null,
            ),
          ],
        ),
        body: Column(
          children: [
            if (!_isPreview)
              Container(
                height: 40,
                decoration: BoxDecoration(
                  color: Theme.of(
                    context,
                  ).colorScheme.surfaceContainerHighest.withAlpha(80),
                  border: Border(
                    bottom: BorderSide(color: Theme.of(context).dividerColor),
                  ),
                ),
                child: ListView(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 4),
                  children: [
                    _toolbarBtn(
                      Icons.format_bold,
                      '加粗',
                      () => _insertMarkdown('**', '**'),
                    ),
                    _toolbarBtn(
                      Icons.format_italic,
                      '斜体',
                      () => _insertMarkdown('*', '*'),
                    ),
                    _toolbarBtn(
                      Icons.format_size,
                      '标题',
                      () => _insertMarkdown('## ', ''),
                    ),
                    _toolbarBtn(
                      Icons.format_list_bulleted,
                      '无序列表',
                      () => _insertMarkdown('- ', ''),
                    ),
                    _toolbarBtn(
                      Icons.format_list_numbered,
                      '有序列表',
                      () => _insertMarkdown('1. ', ''),
                    ),
                    _toolbarBtn(
                      Icons.link,
                      '链接',
                      () => _insertMarkdown('[', '](url)'),
                    ),
                    _toolbarBtn(
                      Icons.image,
                      '图片',
                      () => _insertMarkdown('![', '](url)'),
                    ),
                    _toolbarBtn(
                      Icons.code,
                      '代码块',
                      () => _insertMarkdown('```\n', '\n```'),
                    ),
                    _toolbarBtn(
                      Icons.format_quote,
                      '引用',
                      () => _insertMarkdown('> ', ''),
                    ),
                  ],
                ),
              ),
            Expanded(child: _buildBody()),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
              decoration: BoxDecoration(
                color: Theme.of(
                  context,
                ).colorScheme.surfaceContainerHighest.withAlpha(60),
              ),
              child: Row(
                children: [
                  Text(
                    _isPreview ? '预览模式' : '编辑模式',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                  const Spacer(),
                  if (_isModified)
                    Text(
                      '● 未保存',
                      style: TextStyle(
                        color: Colors.orange.shade700,
                        fontSize: 12,
                      ),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _toolbarBtn(IconData icon, String tooltip, VoidCallback onTap) {
    return IconButton(
      icon: Icon(icon, size: 18),
      tooltip: tooltip,
      onPressed: onTap,
      visualDensity: VisualDensity.compact,
      constraints: const BoxConstraints(minWidth: 36, minHeight: 36),
    );
  }
}
