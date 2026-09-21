import 'package:flutter/cupertino.dart' show CupertinoPageTransitionsBuilder;
import 'package:flutter/material.dart';

import 'design_tokens.dart';

/// JFLove 主题（Material 3 + 设计令牌）
///
/// v1.5.0 变化：
///   - 主色由蓝色种子 `0xFF1565C0` 换成**品牌紫靛** `0xFF6366F1`（三端统一）；
///   - 显式给出 `ColorScheme` 关键角色，不再纯靠 seed 派生
///     （否则无法与 Web / 桌面的色值对齐）；
///   - 注册 `AppTokens` ThemeExtension（间距/圆角/阴影/渐变）；
///   - 卡片由「默认 elevation 浮起」改为「1px 描边 + 极浅阴影」（与效果图一致）；
///   - 补齐 appBar / navigationBar / input / dialog / snackBar 主题，
///     消除原先各页面 ad-hoc 圆角与硬编码 `Colors.*` 的根源。
class AppTheme {
  AppTheme._();

  static ThemeData get light => _build(Brightness.light, AppTokens.light);
  static ThemeData get dark => _build(Brightness.dark, AppTokens.dark);

  static ThemeData _build(Brightness brightness, AppTokens t) {
    final isDark = brightness == Brightness.dark;
    final scheme = ColorScheme.fromSeed(
      seedColor: AppTokens.brand500,
      brightness: brightness,
    ).copyWith(
      primary: AppTokens.brand500,
      onPrimary: Colors.white,
      primaryContainer: isDark ? const Color(0xFF312E81) : AppTokens.brand50,
      onPrimaryContainer: isDark ? AppTokens.brand300 : const Color(0xFF312E81),
      secondary: AppTokens.accent500,
      surface: t.bgSurface,
      onSurface: t.fgDefault,
      surfaceContainerHighest: t.bgSunken,
      outline: t.borderDefault,
      outlineVariant: t.borderSubtle,
      error: AppTokens.danger500,
    );

    final base = ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme: scheme,
      scaffoldBackgroundColor: t.bgCanvas,
      // 字体：不打包 Inter（避免额外资源与授权问题），走系统字体；
      // 字号阶梯由下面的 textTheme 统一，保证三端观感接近。
      fontFamily: null,
    );

    return base.copyWith(
      extensions: <ThemeExtension<dynamic>>[t],

      // ── 卡片：描边 + 极浅阴影（不用默认浮起）──
      cardTheme: CardThemeData(
        elevation: 0,
        color: t.bgSurface,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(t.rLg),
          side: BorderSide(color: t.borderSubtle),
        ),
      ),

      appBarTheme: AppBarTheme(
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        backgroundColor: t.bgSurface,
        foregroundColor: t.fgDefault,
        surfaceTintColor: Colors.transparent,
        titleTextStyle: TextStyle(
          fontSize: 19,
          fontWeight: FontWeight.w600,
          letterSpacing: -0.2,
          color: t.fgDefault,
        ),
      ),

