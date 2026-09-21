import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:markdown/markdown.dart' as md;

import 'package:jflove_app/models/note.dart';
import 'package:jflove_app/pages/notes/note_edit_page.dart';
import 'package:jflove_app/providers/note_provider.dart';
import 'package:jflove_app/services/note_service.dart';
import 'package:jflove_app/utils/http_service.dart';
import 'package:jflove_app/utils/markdown/diagram_templates.dart';
import 'package:jflove_app/utils/markdown/markdown_builder.dart';
import 'package:jflove_app/utils/session.dart';
import 'package:jflove_app/widgets/mermaid_block.dart';

/// 笔记编辑页「插入图表」（需求 AC-15）回归用例
///
/// 用户反馈「笔记本没有插入图表的功能」：桌面端 / Web 端都有入口，只有移动端缺。
/// 这里锁死三件事：
///   1. 模板覆盖需求要求的全部图表类型，且都是合法 mermaid（关键字可被识别）；
///   2. 插入位置正确（光标处 / 无焦点时末尾）、围栏独占行首、光标落在片段之后；
///   3. **插入结果真的会被 MermaidBuilder 判定成图表**——不是普通代码块。
///
/// 注意：涉及 Widget 树的用例全部在结束时卸载并推完渲染超时，避免留下挂起 Timer。
class _FakeNoteService extends NoteService {
  _FakeNoteService(SessionManager session, this._content)
    : super(HttpService(session));

  final String _content;

  @override
  Future<Note> getNote(String filename) async =>
      Note(name: filename, content: _content, mtime: 0);
}

/// 需求 AC-15 要求覆盖的图表类型关键字（模板体首行）
const _requiredKinds = <String>[
  'flowchart',
  'sequenceDiagram',
  'stateDiagram-v2',
  'classDiagram',
  'erDiagram',
  'gantt',
  'pie',
];

