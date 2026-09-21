import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show Clipboard, ClipboardData;
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:markdown/markdown.dart' as md;

import '../../config/design_tokens.dart';
import '../../widgets/mermaid_block.dart';

/// 取出围栏代码块的 `<code>` 元素（`pre > code`），入参已是 `code` 时原样返回
///
/// `markdown` 包对 Fenced code（```` ```lang ````）与缩进代码块都产出
/// `<pre><code ...>`：语言写在 **内层 code** 的 `class` 上，外层的 `pre`
/// 没有 `class`。所以判定 mermaid 必须看内层元素。
md.Element? fenceCodeElement(md.Element element) {
  if (element.tag == 'code') return element;
  for (final child in element.children ?? const <md.Node>[]) {
    if (child is md.Element && child.tag == 'code') return child;
  }
  return null;
}

/// Mermaid 图块分支
///
/// `flutter_markdown` 把 ```` ```mermaid ```` 当作普通围栏代码块处理成
/// `<pre><code class="language-mermaid">`，因此这里在 **`code`（内层）**
/// 元素上做分支：命中 `language-mermaid` 才接管，其余一律返回 `null` 不接管。
///
/// 注意：它**不能**直接注册到 `Markdown.builders` 上（详见 [CodeBlockBuilder]
/// 的注释），只应由 [CodeBlockBuilder] 在 `pre` 分支里调用。
class MermaidBuilder extends MarkdownElementBuilder {
  /// 块级元素必须返回 true，否则会被当作行内元素处理
  @override
  bool isBlockElement() => true;

  static int _counter = 0;
  final void Function(int index)? onJumpToSource;

  MermaidBuilder({this.onJumpToSource});

  @override
  Widget? visitElementAfterWithContext(
    BuildContext context,
    md.Element element,
    TextStyle? preferredStyle,
    TextStyle? parentStyle,
  ) {
    final cls = element.attributes['class'] ?? '';
    // 必须校验 class：否则所有行内代码与代码块都会被误接管
    if (!cls.contains('language-mermaid')) return null;
    final code = element.textContent.trim();
    if (code.isEmpty) return null;
    final index = _counter++;
    return MermaidBlock(
      code: code,
      index: index,
      onJumpToSource: onJumpToSource,
    );
  }

  /// 每次构建文档前重置计数，保证序号从 0 开始且与源码顺序一致
  static void resetCounter() => _counter = 0;
}

/// 围栏代码块统一入口：mermaid 图块分支 + 代码卡分支
///
/// **注册键必须是 `pre`（不是 `code`）** —— 这是被真实缺陷逼出来的硬约束：
///
/// `MarkdownBuilder.build()` 会把 `builders` 里 `isBlockElement() == true`
/// 的**键**写进 **全局** 的 `_kBlockTags`（`flutter_markdown/src/builder.dart`）。
/// 因此把块级 builder 注册在 `code` 键上，会让 `<code>` 在**全进程、永久**
/// 变成块级标签，于是：
///   1. 行内 `` `code` `` 与围栏代码块的文本都会走
///      `builders['code'].visitText(...)`，而 `MarkdownElementBuilder.visitText`
///      默认返回 `null` → **代码文本被整段丢弃**（正文「内容显示不完整」）；
///   2. 块级路径下 `_inlines` 会残留一个空的行内元素，
///      文档在 debug 下直接命中 `assert(_inlines.isEmpty)` 失败 → 渲染区报错。
///
/// 注册在 `pre` 上则完全避开以上两点：`pre` 本来就是块级标签（不需要靠
/// `isBlockElement()` 去注册），`visitElementAfter('pre')` 拿到的 element 里
/// 就是 `<code>`，语言标签照样能读到。
///
/// 注意：注册键落在 `pre` 之后，代码文本会被 flutter_markdown 交给
/// [visitText]（`_blocks.last.tag == 'pre'` 命中 `builders`）。这里**必须返回
/// 非 null** —— `visitText` 的返回值会被并入当前行内元素，而该行内元素是否
/// 非空决定 flutter_markdown 能否回收 `_inlines`；返回 `null` 会让文档收尾时
/// 命中 `assert(_inlines.isEmpty)`。正常路径下这个占位文本会被
/// [visitElementAfterWithContext] 返回的代码卡 / 图表整体替换，不会重复显示。
class CodeBlockBuilder extends MarkdownElementBuilder {
  CodeBlockBuilder({this.mermaid});

  /// mermaid 分支（命中 `language-mermaid` 时优先接管；为 null 则只渲染代码卡）
  final MermaidBuilder? mermaid;

  @override
  bool isBlockElement() => true;

  /// 围栏代码块的文本：交给占位 `Text` 保住 flutter_markdown 的行内账本，
  /// 真正的渲染由 [visitElementAfterWithContext] 接管（见类注释）
  @override
  Widget? visitText(md.Text text, TextStyle? preferredStyle) =>
      Text(text.text, style: preferredStyle);

  @override
  Widget? visitElementAfterWithContext(
    BuildContext context,
    md.Element element,
    TextStyle? preferredStyle,
    TextStyle? parentStyle,
  ) {
    final codeElement = fenceCodeElement(element);
    if (codeElement == null) return null;

    final cls = codeElement.attributes['class'] ?? '';
    // mermaid 优先：交给 MermaidBuilder 判定（它会自行校验 language-mermaid）
    if (cls.contains('language-mermaid')) {
      return mermaid?.visitElementAfterWithContext(
        context,
        codeElement,
        preferredStyle,
        parentStyle,
      );
    }

    final code = codeElement.textContent;
    if (code.trim().isEmpty) return null;

    final lang = cls.startsWith('language-')
        ? cls.substring('language-'.length)
        : '文本';

    return _CodeCard(language: lang, code: code);
  }
}

