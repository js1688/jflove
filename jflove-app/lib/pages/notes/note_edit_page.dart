import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_markdown/flutter_markdown.dart';

import '../../config/design_tokens.dart';
import '../../providers/note_provider.dart';
import '../../utils/exception.dart';
import '../../utils/markdown/diagram_templates.dart';
import '../../utils/markdown/markdown_builder.dart';
import '../../widgets/app_card.dart';
import '../../widgets/error_state.dart';

/// 笔记编辑 / 预览（v1.5.0 设计令牌化）
///
/// 渲染逻辑（`buildMarkdownStyleSheet` / `MermaidBuilder`）保持不变，
/// 本页只把原先散落的硬编码色与 `Theme.of(context).colorScheme.*`
/// 换成 `AppTokens`，并让加载 / 失败态复用统一组件。
///
/// v1.5.0 修补：工具栏补上「插入图表」入口（需求 AC-15），
/// 让移动端与桌面端 / Web 端一样能选类型插入 Mermaid 模板。
class NoteEditPage extends ConsumerStatefulWidget {
  final String noteId;

  const NoteEditPage({super.key, required this.noteId});

  @override
  ConsumerState<NoteEditPage> createState() => _NoteEditPageState();
}

class _NoteEditPageState extends ConsumerState<NoteEditPage> {
  late TextEditingController _editorController;
  final FocusNode _editorFocusNode = FocusNode();
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
    _editorFocusNode.dispose();
    super.dispose();
  }

  /// 统一弹窗外壳
  ///
  /// 与 `note_list_page` 同款：`Dialog` 只提供 Material 层，描边 / 圆角 /
  /// 阴影交给 `AppCard`，避免 M3 默认 28 圆角与卡片风格割裂。
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

  Future<bool> _onWillPop() async {
    if (!_isModified) return true;
    final result = await showDialog<bool>(
      context: context,
      builder: (ctx) => _dialog(
        title: '未保存',
        content: Text(
          '当前笔记有未保存的修改，是否放弃？',
          style: TextStyle(
            fontSize: 14,
            height: 1.5,
            color: context.tokens.fgMuted,
          ),
        ),
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

  /// 在光标处插入 Mermaid 图表模板（需求 AC-15）
  ///
  /// 模板与桌面端 / Web 端同源（见 `kDiagramTemplates`），插入逻辑见
  /// [insertDiagramTemplate]：保证围栏独占行首、整体赋 `TextEditingValue`
  /// （保住 undo 栈）、插入后光标落在片段之后。
  void _insertDiagram(DiagramTemplate template) {
    insertDiagramTemplate(_editorController, template);
    setState(() => _isModified = true);
    // 光标可见并可直接继续输入
    _editorFocusNode.requestFocus();
  }

  /// 「插入图表」类型选择（底部弹层，与移动端既有交互一致）
  Future<void> _showDiagramPicker() async {
    final t = context.tokens;
    final picked = await showModalBottomSheet<DiagramTemplate>(
      context: context,
      backgroundColor: t.bgSurface,
      showDragHandle: true,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(t.rLg)),
      ),
      builder: (ctx) => SafeArea(
        child: SingleChildScrollView(
          padding: EdgeInsets.fromLTRB(t.s4, 0, t.s4, t.s4),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  const Icon(
                    Icons.account_tree_outlined,
                    size: 18,
                    color: AppTokens.brand500,
                  ),
                  SizedBox(width: t.s2),
                  Text(
                    '插入图表',
                    style: TextStyle(
                      fontSize: 15,
                      fontWeight: FontWeight.w600,
                      color: t.fgDefault,
                    ),
                  ),
                ],
              ),
              SizedBox(height: t.s3),
              GridView.count(
                crossAxisCount: 2,
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                mainAxisSpacing: t.s2,
                crossAxisSpacing: t.s2,
                childAspectRatio: 2.6,
                children: [
                  for (final template in kDiagramTemplates)
                    AppCard(
                      padding: EdgeInsets.symmetric(
                        horizontal: t.s3,
                        vertical: t.s2,
                      ),
                      onTap: () => Navigator.pop(ctx, template),
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            template.label,
                            style: TextStyle(
                              fontSize: 13.5,
                              fontWeight: FontWeight.w600,
                              color: t.fgDefault,
                            ),
                          ),
                          Text(
                            // 首行即 mermaid 图表类型关键字，便于用户对照源码
                            template.body.split('\n').first,
                            style: TextStyle(fontSize: 11, color: t.fgSubtle),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ],
                      ),
                    ),
                ],
              ),
              SizedBox(height: t.s3),
              Text(
                '插入后可在编辑区修改源码，预览区会自动渲染成图；语法有误时只影响这一张图。',
                style: TextStyle(fontSize: 11.5, height: 1.5, color: t.fgSubtle),
              ),
            ],
          ),
        ),
      ),
    );
    if (picked == null) return;
    _insertDiagram(picked);
  }

  Widget _buildBody() {
    final t = context.tokens;

    // 加载中：与列表页统一用骨架屏，避免转圈造成的布局跳动
    if (_isLoading) {
      return const ListSkeleton();
    }

    // 加载失败：统一错误态（图标 + 说明 + 重试）
    if (_loadError != null) {
      return ErrorState(
        title: '笔记加载失败',
        message: _loadError!,
        onRetry: () {
          setState(() {
            _isLoading = true;
            _loadError = null;
          });
          _loadContent();
        },
      );
    }

    // 正常显示
    if (_isPreview) {
      // v1.5.0：接入设计令牌样式 + mermaid 图表分支 + 代码块外壳。
      // 每次重建前重置图表序号，保证「跳到源码」定位到的块与源码顺序一致。
      MermaidBuilder.resetCounter();
      return Markdown(
        data: _editorController.text.isEmpty
            ? '*暂无内容*'
            : _editorController.text,
        selectable: true,
        padding: const EdgeInsets.all(16),
        styleSheet: buildMarkdownStyleSheet(context),
        builders: {
          // 键必须是块级标签 `pre`（围栏代码块的外层元素），由 CodeBlockBuilder
          // 统一分派：命中 language-mermaid → 图表，其余 → 代码卡。
          // 注册在 `code` 上会让行内/围栏代码文本被整段丢弃（见 CodeBlockBuilder 注释）。
          'pre': CodeBlockBuilder(mermaid: MermaidBuilder(onJumpToSource: _jumpToDiagramSource)),
        },
      );
    }

    return TextField(
      controller: _editorController,
      // v1.5.0 修补：把页面持有的 FocusNode 真正接到编辑区。
      // 之前这里没传 focusNode，`_editorFocusNode` 是个从未挂到控件上的孤儿节点，
      // 于是 `_jumpToDiagramSource` / 插入图表之后的 `requestFocus()` 全部静默失效
      // （光标不会出现，键盘也不会弹）。
      focusNode: _editorFocusNode,
      maxLines: null,
      expands: true,
      textAlignVertical: TextAlignVertical.top,
      style: TextStyle(
        fontFamily: 'monospace',
        fontSize: 14,
        height: 1.6,
        color: t.fgDefault,
      ),
      decoration: InputDecoration(
        border: InputBorder.none,
        filled: false,
        contentPadding: EdgeInsets.all(t.s4),
        hintText: '使用 Markdown 语法编写笔记…',
        hintStyle: TextStyle(color: t.fgSubtle),
      ),
      onChanged: (_) {
        if (!_isModified) setState(() => _isModified = true);
      },
    );
  }

  /// 从预览区点击图表「跳到源码」：切到编辑模式并把光标移到该图表源码块
  void _jumpToDiagramSource(int index) {
    if (_isPreview) {
      setState(() => _isPreview = false);
    }
    final text = _editorController.text;
    final matches =
        RegExp(r'```mermaid[ \t]*\r?\n').allMatches(text).toList();
    if (index < 0 || index >= matches.length) return;
    final offset = matches[index].start;
    _editorController.selection =
        TextSelection.collapsed(offset: offset);
    // 让编辑区滚动到该位置（RequestFocus 后由框架处理可见性）
    _editorFocusNode.requestFocus();
  }

  /// Markdown 工具栏按钮（统一次要前景色，替代原先的默认图标色）
  Widget _toolbarBtn(IconData icon, String tooltip, VoidCallback onTap) {
    return IconButton(
      icon: Icon(icon, size: 18, color: context.tokens.fgMuted),
      tooltip: tooltip,
      onPressed: onTap,
      visualDensity: VisualDensity.compact,
      constraints: const BoxConstraints(minWidth: 36, minHeight: 36),
    );
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
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
                  // 有未保存修改时用语义警示色（替代原先的硬编码橙色）
                  : Icon(
                      Icons.save,
                      color: _isModified ? AppTokens.warning500 : null,
                    ),
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
                  // 下沉面 + 细分隔线（替代 surfaceContainerHighest.withAlpha）
                  color: t.bgSunken,
                  border: Border(
                    bottom: BorderSide(color: t.borderSubtle),
                  ),
                ),
                // 结构：左边是可横向滚动的 Markdown 按钮，右边是**常驻**的
                // 「插入图表」。原先插入图表排在滚动列表第 10 位，手机宽度下
                // 被挤出屏幕，必须横滑才能看到 —— 用户直接反馈"没有这个功能"。
                // 它是本版新增的能力，必须一眼可见，所以固定在右侧不参与滚动。
                child: Row(
                  children: [
                    Expanded(
                      child: ListView(
                        scrollDirection: Axis.horizontal,
                        padding: EdgeInsets.symmetric(horizontal: t.s1),
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
                    // 常驻分隔线 + 图表入口（用品牌色强调，与新功能的重要性匹配）
                    Container(
                      width: 1,
                      height: 20,
                      color: t.borderSubtle,
                    ),
                    IconButton(
                      key: const ValueKey('note-insert-diagram'),
                      icon: const Icon(
                        Icons.account_tree_outlined,
                        size: 18,
                        color: AppTokens.brand600,
                      ),
                      tooltip: '插入图表',
                      onPressed: _showDiagramPicker,
                      visualDensity: VisualDensity.compact,
                      constraints: const BoxConstraints(
                        minWidth: 40,
                        minHeight: 36,
                      ),
                    ),
                    SizedBox(width: t.s1),
                  ],
                ),
              ),
            Expanded(child: _buildBody()),
            Container(
              padding: EdgeInsets.symmetric(horizontal: t.s4, vertical: t.s1),
              decoration: BoxDecoration(
                // 底部状态条：下沉面 + 上分隔线
                color: t.bgSunken,
                border: Border(top: BorderSide(color: t.borderSubtle)),
              ),
              child: Row(
                children: [
                  Text(
                    _isPreview ? '预览模式' : '编辑模式',
                    style: TextStyle(fontSize: 12, color: t.fgMuted),
                  ),
                  const Spacer(),
                  if (_isModified)
                    // 未保存提示：语义警示色（替代原先的硬编码橙色）
                    const Text(
                      '● 未保存',
                      style: TextStyle(
                        color: AppTokens.warning500,
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
}
