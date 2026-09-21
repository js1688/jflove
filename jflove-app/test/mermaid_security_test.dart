import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart' show Brightness;
import 'package:flutter_test/flutter_test.dart';

import 'package:jflove_app/config/mermaid_theme.dart';
import 'package:jflove_app/services/mermaid/mermaid_render_service.dart';

/// Mermaid 渲染链路安全用例（v1.5.0，移动端）
///
/// 对照 `AGENTS.md §9`：
///   - §9.6 / §9.7：图表渲染不得成为新的注入面 —— 图表源码只能作为**数据**
///     进入渲染页，不能拼进可执行位置；
///   - §9.4：图表源码属笔记正文，日志不得出现明文。
///
/// 移动端渲染页是 `assets/mermaid/renderer.html`（构建期内联 bundle），
/// 本文件既做**模板契约**断言，也做**注入形态**断言（用真实模板跑替换）。
void main() {
  const templatePath = 'assets/mermaid/renderer.html';

  late String template;

  setUpAll(() {
    template = File(templatePath).readAsStringSync();
  });

  group('渲染模板契约', () {
    test('模板必须是纯 ASCII（历史上中文注释被按 ANSI 解码会破坏脚本字面量）', () {
      final bytes = File(templatePath).readAsBytesSync();
      final nonAscii = <int>[];
      for (var i = 0; i < bytes.length; i++) {
        if (bytes[i] > 127) nonAscii.add(i);
      }
      expect(
        nonAscii,
        isEmpty,
        reason: 'assets/mermaid/renderer.html 含 ${nonAscii.length} 个非 ASCII 字节',
      );
    });

    test('securityLevel=strict 且 flowchart.htmlLabels=false（安全基线）', () {
      expect(RegExp(r"securityLevel:\s*'strict'").hasMatch(template), isTrue);
      expect(RegExp(r'htmlLabels:\s*false').hasMatch(template), isTrue);
    });

    test('模板不引用任何远程资源（渲染全程零网络依赖）', () {
      expect(template.contains('http://'), isFalse);
      expect(template.contains('https://'), isFalse);
    });

    test('五个占位符都在脚本区里存在，且替换走 replaceAll（不会丢值）', () {
      for (final ph in ['__JF_BUNDLE__', '__JF_CODE__', '__JF_THEME__', '__JF_ID__', '__JF_WIDTH__']) {
        expect(template.contains(ph), isTrue, reason: '$ph 缺失，页面拿不到注入值');
      }
      // 模板里的赋值语句必须是「裸占位符」，不能自带引号（替换值已自带字面量）
      expect(RegExp(r'var MM_CODE = __JF_CODE__;').hasMatch(template), isTrue);
      expect(RegExp(r'var MM_THEME = __JF_THEME__;').hasMatch(template), isTrue);
      expect(RegExp(r'var MM_ID = __JF_ID__;').hasMatch(template), isTrue);
      // 宽度替换进去的是**裸数字字面量**（360），不是字符串
      expect(RegExp(r'var MM_WIDTH = __JF_WIDTH__;').hasMatch(template), isTrue);
      // 不做任何 JSON.parse（WebEngine 上实测不可靠），注入的已是可直接使用的字面量
      expect(template.contains('JSON.parse('), isFalse);
    });

    test('结果同时写入 window.__JF_RESULT__ 与 JFLoveBridge 通道', () {
      expect(template.contains('window.__JF_RESULT__ = text'), isTrue);
      expect(template.contains('JFLoveBridge'), isTrue);
    });

    /// 回归锁（v1.5.0 修复后）：
    ///
    /// 模板的 HTML 注释里曾经原样列出四个占位符名字，而 `buildHtml` 用的是
    /// `replaceAll`，于是：
    ///   ① `__JF_BUNDLE__` 被内联两次（脚本 1 次 + 注释 1 次）→ 渲染页 HTML
    ///      体积翻倍（约 3.4MB → 6.8MB）；
    ///   ② 图表源码含 `-->` 时注入到注释里会**提前闭合 HTML 注释**，注释剩余
    ///      文字变成页面可见文本。
    ///
    /// 现在注释放到 `<script>` 内的 JS 块注释里，且不再原样写占位符名字。
    /// 这两条断言保证它不会退化。
    test('占位符在模板里只出现一次（bundle 只能内联一次）', () {
      expect(
        '__JF_BUNDLE__'.allMatches(template).length,
        1,
        reason: '模板里多处写了 bundle 占位符 → bundle 会被 replaceAll 内联多次，页面体积翻倍',
      );
      for (final ph in ['__JF_CODE__', '__JF_THEME__', '__JF_ID__', '__JF_WIDTH__']) {
        expect(ph.allMatches(template).length, 1, reason: '$ph 在模板里出现多次');
      }
    });

    test('图表源码里的 `-->` 不会提前闭合注释（注释在 script 内，不是 HTML 注释）', () {
      final withArrow = template
          .replaceAll('__JF_BUNDLE__', '/*bundle*/')
          .replaceAll('__JF_CODE__', jsStringForTest('flowchart LR\n  A["-->"]'))
          .replaceAll('__JF_THEME__', jsObjectForTest(MermaidTheme.light.toPayload()))
          .replaceAll('__JF_ID__', jsStringForTest('sec-arrow'))
          .replaceAll('__JF_WIDTH__', '360');

      // 页面里不得存在 HTML 注释（`<!--`），否则 `-->` 就有机会提前闭合它
      expect(
        withArrow.contains('<!--'),
        isFalse,
        reason: '模板里还有 HTML 注释，注入含 --> 的源码会提前闭合它并泄露注释文字',
      );
      // 文档说明仍以 JS 块注释存在（证明文档没被删掉，只是换了位置）
      expect(withArrow.contains('/*'), isTrue);
      expect(withArrow.contains('HARD CONSTRAINTS'), isTrue);
    });
  });

  /// 尺寸归一化的回归锁（v1.5.0 反馈修复第二轮）。
  ///
  /// 背景：旧实现用 `svgEl.getBBox()` 当"整张图的内容尺寸"，而 mermaid 会**故意**
  /// 画一些远超图幅的辅助线、靠视口把它裁掉：
  ///   - 时序图 `line.actor-line` 画到 y=2000（视口才 ~234）
  ///   - 甘特图 `line.today` 落在 x=28399（视口才 ~980）
  /// 实测该实现把回传串的 viewBox 写成：
  ///   - 甘特图 `0 0 19289 128` → 图被压成一条细线（用户报「无法预览甘特图」）
  ///   - 时序图 `-50 -10 440 2000` → 缩在面板顶部约 12% 高度（用户报「显示面板有问题」）
  ///
  /// 真正端到端验证在 `设计预览/probe_app_renderer.mjs`（用真 bundle 跑这份模板）；
  /// 这里锁的是"别再退回 getBBox / getCTM"这个**做法**本身。
  ///
  /// ⚠ 断言必须打在**去掉注释后的代码**上：模板的说明注释里会提到这些 API 名字
  /// （就是为了讲清为什么不能用），直接对原文做子串断言会被自己的注释绊倒。
  group('尺寸归一化不得用 getBBox（辅助线会污染它）', () {
    /// 去掉 JS 块注释与行注释，只留可执行代码
    String codeOnly(String src) {
      var out = src.replaceAll(RegExp(r'/\*[\s\S]*?\*/'), ' ');
      out = out.replaceAll(RegExp(r'//[^\n]*'), ' ');
      return out;
    }

    late String code;
    setUpAll(() {
      code = codeOnly(template);
    });

    test('代码里不再用整图 getBBox 决定尺寸', () {
      expect(
        code.contains('svgEl.getBBox()'),
        isFalse,
        reason: '不得用整图 getBBox 当尺寸：会被超长辅助线污染，把图压扁',
      );
      // 旧实现的指纹（那一行整图量测）也不该再出现
      expect(code.contains('var bb = svgEl.getBBox();'), isFalse);
    });

    test('按「非空 text 全计入 + 图形按量级筛」来量内容范围', () {
      expect(code.contains('function mmContentBounds'), isTrue);
      expect(RegExp(r"querySelectorAll\('text'\)").hasMatch(code), isTrue);
      expect(
        code.contains("querySelectorAll('rect, path, polygon, circle, ellipse, line')"),
        isTrue,
      );
      // 辅助线筛选：超出量不得超过 viewBox 该方向的尺寸（下限 48）
      expect(code.contains('var limitX = Math.max(vb.w, 48);'), isTrue);
      expect(code.contains('var limitY = Math.max(vb.h, 48);'), isTrue);
    });

    test('defs / marker / clipPath / mask / symbol 里的定义块不参与量测', () {
      expect(code.contains('function mmIsInDefs'), isTrue);
      for (final tag in ['defs', 'marker', 'clippath', 'mask', 'symbol']) {
        expect(code.contains("'$tag'"), isTrue, reason: '缺少对 $tag 的排除');
      }
    });

    test('换算是"相对 SVG 自身矩形"的差值法，不用 getCTM', () {
      // 离屏页里 getCTM() 会把页面位置算进去（实测偏 30 万）
      expect(code.contains('mmRectToUser'), isTrue);
      expect(code.contains('getBoundingClientRect'), isTrue);
      expect(
        code.contains('getCTM'),
        isFalse,
        reason: '不得用 getCTM 做换算：离屏页会带上页面偏移',
      );
    });

    test('只放不缩：装得下时保持 mermaid 原来的 viewBox', () {
      // 生长分支只在"内容确实超出某一侧"时才扩大
      expect(code.contains('if (bounds.x1 < bx1)'), isTrue);
      expect(code.contains('if (bounds.y2 > by2)'), isTrue);
    });

    test('renderConfigVersion 已 +1（v4 缓存里存着错排版，必须失效）', () {
      expect(
        MermaidRenderService.renderConfigVersion,
        '5',
        reason: '改了渲染页尺寸/排版逻辑就必须 +1，否则旧缓存里的坏图会继续显示',
      );
    });

    test('按展示宽度排版：渲染页会把 html/body 钉成 Dart 传入的宽度', () {
      // 这是修「甘特图无法显示」的关键：Android WebView 默认 980px 布局视口，
      // 甘特图按 980 排版后在 360 宽屏上不可读/看不全。只钉 #stage 无效
      // （mermaid 看的是文档宽度），必须钉 html/body。
      expect(code.contains('function mmApplyTargetWidth'), isTrue);
      expect(code.contains('document.documentElement'), isTrue);
      expect(code.contains('document.body'), isTrue);
      // 必须在 initialize/render 之前应用，否则 mermaid 量到的还是 980
      final applyAt = code.indexOf('mmApplyTargetWidth();');
      expect(applyAt, greaterThan(-1), reason: '渲染流程里没有调用 mmApplyTargetWidth()');
      expect(code.indexOf('m.initialize('), greaterThan(applyAt),
          reason: '宽度必须在 initialize/render 之前应用');
    });
  });

  group('注入形态：恶意源码只能作为数据进入渲染页', () {
    const evil = 'flowchart LR\n'
        '  A["<script>alert(1)</script>"]\n'
        '  B["<img src=http://evil.example/x.png onerror=alert(1)>"]\n'
        '  C["[点我](javascript:alert(2))"]';

    /// 与 [MermaidRenderService.buildHtml] 相同的替换顺序（页面侧不做解析）
    String inject(String code) => template
        .replaceAll('__JF_BUNDLE__', '/* bundle inlined */')
        .replaceAll('__JF_CODE__', jsStringForTest(code))
        .replaceAll('__JF_THEME__', jsObjectForTest(MermaidTheme.light.toPayload()))
        .replaceAll('__JF_ID__', jsStringForTest('sec-1'))
        .replaceAll('__JF_WIDTH__', '360');

    test('字符串字面量必须转义 `<`，不能提前闭合 script 标签', () {
      final html = inject(evil);
      expect(html.contains('<script>alert(1)'), isFalse);
      expect(html.contains('</script><script>'), isFalse);
      expect(html.contains(r'\u003c'), isTrue);
      // 模板自带的 script 标签数量不变
      expect(
        '<script'.allMatches(html).length,
        '<script'.allMatches(template).length,
      );
      expect(
        '</script>'.allMatches(html).length,
        '</script>'.allMatches(template).length,
      );
    });

    test('注入后无占位符残留，且源码可被反解析还原（证明它只是数据）', () {
      final html = inject(evil);
      for (final ph in ['__JF_CODE__', '__JF_THEME__', '__JF_ID__']) {
        expect(html.contains(ph), isFalse, reason: '$ph 未被替换');
      }
      final m = RegExp(r'var MM_CODE = (.*?);').firstMatch(html);
      expect(m, isNotNull);
      expect(jsonDecode(m!.group(1)!), evil);
    });

    test('危险协议只作为字符串数据出现，没有进入任何标签属性', () {
      final html = inject(evil);
      // 用完整的危险片段计数（而不是裸的 `javascript:`）——模板注释里本来就写着
      // "no javascript: links" 这句说明，裸词计数会被它干扰。
      expect(
        'javascript:alert(2)'.allMatches(html).length,
        1,
        reason: '恶意 URL 只应作为源码数据出现一次',
      );
      // 排除「进入可执行位置」的形态
      expect(html.contains('href="javascript:'), isFalse);
      expect(html.contains('src="javascript:'), isFalse);
      expect(html.contains('<a href'), isFalse);
      // 外部回连地址同样只作为数据出现（模板里不含任何硬编码外链）
      expect('http://evil.example'.allMatches(html).length, 1);
    });

    test('jsStringForTest 输出合法 JSON 字符串字面量（含引号 / 换行 / 反斜杠）', () {
      for (final raw in [
        'flowchart LR\n  A-->B',
        'A["</script>"]',
        r'path\with\backslash',
        '中文 & <tag> "quoted"',
        "tab\tnewline\nescape\\end",
      ]) {
        expect(jsonDecode(jsStringForTest(raw)), raw);
      }
    });

    test('jsObjectForTest 把 `</` 拆开（对象字面量里 <\\/ 是合法转义）', () {
      final out = jsObjectForTest({'k': '</script>', 'n': 1});
      expect(out.contains('</script>'), isFalse);
      expect(out.contains(r'<\/script>'), isTrue);
      expect(jsonDecode(out.replaceAll(r'<\/', '</')), {'k': '</script>', 'n': 1});
    });

    test('主题载荷不含 CSS 变量（mermaid 不接受 var(--x)，会整图失败）', () {
      for (final theme in [MermaidTheme.light, MermaidTheme.dark]) {
        final payload = jsonEncode(theme.toPayload());
        expect(payload.contains('var('), isFalse);
        // fontFamily 里不能有双引号（jsonEncode 会转义成 \" 导致解析失败）
        expect(theme.toPayload()['fontFamily'].toString().contains('"'), isFalse);
      }
    });

    test('主题载荷里没有远程地址（图表不回连网络）', () {
      expect(jsonEncode(MermaidTheme.dark.toPayload()).contains('http'), isFalse);
    });
  });

  group('渲染页可用性与主题表', () {
    test('亮暗两套 themeVariables 键集合一致（改一处不能漏另一处）', () {
      expect(
        MermaidTheme.light.vars.keys.toSet(),
        MermaidTheme.dark.vars.keys.toSet(),
      );
    });

    test('亮暗关键色不同（AC-11 有实际效果的前提）', () {
      expect(MermaidTheme.light.vars['background'], isNot(MermaidTheme.dark.vars['background']));
      expect(
        MermaidTheme.light.vars['primaryColor'],
        isNot(MermaidTheme.dark.vars['primaryColor']),
      );
      expect(MermaidTheme.light.vars['lineColor'], isNot(MermaidTheme.dark.vars['lineColor']));
    });

    test('主题亮度映射与图表主题名稳定（缓存键依赖主题名）', () {
      expect(MermaidTheme.of(Brightness.dark).name, 'dark');
      expect(MermaidTheme.of(Brightness.light).name, 'light');
    });

    test('图表主题主色与设计令牌一致（品牌 500）', () {
      expect(MermaidTheme.light.vars['primaryBorderColor'], '#6366f1');
      expect(MermaidTheme.dark.vars['primaryBorderColor'], '#818cf8');
    });
  });
}