void main() {
  group('模板与三端同源', () {
    test('覆盖需求要求的 7 种图表类型', () {
      final kinds = kDiagramTemplates
          .map((t) => t.body.split('\n').first.trim())
          .toList();
      for (final kind in _requiredKinds) {
        expect(
          kinds.any((k) => k.startsWith(kind)),
          isTrue,
          reason: '缺少 $kind 模板（实际：$kinds）',
        );
      }
      // 关键字与图表类型一一对应，避免出现重复类型
      expect(kinds.length, _requiredKinds.length);
    });

    test('标签与桌面端 / Web 端一致', () {
      expect(kDiagramTemplates.map((t) => t.label).toList(), [
        '流程图',
        '时序图',
        '状态图',
        '类图',
        'ER 图',
        '甘特图',
        '饼图',
      ]);
    });

    test('模板片段是闭合的 ```mermaid 围栏，且结尾有换行', () {
      for (final template in kDiagramTemplates) {
        final snippet = buildDiagramSnippet(template);
        expect(snippet, startsWith('```mermaid\n'));
        expect(snippet, endsWith('\n```\n'));
        // 恰好两个围栏标记：一个开、一个闭
        expect(
          RegExp('```').allMatches(snippet).length,
          2,
          reason: '${template.label} 的围栏不闭合会把后续正文吞进代码块',
        );
        // 不得自带渲染配置：配置由 assets/mermaid/renderer.html 统一给出
        expect(snippet.contains('%%{'), isFalse);
      }
    });
  });

  group('插入到光标处', () {
    test('行中间插入：关键字在、围栏独占行首、光标落在片段之后', () {
      final controller = TextEditingController(text: '第一段\n\n第二段');
      // 光标停在第二行行首（"第二段" 之前，即第二个 \n 之后）
      controller.selection = const TextSelection.collapsed(offset: 5);
      addTearDown(controller.dispose);

      final template = kDiagramTemplates.first; // 流程图
      final caret = insertDiagramTemplate(controller, template);

      expect(controller.text, contains('flowchart LR'));
      expect(controller.text, contains('A[开始]'));
      // 插入点在光标处：前 4 个字符原样保留
      expect(controller.text.startsWith('第一段\n'), isTrue);
      // 围栏必须独占行首，否则不会被识别为围栏代码块
      expect(RegExp(r'(^|\n)```mermaid\n').hasMatch(controller.text), isTrue);
      // 光标落在插入片段之后
      expect(caret, controller.selection.baseOffset);
      expect(controller.selection.isCollapsed, isTrue);
      expect(
        controller.text.substring(0, caret).endsWith('```\n'),
        isTrue,
        reason: '光标之后应只剩插入点之后的原内容',
      );
      expect(controller.text.substring(caret), '第二段');
    });

    test('光标在文末时直接追加，不产生多余空行丢失正文', () {
      final controller = TextEditingController(text: '正文');
      controller.selection = const TextSelection.collapsed(offset: 2);
      addTearDown(controller.dispose);

      final template = kDiagramTemplates[1]; // 时序图
      final caret = insertDiagramTemplate(controller, template);

      expect(controller.text, startsWith('正文\n\n```mermaid\n'));
      expect(controller.text, contains('sequenceDiagram'));
      expect(controller.text.substring(caret), isEmpty);
      expect(controller.text.substring(0, caret).endsWith('```\n'), isTrue);
    });

    test('控件从未获焦点（selection 非法）时追加到末尾，原内容不丢', () {
      final controller = TextEditingController(text: '原来的正文');
      expect(controller.selection.isValid, isFalse); // text 构造器的初始 selection
      addTearDown(controller.dispose);

      final caret = insertDiagramTemplate(controller, kDiagramTemplates[6]);

      expect(controller.text, startsWith('原来的正文'));
      expect(controller.text, contains('pie showData'));
      expect(caret, controller.text.length);
      expect(controller.selection.baseOffset, controller.text.length);
    });

    test('有选区时替换选中内容（与工具栏其它按钮一致）', () {
      final controller = TextEditingController(text: '保留[删掉这段]保留');
      controller.selection = const TextSelection(baseOffset: 2, extentOffset: 8);
      addTearDown(controller.dispose);

      insertDiagramTemplate(controller, kDiagramTemplates[3]); // 类图

      expect(controller.text.contains('删掉这段'), isFalse);
      expect(controller.text, contains('classDiagram'));
      expect(controller.text.startsWith('保留'), isTrue);
      expect(controller.text.endsWith('保留'), isTrue);
    });

    test('整体赋 TextEditingValue：选区合法且 composing 已清空', () {
      final controller = TextEditingController(text: 'x');
      controller.selection = const TextSelection.collapsed(offset: 1);
      addTearDown(controller.dispose);

      insertDiagramTemplate(controller, kDiagramTemplates.first);

      expect(controller.value.selection.isValid, isTrue);
      expect(controller.value.composing, TextRange.empty);
      expect(controller.value.text.length, controller.value.selection.baseOffset);
    });
  });

  group('与预览渲染链路一致', () {
    testWidgets('插入结果被 MermaidBuilder 判定为图表，而不是普通代码块', (tester) async {
      for (final template in kDiagramTemplates) {
        final snippet = buildDiagramSnippet(template);
        // 与 note_edit_page 完全相同的注册方式
        final builder = CodeBlockBuilder(mermaid: MermaidBuilder());
        Widget? built;
        await tester.pumpWidget(
          MaterialApp(
            home: Builder(
              builder: (ctx) {
                // flutter_markdown 的解析配置（widget.dart: md.Document +
                // gitHubFlavored + encodeHtml:false）
                final document = md.Document(
                  extensionSet: md.ExtensionSet.gitHubFlavored,
                  encodeHtml: false,
                );
                final pre = document.parseLines(snippet.split('\n')).first;
                final code = (pre as md.Element).children!.first as md.Element;
                built = builder.visitElementAfterWithContext(
                  ctx,
                  pre,
                  null,
                  null,
                );
                // 语言标记必须落在内层 code 上（MermaidBuilder 的判定依据）
                expect(code.attributes['class'], 'language-mermaid');
                expect(builder.visitText(md.Text('x'), null), isNotNull);
                return const SizedBox();
              },
            ),
          ),
        );
        expect(
          built,
          isA<MermaidBlock>(),
          reason: '${template.label} 没有被识别成图表（会退化显示成代码块）',
        );
      }
    });

    testWidgets('预览区真的会挂上 MermaidBlock，且不出现代码卡', (tester) async {
      final snippet = buildDiagramSnippet(kDiagramTemplates.first);
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: Markdown(
              data: snippet,
              selectable: true,
              styleSheet: MarkdownStyleSheet(),
              builders: {'pre': CodeBlockBuilder(mermaid: MermaidBuilder())},
            ),
          ),
        ),
      );
      expect(find.byType(MermaidBlock), findsOneWidget);
      // 代码卡的语言标签不应出现（说明走的是图表分支）
      expect(find.text('MERMAID'), findsNothing);
      expect(find.text('复制'), findsNothing);

      // 卸载并推完渲染超时，确保不留挂起 Timer
      await tester.pumpWidget(const SizedBox());
      await tester.pump(const Duration(seconds: 9));
    });
  });

  group('编辑页入口', () {
    testWidgets('工具栏「插入图表」→ 选流程图 → 编辑器出现 ```mermaid 模板', (tester) async {
      tester.view.physicalSize = const Size(1200, 2400);
      tester.view.devicePixelRatio = 3.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            noteServiceProvider.overrideWithValue(
              _FakeNoteService(SessionManager(), '第一段\n\n第二段'),
            ),
          ],
          child: const MaterialApp(home: NoteEditPage(noteId: 'demo.md')),
        ),
      );
      await tester.pumpAndSettle();

      // 默认是预览模式：先切到编辑
      await tester.tap(find.byTooltip('编辑'));
      await tester.pumpAndSettle();
      expect(find.byTooltip('插入图表'), findsOneWidget);

      await tester.tap(find.byTooltip('插入图表'));
      await tester.pumpAndSettle();

      // 类型选择弹层：至少覆盖 7 种类型
      for (final label in kDiagramTemplates.map((t) => t.label)) {
        expect(find.text(label), findsOneWidget, reason: '弹层缺少「$label」');
      }

      await tester.tap(find.text('流程图'));
      await tester.pumpAndSettle();

      final field = tester.widget<TextField>(find.byType(TextField));
      final controller = field.controller!;
      expect(controller.text, contains('```mermaid'));
      expect(controller.text, contains('flowchart LR'));
      expect(controller.text, startsWith('第一段\n\n第二段'));
      // 光标在插入片段之后
      expect(controller.selection.isCollapsed, isTrue);
      expect(controller.selection.baseOffset, controller.text.length);
      // 标记为未保存
      expect(find.text('● 未保存'), findsOneWidget);
    });

    testWidgets('窄屏下「插入图表」必须在可视区内（不能被工具栏滚动挤出去）', (tester) async {
      // 回归锁：用户反馈「APP 端没有插入图表功能」。
      // 实际原因是它原本排在**横向滚动工具栏的第 10 位**，手机宽度下被挤出屏幕，
      // 必须横滑才能看到 —— 也就是用户说的"功能按钮超出范围、显示不全"。
      // `find.byTooltip` 找得到离屏控件，所以光有存在性断言抓不住这个问题，
      // 必须做几何断言。
      tester.view.physicalSize = const Size(1080, 2400);
      tester.view.devicePixelRatio = 3.0; // 逻辑宽 360dp，iPhone SE / 主流安卓宽度
      addTearDown(tester.view.reset);

      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            noteServiceProvider.overrideWithValue(
              _FakeNoteService(SessionManager(), '正文'),
            ),
          ],
          child: const MaterialApp(home: NoteEditPage(noteId: 'narrow.md')),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('编辑'));
      await tester.pumpAndSettle();

      final finder = find.byTooltip('插入图表');
      expect(finder, findsOneWidget);

      final rect = tester.getRect(finder);
      final screen = tester.view.physicalSize / tester.view.devicePixelRatio;
      expect(
        rect.left >= 0 && rect.right <= screen.width,
        isTrue,
        reason: '「插入图表」超出屏幕：rect=$rect 屏幕宽=${screen.width}',
      );
      expect(
        rect.top >= 0 && rect.bottom <= screen.height,
        isTrue,
        reason: '「插入图表」纵向超出屏幕：rect=$rect',
      );

      // 关键：命中测试必须落在按钮上（离屏控件即使"存在"也点不到）
      final center = tester.getCenter(finder);
      final hit = tester.hitTestOnBinding(center);
      final hitSelf = hit.path.any(
        (entry) => entry.target == finder.evaluate().single.renderObject,
      );
      expect(
        hitSelf,
        isTrue,
        reason: '按钮虽在树里但点击命中不到（被挤出可视区或被遮挡）',
      );

      // 它不应位于横向滚动容器内（滚动容器的子项会被滑出屏幕）
      final inHorizontalScrollable = find
          .ancestor(
            of: finder,
            matching: find.byWidgetPredicate(
              (w) =>
                  w is Scrollable && w.axisDirection == AxisDirection.right,
            ),
          )
          .evaluate()
          .isNotEmpty;
      expect(
        inHorizontalScrollable,
        isFalse,
        reason: '「插入图表」不应放在横向滚动工具栏里，否则窄屏会被挤出可视区',
      );

      // 真的能点开类型选择弹层
      await tester.tap(finder);
      await tester.pumpAndSettle();
      expect(find.text('流程图'), findsOneWidget);
    });

    testWidgets('插入可以撤销：撤销后回到插入前', (tester) async {
      tester.view.physicalSize = const Size(1200, 2400);
      tester.view.devicePixelRatio = 3.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            noteServiceProvider.overrideWithValue(
              _FakeNoteService(SessionManager(), ''),
            ),
          ],
          child: const MaterialApp(home: NoteEditPage(noteId: 'demo.md')),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('编辑'));
      await tester.pumpAndSettle();

      final controller = tester
          .widget<TextField>(find.byType(TextField))
          .controller!;

      // 先造一个「用户输入过」的基线（UndoHistory 是 500ms 节流入栈）
      await tester.enterText(find.byType(TextField), '第一段\n\n第二段');
      await tester.pump(const Duration(milliseconds: 600));
      expect(controller.text, '第一段\n\n第二段');

      await tester.tap(find.byTooltip('插入图表'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('饼图'));
      await tester.pumpAndSettle();
      expect(controller.text, contains('pie showData'));

      // 推过插入这一次的节流窗口
      await tester.pump(const Duration(milliseconds: 600));

      // 走框架自己的撤销入口（编辑器获得焦点后 UndoManager 会注册 client）
      final client = UndoManager.client;
      expect(client, isNotNull, reason: '编辑区应已获得焦点（否则撤销链路不生效）');
      expect(
        client!.canUndo,
        isTrue,
        reason: '插入必须记入 undo 栈：`controller.text = ...` 会把 selection '
            '置为非法值（offset -1），EditableText 的 shouldChangeUndoStack '
            '会因此拒绝入栈，插入就再也撤不回来',
      );
      client.undo();
      await tester.pumpAndSettle();

      expect(
        controller.text,
        '第一段\n\n第二段',
        reason: '撤销后应完整回到插入前的正文',
      );

      await tester.pump(const Duration(milliseconds: 600));
    });
  });
}
