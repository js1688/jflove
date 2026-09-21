import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// 展示 SVG 的 WebView 安全约束用例（v1.5.0，移动端）
///
/// `AGENTS.md §9.4 / §9.6` + 设计文档 §4.4.4：
///   1. **渲染页**（需要执行 mermaid JS）用 `JavaScriptMode.unrestricted`，
///      且只通过 `JFLoveBridge` 一条通道回传结果；
///   2. **展示页**（缓存命中的静态 SVG、全屏查看器）必须
///      `JavaScriptMode.disabled` —— 它们只负责显示已经渲染好的 SVG；
///   3. `NavigationDelegate` 只放行 `about:` / `data:`，其余一律 prevent
///      （渲染与展示全程零网络依赖，防回连）；
///   4. 渲染运行时文件不得引用任何远程地址；
///   5. 日志（`debugPrint`）不得出现图表源码 / SVG / bundle 内容。
///
/// 这些都是"代码形态"约束，用静态扫描锁死；行为链路由
/// `mermaid_security_test.dart`（注入契约）与 `mermaid_render_test.dart`（缓存/解析）覆盖。
void main() {
  const blockPath = 'lib/widgets/mermaid_block.dart';
  const servicePath = 'lib/services/mermaid/mermaid_render_service.dart';
  const typesPath = 'lib/services/mermaid/mermaid_types.dart';

  late String block;
  late String service;

  setUpAll(() {
    block = File(blockPath).readAsStringSync();
    service = File(servicePath).readAsStringSync();
  });

  /// 按 `WebViewController()` 出现位置切块，每块代表一个 WebView 实例
  List<String> controllerBlocks(String source) {
    final parts = source.split('WebViewController()');
    return parts.skip(1).toList();
  }

  group('WebView 的 JS 执行模式', () {
    test('恰好创建 3 个 WebView：渲染页 1 个 + 展示页 2 个', () {
      expect(controllerBlocks(block).length, 3);
    });

    test('渲染页允许 JS（渲染必需）并且只通过 JFLoveBridge 回传', () {
      final renderer = controllerBlocks(block).first;
      expect(renderer.contains('JavaScriptMode.unrestricted'), isTrue);
      expect(renderer.contains('addJavaScriptChannel('), isTrue);
      expect(renderer.contains("'JFLoveBridge'"), isTrue);
      expect(renderer.contains('loadHtmlString(html)'), isTrue);
    });

    test('展示 SVG 的 WebView（静态图 + 全屏查看器）必须禁用 JS', () {
      final blocks = controllerBlocks(block);
      for (final b in blocks.skip(1)) {
        expect(
          b.contains('JavaScriptMode.disabled'),
          isTrue,
          reason: '展示 SVG 的 WebView 必须 JavaScriptMode.disabled',
        );
        expect(b.contains('JavaScriptMode.unrestricted'), isFalse);
      }
    });

    test('JS 模式只在渲染页出现一次（新增 WebView 不得悄悄放开 JS）', () {
      expect('JavaScriptMode.unrestricted'.allMatches(block).length, 1);
      expect('setJavaScriptMode('.allMatches(block).length, 3);
    });
  });

  group('导航委托只放行本地内容', () {
    test('每个 WebView 都注册了 NavigationDelegate', () {
      expect('setNavigationDelegate('.allMatches(block).length, 3);
      // 注意 `\b`：否则 `setNavigationDelegate(` 会把 `NavigationDelegate(` 也算进去
      expect(RegExp(r'\bNavigationDelegate\(').allMatches(block).length, 3);
    });

    test('白名单只有 about: / data:，其余一律 prevent', () {
      final schemes = RegExp(r"startsWith\('([^']+)'\)")
          .allMatches(block)
          .map((m) => m.group(1)!)
          .toSet();
      expect(schemes, {'about:', 'data:'});
      expect('NavigationDecision.prevent'.allMatches(block).length, 3);
    });

    test('不得放行 file:// 或 http(s) 导航', () {
      expect(block.contains("startsWith('file:'"), isFalse);
      expect(block.contains("startsWith('http"), isFalse);
    });
  });

  group('渲染运行时零网络引用', () {
    test('mermaid 相关源码不含 http(s) 地址', () {
      for (final entry in {blockPath: block, servicePath: service, typesPath: File(typesPath).readAsStringSync()}.entries) {
        expect(entry.value.contains('http://'), isFalse, reason: '${entry.key} 含 http://');
        expect(entry.value.contains('https://'), isFalse, reason: '${entry.key} 含 https://');
      }
    });

    test('渲染页由 loadHtmlString 加载本地内容（不是 loadRequest 远程地址）', () {
      expect(block.contains('loadHtmlString('), isTrue);
      expect(block.contains('loadRequest('), isFalse);
    });
  });

  group('日志不得出现图表源码 / SVG（§9.4）', () {
    test('debugPrint 的参数里没有源码 / SVG / bundle 变量', () {
      final offenders = <String>[];
      for (final entry in {blockPath: block, servicePath: service}.entries) {
        for (final line in entry.value.split('\n')) {
          final trimmed = line.trim();
          if (!trimmed.startsWith('debugPrint(') && !trimmed.startsWith('print(')) continue;
          for (final banned in ['code', 'svg', 'SVG', 'bundle', 'MM_CODE', 'text']) {
            if (trimmed.contains(banned)) {
              offenders.add('${entry.key}: $trimmed');
            }
          }
        }
      }
      expect(offenders, isEmpty, reason: '日志疑似打印了用户内容：\n${offenders.join('\n')}');
    });

    test('日志条目数量与内容稳定（只记异常对象）', () {
      final lines = service
          .split('\n')
          .map((l) => l.trim())
          .where((l) => l.startsWith('debugPrint('))
          .toList();
      expect(lines.length, 2);
      expect(lines.every((l) => l.contains(r'$e')), isTrue);
    });
  });

  group('实现期踩坑的回归锁', () {
    /// 去掉 Dart 行注释后再断言。
    ///
    /// ⚠ 必需：代码注释里会**提到**被否决的写法（就是为了讲清为什么不能用），
    /// 直接对原文做子串断言会被自己的注释绊倒 —— 例如 `_wrap` 的注释里
    /// 解释了「为什么不能用 max-width:none」，于是 `contains('max-width:none')`
    /// 永远为真。测行为就要测代码。
    String codeOnly(String src) => src
        .split('\n')
        .where((line) => !line.trimLeft().startsWith('//'))
        .join('\n');

    test('静态 SVG 视图必须是 StatefulWidget 且响应 SVG 变化（避免每次重建新建 WebView）', () {
      expect(block.contains('class _StaticSvgView extends StatefulWidget'), isTrue);
      expect(block.contains('class _StaticSvgViewState extends State<_StaticSvgView>'), isTrue);
      expect(block.contains('void didUpdateWidget(covariant _StaticSvgView oldWidget)'), isTrue);
      expect(block.contains('_controller = WebViewController()'), isTrue);
      // 控制器只在 initState / 渲染方法里创建，不能在 build 里内联创建
      // （每次重建都会新建一个原生 WebView 且不释放）
      expect('WebViewController()'.allMatches(block).length, 3);
      expect(
        block.contains('WebViewWidget(controller: WebViewController()'),
        isFalse,
        reason: '不得在 build 内联创建 WebViewController',
      );
      // 渲染路径：先创建局部 controller，再交给状态字段持有
      expect(block.contains('_controller = controller;'), isTrue);    });

    test('SVG 尺寸策略：不放大、必要时等比缩小（v1.5.0 反馈修复）', () {
      // 这条用例**反转**了旧断言。旧断言锁的是 `width:100%!important`，
      // 而那正是缺陷本身：它把窄图横向拉变形（类图自然宽只有 ~131，
      // 拉到手机宽度后方框与文字严重变形）。Web 端实测并修过同一个坑。
      final wraps = codeOnly(block.split('_wrap(String svg)').last);
      expect(
        wraps.contains('width:100%!important'),
        isFalse,
        reason: '不得再用 width:100% 无条件拉伸；应为 width:auto',
      );
      expect(wraps.contains('width:auto!important'), isTrue);
      expect(wraps.contains('height:auto!important'), isTrue);
      // 窄图居中，不要贴在左边
      expect(wraps.contains('margin:0 auto'), isTrue);
      // ⚠ 必须是 max-width:100%（必要时等比缩小）。
      // 曾经用过 `max-width:none`：超宽图会同时向左右两侧溢出，
      // 而文档 scrollWidth 仍等于屏宽 —— **横滑也够不到**，
      // 实测就是这样把甘特图"藏"起来的。
      expect(
        wraps.contains('max-width:100%'),
        isTrue,
        reason: '必须允许等比缩小到屏宽内，否则超宽图会溢出且无法横滑看到',
      );
      expect(
        wraps.contains('max-width:none'),
        isFalse,
        reason: 'max-width:none 会让超宽图两侧溢出且够不到',
      );
      // 展示页也要有 viewport meta，保证 WebView 用设备宽度布局（不被整页缩放）
      expect(wraps.contains('name="viewport"'), isTrue);
    });

    test('全屏查看器同样不拉伸：允许等比缩小、不允许放大', () {
      final viewer = codeOnly(block.split('_viewerHtml(String svg)').last);
      expect(
        viewer.contains('width:100%!important'),
        isFalse,
        reason: '全屏查看器也不得无条件拉伸',
      );
      expect(viewer.contains('width:auto!important'), isTrue);
      expect(viewer.contains('max-width:100%'), isTrue);
      expect(viewer.contains('height:auto!important'), isTrue);
    });

    test('全屏查看器用 InteractiveViewer 支持双指缩放（AC-10）', () {
      expect(block.contains('InteractiveViewer('), isTrue);
      expect(block.contains('minScale: 0.5'), isTrue);
      expect(block.contains('maxScale: 6'), isTrue);
    });

    test('主题变化必须重渲染图表（颜色烘焙进 SVG，不是 CSS 变量）', () {
      expect(block.contains('didChangeDependencies'), isTrue);
      expect(block.contains('_lastBrightness != brightness'), isTrue);
    });

    test('超时降级用 Timer 且渲染超时时间来自渲染服务（3s→8s 统一口径）', () {
      expect(block.contains('MermaidRenderService.renderTimeout'), isTrue);
      expect(block.contains('_Phase.error'), isTrue);
    });
  });
}
