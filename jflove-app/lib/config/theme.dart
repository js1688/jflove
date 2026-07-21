import 'package:flutter/material.dart';

/// JFLove Material Design 3 主题
class AppTheme {
  AppTheme._();

  /// 浅色主题
  static ThemeData get light => ThemeData(
        useMaterial3: true,
        colorSchemeSeed: const Color(0xFF1565C0),
        brightness: Brightness.light,
      );

  /// 深色主题
  static ThemeData get dark => ThemeData(
        useMaterial3: true,
        colorSchemeSeed: const Color(0xFF1565C0),
        brightness: Brightness.dark,
      );
}
