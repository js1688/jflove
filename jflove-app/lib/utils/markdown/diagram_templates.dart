import 'package:flutter/material.dart';

/// 「插入图表」的 Mermaid 模板（需求 AC-15）
///
/// 与桌面端 `_DIAGRAM_TEMPLATES`、Web 端 `DIAGRAM_TEMPLATES` 保持同一份内容
/// （流程图 / 时序图 / 状态图 / 类图 / 甘特图 / 饼图），并补齐三端都还缺的
/// ER 图，使移动端覆盖需求要求的全部图表类型。
///
/// 每个模板都是**可直接渲染**的最小示例：标签用中文，便于用户据此改写成
/// 自己的内容；语法经 mermaid 11.16.0（与 `assets/mermaid/` 内 bundle 同版本）
/// 解析校验通过。
///
/// 注意：模板里**不能**带样式指令（如 `%%{init}%%`）——渲染配置由
/// `assets/mermaid/renderer.html` 统一给出（`htmlLabels` 必须在配置顶层），
/// 笔记内容不得覆盖三端统一的渲染配置。
class DiagramTemplate {
  /// 展示名（与桌面端 / Web 端一致，三端同一标签）
  final String label;

  /// mermaid 源码（不含围栏，围栏由 [buildDiagramSnippet] 生成）
  final String body;

  const DiagramTemplate(this.label, this.body);
}

/// 图表类型与模板（顺序即选择列表顺序）
const List<DiagramTemplate> kDiagramTemplates = <DiagramTemplate>[
  DiagramTemplate(
    '流程图',
    'flowchart LR\n'
        '    A[开始] --> B{条件判断}\n'
        '    B -- 是 --> C[分支一]\n'
        '    B -- 否 --> D[分支二]',
  ),
  DiagramTemplate(
    '时序图',
    'sequenceDiagram\n'
        '    participant C as 客户端\n'
        '    participant S as 服务端\n'
        '    C->>S: 请求\n'
        '    S-->>C: 响应\n'
        '    Note over C,S: 说明',
  ),
  DiagramTemplate(
    '状态图',
    'stateDiagram-v2\n'
        '    [*] --> 待处理\n'
        '    待处理 --> 处理中: 开始\n'
        '    处理中 --> 完成: 成功\n'
        '    处理中 --> 失败: 出错\n'
        '    完成 --> [*]',
  ),
  DiagramTemplate(
    '类图',
    'classDiagram\n'
        '    class 用户 {\n'
        '      +int id\n'
        '      +string name\n'
        '      +登录()\n'
        '    }\n'
        '    用户 --> 会话 : 拥有',
  ),
  DiagramTemplate(
    'ER 图',
    'erDiagram\n'
        '    用户 ||--o{ 会话 : 拥有\n'
        '    用户 {\n'
        '      int id\n'
        '      string name\n'
        '    }',
  ),
  DiagramTemplate(
    '甘特图',
    'gantt\n'
        '    title 项目排期\n'
        '    dateFormat YYYY-MM-DD\n'
        '    section 阶段一\n'
        '    需求梳理 :done, a1, 2026-01-01, 5d\n'
        '    方案设计 :active, a2, after a1, 6d',
  ),
  DiagramTemplate(
    '饼图',
    'pie showData\n'
        '    title 构成占比\n'
        '    "A" : 45\n'
        '    "B" : 30\n'
        '    "C" : 25',
  ),
];

/// 把模板包装成可直接写进笔记的 Markdown 片段（```mermaid 围栏）
///
/// 结尾**必须**带换行：否则游标之后的内容会被吞进围栏里当作 mermaid 源码。
String buildDiagramSnippet(DiagramTemplate template) =>
    '```mermaid\n${template.body}\n```\n';

/// 把图表模板插入 [controller] 的当前光标处，返回插入片段之后的光标偏移
///
/// 细节（都是踩过的坑）：
///   1. **围栏必须独占行首**：光标停在行中间时直接插 ```` ```mermaid ```` 不会被
///      识别为围栏代码块，所以先补换行（前面已有换行时补一个空行，保持排版）；
///   2. **必须整体赋 `TextEditingValue`**：`controller.text = ...` 会把 selection
///      重置为非法值（offset -1），编辑器 undo/redo 栈随之失效——用户插入图表后
///      按撤销就回不去了。这里连同 selection 一起赋值；
///   3. 光标落在插入片段**之后**（新的一行），方便继续输入；
///   4. 有选区时按"替换选中内容"处理，与工具栏其它按钮的行为一致。
int insertDiagramTemplate(
  TextEditingController controller,
  DiagramTemplate template,
) {
  final text = controller.text;
  final selection = controller.selection;
  // 没有有效选区（例如控件从未获得焦点）时追加到末尾
  final hasSelection =
      selection.isValid && selection.start >= 0 && selection.end <= text.length;
  final start = hasSelection ? selection.start : text.length;
  final end = hasSelection ? selection.end : text.length;

  final snippet = buildDiagramSnippet(template);
  final prefix = _linePrefixFor(text, start);
  final inserted = '$prefix$snippet';

  final newText = text.replaceRange(start, end, inserted);
  final caret = start + inserted.length;

  controller.value = TextEditingValue(
    text: newText,
    selection: TextSelection.collapsed(offset: caret),
    composing: TextRange.empty,
  );
  return caret;
}

/// 保证围栏落在行首所需的换行前缀
String _linePrefixFor(String text, int offset) {
  if (offset <= 0 || text.isEmpty) return '';
  if (text[offset - 1] == '\n') return '\n'; // 行首：补空行分隔
  return '\n\n'; // 行中间：先断行，再空一行
}
