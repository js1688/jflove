import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// 设计令牌一致性测试（v1.5.0）
///
/// 锁死「颜色字面量只允许出现在令牌文件里」这条约定（需求 AC-3）。
///
/// 为什么必须静态扫描而不是靠人眼：硬化编码颜色**不会报错**，只会让暗色
/// 模式下出现白底黑字、或者品牌色不一致。这类问题在亮色模式下完全看不出来，
/// 等到用户切暗色才发现，回归成本极高。
void main() {
  /// 唯一允许出现颜色字面量的文件（白名单，逐条注明理由）
  const allowList = <String, String>{
    'lib/config/design_tokens.dart': '设计令牌本体：全部颜色的唯一真源',
    'lib/config/theme.dart': 'Material ColorScheme 种子与角色派生',
    'lib/config/mermaid_theme.dart': 'mermaid themeVariables 必须是字面量，不能是 var()',
    'lib/utils/markdown/markdown_builder.dart':
        'Markdown 语法高亮配色需与三端统一，直接给出字面量',
  };

  /// 明确允许的「非主题色」用法（与主题切换无关，不影响暗色模式）
  const allowedNonThemeColors = [
    'Colors.transparent', // 透明不是颜色，是"无"
    'Colors.white', // 渐变/主色按钮上的前景与进度指示
    'Colors.black', // 同上，阴影与半透明遮罩
  ];

  List<File> dartFilesUnder(String dir) {
    final d = Directory(dir);
    if (!d.existsSync()) return const [];
    return d
        .listSync(recursive: true)
        .whereType<File>()
        .where((f) => f.path.endsWith('.dart'))
        .toList()
      ..sort((a, b) => a.path.compareTo(b.path));
  }

  /// 归一化路径为 `lib/xxx.dart`（相对项目根，正斜杠）
  String relPath(File f) {
    final normalized = f.path.replaceAll(r'\', '/');
    final idx = normalized.indexOf('lib/');
    return idx < 0 ? normalized : normalized.substring(idx);
  }

  test('lib/ 下除白名单外不得出现硬编码颜色', () {
    final offenders = <String>[];

    for (final file in dartFilesUnder('lib')) {
      final rel = relPath(file);
      if (allowList.containsKey(rel)) continue;

      final lines = file.readAsLinesSync();
      for (var i = 0; i < lines.length; i++) {
        final line = lines[i];

        // 跳过注释行（注释里举例说明颜色是允许的）
        final trimmed = line.trimLeft();
        if (trimmed.startsWith('//') || trimmed.startsWith('///')) continue;
        // 跳过行尾注释之后的残留检查：如果去掉注释后没有颜色引用就放过
        final code = line.contains('//') ? line.split('//').first : line;
        if (code.trim().isEmpty) continue;

        // ① Color(0x...) 字面量：任何位置都不允许
        if (RegExp(r'Color\(0x').hasMatch(code)) {
          offenders.add('$rel:${i + 1} 出现 Color(0x…) 字面量');
          continue;
        }

        // ② Colors.xxx 语义色：允许白名单里的非主题色
        for (final m in RegExp(r'Colors\.[A-Za-z0-9_]+').allMatches(code)) {
          final hit = m.group(0)!;
          if (allowedNonThemeColors.contains(hit)) continue;
          // 允许 Colors.transparent / white 的 shade / withValues 变体
          if (allowedNonThemeColors.any(hit.startsWith)) continue;
          offenders.add('$rel:${i + 1} 出现 $hit');
        }
      }
    }

    expect(
      offenders,
      isEmpty,
      reason:
          '以下位置仍在硬编码颜色，请改用 context.tokens（AppTokens）：\n'
          '${offenders.join('\n')}',
    );
  });

  test('令牌白名单文件确实存在（避免白名单写错后静默失效）', () {
    for (final rel in allowList.keys) {
      expect(
        File(rel).existsSync(),
        isTrue,
        reason: '白名单里的 $rel 不存在，说明路径写错了，扫描会失效',
      );
    }
  });

  test('页面不得直接使用 Theme.of(context).colorScheme 取色（应用令牌或统一组件）', () {
    // colorScheme 的 primary 在暗色下仍然是品牌色，看似安全，但 surface/
    // surfaceContainerHighest 等在两端差异很大，混用会导致同一页面出现两种灰。
    final offenders = <String>[];
    for (final file in dartFilesUnder('lib/pages')) {
      final lines = file.readAsLinesSync();
      for (var i = 0; i < lines.length; i++) {
        final code = lines[i].contains('//')
            ? lines[i].split('//').first
            : lines[i];
        if (RegExp(
          r'colorScheme\.(surface|background|onSurface|outline)',
        ).hasMatch(code)) {
          offenders.add('${relPath(file)}:${i + 1} ${code.trim()}');
        }
      }
    }
    expect(offenders, isEmpty, reason: offenders.join('\n'));
  });
}
