/// Mermaid 渲染相关类型（移动端）
///
/// 与桌面端 `src/components/mermaid_render_service.py`、Web 端
/// `src/utils/mermaid/types.ts` 保持同一套语义，便于三端对齐行为。
library;

/// 渲染失败的原因分类
enum MermaidFailure {
  /// 语法/语义错误 —— 可定位到行号
  parse,

  /// 渲染引擎不可用（资源缺失 / WebView 不可用）
  engine,

  /// 渲染超时
  timeout,

  /// 其它运行期错误
  runtime,
}

/// 渲染结果（成功或失败，不抛异常）
class MermaidResult {
  const MermaidResult._({
    required this.ok,
    this.svg = '',
    this.width = 0,
    this.height = 0,
    this.reason,
    this.message = '',
    this.line = 0,
    this.fromCache = false,
  });

  factory MermaidResult.success({
    required String svg,
    required int width,
    required int height,
    bool fromCache = false,
  }) =>
      MermaidResult._(
        ok: true,
        svg: svg,
        width: width,
        height: height,
        fromCache: fromCache,
      );

  factory MermaidResult.failure({
    required MermaidFailure reason,
    required String message,
    int line = 0,
  }) =>
      MermaidResult._(
        ok: false,
        reason: reason,
        message: message,
        line: line,
      );

  final bool ok;
  final String svg;
  final int width;
  final int height;
  final MermaidFailure? reason;
  final String message;

  /// 1-based 行号；0 表示无法定位
  final int line;

  /// 是否命中缓存
  final bool fromCache;

  /// 面向用户的简短原因（不含笔记正文）
  String get displayReason {
    switch (reason) {
      case MermaidFailure.parse:
        return '语法错误';
      case MermaidFailure.timeout:
        return '渲染超时';
      case MermaidFailure.engine:
        return '渲染引擎不可用';
      default:
        return '渲染失败';
    }
  }
}

/// 从源码首行推断图表类型（仅用于展示标签，不参与渲染）。
///
/// 与 Web 端 `detectDiagramKind`、桌面端 `detect_diagram_kind` 保持一致，
/// 保证三端图块头部显示同一个类型名。
String detectDiagramKind(String code) {
  String first = '';
  for (final raw in code.split('\n')) {
    final line = raw.trim();
    if (line.isNotEmpty && !line.startsWith('%%')) {
      first = line.toLowerCase();
      break;
    }
  }
  if (first.isEmpty) return 'Mermaid';

  const table = <String, String>{
    'sequencediagram': 'Sequence',
    'gantt': 'Gantt',
    'classdiagram': 'Class',
    'statediagram': 'State',
    'erdiagram': 'ER',
    'pie': 'Pie',
    'journey': 'Journey',
    'gitgraph': 'Git',
    'mindmap': 'Mindmap',
    'timeline': 'Timeline',
    'quadrantchart': 'Quadrant',
    'xychart': 'XY Chart',
    'block': 'Block',
    'graph': 'Flowchart',
    'flowchart': 'Flowchart',
  };
  for (final entry in table.entries) {
    if (first.startsWith(entry.key)) return entry.value;
  }
  return 'Mermaid';
}