      navigationBarTheme: NavigationBarThemeData(
        height: 64,
        elevation: 0,
        backgroundColor: t.bgSurface,
        surfaceTintColor: Colors.transparent,
        indicatorColor: t.bgActive,
        labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          final selected = states.contains(WidgetState.selected);
          return TextStyle(
            fontSize: 10.5,
            fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
            color: selected ? AppTokens.brand600 : t.fgSubtle,
          );
        }),
        iconTheme: WidgetStateProperty.resolveWith((states) {
          final selected = states.contains(WidgetState.selected);
          return IconThemeData(
            size: 22,
            color: selected
                ? (isDark ? AppTokens.brand300 : AppTokens.brand600)
                : t.fgSubtle,
          );
        }),
      ),

      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: t.bgSunken,
        isDense: true,
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        hintStyle: TextStyle(color: t.fgSubtle, fontSize: 13.5),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(t.rLg),
          borderSide: BorderSide(color: t.borderSubtle),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(t.rLg),
          borderSide: BorderSide(color: t.borderSubtle),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(t.rLg),
          borderSide: const BorderSide(color: AppTokens.brand500, width: 1.4),
        ),
      ),

      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size(0, 44),
          padding: const EdgeInsets.symmetric(horizontal: 18),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(t.rMd)),
          textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size(0, 44),
          padding: const EdgeInsets.symmetric(horizontal: 16),
          foregroundColor: t.fgDefault,
          side: BorderSide(color: t.borderDefault),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(t.rMd)),
          textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: AppTokens.brand600,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(t.rMd)),
          textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500),
        ),
      ),

      floatingActionButtonTheme: FloatingActionButtonThemeData(
        elevation: 0,
        highlightElevation: 0,
        backgroundColor: AppTokens.brand500,
        foregroundColor: Colors.white,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
      ),

      dialogTheme: DialogThemeData(
        elevation: 0,
        backgroundColor: t.bgSurface,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(t.rXl)),
        titleTextStyle: TextStyle(
          fontSize: 16,
          fontWeight: FontWeight.w600,
          color: t.fgDefault,
        ),
        contentTextStyle: TextStyle(fontSize: 14, color: t.fgMuted, height: 1.5),
      ),

      bottomSheetTheme: BottomSheetThemeData(
        elevation: 0,
        backgroundColor: t.bgSurface,
        surfaceTintColor: Colors.transparent,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
        ),
      ),

      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        backgroundColor: isDark ? const Color(0xFF1E293B) : const Color(0xFF0F172A),
        contentTextStyle: const TextStyle(fontSize: 13, color: Colors.white),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(t.rMd)),
        elevation: 0,
      ),

      dividerTheme: DividerThemeData(
        color: t.borderSubtle,
        thickness: 1,
        space: 1,
      ),

      listTileTheme: ListTileThemeData(
        iconColor: t.fgMuted,
        textColor: t.fgDefault,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(t.rMd)),
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      ),

      chipTheme: ChipThemeData(
        elevation: 0,
        side: BorderSide(color: t.borderSubtle),
        backgroundColor: t.bgSunken,
        labelStyle: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600, color: t.fgMuted),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(999)),
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      ),

      progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: AppTokens.brand500,
        linearMinHeight: 6,
      ),

      // 页面切换：淡入 + 轻微上移（比默认的滑动更贴合"工具类应用"观感）
      pageTransitionsTheme: const PageTransitionsTheme(
        builders: <TargetPlatform, PageTransitionsBuilder>{
          TargetPlatform.android: FadeUpwardsPageTransitionsBuilder(),
          TargetPlatform.iOS: CupertinoPageTransitionsBuilder(),
        },
      ),

      textTheme: _textTheme(base.textTheme, t),
    );
  }

  /// 字号阶梯（与 Web / 桌面对齐）
  static TextTheme _textTheme(TextTheme base, AppTokens t) {
    return base.copyWith(
      headlineSmall: TextStyle(fontSize: 22, fontWeight: FontWeight.w700, letterSpacing: -0.3, color: t.fgDefault),
      titleLarge: TextStyle(fontSize: 19, fontWeight: FontWeight.w600, letterSpacing: -0.2, color: t.fgDefault),
      titleMedium: TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: t.fgDefault),
      titleSmall: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600, color: t.fgDefault),
      bodyLarge: TextStyle(fontSize: 14.5, height: 1.5, color: t.fgDefault),
      bodyMedium: TextStyle(fontSize: 13.5, height: 1.5, color: t.fgDefault),
      bodySmall: TextStyle(fontSize: 12, color: t.fgMuted),
      labelLarge: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600, color: t.fgDefault),
      labelMedium: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w500, color: t.fgMuted),
      labelSmall: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w500, color: t.fgSubtle),
    );
  }
}
