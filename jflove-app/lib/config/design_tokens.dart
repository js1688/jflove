import 'package:flutter/material.dart';

/// JFLove 设计令牌（三端统一）
///
/// 与 Web 端 `jflove-web/src/styles/tokens.css`、桌面端
/// `jflove-desktop/src/ui/theme/tokens.py` 是同一套语义名的投影。
///
/// 约定（需求 AC-3）：**本文件是移动端唯一允许出现颜色字面量的地方**；
/// 页面与组件一律通过 `Theme.of(context).extension<AppTokens>()!`
/// 或 `colorScheme.*` 取值，不得再写 `Colors.blue.shade100` 这类硬编码。
@immutable
class AppTokens extends ThemeExtension<AppTokens> {
  const AppTokens({
    required this.bgCanvas,
    required this.bgSurface,
    required this.bgSunken,
    required this.bgActive,
    required this.fgDefault,
    required this.fgMuted,
    required this.fgSubtle,
    required this.borderSubtle,
    required this.borderDefault,
    required this.gradBrand,
    required this.gradBrandSoft,
    required this.rSm,
    required this.rMd,
    required this.rLg,
    required this.rXl,
    required this.s1,
    required this.s2,
    required this.s3,
    required this.s4,
    required this.s5,
    required this.s6,
    required this.e1,
    required this.e2,
    required this.e3,
    required this.durFast,
    required this.durBase,
  });

  // ── 表面（三级）──
  final Color bgCanvas;
  final Color bgSurface;
  final Color bgSunken;
  final Color bgActive;

  // ── 文字 ──
  final Color fgDefault;
  final Color fgMuted;
  final Color fgSubtle;

  // ── 边框 ──
  final Color borderSubtle;
  final Color borderDefault;

  // ── 渐变（仅用于"品牌时刻"：Logo、主按钮、指示器、进度填充）──
  final LinearGradient gradBrand;
  final LinearGradient gradBrandSoft;

  // ── 圆角 ──
  final double rSm;
  final double rMd;
  final double rLg;
  final double rXl;

  // ── 间距（4pt 栅格）──
  final double s1;
  final double s2;
  final double s3;
  final double s4;
  final double s5;
  final double s6;

  // ── 阴影（3 级，够用且不喧宾夺主）──
  final List<BoxShadow> e1;
  final List<BoxShadow> e2;
  final List<BoxShadow> e3;

  // ── 动效 ──
  final Duration durFast;
  final Duration durBase;

  /// 品牌色（与 Web `--brand-500` / 桌面 `BRAND_500` 一致）
  static const Color brand500 = Color(0xFF6366F1);
  static const Color brand600 = Color(0xFF4F46E5);
  static const Color brand300 = Color(0xFFA5B4FC);
  static const Color brand100 = Color(0xFFE0E7FF);
  static const Color brand50 = Color(0xFFEEF2FF);

  /// 语义色
  static const Color success500 = Color(0xFF10B981);
  static const Color warning500 = Color(0xFFF59E0B);
  static const Color danger500 = Color(0xFFEF4444);
  static const Color info500 = Color(0xFF0EA5E9);
  static const Color accent500 = Color(0xFFF43F5E);

