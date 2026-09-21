import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:jflove_app/config/design_tokens.dart';
import 'package:jflove_app/config/theme.dart';
import 'package:jflove_app/providers/theme_provider.dart';

/// 主题模式与令牌投影测试（v1.5.0）
///
/// 覆盖三端必须一致的语义：三态主题（跟随系统 / 亮色 / 暗色）的映射与持久化
/// 标识稳定，以及 AppTokens 的 light/dark 投影与 lerp 行为正确。
void main() {
  group('AppThemeMode', () {
    test('持久化 key 稳定且唯一（不要用 enum.index，重排序会串档）', () {
      final keys = AppThemeMode.values.map((m) => m.key).toList();
      expect(keys, ['system', 'light', 'dark']);
      expect(keys.toSet().length, keys.length, reason: 'key 必须唯一');
    });

    test('每个模式都有中文展示名', () {
      for (final mode in AppThemeMode.values) {
        expect(mode.label, isNotEmpty);
      }
      expect(AppThemeMode.system.label, '跟随系统');
      expect(AppThemeMode.light.label, '亮色');
      expect(AppThemeMode.dark.label, '暗色');
    });

    test('fromKey 能往返，未知值/null 回退 system', () {
      for (final mode in AppThemeMode.values) {
        expect(AppThemeMode.fromKey(mode.key), mode);
      }
      expect(AppThemeMode.fromKey(null), AppThemeMode.system);
      expect(AppThemeMode.fromKey(''), AppThemeMode.system);
      expect(AppThemeMode.fromKey('nonsense'), AppThemeMode.system);
      // 大小写敏感：不接受 'Dark'
      expect(AppThemeMode.fromKey('Dark'), AppThemeMode.system);
    });

    test('映射到 Material ThemeMode 正确', () {
      expect(toMaterialThemeMode(AppThemeMode.system), ThemeMode.system);
      expect(toMaterialThemeMode(AppThemeMode.light), ThemeMode.light);
      expect(toMaterialThemeMode(AppThemeMode.dark), ThemeMode.dark);
    });
  });

  group('AppTokens', () {
    test('亮/暗两套令牌的语义名完全对齐（不能缺字段）', () {
      final l = AppTokens.light;
      final d = AppTokens.dark;

      // 颜色：暗色必须与亮色不同，否则等于没做暗色
      expect(l.bgCanvas, isNot(d.bgCanvas));
      expect(l.bgSurface, isNot(d.bgSurface));
      expect(l.bgSunken, isNot(d.bgSunken));
      expect(l.fgDefault, isNot(d.fgDefault));
      expect(l.fgMuted, isNot(d.fgMuted));
      expect(l.fgSubtle, isNot(d.fgSubtle));
      expect(l.borderSubtle, isNot(d.borderSubtle));
      expect(l.borderDefault, isNot(d.borderDefault));

      // 圆角/间距/动效是跨主题恒定的
      expect(l.rSm, d.rSm);
      expect(l.rLg, d.rLg);
      expect(l.s1, d.s1);
      expect(l.s6, d.s6);
      expect(l.durFast, d.durFast);
      expect(l.durBase, d.durBase);
    });

    test('亮色为浅底深字，暗色为深底浅字（对比方向不能反）', () {
      final l = AppTokens.light;
      final d = AppTokens.dark;

      expect(
        l.bgCanvas.computeLuminance(),
        greaterThan(l.fgDefault.computeLuminance()),
        reason: '亮色模式必须是浅底深字',
      );
      expect(
        d.bgCanvas.computeLuminance(),
        lessThan(d.fgDefault.computeLuminance()),
        reason: '暗色模式必须是深底浅字',
      );
    });

    test('三级表面亮度单调（canvas → surface → sunken 层级不塌陷）', () {
      // 亮色：canvas 比 surface 略暗，sunken 更暗
      final l = AppTokens.light;
      expect(l.bgCanvas.computeLuminance(), lessThan(l.bgSurface.computeLuminance()));
      expect(l.bgSunken.computeLuminance(), lessThan(l.bgCanvas.computeLuminance()));

      // 暗色：canvas 最暗，surface 抬升一档，sunken 回到最暗
      final d = AppTokens.dark;
      expect(d.bgCanvas.computeLuminance(), lessThan(d.bgSurface.computeLuminance()));
      expect(d.bgSunken.computeLuminance(), lessThan(d.bgSurface.computeLuminance()));
    });

    test('品牌色与三端一致（Web --brand-500 / 桌面 BRAND_500）', () {
      expect(AppTokens.brand500, const Color(0xFF6366F1));
      // 品牌渐变两端必须是品牌色系
      final colors = AppTokens.light.gradBrand.colors;
      expect(colors.first, AppTokens.brand500);
      expect(colors.length, greaterThanOrEqualTo(2));
    });

    test('间距是 4pt 栅格且递增', () {
      final t = AppTokens.light;
      final steps = [t.s1, t.s2, t.s3, t.s4, t.s5, t.s6];
      expect(steps, [4, 8, 12, 16, 20, 24]);
      for (var i = 1; i < steps.length; i++) {
        expect(steps[i], greaterThan(steps[i - 1]));
      }
    });

    test('圆角递增且 rSm < rMd < rLg < rXl', () {
      final t = AppTokens.light;
      expect(t.rSm, lessThan(t.rMd));
      expect(t.rMd, lessThan(t.rLg));
      expect(t.rLg, lessThan(t.rXl));
    });

    test('三级阴影都存在且逐级扩散（e1 最收敛）', () {
      final t = AppTokens.light;
      for (final level in [t.e1, t.e2, t.e3]) {
        expect(level, isNotEmpty);
      }
      double maxBlur(List<BoxShadow> l) =>
          l.map((s) => s.blurRadius).reduce((a, b) => a > b ? a : b);
      expect(maxBlur(t.e1), lessThan(maxBlur(t.e2)));
      expect(maxBlur(t.e2), lessThan(maxBlur(t.e3)));
    });

    test('copyWith 只改指定字段', () {
      final base = AppTokens.light;
      final patched = base.copyWith(bgSurface: const Color(0xFF123456));
      expect(patched.bgSurface, const Color(0xFF123456));
      expect(patched.bgCanvas, base.bgCanvas);
      expect(patched.fgDefault, base.fgDefault);
      expect(patched.rLg, base.rLg);
    });

    test('lerp 在 t=0 与 t=1 处分别等于两端', () {
      final l = AppTokens.light;
      final d = AppTokens.dark;

      final at0 = l.lerp(d, 0) as AppTokens;
      expect(at0.bgCanvas, l.bgCanvas);
      expect(at0.bgSurface, l.bgSurface);

      final at1 = l.lerp(d, 1) as AppTokens;
      expect(at1.bgCanvas, d.bgCanvas);
      expect(at1.bgSurface, d.bgSurface);

      // 中途必须是插值出来的第三色，而不是简单取两端之一
      final mid = l.lerp(d, 0.5) as AppTokens;
      expect(mid.bgCanvas, isNot(l.bgCanvas));
      expect(mid.bgCanvas, isNot(d.bgCanvas));
    });

    test('lerp 传入非 AppTokens 时原样返回（不崩）', () {
      final l = AppTokens.light;
      expect(l.lerp(null, 0.5), l);
    });
  });

  group('主题接入', () {
    testWidgets('AppTheme.light 注册了 AppTokens.light', (tester) async {
      late AppTokens fromLight;
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light,
          home: Builder(
            builder: (context) {
              fromLight = context.tokens;
              return const SizedBox.shrink();
            },
          ),
        ),
      );
      expect(fromLight.bgCanvas, AppTokens.light.bgCanvas);
      expect(fromLight.fgDefault, AppTokens.light.fgDefault);
    });

    testWidgets('AppTheme.dark 注册了 AppTokens.dark', (tester) async {
      late AppTokens fromDark;
      // 注意：必须挂 **新的** MaterialApp 根，不能在同一棵树里换 `theme:`。
      // MaterialApp 会把子树包在 AnimatedTheme 里（200ms），换主题后立刻断言
      // 拿到的是 lerp(t=0) —— 即**上一个主题**的令牌。这是真实存在的陷阱，
      // 见下一个用例。
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.dark,
          home: Builder(
            builder: (context) {
              fromDark = context.tokens;
              return const SizedBox.shrink();
            },
          ),
        ),
      );
      expect(fromDark.bgCanvas, AppTokens.dark.bgCanvas);
      expect(fromDark.fgDefault, AppTokens.dark.fgDefault);
    });

    testWidgets('切换 theme 后有 200ms 主题动画，必须 pumpAndSettle 才拿到新令牌', (
      tester,
    ) async {
      // 这条不是"测试写法"问题，而是产品角度的真实约束：任何依赖
      // `context.tokens` 做**一次性计算**（例如把颜色烘焙进 SVG / 图片）的
      // widget，都不能指望换主题那一帧就拿到新值。MermaidBlock 正是靠
      // didChangeDependencies + 重新渲染来处理的。
      late AppTokens seen;
      Widget build(Brightness brightness) => MaterialApp(
        theme: AppTheme.light,
        darkTheme: AppTheme.dark,
        themeMode: brightness == Brightness.dark
            ? ThemeMode.dark
            : ThemeMode.light,
        home: Builder(
          builder: (context) {
            seen = context.tokens;
            return const SizedBox.shrink();
          },
        ),
      );

      await tester.pumpWidget(build(Brightness.light));
      await tester.pumpAndSettle();
      expect(seen.bgCanvas, AppTokens.light.bgCanvas);

      await tester.pumpWidget(build(Brightness.dark));
      // 动画起点仍是亮色令牌（这就是陷阱本身）
      expect(seen.bgCanvas, AppTokens.light.bgCanvas);

      await tester.pumpAndSettle();
      expect(seen.bgCanvas, AppTokens.dark.bgCanvas);
    });

    testWidgets('未注册扩展时 context.tokens 回退到亮色而不是抛异常', (tester) async {      late AppTokens fallback;
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) {
              fallback = context.tokens;
              return const SizedBox.shrink();
            },
          ),
        ),
      );
      expect(fallback.bgCanvas, AppTokens.light.bgCanvas);
    });
  });
}
