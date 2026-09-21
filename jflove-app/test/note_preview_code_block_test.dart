import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:jflove_app/models/note.dart';
import 'package:jflove_app/pages/notes/note_edit_page.dart';
import 'package:jflove_app/providers/note_provider.dart';
import 'package:jflove_app/services/note_service.dart';
import 'package:jflove_app/utils/http_service.dart';
import 'package:jflove_app/utils/markdown/markdown_builder.dart';
import 'package:jflove_app/utils/session.dart';
import 'package:jflove_app/widgets/mermaid_block.dart';

/// 笔记预览的代码块回归用例（用户反馈「内容显示不完整 / 识别不了 ```」）
///
/// 根因（已复现）：`builders` 里把块级 builder 注册在 **`code`** 键上。
/// `flutter_markdown` 的 `MarkdownBuilder.build()` 会把 `isBlockElement()==true`
/// 的键写进**全局** `_kBlockTags`，于是 `<code>` 在全进程内变成块级标签：
///   1. `visitText` 把行内代码与围栏代码的文本交给 `builders['code'].visitText`，
///      而默认实现返回 `null` → **代码文本被整段丢弃**；
///   2. 收尾时命中 `assert(_inlines.isEmpty)` → 预览区直接报错、内容显示不全。
///
/// 修法：注册键改为 `pre`，由 `CodeBlockBuilder` 分派「mermaid 图表 / 代码卡」，
/// 并在 `visitText` 返回占位文本以保住 flutter_markdown 的行内账本。
class _FakeNoteService extends NoteService {
  _FakeNoteService(SessionManager session, this._content)
    : super(HttpService(session));

  final String _content;

  @override
  Future<Note> getNote(String filename) async =>
      Note(name: filename, content: _content, mtime: 0);
}

/// 预览区渲染出的全部文本（`selectable: true` 时正文是 SelectableText.rich）
List<String> _renderedText(WidgetTester tester) => <String>[
  for (final w in tester.widgetList(find.byType(SelectableText)))
    (w as SelectableText).textSpan?.toPlainText() ?? '',
  for (final w in tester.widgetList(find.byType(Text)))
    (w as Text).data ?? w.textSpan?.toPlainText() ?? '',
].where((s) => s.isNotEmpty).toList();

Widget _preview(String data) => MaterialApp(
  home: Scaffold(
    body: Markdown(
      data: data,
      selectable: true,
      styleSheet: MarkdownStyleSheet(),
      // 与 note_edit_page.dart 完全一致
      builders: {'pre': CodeBlockBuilder(mermaid: MermaidBuilder())},
    ),
  ),
);

void main() {
  group('代码块内容不能被吞掉', () {
    testWidgets('行内 `code` 文本照常显示（此前被整段丢弃）', (tester) async {
      await tester.pumpWidget(_preview('这里有一段 `inline_code` 行内代码\n'));
      expect(_renderedText(tester).join(), contains('inline_code'));
      expect(tester.takeException(), isNull);
    });

    testWidgets('```python 围栏渲染成代码卡：语言标签 + 源码都在', (tester) async {
      await tester.pumpWidget(
        _preview('正文\n\n```python\nprint("SENTINEL_CODE")\n```\n\n尾段\n'),
      );
      final texts = _renderedText(tester);
      expect(texts, contains('PYTHON')); // 代码卡语言标签
      expect(texts, contains('复制')); // 代码卡复制按钮
      expect(texts.join(), contains('SENTINEL_CODE'));
      expect(texts.join(), contains('尾段'));
      expect(tester.takeException(), isNull);
    });

    testWidgets('无语言围栏用「文本」作为标签，内容照常显示', (tester) async {
      await tester.pumpWidget(_preview('```\n裸围栏 SENTINEL_RAW\n```\n'));
      final texts = _renderedText(tester);
      expect(texts, contains('文本'));
      expect(texts.join(), contains('SENTINEL_RAW'));
      expect(tester.takeException(), isNull);
    });

    testWidgets('代码块收尾的文档不再触发 assert(_inlines.isEmpty)', (tester) async {
      // 这是修复前最恶性的表现：代码块是文档最后一块时，flutter_markdown 的
      // 行内账本无法回收 → 预览区整块报错（debug 下直接红屏）
      for (final doc in <String>[
        '```\nRAW\n```\n',
        '```python\nprint(1)\n```\n',
        '段落\n\n```python\nx = 1\n```\n',
        '    缩进代码块\n',
        '```mermaid\nflowchart LR\n  A-->B\n```\n',
      ]) {
        await tester.pumpWidget(_preview(doc));
        expect(tester.takeException(), isNull, reason: '文档结尾是代码块时不应报错：$doc');
      }
      await tester.pumpWidget(const SizedBox());
      await tester.pump(const Duration(seconds: 9));
    });
  });

  group('mermaid 分支不受影响', () {
    testWidgets('```mermaid 仍然渲染成图表，不会退化成代码卡', (tester) async {
      await tester.pumpWidget(
        _preview('# 图\n\n```mermaid\nflowchart LR\n  A[开始] --> B[结束]\n```\n\n尾段\n'),
      );
      expect(find.byType(MermaidBlock), findsOneWidget);
      final texts = _renderedText(tester);
      // 图表源码不应作为代码卡再出现一次
      expect(texts.any((s) => s.contains('flowchart LR')), isFalse);
      expect(texts.join(), contains('尾段'));
      expect(tester.takeException(), isNull);

      await tester.pumpWidget(const SizedBox());
      await tester.pump(const Duration(seconds: 9));
    });
  });

  group('编辑页真实预览链路', () {
    testWidgets('默认预览模式：python 围栏显示成代码卡，长笔记尾部内容在', (tester) async {
      tester.view.physicalSize = const Size(1200, 3000);
      tester.view.devicePixelRatio = 3.0;
      addTearDown(tester.view.reset);

      const content =
          '# 项目说明\n\n'
          '第一段，含 `code` 行内代码。\n\n'
          '```python\nprint("hello")\n```\n\n'
          '结尾段落 SENTINEL_END。\n';

      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            noteServiceProvider.overrideWithValue(
              _FakeNoteService(SessionManager(), content),
            ),
          ],
          child: const MaterialApp(home: NoteEditPage(noteId: 'demo.md')),
        ),
      );
      await tester.pumpAndSettle();

      // 默认就是预览模式
      expect(find.text('预览模式'), findsOneWidget);
      final texts = _renderedText(tester).join('\n');
      expect(texts, contains('PYTHON'));
      expect(texts, contains('print("hello")'));
      expect(texts, contains('code')); // 行内代码
      expect(texts, contains('SENTINEL_END'));
      expect(tester.takeException(), isNull);
    });
  });
}