  static const AppTokens light = AppTokens(
    bgCanvas: Color(0xFFF6F7FB),
    bgSurface: Colors.white,
    bgSunken: Color(0xFFF1F5F9),
    bgActive: brand50,
    fgDefault: Color(0xFF0F172A),
    fgMuted: Color(0xFF64748B),
    fgSubtle: Color(0xFF94A3B8),
    borderSubtle: Color(0xFFEDF0F6),
    borderDefault: Color(0xFFE2E8F0),
    gradBrand: LinearGradient(
      begin: Alignment.topLeft,
      end: Alignment.bottomRight,
      colors: [Color(0xFF6366F1), Color(0xFF8B5CF6), Color(0xFFA855F7)],
      stops: [0.0, 0.55, 1.0],
    ),
    gradBrandSoft: LinearGradient(
      begin: Alignment.topLeft,
      end: Alignment.bottomRight,
      colors: [Color(0xFFEEF2FF), Color(0xFFF5F3FF), Color(0xFFFAF5FF)],
      stops: [0.0, 0.6, 1.0],
    ),
    rSm: 6,
    rMd: 8,
    rLg: 12,
    rXl: 16,
    s1: 4,
    s2: 8,
    s3: 12,
    s4: 16,
    s5: 20,
    s6: 24,
    e1: [
      BoxShadow(color: Color(0x0D0F172A), blurRadius: 2, offset: Offset(0, 1)),
      BoxShadow(color: Color(0x080F172A), blurRadius: 1, offset: Offset(0, 1)),
    ],
    e2: [
      BoxShadow(color: Color(0x0A0F172A), blurRadius: 6, offset: Offset(0, 2)),
      BoxShadow(color: Color(0x0A0F172A), blurRadius: 2, offset: Offset(0, 1)),
    ],
    e3: [
      BoxShadow(color: Color(0x120F172A), blurRadius: 20, offset: Offset(0, 8)),
      BoxShadow(color: Color(0x0A0F172A), blurRadius: 4, offset: Offset(0, 2)),
    ],
    durFast: Duration(milliseconds: 120),
    durBase: Duration(milliseconds: 180),
  );

  static const AppTokens dark = AppTokens(
    bgCanvas: Color(0xFF080D18),
    bgSurface: Color(0xFF0F172A),
    bgSunken: Color(0xFF0B1220),
    // 暗色下的"激活底"用品牌色低透明度叠加，避免浅色块在深底上发白
    bgActive: Color(0x246366F1),
    fgDefault: Color(0xFFE8EDF7),
    fgMuted: Color(0xFF94A3B8),
    fgSubtle: Color(0xFF64748B),
    borderSubtle: Color(0x0FFFFFFF),
    borderDefault: Color(0x1AFFFFFF),
    gradBrand: LinearGradient(
      begin: Alignment.topLeft,
      end: Alignment.bottomRight,
      colors: [Color(0xFF6366F1), Color(0xFF8B5CF6), Color(0xFFA855F7)],
      stops: [0.0, 0.55, 1.0],
    ),
    gradBrandSoft: LinearGradient(
      begin: Alignment.topLeft,
      end: Alignment.bottomRight,
      colors: [Color(0x2E6366F1), Color(0x1FA855F7)],
    ),
    rSm: 6,
    rMd: 8,
    rLg: 12,
    rXl: 16,
    s1: 4,
    s2: 8,
    s3: 12,
    s4: 16,
    s5: 20,
    s6: 24,
    e1: [BoxShadow(color: Color(0x66000000), blurRadius: 2, offset: Offset(0, 1))],
    e2: [
      BoxShadow(color: Color(0x59000000), blurRadius: 6, offset: Offset(0, 2)),
      BoxShadow(color: Color(0x66000000), blurRadius: 2, offset: Offset(0, 1)),
    ],
    e3: [
      BoxShadow(color: Color(0x73000000), blurRadius: 20, offset: Offset(0, 8)),
      BoxShadow(color: Color(0x66000000), blurRadius: 4, offset: Offset(0, 2)),
    ],
    durFast: Duration(milliseconds: 120),
    durBase: Duration(milliseconds: 180),
  );

