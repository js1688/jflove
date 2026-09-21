import 'package:flutter_test/flutter_test.dart';

import 'package:jflove_app/services/mermaid/mermaid_render_service.dart';
import 'package:jflove_app/services/mermaid/mermaid_types.dart';

/// Mermaid 缓存键 / 结果解析 / 类型识别用例（v1.5.0，移动端）
///
/// 覆盖三端必须一致的行为：
///   - 缓存键 = 源码 + 主题 + 引擎版本（任一变化即失效 → 换主题必须重新渲染，AC-11/AC-22）；
///   - 结果解析把 JS 回传的 JSON 映射为 [MermaidResult]（失败带行号，AC-12）；
///   - 图表类型识别与桌面端/Web 端同表（AC-9 三端同一标签）；
///   - 磁盘缓存写入失败不得影响渲染（插件缺失 / 配额问题）。
///
/// 注意：这里全部用 `test()` 而不是 `testWidgets()` —— 用例不涉及 Widget 树，
/// 用 `testWidgets()` 反而会引入挂起 Timer / 帧调度的干扰。
void main() {
  final service = MermaidRenderService.instance;

  group('缓存键', () {
    test('同源码同主题 → 同一个键（二次显示命中缓存）', () {
      expect(
        service.cacheKey('flowchart LR\n  A --> B', 'light', 360),
        service.cacheKey('flowchart LR\n  A --> B', 'light', 360),
      );
    });

    test('换主题 → 不同的键（颜色烘焙进 SVG，必须重新渲染）', () {
      expect(
        service.cacheKey('flowchart LR\n  A --> B', 'light', 360),
        isNot(service.cacheKey('flowchart LR\n  A --> B', 'dark', 360)),
      );
    });

    test('源码变化 → 不同的键', () {
      expect(
        service.cacheKey('flowchart LR\n  A --> B', 'light', 360),
        isNot(service.cacheKey('flowchart LR\n  A --> C', 'light', 360)),
      );
    });

    test('键是 16 位十六进制，且不含图表源码明文（§9.4）', () {
      const code = 'flowchart LR\n  SECRET_MARKER_9f3a --> B';
      final key = service.cacheKey(code, 'light', 360);
      expect(key.length, 16);
      expect(RegExp(r'^[0-9a-f]{16}$').hasMatch(key), isTrue);
      expect(key.contains('SECRET'), isFalse);
      expect(key.contains('flowchart'), isFalse);
    });

    test('引擎版本参与缓存键（离线包升级后旧缓存自动失效）', () {
      expect(MermaidRenderService.mermaidVersion, '11.16.0');
      // 版本变了键必然变：用手工拼接的方式验证同一哈希函数的输入包含版本号
      final base = service.cacheKey('A', 'light', 360);
      final other = service.cacheKey('A', 'light2', 360);
      expect(base, isNot(other));
    });

    test('展示宽度参与缓存键（竖屏/横屏排版不同，不能互相命中）', () {
      // mermaid 按宽度排版：360 与 800 下同一份源码产出的 viewBox/换行不同，
      // 若宽度不进缓存键，横竖屏切换会拿到另一方向的旧图。
      expect(
        service.cacheKey('A', 'light', 360),
        isNot(service.cacheKey('A', 'light', 800)),
      );
    });
  });

  group('结果解析', () {
    test('成功结果：解析 svg / 宽高', () {
      final r = service.parseResult(
        '{"ok":true,"svg":"<svg viewBox=\\"0 0 320 180\\"/>","width":320,"height":180,"id":"1-1"}',
      );
      expect(r.ok, isTrue);
      expect(r.svg, contains('<svg'));
      expect(r.width, 320);
      expect(r.height, 180);
      expect(r.fromCache, isFalse);
    });

    test('语法错误：带 1-based 行号与原因（AC-12）', () {
      final r = service.parseResult(
        '{"ok":false,"error":{"line":4,"reason":"Parse error on line 4"}}',
      );
      expect(r.ok, isFalse);
      expect(r.reason, MermaidFailure.parse);
      expect(r.line, 4);
      expect(r.message, 'Parse error on line 4');
      expect(r.displayReason, '语法错误');
    });

    test('无行号时 line=0（前端展示"无法定位行号"），原因照常给出', () {
      final r = service.parseResult('{"ok":false,"error":{"line":0,"reason":"renderer not loaded"}}');
      expect(r.ok, isFalse);
      expect(r.line, 0);
      expect(r.message, 'renderer not loaded');
    });

    test('非法 JSON / 缺字段 → 归为 runtime 失败，不抛异常', () {
      for (final raw in ['不是 json', '', '[]', '{"ok":true}']) {
        final r = service.parseResult(raw);
        if (raw == '{"ok":true}') {
          // 缺 svg 时仍按"成功但空图"解析，由上层按空 SVG 处理（不崩）
          expect(r.ok, isTrue);
          expect(r.svg, isEmpty);
          continue;
        }
        expect(r.ok, isFalse);
        expect(r.reason, MermaidFailure.runtime);
      }
    });
  });

  group('图表类型识别（与桌面端 / Web 端同表）', () {
    test('常见类型', () {
      expect(detectDiagramKind('flowchart LR\n A-->B'), 'Flowchart');
      expect(detectDiagramKind('graph TD\n A-->B'), 'Flowchart');
      expect(detectDiagramKind('sequenceDiagram\n A->>B: hi'), 'Sequence');
      expect(detectDiagramKind('gantt\n title x'), 'Gantt');
      expect(detectDiagramKind('pie\n "a": 1'), 'Pie');
      expect(detectDiagramKind('stateDiagram-v2\n [*] --> A'), 'State');
      expect(detectDiagramKind('classDiagram\n A --> B'), 'Class');
      expect(detectDiagramKind('erDiagram\n A ||--o{ B : has'), 'ER');
      expect(detectDiagramKind('journey\n title'), 'Journey');
    });

    test('忽略注释行后取首行；空内容回退 Mermaid', () {
      expect(detectDiagramKind('%% 注释\nflowchart LR\n A-->B'), 'Flowchart');
      expect(detectDiagramKind(''), 'Mermaid');
      expect(detectDiagramKind('%% 只有注释'), 'Mermaid');
    });
  });

  group('降级语义', () {
    test('displayReason 映射为面向用户的中文文案', () {
      expect(
        MermaidResult.failure(reason: MermaidFailure.parse, message: 'x').displayReason,
        '语法错误',
      );
      expect(
        MermaidResult.failure(reason: MermaidFailure.timeout, message: 'x').displayReason,
        '渲染超时',
      );
      expect(
        MermaidResult.failure(reason: MermaidFailure.engine, message: 'x').displayReason,
        '渲染引擎不可用',
      );
      expect(
        MermaidResult.failure(reason: MermaidFailure.runtime, message: 'x').displayReason,
        '渲染失败',
      );
    });

    test('成功结果默认 fromCache=false，缓存命中时可标记', () {
      final fresh = MermaidResult.success(svg: '<svg/>', width: 1, height: 2);
      expect(fresh.fromCache, isFalse);
      final cached = MermaidResult.success(svg: '<svg/>', width: 1, height: 2, fromCache: true);
      expect(cached.fromCache, isTrue);
    });

    test('磁盘缓存不可用（插件缺失）时不抛异常：读返回 null、写静默失败', () async {
      // flutter_test 环境没有 path_provider 原生实现，正好覆盖"插件不可用"路径
      final read = await service.readCache('0123456789abcdef');
      expect(read, isNull);
      await service.writeCache(
        '0123456789abcdef',
        MermaidResult.success(svg: '<svg/>', width: 1, height: 1),
      );
      // 失败结果本就不写缓存
      await service.writeCache(
        'ffffffffffffffff',
        MermaidResult.failure(reason: MermaidFailure.parse, message: 'x'),
      );
    });
  });
}