class _CodeCard extends StatelessWidget {
  const _CodeCard({required this.language, required this.code});

  final String language;
  final String code;

  @override
  Widget build(BuildContext context) {
    final t = context.tokens;
    // 代码块在亮暗主题下都保持深底（与 Web / 桌面端一致）
    const codeBg = Color(0xFF080D18);
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 10),
      decoration: BoxDecoration(
        color: codeBg,
        borderRadius: BorderRadius.circular(t.rLg),
        border: Border.all(color: t.borderDefault),
        boxShadow: t.e2,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
            decoration: const BoxDecoration(
              border: Border(
                bottom: BorderSide(color: Color(0x1AFFFFFF)),
              ),
            ),
            child: Row(
              children: [
                Text(
                  language.toUpperCase(),
                  style: const TextStyle(
                    fontSize: 10.5,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.5,
                    color: Color(0xFF94A3B8),
                  ),
                ),
                const Spacer(),
                _CopyButton(code: code),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(12),
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Text(
                code,
                style: const TextStyle(
                  fontFamily: 'monospace',
                  fontSize: 12,
                  height: 1.65,
                  color: Color(0xFFE2E8F0),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _CopyButton extends StatelessWidget {
  const _CopyButton({required this.code});

  final String code;

  @override
  Widget build(BuildContext context) {
    return TextButton.icon(
      onPressed: () async {
        await Clipboard.setData(ClipboardData(text: code));
        if (!context.mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('已复制代码')),
        );
      },
      icon: const Icon(Icons.copy_rounded, size: 13),
      label: const Text('复制', style: TextStyle(fontSize: 11)),
      style: TextButton.styleFrom(
        foregroundColor: const Color(0xFF94A3B8),
        minimumSize: const Size(0, 26),
        padding: const EdgeInsets.symmetric(horizontal: 8),
        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
      ),
    );
  }
}

/// 复选框渲染（GFM 任务清单）
class TaskCheckboxBuilder extends MarkdownElementBuilder {
  TaskCheckboxBuilder();

  @override
  Widget? visitElementAfter(md.Element element, TextStyle? preferredStyle) {
    final checked = element.attributes['checked'] != null;
    return Container(
      width: 16,
      height: 16,
      margin: const EdgeInsets.only(right: 8, top: 2),
      decoration: BoxDecoration(
        color: checked ? AppTokens.brand500 : Colors.transparent,
        border: Border.all(
          color: checked ? Colors.transparent : const Color(0xFFCBD5E1),
          width: 1.5,
        ),
        borderRadius: BorderRadius.circular(5),
      ),
      child: checked
          ? const Icon(Icons.check, size: 11, color: Colors.white)
          : null,
    );
  }
}

/// 构建与设计令牌对齐的 Markdown 样式表
MarkdownStyleSheet buildMarkdownStyleSheet(BuildContext context) {
  final t = context.tokens;
  return MarkdownStyleSheet(
    h1: TextStyle(fontSize: 24, fontWeight: FontWeight.w700, height: 1.35, color: t.fgDefault),
    h2: TextStyle(fontSize: 19, fontWeight: FontWeight.w600, height: 1.4, color: t.fgDefault),
    h3: TextStyle(fontSize: 16, fontWeight: FontWeight.w600, height: 1.4, color: t.fgDefault),
    h4: TextStyle(fontSize: 14.5, fontWeight: FontWeight.w600, color: t.fgDefault),
    h5: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600, color: t.fgMuted),
    h6: TextStyle(fontSize: 13, fontWeight: FontWeight.w600, color: t.fgMuted),
    h1Padding: const EdgeInsets.only(top: 6, bottom: 8),
    h2Padding: const EdgeInsets.only(top: 22, bottom: 8),
    h3Padding: const EdgeInsets.only(top: 18, bottom: 6),
    p: TextStyle(fontSize: 14.5, height: 1.72, color: t.fgDefault),
    pPadding: const EdgeInsets.symmetric(vertical: 5),
    a: const TextStyle(color: AppTokens.brand500, fontWeight: FontWeight.w500),
    em: const TextStyle(fontStyle: FontStyle.italic),
    strong: const TextStyle(fontWeight: FontWeight.w700),
    del: TextStyle(decoration: TextDecoration.lineThrough, color: t.fgSubtle),
    code: TextStyle(
      fontFamily: 'monospace',
      fontSize: 12.5,
      color: const Color(0xFFBE185D),
      backgroundColor: t.bgSunken,
    ),
    blockquote: TextStyle(fontSize: 14, height: 1.7, color: t.fgMuted),
    blockquotePadding: const EdgeInsets.fromLTRB(14, 8, 12, 8),
    blockquoteDecoration: BoxDecoration(
      color: t.bgSunken,
      borderRadius: const BorderRadius.only(
        topRight: Radius.circular(8),
        bottomRight: Radius.circular(8),
      ),
      border: const Border(
        left: BorderSide(color: AppTokens.brand300, width: 3),
      ),
    ),
    listBullet: TextStyle(fontSize: 14.5, height: 1.7, color: t.fgMuted),
    listIndent: 22,
    listBulletPadding: const EdgeInsets.only(right: 6),
    blockSpacing: 10,
    tableHead: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: t.fgDefault),
    tableBody: TextStyle(fontSize: 13, color: t.fgDefault),
    tableBorder: TableBorder.all(color: t.borderDefault, width: 1),
    tableCellsPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
    tableHeadAlign: TextAlign.left,
    tableColumnWidth: const IntrinsicColumnWidth(),
    horizontalRuleDecoration: BoxDecoration(
      border: Border(top: BorderSide(color: t.borderSubtle)),
    ),
  );
}