  @override
  AppTokens copyWith({
    Color? bgCanvas,
    Color? bgSurface,
    Color? bgSunken,
    Color? bgActive,
    Color? fgDefault,
    Color? fgMuted,
    Color? fgSubtle,
    Color? borderSubtle,
    Color? borderDefault,
    LinearGradient? gradBrand,
    LinearGradient? gradBrandSoft,
    double? rSm,
    double? rMd,
    double? rLg,
    double? rXl,
    double? s1,
    double? s2,
    double? s3,
    double? s4,
    double? s5,
    double? s6,
    List<BoxShadow>? e1,
    List<BoxShadow>? e2,
    List<BoxShadow>? e3,
    Duration? durFast,
    Duration? durBase,
  }) {
    return AppTokens(
      bgCanvas: bgCanvas ?? this.bgCanvas,
      bgSurface: bgSurface ?? this.bgSurface,
      bgSunken: bgSunken ?? this.bgSunken,
      bgActive: bgActive ?? this.bgActive,
      fgDefault: fgDefault ?? this.fgDefault,
      fgMuted: fgMuted ?? this.fgMuted,
      fgSubtle: fgSubtle ?? this.fgSubtle,
      borderSubtle: borderSubtle ?? this.borderSubtle,
      borderDefault: borderDefault ?? this.borderDefault,
      gradBrand: gradBrand ?? this.gradBrand,
      gradBrandSoft: gradBrandSoft ?? this.gradBrandSoft,
      rSm: rSm ?? this.rSm,
      rMd: rMd ?? this.rMd,
      rLg: rLg ?? this.rLg,
      rXl: rXl ?? this.rXl,
      s1: s1 ?? this.s1,
      s2: s2 ?? this.s2,
      s3: s3 ?? this.s3,
      s4: s4 ?? this.s4,
      s5: s5 ?? this.s5,
      s6: s6 ?? this.s6,
      e1: e1 ?? this.e1,
      e2: e2 ?? this.e2,
      e3: e3 ?? this.e3,
      durFast: durFast ?? this.durFast,
      durBase: durBase ?? this.durBase,
    );
  }

  @override
  AppTokens lerp(ThemeExtension<AppTokens>? other, double t) {
    if (other is! AppTokens) return this;
    return AppTokens(
      bgCanvas: Color.lerp(bgCanvas, other.bgCanvas, t)!,
      bgSurface: Color.lerp(bgSurface, other.bgSurface, t)!,
      bgSunken: Color.lerp(bgSunken, other.bgSunken, t)!,
      bgActive: Color.lerp(bgActive, other.bgActive, t)!,
      fgDefault: Color.lerp(fgDefault, other.fgDefault, t)!,
      fgMuted: Color.lerp(fgMuted, other.fgMuted, t)!,
      fgSubtle: Color.lerp(fgSubtle, other.fgSubtle, t)!,
      borderSubtle: Color.lerp(borderSubtle, other.borderSubtle, t)!,
      borderDefault: Color.lerp(borderDefault, other.borderDefault, t)!,
      gradBrand: t < 0.5 ? gradBrand : other.gradBrand,
      gradBrandSoft: t < 0.5 ? gradBrandSoft : other.gradBrandSoft,
      rSm: lerpDouble(rSm, other.rSm, t),
      rMd: lerpDouble(rMd, other.rMd, t),
      rLg: lerpDouble(rLg, other.rLg, t),
      rXl: lerpDouble(rXl, other.rXl, t),
      s1: lerpDouble(s1, other.s1, t),
      s2: lerpDouble(s2, other.s2, t),
      s3: lerpDouble(s3, other.s3, t),
      s4: lerpDouble(s4, other.s4, t),
      s5: lerpDouble(s5, other.s5, t),
      s6: lerpDouble(s6, other.s6, t),
      e1: BoxShadow.lerpList(e1, other.e1, t)!,
      e2: BoxShadow.lerpList(e2, other.e2, t)!,
      e3: BoxShadow.lerpList(e3, other.e3, t)!,
      durFast: durFast,
      durBase: durBase,
    );
  }

  static double lerpDouble(double a, double b, double t) => a + (b - a) * t;
}

/// 便捷扩展：`context.tokens.bgSurface`
extension AppTokensX on BuildContext {
  AppTokens get tokens => Theme.of(this).extension<AppTokens>() ?? AppTokens.light;
}
